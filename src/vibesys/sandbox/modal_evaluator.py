"""Run a trusted evaluator against a candidate deployed on Modal.

The Modal run environment edits candidates in a local CPU-only container, so
service-style evaluators cannot use their default localhost URL. This helper is
mounted read-only by the runtime adapter. It deploys the current candidate,
discovers the emitted web endpoint, waits for readiness, and appends ``--url``
to the trusted evaluator command.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
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


def run_evaluator(
    command: Sequence[str],
    *,
    workspace: str = "/workspace",
    entrypoint: str = "main.py",
    readiness_timeout_seconds: float = 90,
) -> int:
    """Deploy the candidate and run ``command`` against its live URL."""
    if not command:
        raise ValueError("missing evaluator command after '--'")  # noqa: TRY003  # tracked: #288

    with _exclusive_evaluation():
        return _run_evaluator_unlocked(
            command,
            workspace=workspace,
            entrypoint=entrypoint,
            readiness_timeout_seconds=readiness_timeout_seconds,
        )


def _run_evaluator_unlocked(  # noqa: C901, PLR0912, PLR0915  # tracked: #288
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
            if lease.candidate_revision == candidate_revision and _healthy_now(lease.base_url):
                print(  # noqa: T201  # tracked: #288
                    "Reusing healthy Modal deployment for candidate revision "
                    f"{candidate_revision}.",
                    file=sys.stderr,
                )
                try:
                    result = subprocess.run(  # noqa: S603  # tracked: #288
                        [*command, "--url", lease.base_url],
                        cwd=workspace,
                        check=False,
                    )
                    return result.returncode
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
        try:  # noqa: SIM105  # tracked: #288
            app_identifier = extract_modal_app_identifier(deploy_output)
        except ValueError:
            pass
        wait_for_health(base_url, timeout_seconds=readiness_timeout_seconds)
    except (TimeoutError, ValueError) as exc:
        print(f"Modal evaluator setup failed: {exc}", file=sys.stderr)  # noqa: T201  # tracked: #288
        try:
            app_identifier = extract_modal_app_identifier(deploy_output)
        except ValueError:
            pass
        else:
            print(  # noqa: T201  # tracked: #288
                f"Recent Modal logs:\n{recent_modal_logs(app_identifier, workspace=workspace)}",
                file=sys.stderr,
            )
            _stop_modal_app(app_identifier, workspace=workspace)
        return 1

    if candidate_revision:
        _write_deployment_lease(candidate_revision, base_url, app_identifier)

    try:
        result = subprocess.run(  # noqa: S603  # tracked: #288
            [*command, "--url", base_url],
            cwd=workspace,
            check=False,
        )
        return result.returncode
    finally:
        if _release_requested():
            lease = _read_deployment_lease()
            if (
                candidate_revision
                and lease is not None
                and lease.candidate_revision == candidate_revision
            ):
                _retire_deployment(lease, workspace=workspace)
            elif app_identifier is not None:
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
