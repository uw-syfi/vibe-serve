from pathlib import Path

import pytest

from vibesys.agents import cli_docker
from vibesys.agents.cli_docker import DockerAuthPath


def test_auth_import_copies_directories_and_files_to_private_writable_paths(  # noqa: ANN201  # tracked: #288
    tmp_path: Path,
    monkeypatch,  # noqa: ANN001  # tracked: #288
):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    auth_file = tmp_path / "auth.json"
    auth_file.write_text('{"synthetic": true}\n')
    monkeypatch.setitem(
        cli_docker.DOCKER_AUTH_PATHS,
        "fixture",
        [
            DockerAuthPath(state_dir, "/root/.fixture"),
            DockerAuthPath(auth_file, "/root/.fixture.json"),
        ],
    )

    assert cli_docker.auth_bind_mounts("fixture") == [
        (str(state_dir), "/opt/vibesys-auth/0", True),
        (str(auth_file), "/opt/vibesys-auth/1", True),
    ]
    assert cli_docker.auth_copy_commands("fixture") == [
        "mkdir -p /root/.fixture && cp -a /opt/vibesys-auth/0/. /root/.fixture/",
        "mkdir -p /root && cp -a /opt/vibesys-auth/1 /root/.fixture.json",
    ]


def test_auth_import_uses_provider_native_container_paths():  # noqa: ANN201  # tracked: #288
    assert {
        provider: [
            (spec.host_path.relative_to(Path.home()).as_posix(), spec.container_path)
            for spec in specs
        ]
        for provider, specs in cli_docker.DOCKER_AUTH_PATHS.items()
    } == {
        "claude": [
            (".claude/.credentials.json", "/root/.claude/.credentials.json"),
            (".claude/settings.json", "/root/.claude/settings.json"),
            (".claude/settings.local.json", "/root/.claude/settings.local.json"),
            (".claude.json", "/root/.claude.json"),
        ],
        "gemini": [
            (".gemini/oauth_creds.json", "/root/.gemini/oauth_creds.json"),
            (".gemini/google_accounts.json", "/root/.gemini/google_accounts.json"),
            (".gemini/settings.json", "/root/.gemini/settings.json"),
            (".gemini/.env", "/root/.gemini/.env"),
        ],
        "codex": [
            (".codex/auth.json", "/root/.codex/auth.json"),
            (".codex/config.toml", "/root/.codex/config.toml"),
        ],
        "opencode": [
            (
                ".local/share/opencode/auth.json",
                "/root/.local/share/opencode/auth.json",
            ),
            (".config/opencode/opencode.json", "/root/.config/opencode/opencode.json"),
            (
                ".config/opencode/opencode.jsonc",
                "/root/.config/opencode/opencode.jsonc",
            ),
            (".config/opencode/config.json", "/root/.config/opencode/config.json"),
            (
                ".config/opencode/config.jsonc",
                "/root/.config/opencode/config.jsonc",
            ),
            (".config/opencode/.env", "/root/.config/opencode/.env"),
        ],
    }


def test_provider_auth_imports_exclude_bulk_runtime_roots():  # noqa: ANN201  # tracked: #288
    configured_sources = {
        spec.host_path for specs in cli_docker.DOCKER_AUTH_PATHS.values() for spec in specs
    }

    assert configured_sources.isdisjoint(
        {
            Path.home() / ".claude",
            Path.home() / ".gemini",
            Path.home() / ".codex",
            Path.home() / ".local" / "share" / "opencode",
            Path.home() / ".config" / "opencode",
        }
    )


def test_provider_auth_env_registry_covers_credentials_not_model_selection():  # noqa: ANN201  # tracked: #288
    assert cli_docker.DOCKER_AUTH_ENV_VARS == {
        "claude": (
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_CUSTOM_HEADERS",
        ),
        "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "codex": ("OPENAI_API_KEY", "OPENAI_BASE_URL"),
        "opencode": (),
    }
    # VibeSys owns per-role model selection; a host export must not override it.
    forwarded = {name for names in cli_docker.DOCKER_AUTH_ENV_VARS.values() for name in names}
    assert forwarded.isdisjoint({"ANTHROPIC_MODEL", "OPENAI_MODEL", "GEMINI_MODEL"})
    assert set(cli_docker.DOCKER_AUTH_ENV_VARS) == set(cli_docker.DOCKER_AUTH_PATHS)


def test_auth_env_passthrough_forwards_only_variables_the_host_actually_set(  # noqa: ANN201  # tracked: #288
    monkeypatch,  # noqa: ANN001  # tracked: #288
):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token-value")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.invalid/v1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    monkeypatch.delenv("ANTHROPIC_CUSTOM_HEADERS", raising=False)
    monkeypatch.setenv("ANTHROPIC_MODEL", "host-selected-model")

    assert cli_docker.auth_env_passthrough("claude") == {
        "ANTHROPIC_AUTH_TOKEN": "token-value",
        "ANTHROPIC_BASE_URL": "https://proxy.invalid/v1",
    }


def test_auth_env_passthrough_is_empty_without_host_credentials(monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
    for name in cli_docker.DOCKER_AUTH_ENV_VARS["claude"]:
        monkeypatch.delenv(name, raising=False)

    assert cli_docker.auth_env_passthrough("claude") == {}
    assert cli_docker.auth_env_passthrough("unregistered-provider") == {}


def test_codex_container_installs_luna_capable_cli_version():  # noqa: ANN201  # tracked: #288
    commands = cli_docker.docker_init_commands("codex")

    assert (
        f"npm install -g --include=optional @openai/codex@{cli_docker.CODEX_DOCKER_CLI_VERSION}"
    ) in commands
    assert cli_docker.CODEX_DOCKER_CLI_VERSION == "0.144.4"


@pytest.mark.parametrize("provider", ["claude", "gemini", "codex", "opencode"])
def test_editor_container_installs_only_mcp_v1(provider: str) -> None:
    commands = cli_docker.docker_init_commands(provider)

    assert any("command -v pip3" in command for command in commands)
    assert "PIP_BREAK_SYSTEM_PACKAGES=1 python3 -m pip install --quiet 'mcp>=1.0,<2'" in commands


@pytest.mark.parametrize("provider", ["claude", "gemini", "codex", "opencode"])
def test_editor_container_installs_pinned_rust_toolchain(provider: str):  # noqa: ANN201  # tracked: #288
    commands = cli_docker.docker_init_commands(provider)
    rust_install = next(command for command in commands if "rustup-init.sh" in command)

    assert "command -v cargo" in rust_install
    assert f"--default-toolchain {cli_docker.RUST_DOCKER_TOOLCHAIN_VERSION}" in rust_install
    assert "--profile minimal" in rust_install
    assert "--component rustfmt --component clippy" in rust_install
    assert "ln -sf /root/.cargo/bin/* /usr/local/bin/" in rust_install
    assert cli_docker.RUST_DOCKER_TOOLCHAIN_VERSION == "1.92.0"
