"""Backend contracts consumed by the TUI replay harness in ``clients/tui/dev``.

The harness is development-only tooling, but its inputs are backend contracts:
its fixtures are recorded ``RunEvent`` journals, and its mock server answers
with hand-written response bodies. The TypeScript client validates neither at
runtime, so a renamed or removed field leaves the harness rendering through its
raw-content fallback while nothing fails.

Everything here validates against the Python models. Those are the same
contract as ``clients/backend-client/src/generated/protocol.schema.json``, which
is generated from them and pinned to them by ``test_protocol_schema.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from server.api.protocol import PROTOCOL_VERSION, ProtocolRequest, Response
from server.events import RunEvent

_HARNESS_DIR = Path(__file__).resolve().parents[2] / "clients" / "tui" / "dev"
_FIXTURE_DIR = _HARNESS_DIR / "fixtures"
_RESPONSES_PATH = _HARNESS_DIR / "mock-responses.json"


class SchemaAge(StrEnum):
    """How closely a recorded fixture still matches today's ``RunEvent``."""

    CURRENT = "current"
    """Validates and re-serializes byte-for-byte, so it also pins additive drift."""

    LEGACY = "legacy"
    """Validates only. Predates fields the current schema defaults in."""


@dataclass(frozen=True)
class FixtureContract:
    """One recorded fixture and the guarantee it is held to."""

    name: str
    age: SchemaAge
    reason: str


# Every fixture validates. Only a CURRENT one round-trips, and that asymmetry is
# the reason both exist: the current capture catches a field being added, and the
# legacy capture is what proves the renderers still degrade when one is absent.
FIXTURES: tuple[FixtureContract, ...] = (
    FixtureContract(
        name="queue-rs-payloads.jsonl",
        age=SchemaAge.CURRENT,
        reason=(
            "Recorded on the current schema, with a typed payload on every "
            "tool_result. It is the fixture that notices an added field."
        ),
    ),
    FixtureContract(
        name="markdown.jsonl",
        age=SchemaAge.CURRENT,
        reason=(
            "Synthetic, written by markdown.py against the current models "
            "because no real capture contains a table or a fenced code block. "
            "Being generated is why it must round-trip: a drift here means the "
            "generator, not a recording, has gone out of contract."
        ),
    ),
    FixtureContract(
        name="bad-cpp-round1.jsonl",
        age=SchemaAge.LEGACY,
        reason=(
            "Recorded before execution_id, chat_thread_id, diagnostic, and typed "
            "tool_result payloads existed, so re-serializing it adds those keys. "
            "Kept for exactly that: it is the harness's only capture of a client "
            "falling back to raw content."
        ),
    ),
)

_CURRENT_FIXTURES = tuple(fixture for fixture in FIXTURES if fixture.age is SchemaAge.CURRENT)

_MISSING = object()
"""Stands in for an absent key, so ``None`` and absent stay distinguishable."""


def _records(fixture: FixtureContract) -> list[tuple[int, dict[str, Any]]]:
    """The fixture's non-empty lines, parsed, with 1-based line numbers."""
    lines = (_FIXTURE_DIR / fixture.name).read_text().splitlines()
    records = [(number, line) for number, line in enumerate(lines, 1) if line.strip()]
    assert records, f"{fixture.name} carries no events"
    return [(number, json.loads(line)) for number, line in records]


def _response_bodies() -> dict[str, dict[str, Any]]:
    """The mock server's static response bodies, keyed by request type."""
    bodies: dict[str, dict[str, Any]] = json.loads(_RESPONSES_PATH.read_text())
    return bodies


def _request_types() -> set[str]:
    """Every ``type`` discriminator the protocol accepts on a request."""
    union, _discriminator = get_args(ProtocolRequest)
    return {member.model_fields["type"].default for member in get_args(union)}


def test_every_fixture_declares_a_schema_guarantee() -> None:
    """An undeclared fixture would be replayed by the harness and checked by nothing.

    Only replayable journals are fixtures. The directory also holds the
    generators that write them and per-fixture sidecars the mock reads
    (`markdown.py`, `<fixture>.experiments.json`), which carry no `RunEvent`
    and so have nothing to declare.
    """
    present = {
        path.name
        for path in _FIXTURE_DIR.iterdir()
        if path.is_file() and path.name.endswith((".jsonl", ".jsonl.gz"))
    }
    assert present == {fixture.name for fixture in FIXTURES}


def test_some_fixture_carries_the_current_schema() -> None:
    """Grandfathering every fixture as LEGACY would silently drop the round trip."""
    assert _CURRENT_FIXTURES


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda fixture: fixture.name)
def test_fixture_validates_as_run_events(fixture: FixtureContract) -> None:
    """Catches a breaking change: a renamed or dropped field, or an unknown kind."""
    for number, record in _records(fixture):
        try:
            RunEvent.model_validate(record)
        except ValidationError as error:
            # Reported outside the handler so the report is the message alone,
            # not the message chained onto the same error's traceback.
            failure = f"{fixture.name}:{number} is no longer a RunEvent:\n{error}"
        else:
            continue
        pytest.fail(failure, pytrace=False)


@pytest.mark.parametrize("fixture", _CURRENT_FIXTURES, ids=lambda fixture: fixture.name)
def test_current_fixture_round_trips_exactly(fixture: FixtureContract) -> None:
    """Catches additive drift, which validation alone accepts, and names the key."""
    for number, record in _records(fixture):
        dumped = RunEvent.model_validate(record).model_dump(mode="json")
        if dumped == record:
            continue
        drifted = sorted(
            key
            for key in dumped.keys() | record.keys()
            if dumped.get(key, _MISSING) != record.get(key, _MISSING)
        )
        pytest.fail(
            f"{fixture.name}:{number} no longer re-serializes to itself; "
            f"drifted keys: {', '.join(drifted)}. Re-record the fixture, or "
            f"declare it {SchemaAge.LEGACY.name} if the harness should keep "
            f"replaying the older shape.",
            pytrace=False,
        )


def test_mock_response_bodies_are_keyed_by_request_type() -> None:
    """A typo in a key would leave that body unreachable and unvalidated."""
    assert _response_bodies().keys() <= _request_types()


@pytest.mark.parametrize("request_type", sorted(_response_bodies()))
def test_mock_response_body_is_a_valid_response(request_type: str) -> None:
    """Holds ``mock-server.ts`` to the wire contract it hand-writes answers for.

    This validates the file the mock server reads its bodies from, so what it
    checks is what goes on the wire. It cannot check the values the server
    substitutes at runtime (run id, sequence, status, theme, backfilled events),
    because CI's pytest job has no JavaScript runtime to run the harness under;
    ``withValues`` in ``mock-server.ts`` guards those by rejecting any key this
    file does not already carry.
    """
    body = _response_bodies()[request_type]
    envelope = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": "schema-check",
        "ok": True,
        **body,
    }
    Response.model_validate(envelope)
