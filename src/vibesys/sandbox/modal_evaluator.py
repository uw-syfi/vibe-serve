"""Run a trusted evaluator against a candidate deployed on Modal.

The Modal run environment edits candidates in a local CPU-only container, so
service-style evaluators cannot reach the serving process at their default
``http://localhost:8000``. This helper is mounted read-only by the runtime
adapter. It deploys the current candidate, discovers the emitted web endpoint,
waits for readiness, and then runs the trusted command *unmodified inside the
serving container* (via ``modal container exec``) so the task's localhost
measurement contract holds: metrics reflect the engine, not TLS, WAN, Modal
ingress, or Modal's function-admission queue.

The public endpoint is used only for deploy discovery, readiness, and periodic
keep-warm probes during the in-container run (colocated traffic does not reset
Modal's idle scaledown timer). Workspace-relative input files referenced by the
command are staged into the container; absolute not-yet-existing paths in the
command (for example a ``--output-json`` target) are relayed back to the caller
after the run.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Generator, Sequence  # noqa: TC003  # tracked: #288
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_MODAL_WEB_URL = re.compile(r"https://[a-zA-Z0-9.-]+\.modal\.run")
_MODAL_DEPLOYMENT = re.compile(
    r"https://modal\.com/apps/[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+/deployed/"
    r"([a-zA-Z0-9_.-]+)"
)
_LOCK_PATH = "/tmp/vibesys-modal-evaluator.lock"  # noqa: S108  # tracked: #288
_DEPLOYMENT_LEASE_PATH = Path("/tmp/vibesys-modal-evaluator-deployment.json")  # noqa: S108  # tracked: #288
_CANDIDATE_REVISION_ENV = "VIBESYS_CANDIDATE_REVISION"
_RELEASE_DEPLOYMENT_ENV = "VIBESYS_RELEASE_MODAL_DEPLOYMENT"
_MAX_DIAGNOSTIC_CHARS = 20_000
_EXEC_RC_MARKER = "__VIBESYS_EXEC_RC__="
_OUTPUT_FILE_MARKER = "__VIBESYS_OUTPUT_FILE__"
_OUTPUT_END_MARKER = "__VIBESYS_OUTPUT_END__"
# Linux caps a single argv string at 128 KiB; stay well under it per chunk.
_B64_CHUNK_CHARS = 60_000
_MAX_STAGE_ARCHIVE_BYTES = 8 * 1024 * 1024
_CONTAINER_DISCOVERY_TIMEOUT_SECONDS = 90.0
_KEEPWARM_INTERVAL_SECONDS = 30.0


@dataclass(frozen=True)
class _DeploymentLease:
    candidate_revision: str
    base_url: str
    app_identifier: str | None = None


def _compact_rich_output(output: str) -> str:
    """Remove terminal formatting that Rich can insert inside wrapped URLs."""
    plain = _ANSI_ESCAPE.sub("", output)
    # Modal renders deployment summaries in a Rich tree. Long URLs can wrap
    # onto the next line after the tree's vertical guide, for example
    # ``.moda\n│   l.run``. Whitespace removal alone leaves the guide inside the
    # URL, so discard Unicode box-drawing characters as well.
    return re.sub(r"[\s\u2500-\u257f]+", "", plain)


@contextmanager
def _exclusive_evaluation() -> Generator[None]:
    """Serialize deploy-and-evaluate callers sharing the editor container."""
    with open(_LOCK_PATH, "w") as lock_file:  # noqa: PTH123  # tracked: #288
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def extract_modal_web_url(output: str) -> str:
    """Return the last Modal web endpoint printed by ``modal deploy``."""
    # Rich may wrap a long endpoint between ``moda`` and ``l.run``. Removing
    # terminal layout is safe before matching because Modal hostnames contain
    # neither whitespace nor box-drawing characters.
    compact = _compact_rich_output(output)
    matches = _MODAL_WEB_URL.findall(compact)
    if not matches:
        raise ValueError("modal deploy did not print a *.modal.run web endpoint")  # noqa: TRY003  # tracked: #288
    return matches[-1]


def extract_modal_app_identifier(output: str) -> str:
    """Return the deployed Modal app name printed by ``modal deploy``."""
    compact = _compact_rich_output(output)
    matches = _MODAL_DEPLOYMENT.findall(compact)
    if not matches:
        raise ValueError("modal deploy did not print a deployment URL")  # noqa: TRY003  # tracked: #288
    return matches[-1]


def recent_modal_logs(app_identifier: str, *, workspace: str) -> str:
    """Fetch a bounded recent-log excerpt for a failed readiness check."""
    try:
        result = subprocess.run(  # noqa: S603  # tracked: #288
            [  # noqa: S607  # tracked: #288
                "uv",
                "run",
                "modal",
                "app",
                "logs",
                app_identifier,
                "--since",
                "10m",
                "--tail",
                "200",
                "--timestamps",
            ],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"Could not fetch Modal logs: {type(exc).__name__}: {exc}"

    output = _ANSI_ESCAPE.sub("", f"{result.stdout}\n{result.stderr}".strip())
    if not output:
        return "Modal returned no recent logs."
    if len(output) > _MAX_DIAGNOSTIC_CHARS:
        output = f"[... earlier Modal logs omitted ...]\n{output[-_MAX_DIAGNOSTIC_CHARS:]}"
    return output


def wait_for_health(base_url: str, *, timeout_seconds: float) -> None:
    """Wait until the deployed candidate returns HTTP 200 from ``/health``."""
    deadline = time.monotonic() + timeout_seconds
    health_url = f"{base_url.rstrip('/')}/health"
    last_error = "no response"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=10) as response:  # noqa: S310  # tracked: #288
                if response.status == 200:  # noqa: PLR2004  # tracked: #288
                    return
                last_error = f"HTTP {response.status}"
        except (OSError, urllib.error.URLError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(2)
    raise TimeoutError(f"{health_url} did not become ready: {last_error}")  # noqa: TRY003  # tracked: #288


def _healthy_now(base_url: str) -> bool:
    """Return whether an existing deployment is immediately reusable."""
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/health", timeout=5) as response:  # noqa: S310  # tracked: #288
            return response.status == 200  # noqa: PLR2004  # tracked: #288
    except (OSError, urllib.error.URLError):
        return False


def _read_deployment_lease() -> _DeploymentLease | None:
    try:
        payload = json.loads(_DEPLOYMENT_LEASE_PATH.read_text())
        revision = payload["candidate_revision"]
        base_url = payload["base_url"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(revision, str) or not isinstance(base_url, str):
        return None
    app_identifier = payload.get("app_identifier")
    if app_identifier is not None and not isinstance(app_identifier, str):
        return None
    return _DeploymentLease(revision, base_url, app_identifier)


def _write_deployment_lease(
    candidate_revision: str,
    base_url: str,
    app_identifier: str | None,
) -> None:
    temporary = _DEPLOYMENT_LEASE_PATH.with_suffix(".tmp")
    payload = {
        "candidate_revision": candidate_revision,
        "base_url": base_url,
    }
    if app_identifier is not None:
        payload["app_identifier"] = app_identifier
    temporary.write_text(json.dumps(payload))
    temporary.replace(_DEPLOYMENT_LEASE_PATH)


def _release_requested() -> bool:
    return os.environ.get(_RELEASE_DEPLOYMENT_ENV, "").lower() in {"1", "true", "yes"}


def _stop_modal_app(app_identifier: str, *, workspace: str) -> bool:
    """Stop a deployed app without prompting, returning whether it succeeded."""
    try:
        result = subprocess.run(  # noqa: S603  # tracked: #288
            ["uv", "run", "modal", "app", "stop", app_identifier, "--yes"],  # noqa: S607  # tracked: #288
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(  # noqa: T201  # tracked: #288
            f"Could not stop Modal app {app_identifier}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return False
    if result.returncode == 0:
        print(f"Stopped Modal app {app_identifier}.", file=sys.stderr)  # noqa: T201  # tracked: #288
        return True
    output = f"{result.stdout}\n{result.stderr}".strip()
    print(  # noqa: T201  # tracked: #288
        f"Could not stop Modal app {app_identifier} (exit {result.returncode}): {output[-2000:]}",
        file=sys.stderr,
    )
    return False


def _retire_deployment(lease: _DeploymentLease, *, workspace: str) -> None:
    """Best-effort stop and forget a lease that must not be reused."""
    if lease.app_identifier is not None:
        _stop_modal_app(lease.app_identifier, workspace=workspace)
    _DEPLOYMENT_LEASE_PATH.unlink(missing_ok=True)


def _deployment_path(workspace: str, entrypoint: str) -> Path:
    """Resolve a candidate-owned Modal file without allowing project escapes."""
    relative = Path(entrypoint)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("Modal entrypoint must be a project-relative path")  # noqa: TRY003
    workspace_root = Path(workspace).resolve(strict=True)
    candidate = (workspace_root / relative).resolve(strict=True)
    try:
        candidate.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError(f"Modal entrypoint escapes the project: {entrypoint}") from exc  # noqa: TRY003
    if not candidate.is_file():
        raise ValueError(f"Modal entrypoint is not a file: {candidate}")  # noqa: TRY003
    return candidate


@dataclass(frozen=True)
class _CommandTransferPlan:
    """Files to stage into the serving container and outputs to relay back."""

    stage_paths: tuple[str, ...]
    output_paths: tuple[str, ...]


def _plan_command_transfer(command: Sequence[str], workspace: str) -> _CommandTransferPlan:
    """Classify command tokens into staged inputs and relayed outputs.

    Workspace-relative tokens that resolve to files are staged with their
    parent directory (scripts commonly import or read siblings); directory
    tokens are staged whole. Absolute tokens that do not exist but whose
    parent directory does are treated as output artifacts the command will
    write, and are relayed back after the in-container run.
    """
    workspace_root = Path(workspace).resolve(strict=True)
    staged: list[str] = []
    outputs: list[str] = []
    for token in command:
        if not token or token.startswith("-"):
            continue
        if token.startswith("/"):
            path = Path(token)
            if not path.exists() and path.parent.is_dir() and token not in outputs:
                outputs.append(token)
            continue
        candidate = workspace_root / token
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(workspace_root)
        except (OSError, ValueError):
            continue
        if resolved.is_dir() or resolved.parent == workspace_root:
            stage = resolved
        else:
            stage = resolved.parent
        relative = stage.relative_to(workspace_root).as_posix()
        if relative not in staged:
            staged.append(relative)

    def _covered(path: str) -> bool:
        return any(other != path and path.startswith(f"{other}/") for other in staged)

    kept = tuple(path for path in staged if not _covered(path))
    return _CommandTransferPlan(stage_paths=kept, output_paths=tuple(outputs))


def _build_stage_archive(workspace: str, stage_paths: Sequence[str]) -> bytes:
    """Produce a gzipped tar of the staged paths, keyed by their relative paths."""
    workspace_root = Path(workspace).resolve(strict=True)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for relative in stage_paths:
            archive.add(str(workspace_root / relative), arcname=relative)
    payload = buffer.getvalue()
    if len(payload) > _MAX_STAGE_ARCHIVE_BYTES:
        raise ValueError(  # noqa: TRY003
            f"evaluator inputs too large to stage into the serving container "
            f"({len(payload)} bytes compressed, cap {_MAX_STAGE_ARCHIVE_BYTES})"
        )
    return payload


def _find_app_container(
    app_identifier: str,
    *,
    workspace: str,
    base_url: str,
    timeout_seconds: float = _CONTAINER_DISCOVERY_TIMEOUT_SECONDS,
) -> str:
    """Return the id of a running container for the deployed app.

    Health probes double as warm-up: if the app scaled to zero between
    readiness and discovery, probing the public endpoint starts a container.
    """
    deadline = time.monotonic() + timeout_seconds
    last_error = "no container listed"
    while True:
        try:
            result = subprocess.run(  # tracked: #288
                ["uv", "run", "modal", "container", "list", "--json"],  # noqa: S607  # tracked: #288
                cwd=workspace,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            entries = json.loads(result.stdout) if result.returncode == 0 else []
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            entries = []
            last_error = f"{type(exc).__name__}: {exc}"
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("App Name") == app_identifier and entry.get("Container ID"):
                return str(entry["Container ID"])
        if time.monotonic() >= deadline:
            raise TimeoutError(  # noqa: TRY003
                f"no running container found for Modal app {app_identifier}: {last_error}"
            )
        _healthy_now(base_url)
        time.sleep(5)


def _bootstrap_script(command: Sequence[str], output_paths: Sequence[str]) -> str:
    """Build the in-container POSIX shell bootstrap.

    Receives the staged archive as base64 chunks in ``"$@"``, recreates the
    workspace-relative layout in a temp directory, provides ``uv`` through a
    ``python3 -m uv`` shim, runs the trusted command verbatim from that
    directory (so its localhost defaults and relative paths hold), then emits
    each existing output file and the command's exit code between sentinel
    markers — ``modal container exec`` does not propagate exit codes.
    """
    quoted_command = " ".join(shlex.quote(token) for token in command)
    relay_blocks = "\n".join(
        f"if [ -f {shlex.quote(path)} ]; then\n"
        f"  printf '\\n%s %s\\n' {shlex.quote(_OUTPUT_FILE_MARKER)} {shlex.quote(path)}\n"
        f"  base64 {shlex.quote(path)}\n"
        f"  printf '%s\\n' {shlex.quote(_OUTPUT_END_MARKER)}\n"
        "fi"
        for path in output_paths
    )
    return f"""set -u
