"""Public API of the ``vs_loop_state`` library.

``RoundRecord``, ``RoundHistory``, and the stable record codec are the deliberate
surface consumers depend on. Atomic file persistence and per-field legacy
migrations remain implementation details.
"""

from vs_loop_state.agent import (
    RoundHistory,
    RoundRecord,
    parse_round_record,
    serialize_round_record,
)

__all__ = [
    "RoundHistory",
    "RoundRecord",
    "parse_round_record",
    "serialize_round_record",
]
