"""Typed round records and in-memory history for the agent loop.

``hypothesis_outcome`` and ``candidate_disposition`` are plain strings rather
than enums: this library intentionally does not depend on ``vibesys``, which
owns the actual ``HypothesisOutcome``/``CandidateDisposition`` vocabularies.
Callers validate those values against their own enums and pass in the
classification (e.g. which outcome strings count as a failure) rather than
this module hard-coding it.

``RoundRecord`` is a ``pydantic.dataclasses.dataclass`` rather than a
``BaseModel``: callers construct it positionally/by-keyword exactly like a
plain dataclass (matching how the framework's own tests build fixtures),
while still getting field validation and ``extra="forbid"`` on load. On disk,
``round_number`` is persisted under the key ``"round"``; that rename is
handled at the JSON boundary rather than via a pydantic field alias, since an
aliased first field without a plain default conflicts with dataclass
field-ordering rules (a later required field would "follow a default
argument").

``RoundHistory`` owns only in-memory collection behavior and rollback-base
resolution. The application-level project-state library owns persistence and
the on-disk layout.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import ConfigDict, Field, TypeAdapter
from pydantic.dataclasses import dataclass

if TYPE_CHECKING:
    from collections.abc import Container


@dataclass(config=ConfigDict(extra="forbid"))
class RoundRecord:
    """One completed round of the agent loop."""

    round_number: int
    commit: str | None
    perf_metric: float | None
    perf_unit: str | None
    passed: bool
    # True when the orchestrator chose to skip profiling this round; the
    # perf_metric (if any) was reused / inherited from a prior measurement
    # rather than freshly measured this round. Plateau detection ignores
    # these so a chain of skipped-profile rounds doesn't masquerade as a
    # real plateau.
    profile_skipped: bool = False
    # False means the implementer was still investigating and sparse-review
    # policy intentionally deferred both the independent judge and official
    # framework gates. Such a round is provisional, not a failed attempt.
    reviewed: bool = True
    hypothesis_id: str | None = None
    hypothesis_declared_outcome: str | None = None
    judge_verdict: Literal["pass", "fail", "deferred"] | None = None
    hypothesis_outcome: str | None = None
    # Plan text carried alongside the id so a resolved hypothesis stays
    # readable after ``active_hypothesis.json`` has moved on. Only the live
    # plan is persisted there, so without this the claim and the attempted
    # change are recoverable for the active hypothesis only.
    hypothesis_claim: str | None = None
    hypothesis_task: str | None = None
    hypothesis_parent_round: int | None = None
    # Exact tree from which this hypothesis started. This can be newer than
    # the historical end-of-round checkpoint when an operator or framework
    # repair lands between rounds.
    hypothesis_parent_commit: str | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    evaluation_artifact: str | None = None
    # Only framework-owned gates can set this. A judge-approved hypothesis may
    # be a useful provisional working checkpoint without becoming the latest
    # officially verified checkpoint.
    official_evaluation: bool = False
    official_evaluation_reason: str | None = None
    # Candidate evidence is deliberately separate from official tracking. It
    # may describe one directly comparable representative point rather than a
    # full canonical evaluation and therefore never updates ``perf_metric`` or
    # plateau detection by itself.
    #
    # Mirrors ``vibesys.schemas.CandidateDisposition.UNASSESSED.value``; kept
    # as a plain string default rather than importing the enum (see module
    # docstring).
    candidate_disposition: str = "unassessed"
    candidate_metrics: dict[str, float] = Field(default_factory=dict)
    candidate_evaluation_artifact: str | None = None
    candidate_operating_point: str = ""
    candidate_retention_reason: str = ""
    # Framework-derived retention. ``None`` identifies a legacy or
    # not-yet-assessed record; presentation code must not infer it from
    # ``official_evaluation``.
    candidate_retained: bool | None = None
    perf_direction: Literal["max", "min"] | None = None
    perf_baseline_round: int | None = None
    perf_baseline_commit: str | None = None
    perf_baseline_metric: float | None = None
    perf_delta_pct: float | None = None
    # Who produced ``perf_metric``: a framework-owned benchmark contract
    # ("framework") or the agent's own report ("implementer"). ``None``
    # identifies a legacy record written before provenance was tracked;
    # consumers treat legacy records as trusted to preserve their existing
    # resolutions. ``official_evaluation`` only says the framework gates ran;
    # it does not vouch for the metric's source.
    perf_provenance: Literal["framework", "implementer"] | None = None


_ROUND_RECORD_ADAPTER: TypeAdapter[RoundRecord] = TypeAdapter(RoundRecord)


def serialize_round_record(record: RoundRecord) -> dict[str, Any]:
    """Return the stable JSON-compatible representation of *record*."""
    dumped = _ROUND_RECORD_ADAPTER.dump_python(record, mode="json")
    round_number = dumped.pop("round_number")
    return {"round": round_number, **dumped}


def parse_round_record(data: dict[str, Any]) -> RoundRecord:
    """Parse one JSON-compatible round record from the portable schema."""
    if "round_number" not in data and "round" in data:
        data = dict(data)
        data["round_number"] = data.pop("round")
    return _ROUND_RECORD_ADAPTER.validate_python(data)


class RoundHistory:
    """The in-memory round-by-round history of an agent-loop run."""

    def __init__(self, records: list[RoundRecord] | None = None) -> None:
        """Wrap *records*, starting with an empty history by default."""
        self.records: list[RoundRecord] = records if records is not None else []

    def append(self, record: RoundRecord) -> None:
        """Add *record* to the end of the history."""
        self.records.append(record)

    def resolve_rollback_commit(
        self,
        target: RoundRecord,
        failed_outcomes: Container[str],
    ) -> tuple[str | None, int | None]:
        """Resolve a requested parent round to the actual failed-child base tree.

        A round record names the historical tree at the end of that round. An
        immediately following hypothesis can start from a newer tree after a
        validated operator or framework repair. If that child later fails,
        restore its recorded pre-hypothesis tree instead of erasing those
        independent repairs along with the failed implementation.

        *failed_outcomes* names which ``hypothesis_outcome`` values count as
        a failed hypothesis; the caller derives this from its own outcome
        enum.

        The second return value identifies the failed child whose base was
        chosen; ``None`` means the historical target commit is used
        unchanged.
        """
        if not self.records:
            return target.commit, None
        latest = self.records[-1]
        if (
            latest.round_number > target.round_number
            and latest.hypothesis_parent_round == target.round_number
            and latest.hypothesis_parent_commit is not None
            and latest.hypothesis_outcome is not None
            and latest.hypothesis_outcome in failed_outcomes
        ):
            return latest.hypothesis_parent_commit, latest.round_number
        return target.commit, None