stage=$(mktemp -d /tmp/vibesys-eval-XXXXXX)
printf '%s' "$@" | base64 -d | tar -xzf - -C "$stage"
mkdir -p "$stage/.bin"
printf '#!/bin/sh\\nexec python3 -m uv "$@"\\n' > "$stage/.bin/uv"
chmod +x "$stage/.bin/uv"
if ! python3 -m pip install --quiet --target "$stage/.pip" uv >&2; then
  echo 'vibesys evaluator bootstrap failed: serving container lacks python3 -m pip' >&2
  printf '\\n{_EXEC_RC_MARKER}%s\\n' 97
  exit 0
fi
PATH="$stage/.bin:$PATH"; export PATH
PYTHONPATH="$stage/.pip${{PYTHONPATH:+:$PYTHONPATH}}"; export PYTHONPATH
UV_CACHE_DIR="$stage/.uv-cache"; export UV_CACHE_DIR
cd "$stage"
{quoted_command}
rc=$?
{relay_blocks}
printf '\\n{_EXEC_RC_MARKER}%s\\n' "$rc"
"""


def _parse_exec_output(stdout: str) -> tuple[int | None, dict[str, bytes], str]:
    """Split exec stdout into exit code, relayed output files, and passthrough."""
    exit_code: int | None = None
    files: dict[str, bytes] = {}
    passthrough: list[str] = []
    collecting: str | None = None
    encoded: list[str] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if collecting is not None:
            if stripped == _OUTPUT_END_MARKER:
                try:
                    files[collecting] = base64.b64decode("".join(encoded))
                except ValueError:
                    files.pop(collecting, None)
                collecting = None
                encoded = []
            else:
                encoded.append(stripped)
            continue
        if stripped.startswith(f"{_OUTPUT_FILE_MARKER} "):
            collecting = stripped[len(_OUTPUT_FILE_MARKER) + 1 :]
            continue
        if stripped.startswith(_EXEC_RC_MARKER):
            suffix = stripped[len(_EXEC_RC_MARKER) :]
            if suffix.isdigit():
                exit_code = int(suffix)
            continue
        passthrough.append(line)
    return exit_code, files, "\n".join(passthrough)


class _DeploymentKeepWarm:
    """Probe the public endpoint periodically while the colocated run executes.

    Colocated traffic never crosses Modal ingress, so it does not reset the
    app's idle scaledown timer; without these probes Modal may retire the
    serving container mid-measurement.
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> _DeploymentKeepWarm:
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._stop.set()
        self._thread.join(timeout=10)

    def _run(self) -> None:
        while not self._stop.wait(_KEEPWARM_INTERVAL_SECONDS):
            _healthy_now(self._base_url)


