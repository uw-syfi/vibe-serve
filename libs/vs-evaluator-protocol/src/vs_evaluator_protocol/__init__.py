"""Public API of the ``vs_evaluator_protocol`` library.

The public surface is the framework side of the evaluator result protocol:
strict record models, a line parser, and the reader that turns a record stream
into one validated measurement. Running evaluators, resolving output paths, and
scoring measurements belong to VibeSys.
"""

from vs_evaluator_protocol.errors import ProtocolError, ReasonCode
from vs_evaluator_protocol.measurement import (
    Measurement,
    check_objectives,
    read_measurement,
)
from vs_evaluator_protocol.records import (
    PROTOCOL_VERSION,
    ErrorRecord,
    Hello,
    MetricSpec,
    Record,
    Result,
    parse_records,
)

__all__ = [
    "PROTOCOL_VERSION",
    "ErrorRecord",
    "Hello",
    "Measurement",
    "MetricSpec",
    "ProtocolError",
    "ReasonCode",
    "Record",
    "Result",
    "check_objectives",
    "parse_records",
    "read_measurement",
]
