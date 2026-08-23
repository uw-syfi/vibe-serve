"""Default host resource declarations for local coding-agent CLIs.

This is the policy layer: it contains the actual list of resources agents need,
expressed only through the public :mod:`vs_sandbox.host_resources` SDK. It does not
know whether a consumer uses bubblewrap, Seatbelt, bind mounts, or another
resource-import mechanism.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Iterable, Mapping  # noqa: TC003  # tracked: #288
from pathlib import Path

from vs_sandbox import (
    HostResource,
    HostResourceAccess,
    HostResourceContext,
    HostResourceDeclarer,
    declare_resources,
)

ALLOW_ENV = "VIBESYS_AGENT_SANDBOX_ALLOW"


def _install_root(real_path: Path) -> Path:
    """Return the subtree needed by an installed agent executable."""
    parts = real_path.parts
    if "node_modules" in parts:
        idx = parts.index("node_modules")
        if idx > 0:
            return Path(*parts[:idx])
    return real_path.parent


def _resources(
    paths: Iterable[Path],
    *,
    access: HostResourceAccess = HostResourceAccess.READ_ONLY,
    purpose: str,
) -> tuple[HostResource, ...]:
    return tuple(HostResource(path, access, purpose) for path in paths)


def _interpreter_alias_roots() -> set[Path]:
    """Symlinked directories the interpreter is reached through.

    A virtualenv's ``bin/python`` often reaches its base install through an
    alias directory (uv keeps ``cpython-3.14`` pointing at ``cpython-3.14.7``).
    ``sys.base_prefix`` is already symlink-resolved, so importing it alone
    leaves the alias dangling inside the sandbox and every ``sys.executable``
    exec fails with ENOENT: the agent then loses its stdio MCP servers.
    Importing the alias directory itself binds the real install under the name
    the interpreter actually walks.
    """
    roots: set[Path] = set()
    current = Path(sys.executable)
    seen: set[Path] = set()
    while current.is_symlink() and current not in seen:
        seen.add(current)
        target = current.readlink()
        current = target if target.is_absolute() else current.parent / target
        roots.update(parent for parent in current.parents if parent.is_symlink())
    return roots


def _python_runtime(ctx: HostResourceContext) -> Iterable[HostResource]:
    del ctx
    return _resources(
        (Path(sys.base_prefix), Path(sys.prefix), *sorted(_interpreter_alias_roots())),
        purpose="Python runtime",
    )


def _path_toolchain(ctx: HostResourceContext) -> Iterable[HostResource]:
    paths = (
        Path(entry).expanduser() for entry in ctx.env.get("PATH", "").split(os.pathsep) if entry
    )
    return _resources(paths, purpose="launcher PATH toolchain")


def _rust_toolchain(ctx: HostResourceContext) -> Iterable[HostResource]:
    home = ctx.env.get("HOME")
    if not home:
        return ()
    home_path = Path(home)
    cargo_home = Path(ctx.env.get("CARGO_HOME", home_path / ".cargo")).expanduser()
    rustup_home = Path(ctx.env.get("RUSTUP_HOME", home_path / ".rustup")).expanduser()
    return _resources(
        (cargo_home / "bin", cargo_home / "env", rustup_home),
        purpose="Rust toolchain",
    )


def _shell_setup(ctx: HostResourceContext) -> Iterable[HostResource]:
    home = ctx.env.get("HOME")
    if not home:
        return ()
    base = Path(home)
    return _resources(
        (
            base / ".bash_profile",
            base / ".bash_login",
            base / ".profile",
            base / ".bashrc",
        ),
        purpose="shell setup",
    )


def _agent_runtime(ctx: HostResourceContext) -> Iterable[HostResource]:
    paths: list[Path] = []
    if ctx.binary_path:
        real_binary = Path(ctx.binary_path).resolve()
        paths.extend((_install_root(real_binary), real_binary.parent))

    node = shutil.which("node", path=ctx.env.get("PATH"))
    if node:
        real_node = Path(node).resolve()
        paths.extend((real_node.parent, real_node.parent.parent))

    try:
        import vibesys  # noqa: PLC0415  # tracked: #288

        pkg_file = getattr(vibesys, "__file__", None)
        if pkg_file:
            paths.append(Path(pkg_file).resolve().parents[1])
    except Exception:  # pragma: no cover - defensive; import cannot normally fail here  # noqa: BLE001, S110  # tracked: #288
        pass

    return _resources(paths, purpose="agent and VibeSys runtime")


def _provider_state(ctx: HostResourceContext) -> Iterable[HostResource]:
    home = ctx.env.get("HOME")
    if not home:
        return ()
    base = Path(home)
    paths: list[Path]
    if ctx.provider == "codex":
        codex_home = Path(ctx.env.get("CODEX_HOME", base / ".codex")).expanduser()
        paths = [
            codex_home / "auth.json",
            codex_home / "config.toml",
            base / ".config" / "codex",
        ]
    elif ctx.provider == "claude":
        paths = [base / ".claude", base / ".claude.json", base / ".config" / "claude"]
    elif ctx.provider == "gemini":
        paths = [base / ".gemini", base / ".config" / "gemini"]
    elif ctx.provider == "opencode":
        paths = [base / ".local" / "share" / "opencode", base / ".config" / "opencode"]
    else:
        paths = []

    if sys.platform == "darwin":
        support = base / "Library" / "Application Support"
        caches = base / "Library" / "Caches"
        if ctx.provider == "codex":
            paths.extend((support / "codex", support / "com.openai.codex", caches / "codex"))
        elif ctx.provider == "claude":
            paths.extend((support / "claude", caches / "claude"))

    return _resources(
        paths,
        access=HostResourceAccess.READ_WRITE,
        purpose=f"{ctx.provider or 'unknown'} agent state",
    )


#: Conventional host scratch root for a repository-native task. Container
#: workloads bind-mount capture directories from here, and Docker resolves a
#: bind source in the daemon's namespace rather than the agent's, so the path
#: only works when it names the same directory inside and outside confinement.
TASK_SCRATCH_ROOT = Path("/tmp")  # noqa: S108  # tracked: #288


def task_scratch_dir(task_name: str) -> Path:
    """Return the host scratch directory shared with a task's containers."""
    return TASK_SCRATCH_ROOT / f"vibesys-{task_name}"


