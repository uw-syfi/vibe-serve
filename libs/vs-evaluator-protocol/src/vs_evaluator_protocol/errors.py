"""Rejection reasons for the evaluator result protocol."""

from __future__ import annotations

from enum import StrEnum
from typing import NoReturn


class ReasonCode(StrEnum):
    """Why a reader rejected an evaluator record stream.

    The codes are part of the protocol contract and are shared by every
    conforming reader; the wording of the accompanying message is not.
    """

    MISSING_HELLO = "MISSING_HELLO"
    HELLO_NOT_FIRST = "HELLO_NOT_FIRST"
    DUPLICATE_HELLO = "DUPLICATE_HELLO"
    UNSUPPORTED_PROTOCOL = "UNSUPPORTED_PROTOCOL"
    EMPTY_METRICS = "EMPTY_METRICS"
    INVALID_METRIC_NAME = "INVALID_METRIC_NAME"
    NO_OUTCOME = "NO_OUTCOME"
    UNKNOWN_METRIC = "UNKNOWN_METRIC"
    MISSING_METRIC = "MISSING_METRIC"
    NON_NUMERIC_VALUE = "NON_NUMERIC_VALUE"
    NON_FINITE_VALUE = "NON_FINITE_VALUE"
    UNSUPPORTED_LABEL = "UNSUPPORTED_LABEL"
    UNKNOWN_KIND = "UNKNOWN_KIND"
    UNKNOWN_KEY = "UNKNOWN_KEY"
    MALFORMED_LINE = "MALFORMED_LINE"
    INVALID_RECORD = "INVALID_RECORD"


class ProtocolError(Exception):
    """One rejected evaluator record stream, carrying its reason code."""

    def __init__(self, code: ReasonCode, message: str) -> None:
        """Build a rejection with its contract *code* and a naming *message*."""
        super().__init__(message)
        self.code = code


def reject(code: ReasonCode, message: str) -> NoReturn:
    """Reject a record stream with *code* and a message naming the offender."""
    raise ProtocolError(code, message)
