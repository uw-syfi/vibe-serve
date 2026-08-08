"""agentshim command executor for existing vibesys Docker sandboxes."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Sequence  # noqa: TC003  # tracked: #288
from dataclasses import dataclass
from typing import Any, cast

from agentshim.executor import CommandRequest, CommandResult, CommandStreamSink


@dataclass
class DockerCommandHandle:
    """Command handle for a running ``docker exec`` process."""

    process: subprocess.Popen[str]

    def terminate(self) -> None:  # noqa: D102  # tracked: #288
        try:
            os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            self.process.terminate()

    def kill(self) -> None:  # noqa: D102  # tracked: #288
        try:
            os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            self.process.kill()


@dataclass(frozen=True)
class _CodexRolloutCompletion:
    """Terminal response recovered from a stalled resumed Codex rollout."""

    fingerprint: str
    message: str


_CODEX_RESUME_TERMINATION_SCRIPT = r"""
import os
import signal
import sys

thread_id = sys.argv[1]
own_pid = os.getpid()
for entry in os.listdir("/proc"):
    if not entry.isdigit() or int(entry) == own_pid:
        continue
    try:
        raw = open(f"/proc/{entry}/cmdline", "rb").read()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        continue
    argv = [part.decode("utf-8", errors="replace") for part in raw.split(b"\0") if part]
    if "exec" not in argv or "resume" not in argv or thread_id not in argv:
        continue
    if not any(os.path.basename(part) == "codex" for part in argv):
        continue
    try:
        os.kill(int(entry), signal.SIGTERM)
    except ProcessLookupError:
        pass
