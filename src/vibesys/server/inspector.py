"""Read-only queries over live and persisted run state."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path  # noqa: TC003  # tracked: #288
from typing import TYPE_CHECKING

from vibesys.server.events import ConfigurationFailedData, EventStatus, EventType, RunEvent

if TYPE_CHECKING:
    from vibesys.server.supervisor import RunSupervisor


@dataclass(frozen=True)
class _HistoryDocument:
    """Decoded read-only evidence from persisted state or a run log."""

    name: str
    text: str


class RunInspector:
    """Answer operator questions without mutating agent behavior."""

    def __init__(self, supervisor: RunSupervisor):  # noqa: ANN204, D107  # tracked: #288
        self.supervisor = supervisor

    def answer(self, question: str) -> str:  # noqa: D102, PLR0911  # tracked: #288
        configuration_failure = self._latest_configuration_failure()
        if configuration_failure is not None:
            return self._status_answer(question, configuration_failure)
        query = question.lower()
        if any(word in query for word in ("doing", "current", "status", "now")):
            return self._status_answer(question, self.supervisor.status())
        if any(word in query for word in ("failed", "failure", "why")):
            failed = self._latest_invocation(status=EventStatus.FAILED)
            answer = (
                "Latest failed agent invocation:\n" + failed
                if failed
                else self._search_latest(("judge", "fail", "feedback", "verdict"), "judge result")
            )
            return self._status_answer(question, answer)
        if "judge" in query:
            judge = self._latest_invocation(agent_kind="judge")
            answer = (
                "Latest judge invocation:\n" + judge
                if judge
                else self._search_latest(("judge", "feedback", "verdict"), "judge result")
            )
            return self._status_answer(question, answer)
        if any(word in query for word in ("benchmark", "performance", "metric", "latest result")):
            return self._status_answer(
                question,
                self._search_latest(
                    ("benchmark", "metric", "latency", "throughput"), "benchmark result"
                ),
            )
        match = re.search(r"round\s+(\d+)", query)
        if match:
            return self._status_answer(question, self.round_detail(int(match.group(1))))
        if "previous" in query or "last round" in query:
            current = re.search(
                r"(?i)(?:round|iter(?:ation)?)\D*(\d+)", self.supervisor.current_round or ""
            )
            number = int(current.group(1)) if current else self._latest_round_number()
            if number:
                return self._status_answer(question, self.round_detail(max(1, number - 1)))
        # Names only what this read-only matcher can actually answer. It must
        # not advertise slash commands: this string is shown in the experiment
        # chat, and the operator would have to leave it to run one.
        return (
            f"{self.supervisor.status()}. This summary matches on keywords, so it answers "
            "questions about a round, a failure, the judge, or a benchmark."
        )

    def round_detail(self, number: int) -> str:  # noqa: D102  # tracked: #288
        pattern = re.compile(rf"(?i)(round|iter(?:ation)?)\D*{number}\b")
        chunks = []
        for document in self._history_documents():
            lines = document.text.splitlines()
            indexes = [i for i, line in enumerate(lines) if pattern.search(line)]
            if indexes:
                start = max(0, indexes[-1] - 2)
                chunks.append(f"--- {document.name} ---\n" + "\n".join(lines[start : start + 80]))
        return "\n\n".join(chunks) or f"No persisted detail found for round {number}."

    def latest_run_log(self) -> Path | None:  # noqa: D102  # tracked: #288
        log_dir = self.supervisor.log_dir
        if log_dir is None:
            return None
        candidates = sorted(
            path for path in log_dir.glob("run-*.log") if path.is_file() and not path.is_symlink()
        )
        if candidates:
            return candidates[-1]
        return None

    def _status_answer(self, question: str, answer: str) -> str:
        self.supervisor.record(EventType.STATUS_QUERY, question)
        return answer

    def _history_documents(self) -> list[_HistoryDocument]:
        documents: list[_HistoryDocument] = []
        project_run = self.supervisor.project_run
        if project_run is not None:
            for snapshot in project_run.history_snapshots():
                documents.extend(
                    _HistoryDocument(
                        name=item.relative_path.name,
                        text=item.contents.decode("utf-8", errors="replace"),
                    )
                    for item in snapshot.files
                    if item.relative_path.suffix in {".json", ".jsonl", ".log", ".md", ".txt"}
                )
        latest = self.latest_run_log()
        if latest is not None:
            documents.append(
                _HistoryDocument(
                    name=latest.name,
                    text=latest.read_text(encoding="utf-8", errors="replace"),
                )
            )
        return documents

    def _search_latest(self, terms: tuple[str, ...], label: str) -> str:
        for document in reversed(self._history_documents()):
            lines = document.text.splitlines()
            hits = [
                i for i, line in enumerate(lines) if any(term in line.lower() for term in terms)
            ]
            if hits:
                start = max(0, hits[-1] - 8)
                return f"Latest {label} ({document.name}):\n" + "\n".join(
                    lines[start : hits[-1] + 12]
                )
        return f"No {label} has been persisted yet."

    def _latest_invocation(
        self, *, status: EventStatus | None = None, agent_kind: str | None = None
    ) -> str | None:
        for event in reversed(self.supervisor.read_events()):
            if event.type is not EventType.INVOCATION_FINISHED:
                continue
            if status is not None and event.status is not status:
                continue
            if agent_kind is not None and event.agent_kind != agent_kind:
                continue
            return self._format_event(event)
        return None

    def _latest_configuration_failure(self) -> str | None:
        for event in reversed(self.supervisor.read_history_events()):
            if event.type is not EventType.CONFIGURATION_FAILED:
                continue
            data = event.data
            if not isinstance(data, ConfigurationFailedData):
                continue
            answer = f"Experiment configuration failed during {data.stage}: {data.message}"
            if data.usage:
                answer += f"\n\n{data.usage}"
            return answer
        return None

    def _latest_round_number(self) -> int | None:
        numbers = []
        pattern = re.compile(r"(?i)(?:round|iter(?:ation)?)\D*(\d+)")
        for document in self._history_documents():
            numbers.extend(int(match.group(1)) for match in pattern.finditer(document.text))
        return max(numbers) if numbers else None

    @staticmethod
    def _format_event(event: RunEvent) -> str:
        return json.dumps(event.model_dump(mode="json"), indent=2, ensure_ascii=False)
