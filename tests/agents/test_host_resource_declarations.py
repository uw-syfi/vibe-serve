from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from vibesys.agents import host_resource_declarations
from vs_sandbox import HostResourceAccess


class TestInstallRoot:
    """Agent packages may need binaries from sibling installation paths."""

    def test_node_package_imports_whole_package_tree(self):  # noqa: ANN201  # tracked: #288
        launcher = Path(
            "/home/u/.nvm/versions/node/v24/lib/node_modules/@openai/codex/bin/codex.js"
        )
        root = host_resource_declarations._install_root(launcher)  # noqa: SLF001  # tracked: #288

        assert root == Path("/home/u/.nvm/versions/node/v24/lib")
        platform_bin = Path(
            "/home/u/.nvm/versions/node/v24/lib/node_modules/@openai/"
            "codex/node_modules/@openai/codex-linux-x64/bin/codex"
        )
        assert platform_bin.is_relative_to(root)

    def test_plain_binary_imports_its_directory(self):  # noqa: ANN201  # tracked: #288
        assert host_resource_declarations._install_root(Path("/opt/tool/bin/agent")) == Path(  # noqa: SLF001  # tracked: #288
            "/opt/tool/bin"
        )


class TestInterpreterAliasRoots:
    """A venv reached through an alias directory must import that alias."""

    def test_alias_directory_between_venv_and_install_is_declared(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        install = tmp_path / "cpython-3.14.7"
        (install / "bin").mkdir(parents=True)
        real = install / "bin" / "python3.14"
        real.write_text("#!/bin/false\n")
        alias = tmp_path / "cpython-3.14"
        alias.symlink_to(install)
        venv_bin = tmp_path / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").symlink_to(alias / "bin" / "python3.14")
        (venv_bin / "python3").symlink_to("python")
        monkeypatch.setattr(host_resource_declarations.sys, "executable", str(venv_bin / "python3"))

        roots = host_resource_declarations._interpreter_alias_roots()  # noqa: SLF001  # tracked: #288

        # The alias, not the resolved install: sys.base_prefix already covers
        # the resolved path, and only the alias name dangles in the sandbox.
        assert roots == {alias}

    def test_no_alias_declares_nothing_extra(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        real = tmp_path / "usr" / "bin" / "python3.14"
        real.parent.mkdir(parents=True)
        real.write_text("#!/bin/false\n")
        monkeypatch.setattr(host_resource_declarations.sys, "executable", str(real))

        assert host_resource_declarations._interpreter_alias_roots() == set()  # noqa: SLF001  # tracked: #288


def test_defaults_declare_path_rust_and_shell_resources(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    home = tmp_path / "home"
    tool_bin = home / "tools" / "bin"
    cargo_bin = home / ".cargo" / "bin"
    rustup_home = home / ".rustup"
    bash_profile = home / ".bash_profile"

    declarations = host_resource_declarations.declare_agent_host_resources(
        {"HOME": str(home), "PATH": f"{tool_bin}:/usr/bin"},
        binary_path=None,
        provider="codex",
    )
    resources = {resource.path: resource.access for resource in declarations}

    assert resources[tool_bin] is HostResourceAccess.READ_ONLY
    assert resources[cargo_bin] is HostResourceAccess.READ_ONLY
    assert resources[rustup_home] is HostResourceAccess.READ_ONLY
    assert resources[bash_profile] is HostResourceAccess.READ_ONLY
    assert home not in resources


def test_active_rust_toolchain_declaration_is_narrow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    rustup_home = home / ".rustup"
    sysroot = rustup_home / "toolchains" / "stable"
    target_libdir = sysroot / "lib" / "rustlib" / "host" / "lib"
    lib_dir = sysroot / "lib"
    lib_dir.mkdir(parents=True)
    target_libdir.mkdir(parents=True)
    monkeypatch.setattr(
        host_resource_declarations.shutil, "which", lambda *_args, **_kwargs: "rustc"
    )
    outputs = iter((f"{sysroot}\n", f"{target_libdir}\n"))
    monkeypatch.setattr(
        host_resource_declarations.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=next(outputs)),
    )

    declarations = tuple(
        host_resource_declarations.declare_active_rust_toolchain_resources(
            host_resource_declarations.HostResourceContext(
                env={"HOME": str(home), "PATH": str(home / ".cargo" / "bin")}
            )
        )
    )
    paths = {resource.path for resource in declarations}

    assert sysroot / "bin" in paths
    assert sysroot / "lib" in paths
    assert rustup_home not in paths


@pytest.mark.parametrize(
    ("provider", "expected", "forbidden"),
    [
        ("codex", ".codex/auth.json", ".claude"),
        ("claude", ".claude", ".gemini"),
        ("gemini", ".gemini", ".config/opencode"),
        ("opencode", ".config/opencode", ".codex/auth.json"),
    ],
)
def test_provider_state_is_scoped_to_selected_agent(tmp_path, provider, expected, forbidden):  # noqa: ANN001, ANN201  # tracked: #288
    declarations = host_resource_declarations.declare_agent_host_resources(
        {"HOME": str(tmp_path)},
        binary_path=None,
        provider=provider,
    )
    writable = {
        resource.path.relative_to(tmp_path).as_posix()
        for resource in declarations
        if resource.access is HostResourceAccess.READ_WRITE
        and resource.path.is_relative_to(tmp_path)
    }

    assert expected in writable
    assert forbidden not in writable
