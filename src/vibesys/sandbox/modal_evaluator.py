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
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Generator, Sequence
from contextlib import contextmanager

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_MODAL_WEB_URL = re.compile(r"https://[a-zA-Z0-9.-]+\.modal\.run")
_MODAL_DEPLOYMENT = re.compile(
    r"https://modal\.com/apps/[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+/deployed/"
    r"([a-zA-Z0-9_.-]+)"
)
_LOCK_PATH = "/tmp/vibesys-modal-evaluator.lock"
_MAX_DIAGNOSTIC_CHARS = 20_000


@contextmanager
def _exclusive_evaluation() -> Generator[None]:
    """Serialize deploy-and-evaluate callers sharing the editor container."""
    with open(_LOCK_PATH, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def extract_modal_web_url(output: str) -> str:
    """Return the last Modal web endpoint printed by ``modal deploy``."""
    plain = _ANSI_ESCAPE.sub("", output)
    # Rich may wrap a long endpoint between ``moda`` and ``l.run``. Removing
    # whitespace is safe before matching because Modal hostnames contain none.
    compact = re.sub(r"\s+", "", plain)
    matches = _MODAL_WEB_URL.findall(compact)
    if not matches:
        raise ValueError("modal deploy did not print a *.modal.run web endpoint")
    return matches[-1]


def extract_modal_app_identifier(output: str) -> str:
    """Return the deployed Modal app name printed by ``modal deploy``."""
    plain = _ANSI_ESCAPE.sub("", output)
    compact = re.sub(r"\s+", "", plain)
    matches = _MODAL_DEPLOYMENT.findall(compact)
    if not matches:
        raise ValueError("modal deploy did not print a deployment URL")
    return matches[-1]


def recent_modal_logs(app_identifier: str, *, workspace: str) -> str:
    """Fetch a bounded recent-log excerpt for a failed readiness check."""
    try:
        result = subprocess.run(
            [
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
            with urllib.request.urlopen(health_url, timeout=10) as response:
                if response.status == 200:
                    return
                last_error = f"HTTP {response.status}"
        except (OSError, urllib.error.URLError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(2)
    raise TimeoutError(f"{health_url} did not become ready: {last_error}")


def run_evaluator(
    command: Sequence[str],
    *,
    workspace: str = "/workspace",
    readiness_timeout_seconds: float = 90,
) -> int:
    """Deploy the candidate and run ``command`` against its live URL."""
    if not command:
        raise ValueError("missing evaluator command after '--'")

    with _exclusive_evaluation():
        return _run_evaluator_unlocked(
            command,
            workspace=workspace,
            readiness_timeout_seconds=readiness_timeout_seconds,
        )


def _run_evaluator_unlocked(
    command: Sequence[str],
    *,
    workspace: str,
    readiness_timeout_seconds: float,
) -> int:
    deploy = subprocess.run(
        ["uv", "run", "modal", "deploy", f"{workspace}/main.py"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    deploy_output = f"{deploy.stdout}\n{deploy.stderr}".strip()
    if deploy_output:
        print(deploy_output, file=sys.stderr)
    if deploy.returncode != 0:
        return deploy.returncode

    try:
        base_url = extract_modal_web_url(deploy_output)
        wait_for_health(base_url, timeout_seconds=readiness_timeout_seconds)
    except (TimeoutError, ValueError) as exc:
        print(f"Modal evaluator setup failed: {exc}", file=sys.stderr)
        try:
            app_identifier = extract_modal_app_identifier(deploy_output)
        except ValueError:
            pass
        else:
            print(
                f"Recent Modal logs:\n{recent_modal_logs(app_identifier, workspace=workspace)}",
                file=sys.stderr,
            )
        return 1

    result = subprocess.run(
        [*command, "--url", base_url],
        cwd=workspace,
        check=False,
    )
    return result.returncode


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default="/workspace")
    parser.add_argument("--readiness-timeout-seconds", type=float, default=90)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    return run_evaluator(
        command,
        workspace=args.workspace,
        readiness_timeout_seconds=args.readiness_timeout_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
