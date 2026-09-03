"""Exhaustive rules of the run lifecycle state machine."""

import pytest

from server.run_lifecycle import (
    IllegalRunTransitionError,
    RunStatus,
    RunTrigger,
    transition,
)

LEGAL: dict[tuple[RunStatus, RunTrigger], RunStatus] = {
    (RunStatus.STARTING, RunTrigger.ATTACHED): RunStatus.RUNNING,
    (RunStatus.STARTING, RunTrigger.COMPLETED): RunStatus.COMPLETED,
    (RunStatus.STARTING, RunTrigger.FAILED): RunStatus.FAILED,
    (RunStatus.RUNNING, RunTrigger.ATTACHED): RunStatus.RUNNING,
    (RunStatus.RUNNING, RunTrigger.PAUSE_REQUESTED): RunStatus.PAUSING,
    (RunStatus.RUNNING, RunTrigger.INVOCATION_FINISHED): RunStatus.RUNNING,
    (RunStatus.RUNNING, RunTrigger.RESUMED): RunStatus.RUNNING,
    (RunStatus.RUNNING, RunTrigger.COMPLETED): RunStatus.COMPLETED,
    (RunStatus.RUNNING, RunTrigger.FAILED): RunStatus.FAILED,
    (RunStatus.PAUSING, RunTrigger.ATTACHED): RunStatus.PAUSING,
    (RunStatus.PAUSING, RunTrigger.PAUSE_REQUESTED): RunStatus.PAUSING,
    (RunStatus.PAUSING, RunTrigger.INVOCATION_FINISHED): RunStatus.PAUSED,
    (RunStatus.PAUSING, RunTrigger.RESUMED): RunStatus.RUNNING,
    (RunStatus.PAUSING, RunTrigger.COMPLETED): RunStatus.COMPLETED,
    (RunStatus.PAUSING, RunTrigger.FAILED): RunStatus.FAILED,
    (RunStatus.PAUSED, RunTrigger.ATTACHED): RunStatus.PAUSED,
    (RunStatus.PAUSED, RunTrigger.PAUSE_REQUESTED): RunStatus.PAUSED,
    (RunStatus.PAUSED, RunTrigger.INVOCATION_FINISHED): RunStatus.PAUSED,
    (RunStatus.PAUSED, RunTrigger.RESUMED): RunStatus.RUNNING,
    (RunStatus.PAUSED, RunTrigger.COMPLETED): RunStatus.COMPLETED,
    (RunStatus.PAUSED, RunTrigger.FAILED): RunStatus.FAILED,
}
"""The whole table, restated independently of the module under test."""

ENDED = (RunStatus.COMPLETED, RunStatus.FAILED)
LIVE = (RunStatus.STARTING, RunStatus.RUNNING, RunStatus.PAUSING, RunStatus.PAUSED)

ILLEGAL = [
    (current, trigger)
    for current in LIVE
    for trigger in RunTrigger
    if (current, trigger) not in LEGAL
]


def test_every_status_is_either_ended_or_live() -> None:
    """``has_ended`` partitions the enum, so a new member has to be placed."""
    assert set(ENDED) | set(LIVE) == set(RunStatus)
    assert not set(ENDED) & set(LIVE)
    assert [status for status in RunStatus if status.has_ended] == list(ENDED)
    assert [status for status in RunStatus if not status.has_ended] == list(LIVE)


@pytest.mark.parametrize(("pair", "expected"), LEGAL.items(), ids=str)
def test_legal_transition(pair: tuple[RunStatus, RunTrigger], expected: RunStatus) -> None:
    """Every legal move produces the status the table names."""
    current, trigger = pair
    assert transition(current, trigger) is expected


@pytest.mark.parametrize(("current", "trigger"), ILLEGAL, ids=str)
def test_illegal_transition_raises(current: RunStatus, trigger: RunTrigger) -> None:
    """Every move outside the table is rejected with the offending pair."""
    with pytest.raises(IllegalRunTransitionError) as raised:
        transition(current, trigger)
    assert raised.value.current is current
    assert raised.value.trigger is trigger


def test_illegal_pairs_are_the_ones_we_expect() -> None:
    """Nothing pause-related can happen before the run is attached.

    Pinned so widening the table stays a deliberate edit.
    """
    assert set(ILLEGAL) == {
        (RunStatus.STARTING, RunTrigger.PAUSE_REQUESTED),
        (RunStatus.STARTING, RunTrigger.INVOCATION_FINISHED),
        (RunStatus.STARTING, RunTrigger.RESUMED),
    }


@pytest.mark.parametrize("ended", ENDED, ids=str)
@pytest.mark.parametrize("trigger", list(RunTrigger), ids=str)
def test_ended_statuses_absorb_every_trigger(ended: RunStatus, trigger: RunTrigger) -> None:
    """A run that has ended never leaves the status it ended in."""
    assert transition(ended, trigger) is ended


def test_pause_reaches_its_boundary_then_resumes() -> None:
    """The canonical pause round trip, as the controller drives it."""
    status = transition(RunStatus.STARTING, RunTrigger.ATTACHED)
    assert status is RunStatus.RUNNING
    status = transition(status, RunTrigger.PAUSE_REQUESTED)
    assert status is RunStatus.PAUSING
    status = transition(status, RunTrigger.INVOCATION_FINISHED)
    assert status is RunStatus.PAUSED
    status = transition(status, RunTrigger.RESUMED)
    assert status is RunStatus.RUNNING


def test_resume_before_the_boundary_cancels_the_pending_pause() -> None:
    """A ``/resume`` that beats the boundary leaves nothing pending."""
    status = transition(RunStatus.RUNNING, RunTrigger.PAUSE_REQUESTED)
    status = transition(status, RunTrigger.RESUMED)
    assert status is RunStatus.RUNNING
    assert transition(status, RunTrigger.INVOCATION_FINISHED) is RunStatus.RUNNING
