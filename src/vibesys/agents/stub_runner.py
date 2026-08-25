"""Fast deterministic agent client for end-to-end interface smoke tests."""

from __future__ import annotations

import re
import time
from collections.abc import Callable  # noqa: TC003  # tracked: #288
from pathlib import Path  # noqa: TC003  # tracked: #288
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel

from vibesys._agent_cli.base import MCPServerSpec  # noqa: TC001  # tracked: #288
from vibesys.agents.client import AgentClient
from vibesys.agents.contracts import AgentCapabilities
from vibesys.agents.progress import AgentProgress  # noqa: TC001  # tracked: #288
from vibesys.schemas import HypothesisOutcome

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool  # annotation only; avoid eager agent-stack import

T = TypeVar("T", bound=BaseModel)


class StubAgentClient(AgentClient):
    """Return valid canned responses without invoking an external agent."""

    backend_name = "stub"

    def __init__(self) -> None:
        """Create a stateless deterministic client."""

    @property
    def capabilities(self) -> AgentCapabilities:
        """The deterministic stub does not expose external tools."""
        return AgentCapabilities(session_reuse=False)

    def close(self) -> None:
        """The deterministic stub owns no external resources."""

    def set_log_file(self, stream: object) -> None:
        """Accept log retargeting; the deterministic stub emits no file logs."""
        del stream

    def invoke(  # noqa: D102, PLR0913  # tracked: #288
        self,
        *,
        kind: str,
        workspace: Path,
        system_prompt: str,
        user_prompt: str,
        response_cls: type[T],
        fallback_factory: Callable[[], T],
        round_label: str,
        progress: AgentProgress | None = None,
        **kwargs: object,
    ) -> T:
        del workspace, system_prompt, user_prompt, progress, kwargs
        from vibesys.render.sink import output_sink  # noqa: PLC0415  # tracked: #288

        output_sink().agent_output(
            f"[stub-agent] {round_label}: starting {kind}\n",
            channel="diagnostic",
            agent_kind=kind,
        )
        time.sleep(0.05)
        response = _response_data(response_cls.__name__, _round_number(round_label))
        output_sink().agent_output(
            f"[stub-agent] {round_label}: completed {kind}\n",
            channel="diagnostic",
            agent_kind=kind,
        )
        return response_cls.model_validate(response) if response is not None else fallback_factory()

    def invoke_text(  # noqa: PLR0913  # tracked: #288
        self,
        *,
        kind: str,
        workspace: Path,
        system_prompt: str,
        env: dict[str, str] | None = None,
        user_prompt: str,
        round_label: str,
        invocation_id: str | None = None,
        progress: AgentProgress | None = None,
        mcp_servers: list[MCPServerSpec] | None = None,
        tools: list[BaseTool] | None = None,
        reuse_session: bool | None = None,
        session_key: str | None = None,
    ) -> str:
        """Return a deterministic conversational answer for TUI smoke tests."""
        del (
            workspace,
            system_prompt,
            env,
            progress,
            mcp_servers,
            tools,
            reuse_session,
            session_key,
        )
        from vibesys.render.sink import output_sink  # noqa: PLC0415  # tracked: #288

        output_sink().agent_output(
            f"[stub-agent] investigating: {user_prompt}\n",
            channel="analysis",
            agent_kind=kind,
            round_label=round_label,
            invocation_id=invocation_id,
        )
        return "Stub chat inspected the available experiment trajectory."


# Each stub hypothesis spans two rounds so a smoke run exercises the
# continuation path, and every third one is disproven so the experiment log
# shows proven, rejected, and multi-round entries without a live backend.
_STUB_HYPOTHESIS_ROUNDS = 2


def _round_number(round_label: str) -> int:
    match = re.search(r"(\d+)", round_label or "")
    return int(match.group(1)) if match else 1


def _stub_hypothesis(round_number: int) -> tuple[str, str, str]:
    index = (round_number - 1) // _STUB_HYPOTHESIS_ROUNDS + 1
    claims = [
        "batching the prefill step removes per-request launch overhead",
        "a larger KV cache block trades memory for fewer allocations",
        "reordering the sampler avoids a redundant device sync",
    ]
    actions = [
        "batch the prefill step",
        "grow the KV cache block size",
        "reorder the sampler",
    ]
    position = (index - 1) % len(claims)
    return f"H-{index:02d}", claims[position], actions[position]


# A rising series with a dip on the disproven hypothesis, so a smoke run
# exercises the measured column and its baseline delta rather than leaving it
# empty for want of any measurement at all.
_STUB_BASE_METRIC = 1000.0
_STUB_METRIC_STEP = 45.0
_STUB_METRIC_REGRESSION = 20.0
_STUB_METRIC_UNIT = "median_tok_per_sec"


def _stub_metric(round_number: int) -> float:
    index = (round_number - 1) // _STUB_HYPOTHESIS_ROUNDS
    regressed = (index + 1) % 3 == 0
    value = _STUB_BASE_METRIC + index * _STUB_METRIC_STEP
    return value - _STUB_METRIC_REGRESSION if regressed else value


def _stub_outcome(round_number: int) -> str:
    index = (round_number - 1) // _STUB_HYPOTHESIS_ROUNDS + 1
    if round_number % _STUB_HYPOTHESIS_ROUNDS != 0:
        return HypothesisOutcome.CONTINUE.value
    return (
        HypothesisOutcome.DISPROVEN.value if index % 3 == 0 else HypothesisOutcome.SUPPORTED.value
    )


def _response_data(model_name: str, round_number: int) -> dict[str, object] | None:
    hypothesis_id, claim, action = _stub_hypothesis(round_number)
    responses: dict[str, dict[str, object]] = {
        "PreRoundDecision": {
            "need_profile": False,
            "profile_focus": "",
            "reasoning": "Stub smoke test skips profiling.",
        },
        "OrchestratorPlan": {
            "hypothesis_id": hypothesis_id,
            "hypothesis": claim,
            "task": action,
            "pass_criteria": "The stub judge returns a deterministic pass.",
            "reasoning": "Stub smoke test plan.",
            # Real runs measure on a sparse cadence, so most rounds carry no
            # verified metric. Requesting one on each closing round gives a
            # smoke run the same shape a measured run has, one number per
            # hypothesis, without waiting for the cadence to come round.
            "request_official_evaluation": round_number % _STUB_HYPOTHESIS_ROUNDS == 0,
        },
        "ImplementerResponse": {
            "summary": "Stub implementer completed without workspace changes.",
            "expected_behavior": "The run advances immediately to the judge.",
            "hypothesis_outcome": _stub_outcome(round_number),
            "perf_metric": _stub_metric(round_number),
            "perf_unit": _STUB_METRIC_UNIT,
        },
        "JudgeResponse": {
            "analysis": "Stub judge accepted the smoke-test invocation.",
            "feedback": "",
            "verdict": "pass",
        },
        "ProfilerSummary": {
            "analysis": "Stub profile.",
            "bottlenecks": "None; no workload was executed.",
            "suggestions": "None.",
            "perf_metric": _stub_metric(round_number),
            "perf_unit": _STUB_METRIC_UNIT,
        },
        "SingleAgentRoundResponse": {
            "summary": "Stub single-agent round completed.",
            "expected_behavior": "The lifecycle completes immediately.",
            "self_review": "Stub review passed.",
            "feedback": "",
            "verdict": "pass",
            "bottlenecks": "None.",
            "suggestions": "None.",
            "profile_analysis": "Stub profile.",
            "perf_metric": _stub_metric(round_number),
            "perf_unit": _STUB_METRIC_UNIT,
        },
    }
    return responses.get(model_name)
