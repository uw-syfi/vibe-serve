"""Docker configuration registries for CLI agent providers."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path

# Per-provider environment variables to set inside the container.  Used as
# the canonical "supported with --docker" registry — providers absent from
# this dict are rejected up front in ``build_agent_client``.
#
# Claude Code refuses ``--dangerously-skip-permissions`` when running as
# root unless ``IS_SANDBOX=1`` is set, so we set it here.  We run everything
# as root inside the container (the default) to avoid uv/pip permission
# errors when the agent installs packages.
# Every provider also gets ``PYTHONPATH=/opt/vibesys`` so the in-container
# CLI can spawn ``python -m vs_issue_board.mcp`` against the
# bind-mounted project root (added in ``DockerSandbox.start`` for all four
# CLI providers). Without this the MCP server module wouldn't be importable
# inside the container.
DOCKER_PROVIDER_ENV: dict[str, dict[str, str]] = {
    "claude": {"IS_SANDBOX": "1", "PYTHONPATH": "/opt/vibesys"},
    "gemini": {"PYTHONPATH": "/opt/vibesys"},
    "codex": {"PYTHONPATH": "/opt/vibesys"},
    "opencode": {"PYTHONPATH": "/opt/vibesys"},
}


# Keep the editor container aligned with the verified host CLI feature set.
# Luna and its Max reasoning level require a newer CLI than the old 0.125 pin.
CODEX_DOCKER_CLI_VERSION = "0.144.4"

# Native implementations are valid candidate designs across domains, so the
# editor container must be able to build and test them before paid target work.
# Pin the toolchain for reproducible local checks instead of letting each agent
# independently bootstrap an arbitrary Rust release.
RUST_DOCKER_TOOLCHAIN_VERSION = "1.92.0"


# Bash one-liners run inside the container at start() time, per provider.
# Each list runs sequentially; a non-zero exit at any step raises RuntimeError.
#
# Every provider gets the python3 + ``mcp`` install at the end so that the
# in-container CLI can spawn ``python -m vs_issue_board.mcp``
# as a stdio MCP child (via the per-provider config installed by the
# active ``CodingAgent.install_mcp_servers`` hook). The default base image
# ``nvcr.io/nvidia/pytorch:25.04-py3`` already ships python3 + pip + a
# compatible ``mcp`` install, so this is a defensive no-op for the default
# image but keeps the install resilient on alternative images.
# Retry apt-get up to 5x with backoff — Ubuntu archive mirrors regularly
# return transient "connection timed out" / "mirror sync in progress"
# errors that fail a single-shot `apt-get update`.
def _apt_install(pkgs: str, check_bin: str | None = None) -> str:
    bin_ = check_bin or pkgs.split(maxsplit=1)[0]
    return (
        f"command -v {bin_} >/dev/null || "
        "{ for i in 1 2 3 4 5; do "
        f"  apt-get update -qq && apt-get install -y -qq {pkgs} && break || "
        '  (echo "apt retry $i..." >&2; sleep $((i*5))); '
        "done; "
        f"command -v {bin_} >/dev/null; }}"
    )


# Tarball install for node/npm — apt-get against archive.ubuntu.com is
# unreliable from inside several of our hosts (intermittent connection
# timeouts). nodejs.org / Cloudflare-fronted endpoints reach reliably.
_NODE_TARBALL_INSTALL = (
    "command -v node >/dev/null || { set -e; "
    "V=v20.18.1; A=linux-x64; "
    "cd /tmp && "
    "curl -fsSL --retry 5 --retry-delay 5 -o node.tgz "
    '  "https://nodejs.org/dist/$V/node-$V-$A.tar.gz" && '
    "mkdir -p /opt/node && "
    "tar -xzf node.tgz -C /opt/node --strip-components=1 && "
    "ln -sf /opt/node/bin/node /usr/local/bin/node && "
    "ln -sf /opt/node/bin/npm /usr/local/bin/npm && "
    "ln -sf /opt/node/bin/npx /usr/local/bin/npx && "
    "/opt/node/bin/npm config set prefix /usr/local && "
    "rm -f node.tgz; }"
)


_RUST_TOOLCHAIN_INSTALL = (
    "command -v cargo >/dev/null || { set -e; "
    "curl -fsSL --retry 5 --retry-delay 5 -o /tmp/rustup-init.sh "
    "https://sh.rustup.rs && "
    "sh /tmp/rustup-init.sh -y --profile minimal "
    f"--default-toolchain {RUST_DOCKER_TOOLCHAIN_VERSION} "
    "--component rustfmt --component clippy && "
    "ln -sf /root/.cargo/bin/* /usr/local/bin/ && "
    "rm -f /tmp/rustup-init.sh; }"
)


_COMMON_DOCKER_TOOLING_INSTALL = [
    _apt_install("curl ca-certificates", check_bin="curl"),
    _RUST_TOOLCHAIN_INSTALL,
    _apt_install("ripgrep", check_bin="rg"),
    _apt_install("python3 python3-pip", check_bin="pip3"),
    "PIP_BREAK_SYSTEM_PACKAGES=1 python3 -m pip install --quiet 'mcp>=1.0,<2'",
]

_DOCKER_INSTALL_COMMANDS: dict[str, list[str]] = {
    "claude": [
        _apt_install("curl ca-certificates", check_bin="curl"),
        "curl -fsSL https://claude.ai/install.sh | bash",
        # Anthropic's installer drops the binary in /root/.local/bin —
        # symlink to /usr/local/bin so PATH doesn't need adjustment.
        "ln -sf /root/.local/bin/claude /usr/local/bin/claude",
        *_COMMON_DOCKER_TOOLING_INSTALL,
    ],
    "opencode": [
        _apt_install("curl ca-certificates", check_bin="curl"),
        "curl -fsSL https://opencode.ai/install | bash",
        "ln -sf /root/.opencode/bin/opencode /usr/local/bin/opencode 2>/dev/null || "
        "ln -sf /root/.local/bin/opencode /usr/local/bin/opencode",
        *_COMMON_DOCKER_TOOLING_INSTALL,
    ],
    "gemini": [
        _NODE_TARBALL_INSTALL,
        "npm install -g @google/gemini-cli",
        *_COMMON_DOCKER_TOOLING_INSTALL,
    ],
    "codex": [
        _NODE_TARBALL_INSTALL,
        # Pin the verified Luna-capable CLI rather than floating editor images.
        # `--include=optional` because newer codex packages ship the
        # Linux-x64 native binary as an optional dependency that
        # `npm install -g` silently skips on some npm configurations.
        f"npm install -g --include=optional @openai/codex@{CODEX_DOCKER_CLI_VERSION}",
        *_COMMON_DOCKER_TOOLING_INSTALL,
    ],
}


def docker_init_commands(provider: str) -> list[str]:
    """Return the list of init commands for *provider*."""
    return list(_DOCKER_INSTALL_COMMANDS.get(provider, []))


@dataclass(frozen=True)
class DockerAuthPath:
    """Host provider state and its writable location inside the container."""

    host_path: Path
    container_path: str


# Authentication and user configuration are mounted read-only under
# ``/opt/vibesys-auth`` and copied into the container's ephemeral writable
# layer before the CLI starts. Keep this list to provider-owned leaf files:
# provider homes also contain large caches, worktrees, session history, package
# installations, and databases that are neither required for authentication
# nor appropriate to duplicate for every sandbox.
DOCKER_AUTH_PATHS: dict[str, list[DockerAuthPath]] = {
    "claude": [
        DockerAuthPath(
            Path.home() / ".claude" / ".credentials.json",
            "/root/.claude/.credentials.json",
        ),
        DockerAuthPath(
            Path.home() / ".claude" / "settings.json",
            "/root/.claude/settings.json",
        ),
        DockerAuthPath(
            Path.home() / ".claude" / "settings.local.json",
            "/root/.claude/settings.local.json",
        ),
        DockerAuthPath(Path.home() / ".claude.json", "/root/.claude.json"),
    ],
    "gemini": [
        DockerAuthPath(
            Path.home() / ".gemini" / "oauth_creds.json",
            "/root/.gemini/oauth_creds.json",
        ),
        DockerAuthPath(
            Path.home() / ".gemini" / "google_accounts.json",
            "/root/.gemini/google_accounts.json",
        ),
        DockerAuthPath(
            Path.home() / ".gemini" / "settings.json",
            "/root/.gemini/settings.json",
        ),
        DockerAuthPath(Path.home() / ".gemini" / ".env", "/root/.gemini/.env"),
    ],
    "codex": [
        DockerAuthPath(Path.home() / ".codex" / "auth.json", "/root/.codex/auth.json"),
        DockerAuthPath(
            Path.home() / ".codex" / "config.toml",
            "/root/.codex/config.toml",
        ),
    ],
    "opencode": [
        DockerAuthPath(
            Path.home() / ".local" / "share" / "opencode" / "auth.json",
            "/root/.local/share/opencode/auth.json",
        ),
        DockerAuthPath(
            Path.home() / ".config" / "opencode" / "opencode.json",
            "/root/.config/opencode/opencode.json",
        ),
        DockerAuthPath(
            Path.home() / ".config" / "opencode" / "opencode.jsonc",
            "/root/.config/opencode/opencode.jsonc",
        ),
        # Older OpenCode releases used config.json/config.jsonc.
        DockerAuthPath(
            Path.home() / ".config" / "opencode" / "config.json",
            "/root/.config/opencode/config.json",
        ),
        DockerAuthPath(
            Path.home() / ".config" / "opencode" / "config.jsonc",
            "/root/.config/opencode/config.jsonc",
        ),
        DockerAuthPath(
            Path.home() / ".config" / "opencode" / ".env",
            "/root/.config/opencode/.env",
        ),
    ],
}


# Host environment variables that carry provider authentication, forwarded
# into the container alongside the staged files above.  A host may authenticate
# a CLI entirely through the environment — an ``ANTHROPIC_AUTH_TOKEN`` plus
# ``ANTHROPIC_BASE_URL`` pointing at a proxy or enterprise gateway is a
# first-class Claude Code auth mechanism, and plain API keys are another — in
# which case no provider state file exists to stage and the container CLI would
# start unauthenticated.  Host sandboxes never hit this because they inherit
# the host environment directly.
#
# Keep this registry to credential and endpoint variables.  Model-selection
# variables such as ``ANTHROPIC_MODEL`` are deliberately excluded: VibeSys owns
# per-role model selection, and forwarding a host export would let it silently
# override the configured model inside the container.
DOCKER_AUTH_ENV_VARS: dict[str, tuple[str, ...]] = {
    "claude": (
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_CUSTOM_HEADERS",
    ),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "codex": ("OPENAI_API_KEY", "OPENAI_BASE_URL"),
    # OpenCode resolves credentials per *model* provider from the models.dev
    # registry, so the variable names depend on whichever provider the user
    # configured rather than on OpenCode itself; it documents no CLI-owned auth
    # variable to enumerate here.  Its ``auth.json`` and config files are
    # already staged through ``DOCKER_AUTH_PATHS``.
    "opencode": (),
}


def auth_env_passthrough(provider: str) -> dict[str, str]:
    """Return the host auth environment variables *provider* can actually use.

    Unset and blank variables are dropped: an empty host export carries no
    credential and must not shadow staged file authentication or make the
    preflight check believe the container is authenticated.
    """
    values: dict[str, str] = {}
    for name in DOCKER_AUTH_ENV_VARS.get(provider, ()):
        value = os.environ.get(name)
        if value and value.strip():
            values[name] = value
    return values


def auth_bind_mounts(provider: str) -> list[tuple[str, str, bool]]:
    """Return read-only staging mounts for existing provider state."""
    out: list[tuple[str, str, bool]] = []
    for index, spec in enumerate(DOCKER_AUTH_PATHS.get(provider, [])):
        if spec.host_path.exists():
            out.append(
                (
                    str(spec.host_path),
                    f"/opt/vibesys-auth/{index}",
                    True,
                )
            )
    return out


def auth_copy_commands(provider: str) -> list[str]:
    """Return commands that copy staged provider state into writable storage.

    Directories contain runtime state such as sessions and history, so mounting
    them read-only at their final locations can break otherwise valid CLI runs.
    Copying from read-only staging keeps those writes inside the disposable
    container layer.
    """
    commands: list[str] = []
    for index, spec in enumerate(DOCKER_AUTH_PATHS.get(provider, [])):
        if not spec.host_path.exists():
            continue
        source = shlex.quote(f"/opt/vibesys-auth/{index}")
        destination = shlex.quote(spec.container_path)
        parent = shlex.quote(str(Path(spec.container_path).parent))
        if spec.host_path.is_dir():
            commands.append(f"mkdir -p {destination} && cp -a {source}/. {destination}/")
        else:
            commands.append(f"mkdir -p {parent} && cp -a {source} {destination}")
    return commands