def container_runtime_resources(env: Mapping[str, str] | None = None) -> tuple[HostResource, ...]:
    """Declare the Docker control socket for tasks that orchestrate containers.

    A microservice benchmark *is* a container topology: without the socket the
    agent cannot build, start, or profile the system it is optimizing, and the
    round-one routing check concludes the candidate does not run. The default
    Linux confinement exposes no ``/var/run``, so the socket has to be imported
    deliberately.

    This is a real widening. Access to the daemon is equivalent to root on the
    host, so it is declared only for the domain that needs it rather than for
    every local agent. Run with ``--docker`` when the workload should be
    confined to a container instead.
    """
    env = env if env is not None else os.environ
    paths = [Path("/var/run/docker.sock")]  # tracked: #288
    host = env.get("DOCKER_HOST", "")
    if host.startswith("unix://"):
        paths.append(Path(host.removeprefix("unix://")))
    return _resources(
        paths,
        access=HostResourceAccess.READ_WRITE,
        purpose="Docker control socket",
    )


def _operator_allowlist(ctx: HostResourceContext) -> Iterable[HostResource]:
    raw = ctx.env.get(ALLOW_ENV, "")
    paths = (Path(path).expanduser() for path in raw.split(os.pathsep) if path.strip())
    return _resources(paths, purpose=f"{ALLOW_ENV} entry")


DEFAULT_AGENT_HOST_RESOURCE_DECLARERS: tuple[HostResourceDeclarer, ...] = (
    _python_runtime,
    _path_toolchain,
    _rust_toolchain,
    _shell_setup,
    _agent_runtime,
    _provider_state,
    _operator_allowlist,
)


def declare_agent_host_resources(
    env: Mapping[str, str],
    *,
    binary_path: str | None,
    provider: str,
    additional: Iterable[HostResource] = (),
) -> tuple[HostResource, ...]:
    """Declare the complete local resource set for one CLI provider."""
    return declare_resources(
        HostResourceContext(env=env, binary_path=binary_path, provider=provider),
        DEFAULT_AGENT_HOST_RESOURCE_DECLARERS,
        additional=additional,
    )
