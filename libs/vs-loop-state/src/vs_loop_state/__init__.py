"""Public API of the ``vs_loop_state`` library.

The public surface contains strict persisted-state records and pure codecs for
all three loops. Project layout and filesystem persistence belong to
``vs-project``.
"""

from vs_loop_state.agent import (
    RoundHistory,
    RoundRecord,
    parse_round_record,
    serialize_round_record,
)
from vs_loop_state.evolve import (
    IndividualRecord,
    PopulationSnapshot,
    parse_population_snapshot,
    serialize_population_snapshot,
)
from vs_loop_state.plain import (
    PlainLoopCursor,
    PlainPerformanceRecord,
    PlainPerformanceSnapshot,
    parse_plain_loop_cursor,
    parse_plain_performance_snapshot,
    serialize_plain_loop_cursor,
    serialize_plain_performance_snapshot,
)

__all__ = [
    "IndividualRecord",
    "PlainLoopCursor",
    "PlainPerformanceRecord",
    "PlainPerformanceSnapshot",
    "PopulationSnapshot",
    "RoundHistory",
    "RoundRecord",
    "parse_plain_loop_cursor",
    "parse_plain_performance_snapshot",
    "parse_population_snapshot",
    "parse_round_record",
    "serialize_plain_loop_cursor",
    "serialize_plain_performance_snapshot",
    "serialize_population_snapshot",
    "serialize_round_record",
]