"""


class DockerCommandExecutor:
    """Run agentshim CLI commands inside an already-running Docker container."""

    _CODEX_ROLLOUT_POLL_SECONDS = 15.0
    _CODEX_COMPLETION_GRACE_SECONDS = 30.0

    def __init__(self, container_id: str) -> None:  # noqa: D107  # tracked: #288
        self.container_id = container_id
        self._codex_rollout_paths: dict[str, str] = {}

    def find_binary(self, binary_name: str, env: dict[str, str]) -> str:  # noqa: ARG002, D102  # tracked: #288
        return binary_name

    def check_binary(  # noqa: D102  # tracked: #288
        self,
        binary_path: str,  # noqa: ARG002  # tracked: #288
        env: dict[str, str],  # noqa: ARG002  # tracked: #288
        *,
        timeout: int,  # noqa: ARG002  # tracked: #288
    ) -> None:
        return None

    def repair_workspace_ownership(self, *, uid: int, gid: int) -> None:
        """Return bind-mounted workspace files to the host user.

        CLI agents run as root in the editor container. Some editors replace
        files atomically, which leaves the replacement owned by root and can
        make the host-side checkpoint code unable to read it. Repair ownership
        before control returns to the framework rather than waiting until a
        later resume.
        """
        result = subprocess.run(  # noqa: S603  # tracked: #288
            [  # noqa: S607  # tracked: #288
                "docker",
                "exec",
                self.container_id,
                "find",
                "/workspace",
                "-xdev",
                "-user",
                "0",
                "-writable",
                "-exec",
                "chown",
                f"{uid}:{gid}",
                "{}",
                "+",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(
                "failed to restore writable Docker workspace ownership"
                + (f": {detail}" if detail else "")
            )

    def run(  # noqa: C901, D102  # tracked: #288
        self,
        request: CommandRequest,
        sink: CommandStreamSink,
    ) -> CommandResult:
        docker_cmd = [
            "docker",
            "exec",
            "-i",
            "-w",
            "/workspace",
            self.container_id,
            *request.argv,
        ]
        process = subprocess.Popen(  # noqa: S603  # tracked: #288
            docker_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=os.environ.copy(),
            start_new_session=True,
        )

        sink.started(DockerCommandHandle(process))

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        def read_pipe(pipe: Any, callback: Callable[[str], None], sink: list[str]) -> None:  # noqa: ANN401  # tracked: #288
            try:
                for line in iter(pipe.readline, ""):
                    if not line:
                        break
                    sink.append(line)
                    callback(line)
            except (OSError, ValueError):
                pass
            finally:
                try:  # noqa: SIM105  # tracked: #288
                    pipe.close()
                except (OSError, ValueError):
                    pass

        stdout_thread = threading.Thread(
            target=read_pipe,
            args=(process.stdout, sink.stdout, stdout_lines),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=read_pipe,
            args=(process.stderr, sink.stderr, stderr_lines),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        try:
            if process.stdin:
                process.stdin.write(request.stdin)
                process.stdin.close()
        except BrokenPipeError:
            pass

        watchdog_killed = False
        try:
            if request.timeout is not None and not self._is_codex_json_command(request.argv):
                process.wait(timeout=request.timeout)
            else:
                watchdog_killed = self._wait_with_docker_exec_watchdog(
                    process,
                    request.argv,
                    sink=sink,
                    stdout_lines=stdout_lines,
                    timeout=request.timeout,
                )
        except subprocess.TimeoutExpired:
            self._kill_process_group(process)
            process.wait()
            # TimeoutExpired is only raised by the wait(timeout=...) branch,
            # so request.timeout is non-None on this path.
            raise subprocess.TimeoutExpired(
                list(request.argv), cast("float", request.timeout)
            ) from None
        finally:
            if process.poll() is None:
                self._kill_process_group(process)
                process.wait()

        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)

        returncode = 0 if watchdog_killed else process.returncode
        return CommandResult(
            returncode=returncode,
            stdout="".join(stdout_lines),
            stderr="".join(stderr_lines),
        )

    def _wait_with_docker_exec_watchdog(  # noqa: C901  # tracked: #288
        self,
        process: subprocess.Popen[str],
        cmd: Sequence[str],
        *,
        sink: CommandStreamSink,
        stdout_lines: list[str],
        timeout: float | None,
    ) -> bool:
        child_binary = os.path.basename(cmd[0]) if cmd else ""  # noqa: PTH119  # tracked: #288
        watchdog_killed = False
        codex_thread_id = self._codex_resume_thread_id(cmd)
        watch_codex_rollout = self._is_codex_json_command(cmd)
        next_codex_poll = time.monotonic()
        completion_fingerprint: str | None = None
        completion_seen_at: float | None = None
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                assert timeout is not None  # noqa: S101  # tracked: #288
                raise subprocess.TimeoutExpired(list(cmd), timeout)
            wait_seconds = 5.0 if deadline is None else min(5.0, deadline - now)
            try:
                process.wait(timeout=wait_seconds)
                return watchdog_killed  # noqa: TRY300  # tracked: #288
            except subprocess.TimeoutExpired:
                now = time.monotonic()
                if deadline is not None and now >= deadline:
                    assert timeout is not None  # noqa: S101  # tracked: #288
                    raise subprocess.TimeoutExpired(list(cmd), timeout) from None
                if codex_thread_id is None and watch_codex_rollout:
                    codex_thread_id = self._codex_started_thread_id(stdout_lines)
                if codex_thread_id is not None and now >= next_codex_poll:
                    next_codex_poll = now + self._CODEX_ROLLOUT_POLL_SECONDS
                    completion = self._read_codex_rollout_completion(codex_thread_id)
                    if completion is None:
                        completion_fingerprint = None
                        completion_seen_at = None
                    elif completion.fingerprint != completion_fingerprint:
                        completion_fingerprint = completion.fingerprint
                        completion_seen_at = now
                    elif (
                        completion_seen_at is not None
                        and now - completion_seen_at >= self._CODEX_COMPLETION_GRACE_SECONDS
                    ):
                        self._forward_codex_completion(completion, sink, stdout_lines)
                        self._terminate_codex_resume(codex_thread_id)
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                        return True

                check = subprocess.run(  # noqa: S603  # tracked: #288
                    [  # noqa: S607  # tracked: #288
                        "docker",
                        "exec",
                        self.container_id,
                        "pgrep",
                        "-f",
                        child_binary,
                    ],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
                if check.returncode != 0:
                    watchdog_killed = True
                    process.kill()
                    process.wait()
                    return watchdog_killed

    @staticmethod
    def _is_codex_json_command(cmd: Sequence[str]) -> bool:
        """Return whether *cmd* is a machine-readable Codex exec invocation."""
        if not cmd or os.path.basename(cmd[0]) != "codex" or "--json" not in cmd:  # noqa: PTH119  # tracked: #288
            return False
        return "exec" in cmd

    @classmethod
    def _codex_resume_thread_id(cls, cmd: Sequence[str]) -> str | None:
        """Return the validated thread ID for ``codex exec resume`` commands."""
        if not cls._is_codex_json_command(cmd):
            return None
        try:
            resume_index = cmd.index("resume")
            thread_id = cmd[resume_index + 1]
        except (ValueError, IndexError):
            return None
        if not re.fullmatch(r"[0-9a-fA-F-]{32,64}", thread_id):
            return None
        return thread_id

    @staticmethod
    def _codex_started_thread_id(stdout_lines: Sequence[str]) -> str | None:
        """Recover a fresh invocation's thread ID from its streamed JSON."""
        for line in reversed(stdout_lines):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("type") != "thread.started":
                continue
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str) and re.fullmatch(r"[0-9a-fA-F-]{32,64}", thread_id):
                return thread_id
        return None

    def _read_codex_rollout_completion(  # noqa: C901, PLR0912  # tracked: #288
        self,
        thread_id: str,
    ) -> _CodexRolloutCompletion | None:
        """Read stable terminal evidence from a resumed Codex rollout, if any."""
        rollout_path = self._codex_rollout_paths.get(thread_id)
        if rollout_path is None:
            located = subprocess.run(  # noqa: S603  # tracked: #288
                [  # noqa: S607  # tracked: #288
                    "docker",
                    "exec",
                    self.container_id,
                    "find",
                    "/root/.codex/sessions",
                    "-type",
                    "f",
                    "-name",
                    f"rollout-*-{thread_id}.jsonl",
                    "-print",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            paths = [line for line in located.stdout.splitlines() if line]
            if located.returncode != 0 or not paths:
                return None
            rollout_path = max(paths)
            self._codex_rollout_paths[thread_id] = rollout_path

        result = subprocess.run(  # noqa: S603  # tracked: #288
            ["docker", "exec", self.container_id, "tail", "-n", "512", rollout_path],  # noqa: S607  # tracked: #288
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None

        events: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)

        last_lifecycle_index = -1
        last_lifecycle_type: str | None = None
        for index, event in enumerate(events):
            if event.get("type") != "event_msg":
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            payload_type = payload.get("type")
            if payload_type in {"task_started", "task_complete"}:
                last_lifecycle_index = index
                last_lifecycle_type = cast("str", payload_type)
        if last_lifecycle_type != "task_complete":
            return None

        message: str | None = None
        for event in reversed(events[: last_lifecycle_index + 1]):
            if event.get("type") != "event_msg":
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "agent_message":
                continue
            candidate_message = payload.get("message")
            if isinstance(candidate_message, str) and candidate_message:
                message = candidate_message
                break
        if message is None:
            return None

        fingerprint = events[last_lifecycle_index].get("timestamp")
        if not isinstance(fingerprint, str) or not fingerprint:
            return None
        return _CodexRolloutCompletion(fingerprint=fingerprint, message=message)

    @staticmethod
    def _forward_codex_completion(
        completion: _CodexRolloutCompletion,
        sink: CommandStreamSink,
        stdout_lines: list[str],
    ) -> None:
        """Send recovered output through agentshim's ordinary Codex parser."""
        synthetic_lines = [
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "vibesys-codex-rollout-watchdog",
                        "type": "agent_message",
                        "text": completion.message,
                    },
                }
            )
            + "\n",
            json.dumps({"type": "turn.completed"}) + "\n",
        ]
        for line in synthetic_lines:
            stdout_lines.append(line)
            sink.stdout(line)

    def _terminate_codex_resume(self, thread_id: str) -> None:
        """Stop only the completed resumed Codex process inside this container."""
        subprocess.run(  # noqa: S603  # tracked: #288
            [  # noqa: S607  # tracked: #288
                "docker",
                "exec",
                self.container_id,
                "python3",
                "-c",
                _CODEX_RESUME_TERMINATION_SCRIPT,
                thread_id,
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

    def _kill_process_group(self, process: subprocess.Popen[str]) -> None:
        try:  # noqa: SIM105  # tracked: #288
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
