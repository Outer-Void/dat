"""Execution failures. These are never findings: a finding is something DAT observed about the target,
an error is DAT failing to observe. Exit codes keep the two apart (docs/EXIT_CODES.md)."""

from __future__ import annotations

import enum
from typing import Any

EXIT_OK = 0  # audit completed; gate passed or not requested
EXIT_GATE_FAILED = 1  # audit completed; blocking findings
EXIT_USAGE = 2  # bad command line
EXIT_FAILURE = 3  # DAT could not complete the audit (target, policy, analyzer or internal failure)


class ErrorCode(enum.StrEnum):
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    TARGET_UNREADABLE = "TARGET_UNREADABLE"
    INVALID_TARGET = "INVALID_TARGET"
    INVALID_POLICY = "INVALID_POLICY"
    POLICY_NOT_FOUND = "POLICY_NOT_FOUND"
    POLICY_UNREADABLE = "POLICY_UNREADABLE"
    INVALID_REPORT = "INVALID_REPORT"
    INVALID_INPUT = "INVALID_INPUT"
    ANALYZER_FAILED = "ANALYZER_FAILED"
    ANALYZER_CONTRACT_VIOLATION = "ANALYZER_CONTRACT_VIOLATION"
    PLUGIN_LOAD_FAILED = "PLUGIN_LOAD_FAILED"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    OUTPUT_FAILED = "OUTPUT_FAILED"
    SIGNING_FAILED = "SIGNING_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class DatError(Exception):
    def __init__(self, code: ErrorCode, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message, "details": self.details}
