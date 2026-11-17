"""Schema validation entry point.

``validate_message`` is deliberately free of Redis and PostgreSQL access so it
can be unit-tested directly and reused by any consumer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from shifting_hulls.schemas import (
    AisMessage,
    ComplianceEvent,
    InspectionResult,
    Message,
)

_MODELS: dict[str, type[AisMessage] | type[ComplianceEvent] | type[InspectionResult]] = {
    "ais": AisMessage,
    "compliance": ComplianceEvent,
    "results": InspectionResult,
}


@dataclass(frozen=True)
class Rejection:
    """A message that failed validation, kept with its original payload."""

    reason: str
    raw: dict[str, Any]


ValidationResult = Message | Rejection


def _format_error(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)


def validate_message(stream: str, raw: dict[str, Any]) -> ValidationResult:
    """Validate one raw message and return either a model or a Rejection.

    Unknown stream names and schema failures both produce a Rejection, so the
    caller has a single path for quarantine and never drops a payload.
    """
    model = _MODELS.get(stream)
    if model is None:
        return Rejection(reason=f"unknown stream {stream!r}", raw=dict(raw))
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        return Rejection(reason=_format_error(exc), raw=dict(raw))
