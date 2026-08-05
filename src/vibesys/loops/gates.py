"""Framework-owned correctness gates shared by optimization loops."""

from __future__ import annotations

from dataclasses import dataclass

from vibesys.run import LoopContext
from vibesys.server.events import EventType, SubprocessOutputData


@dataclass(frozen=True)
class AccuracyGateResult:
    """Outcome of running the immutable accuracy command for a candidate."""

    command: str | None
    passed: bool
    output: str
    feedback: str | None
    executed: bool


def run_accuracy_gate(
    ctx: LoopContext,
    *,
    process_id: str,
    timeout_seconds: int | None = None,
    execution_command: str | None = None,
) -> AccuracyGateResult:
    """Run the trusted accuracy command without delegating acceptance to an agent."""
    changed = ctx.trusted_input_changes()
    command = ctx.judge_accuracy_command
    if changed:
        output = "Evaluator-owned files were modified: " + ", ".join(changed)
        ctx.lprint(f"[framework-accuracy] FAIL: {output}")
        return AccuracyGateResult(
            command=command,
            passed=False,
            output=output,
            feedback=output,
            executed=False,
        )
    if not command:
        return AccuracyGateResult(
            command=None,
            passed=True,
            output="",
            feedback=None,
            executed=False,
        )

    ctx.lprint(f"[framework-accuracy] running: {command}")
    command_to_execute = execution_command or command
    try:
        if timeout_seconds is None:
            result = ctx.judge_backend.execute(command_to_execute)
        else:
            result = ctx.judge_backend.execute(command_to_execute, timeout=timeout_seconds)
        output = result.output.strip()
        passed = result.exit_code == 0
        _publish_subprocess_output(ctx, process_id=process_id, content=result.output)
    except Exception as exc:
        output = f"accuracy command could not be executed: {exc}"
        passed = False

    changed_after_execution = ctx.trusted_input_changes()
    if changed_after_execution:
        mutation = "Evaluator-owned files changed during accuracy execution: " + ", ".join(
            changed_after_execution
        )
        output = f"{output}\n{mutation}".strip()
        passed = False

    if passed:
        ctx.lprint("[framework-accuracy] PASS")
        feedback = None
    else:
        ctx.lprint(f"[framework-accuracy] FAIL: {output[-1000:]}")
        feedback = f"Framework accuracy gate failed.\n{output[-4000:]}"

    return AccuracyGateResult(
        command=command,
        passed=passed,
        output=output,
        feedback=feedback,
        executed=True,
    )


def _publish_subprocess_output(
    ctx: LoopContext,
    *,
    process_id: str,
    content: str,
) -> None:
    if ctx.supervisor is None or not content:
        return
    ctx.supervisor.record(
        EventType.SUBPROCESS_OUTPUT,
        data=SubprocessOutputData(
            process_id=process_id,
            process_kind="accuracy_checker",
            stream="stdout",
            content=content,
        ),
    )
