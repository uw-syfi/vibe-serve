"""Record definitions and line parsing for the evaluator result protocol."""

from __future__ import annotations

import json
from typing import Literal, NoReturn, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from vs_evaluator_protocol.errors import ReasonCode, reject

PROTOCOL_VERSION: int = 2

# TRANSITIONAL: the two Go evaluators in this repository still emit protocol 1,
# so the reader keeps accepting it until they are rebuilt against an SDK that
# emits protocol 2. Version 1 has no `required` key, so a version 1 stream is
# read as declaring every metric required. Delete `LEGACY_PROTOCOL_VERSION`,
# `SUPPORTED_PROTOCOL_VERSIONS`, and `Hello._require_every_legacy_metric` in
# that follow-up; nothing else carries version 1 behavior.
LEGACY_PROTOCOL_VERSION: int = 1
SUPPORTED_PROTOCOL_VERSIONS: frozenset[int] = frozenset({LEGACY_PROTOCOL_VERSION, PROTOCOL_VERSION})


class _StrictRecord(BaseModel):
    """Base for records that reject unknown keys and type coercion."""

    model_config = ConfigDict(extra="forbid", strict=True)


class MetricSpec(_StrictRecord):
    """Declaration of one metric the evaluator produces.

    `unit` and `direction` are advisory and select nothing: `unit` is
    human-facing and `direction` states the metric's intrinsic
    better-direction. `required` is not advisory: it says whether every
    successful run must report the metric. An optional metric is still
    declared, so it can never be reported under a name the reader has not
    seen, but it may be absent from a result row.
    """

    unit: str | None = None
    direction: Literal["max", "min"] | None = None
    required: bool = True


class Hello(_StrictRecord):
    """Opening record declaring the protocol version and produced metrics."""

    kind: Literal["hello"] = "hello"
    protocol: int
    metrics: dict[str, MetricSpec]

    @model_validator(mode="after")
    def _require_every_legacy_metric(self) -> Self:
        """Read a version 1 declaration as marking every metric required.

        TRANSITIONAL, removed with the rest of version 1 support. Version 1
        has no `required` key, so normalizing here keeps every consumer of a
        `Hello`, `read_measurement` and `check_objectives` alike, from having
        to branch on the protocol version.
        """
        if self.protocol == LEGACY_PROTOCOL_VERSION:
            self.metrics = {
                name: spec.model_copy(update={"required": True})
                for name, spec in self.metrics.items()
            }
        return self


class Result(_StrictRecord):
    """Measured row for one operating point.

    `values` is intentionally untyped beyond JSON: rejecting non-numbers,
    booleans, and non-finite numbers is the reader's job and carries its own
    reason codes.
    """

    kind: Literal["result"] = "result"
    label: str = ""
    values: dict[str, JsonValue]


class ErrorRecord(_StrictRecord):
    """Terminating record reporting that the evaluator produced no row."""

    kind: Literal["error"] = "error"
    message: str = Field(min_length=1)


Record = Hello | Result | ErrorRecord

_RECORD_TYPES: dict[str, type[Record]] = {
    "hello": Hello,
    "result": Result,
    "error": ErrorRecord,
}


def parse_records(text: str) -> list[Record]:
    """Parse a record stream into typed records, one per non-blank line.

    Validates each line on its own: JSON shape, record kind, key set, and
    field types. Cross-record obligations belong to `read_measurement`.

    Raises:
        ProtocolError: when a line is not a record of a known kind, carries an
            unknown key, or has a field that violates the record definition.
    """
    return [
        _parse_line(line, number)
        for number, line in enumerate(text.splitlines(), start=1)
        if line.strip()
    ]


def _parse_line(line: str, number: int) -> Record:
    """Parse one non-blank line into the record its `kind` names."""
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        reject(ReasonCode.MALFORMED_LINE, f"line {number} is not valid JSON")
    if not isinstance(payload, dict):
        reject(ReasonCode.MALFORMED_LINE, f"line {number} is not a JSON object")
    kind = payload.get("kind")
    if not isinstance(kind, str) or kind not in _RECORD_TYPES:
        reject(ReasonCode.UNKNOWN_KIND, f"line {number} has unknown record kind {kind!r}")
    try:
        return _RECORD_TYPES[kind].model_validate_json(line)
    except ValidationError as error:
        _reject_invalid_record(error, line=number, kind=kind)


def _reject_invalid_record(error: ValidationError, *, line: int, kind: str) -> NoReturn:
    """Translate a pydantic failure into the reason code the protocol names."""
    details = error.errors()
    unknown_key = next((detail for detail in details if detail["type"] == "extra_forbidden"), None)
    detail = unknown_key or details[0]
    key = ".".join(str(part) for part in detail["loc"])
    if unknown_key is not None:
        reject(ReasonCode.UNKNOWN_KEY, f"line {line}: {kind} record has unknown key {key!r}")
    reject(
        ReasonCode.INVALID_RECORD,
        f"line {line}: {kind} record has invalid key {key!r}: {detail['msg']}",
    )