def _execute_colocated(
    command: Sequence[str],
    *,
    workspace: str,
    app_identifier: str,
    base_url: str,
) -> int:
    """Run the trusted command inside the app's serving container."""
    plan = _plan_command_transfer(command, workspace)
    archive = _build_stage_archive(workspace, plan.stage_paths)
    encoded = base64.b64encode(archive).decode("ascii")
    chunks = [
        encoded[offset : offset + _B64_CHUNK_CHARS]
        for offset in range(0, len(encoded), _B64_CHUNK_CHARS)
    ] or [""]
    container_id = _find_app_container(
        app_identifier,
        workspace=workspace,
        base_url=base_url,
    )
    script = _bootstrap_script(command, plan.output_paths)
    with _DeploymentKeepWarm(base_url):
        result = subprocess.run(  # noqa: S603  # tracked: #288
            [  # noqa: S607  # tracked: #288
                "uv",
                "run",
                "modal",
                "container",
                "exec",
                container_id,
                "--",
                "sh",
                "-c",
                script,
                "vibesys-eval",
                *chunks,
            ],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
    exit_code, files, passthrough = _parse_exec_output(result.stdout)
    if passthrough:
        print(passthrough)  # noqa: T201  # tracked: #288
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")  # noqa: T201  # tracked: #288
    for path, payload in files.items():
        Path(path).write_bytes(payload)
    if exit_code is None:
        tail = result.stdout[-_MAX_DIAGNOSTIC_CHARS:]
        print(  # noqa: T201  # tracked: #288
            "Modal evaluator exec did not report an exit code "
            f"(modal exit {result.returncode}); output tail:\n{tail}",
            file=sys.stderr,
        )
        return result.returncode or 1
    return exit_code


def run_evaluator(
    command: Sequence[str],
    *,
    workspace: str = "/workspace",
    entrypoint: str = "main.py",
    readiness_timeout_seconds: float = 90,
) -> int:
    """Deploy the candidate and run ``command`` inside its serving container."""
    if not command:
        raise ValueError("missing evaluator command after '--'")  # noqa: TRY003  # tracked: #288

    with _exclusive_evaluation():
        return _run_evaluator_unlocked(
            command,
            workspace=workspace,
            entrypoint=entrypoint,
            readiness_timeout_seconds=readiness_timeout_seconds,
        )


def _run_evaluator_unlocked(  # noqa: C901, PLR0911, PLR0912, PLR0915  # tracked: #288
    command: Sequence[str],
    *,
    workspace: str,
    entrypoint: str,
    readiness_timeout_seconds: float,
) -> int:
    candidate_revision = os.environ.get(_CANDIDATE_REVISION_ENV)
    if candidate_revision:
        lease = _read_deployment_lease()
        if lease is not None:
            if (
                lease.candidate_revision == candidate_revision
                and lease.app_identifier is not None
                and _healthy_now(lease.base_url)
            ):
                print(  # noqa: T201  # tracked: #288
                    "Reusing healthy Modal deployment for candidate revision "
                    f"{candidate_revision}.",
                    file=sys.stderr,
                )
                try:
                    return _execute_colocated(
                        command,
                        workspace=workspace,
                        app_identifier=lease.app_identifier,
                        base_url=lease.base_url,
                    )
                except (TimeoutError, ValueError) as exc:
                    print(f"Modal evaluator setup failed: {exc}", file=sys.stderr)  # noqa: T201
                    return 1
                finally:
                    if _release_requested():
                        _retire_deployment(lease, workspace=workspace)
            _retire_deployment(lease, workspace=workspace)

    try:
        deployment_path = _deployment_path(workspace, entrypoint)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Modal evaluator setup failed: {exc}", file=sys.stderr)  # noqa: T201
        return 1

    deploy = subprocess.run(  # noqa: S603  # tracked: #288
        ["uv", "run", "modal", "deploy", str(deployment_path)],  # noqa: S607  # tracked: #288
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    deploy_output = f"{deploy.stdout}\n{deploy.stderr}".strip()
    if deploy_output:
        print(deploy_output, file=sys.stderr)  # noqa: T201  # tracked: #288
    if deploy.returncode != 0:
        return deploy.returncode

    app_identifier: str | None = None
    try:
        base_url = extract_modal_web_url(deploy_output)
        app_identifier = extract_modal_app_identifier(deploy_output)
        wait_for_health(base_url, timeout_seconds=readiness_timeout_seconds)
    except (TimeoutError, ValueError) as exc:
        print(f"Modal evaluator setup failed: {exc}", file=sys.stderr)  # noqa: T201  # tracked: #288
        if app_identifier is None:
            try:  # noqa: SIM105  # tracked: #288
                app_identifier = extract_modal_app_identifier(deploy_output)
            except ValueError:
                pass
        if app_identifier is not None:
            print(  # noqa: T201  # tracked: #288
                f"Recent Modal logs:\n{recent_modal_logs(app_identifier, workspace=workspace)}",
                file=sys.stderr,
            )
            _stop_modal_app(app_identifier, workspace=workspace)
        return 1

    if candidate_revision:
        _write_deployment_lease(candidate_revision, base_url, app_identifier)

    try:
        return _execute_colocated(
            command,
            workspace=workspace,
            app_identifier=app_identifier,
            base_url=base_url,
        )
    except (TimeoutError, ValueError) as exc:
        print(f"Modal evaluator setup failed: {exc}", file=sys.stderr)  # noqa: T201  # tracked: #288
        return 1
    finally:
        if _release_requested():
            lease = _read_deployment_lease()
            if (
                candidate_revision
                and lease is not None
                and lease.candidate_revision == candidate_revision
            ):
                _retire_deployment(lease, workspace=workspace)
            else:
                _stop_modal_app(app_identifier, workspace=workspace)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default="/workspace")
    parser.add_argument("--entrypoint", default="main.py")
    parser.add_argument("--readiness-timeout-seconds", type=float, default=90)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:  # noqa: D103  # tracked: #288
    args = _parser().parse_args(argv)
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    return run_evaluator(
        command,
        workspace=args.workspace,
        entrypoint=args.entrypoint,
        readiness_timeout_seconds=args.readiness_timeout_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
