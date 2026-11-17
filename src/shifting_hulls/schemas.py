"""Schemas for the three simulated data streams.

Each stream has one Pydantic model. ``validate_message`` in ``ingest`` is the
single entry point that applies these rules; the rules themselves live here.

Validation rules, in plain English:

* MMSI is exactly nine digits. Integers from JSON are accepted and converted
  to text, because a leading zero would otherwise be lost.
* IMO is optional for AIS and compliance events, but when present it must have
  seven digits and a correct IMO check digit. The check digit rule: multiply
  the first six digits by 7, 6, 5, 4, 3 and 2 from left to right, add the
  results, and the last digit must equal the sum modulo 10.
* Latitude is between -90 and 90 degrees, longitude between -180 and 180.
* Speed over ground is not negative and not above the AIS maximum of 102.2
  knots, above which the real system reports "102.3 or higher".
* Every timestamp carries a timezone. ``publish_time`` in a compliance event
  cannot be earlier than ``event_time``.
* ``name`` and ``note`` are free text and are truncated to their maximum
  length. They are never parsed or executed: a ship name that looks like an
  instruction stays a string. The full original text is still kept in the raw
  table, so truncation only affects the clean row.
* A compliance event must carry at least one identifier (IMO, name or MMSI).
* Unknown fields are rejected, so a change in the producer is visible as a
  quarantine row instead of being silently ignored.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

STREAMS: tuple[str, ...] = ("ais", "compliance", "results")

MAX_NAME_LENGTH = 120
MAX_NOTE_LENGTH = 500
MAX_SOG_KNOTS = 102.2

_MMSI_RE = re.compile(r"[0-9]{9}")
_IMO_RE = re.compile(r"[0-9]{7}")
_IMO_WEIGHTS = (7, 6, 5, 4, 3, 2)


def is_valid_imo(value: str) -> bool:
    """Return True when value is seven digits with a correct IMO check digit."""
    if _IMO_RE.fullmatch(value) is None:
        return False
    digits = [int(character) for character in value]
    total = sum(digit * weight for digit, weight in zip(digits[:6], _IMO_WEIGHTS, strict=True))
    return total % 10 == digits[6]


def _to_text(value: Any) -> Any:
    """Convert integers from JSON to text, but leave every other type alone."""
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return value


def _clean_mmsi(value: Any) -> str:
    text = _to_text(value)
    if not isinstance(text, str):
        raise ValueError("mmsi must be a 9-digit string")
    text = text.strip()
    if _MMSI_RE.fullmatch(text) is None:
        raise ValueError("mmsi must contain exactly 9 digits")
    return text


def _clean_optional_mmsi(value: Any) -> str | None:
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    return _clean_mmsi(value)


def _clean_optional_imo(value: Any) -> str | None:
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    text = _to_text(value)
    if not isinstance(text, str):
        raise ValueError("imo must be a 7-digit string")
    text = text.strip()
    if _IMO_RE.fullmatch(text) is None:
        raise ValueError("imo must contain exactly 7 digits")
    if not is_valid_imo(text):
        raise ValueError("imo has an invalid check digit")
    return text


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone offset")
    return value


def _truncate(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value[:limit]


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AisMessage(_Message):
    """One AIS-like position report from the fast stream."""

    ts: datetime
    mmsi: str
    imo: str | None = None
    name: str
    flag: str = Field(min_length=1, max_length=10)
    call_sign: str = Field(max_length=20)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    sog: float = Field(ge=0, le=MAX_SOG_KNOTS)
    cog: float
    heading: int
    nav_status: int
    draft: float | None = Field(default=None, ge=0)
    ship_type: int

    @field_validator("ts")
    @classmethod
    def _check_ts(cls, value: datetime) -> datetime:
        return _require_aware(value, "ts")

    @field_validator("mmsi", mode="before")
    @classmethod
    def _check_mmsi(cls, value: Any) -> str:
        return _clean_mmsi(value)

    @field_validator("imo", mode="before")
    @classmethod
    def _check_imo(cls, value: Any) -> str | None:
        return _clean_optional_imo(value)

    @field_validator("draft", mode="before")
    @classmethod
    def _blank_draft(cls, value: Any) -> Any:
        return None if value == "" else value

    @field_validator("name")
    @classmethod
    def _limit_name(cls, value: str) -> str:
        return value[:MAX_NAME_LENGTH]


class ComplianceEventType(StrEnum):
    """Kinds of event the compliance stream can report."""

    listing = "listing"
    delisting = "delisting"
    detention = "detention"
    registry_change = "registry_change"


class ComplianceEvent(_Message):
    """One slow, sometimes delayed compliance event."""

    event_id: str = Field(min_length=1, max_length=64)
    event_type: ComplianceEventType
    imo: str | None = None
    name: str | None = None
    mmsi: str | None = None
    event_time: datetime
    publish_time: datetime
    note: str | None = None

    @field_validator("event_time")
    @classmethod
    def _check_event_time(cls, value: datetime) -> datetime:
        return _require_aware(value, "event_time")

    @field_validator("publish_time")
    @classmethod
    def _check_publish_time(cls, value: datetime) -> datetime:
        return _require_aware(value, "publish_time")

    @field_validator("imo", mode="before")
    @classmethod
    def _check_imo(cls, value: Any) -> str | None:
        return _clean_optional_imo(value)

    @field_validator("mmsi", mode="before")
    @classmethod
    def _check_mmsi(cls, value: Any) -> str | None:
        return _clean_optional_mmsi(value)

    @field_validator("name")
    @classmethod
    def _limit_name(cls, value: str | None) -> str | None:
        return _truncate(value, MAX_NAME_LENGTH)

    @field_validator("note")
    @classmethod
    def _limit_note(cls, value: str | None) -> str | None:
        return _truncate(value, MAX_NOTE_LENGTH)

    @model_validator(mode="after")
    def _check_event(self) -> ComplianceEvent:
        if self.publish_time < self.event_time:
            raise ValueError("publish_time must not be before event_time")
        if not any((self.imo, self.name, self.mmsi)):
            raise ValueError("at least one of imo, name or mmsi is required")
        return self


class InspectionOutcome(StrEnum):
    """Ground truth returned by the simulator for one inspection."""

    shadow = "shadow"
    clean = "clean"


class InspectionResult(_Message):
    """One inspection result from the feedback stream."""

    day: int = Field(ge=0)
    plan_id: str = Field(min_length=1, max_length=64)
    imo: str
    mmsi: str
    outcome: InspectionOutcome
    reported_at: datetime

    @field_validator("reported_at")
    @classmethod
    def _check_reported_at(cls, value: datetime) -> datetime:
        return _require_aware(value, "reported_at")

    @field_validator("imo", mode="before")
    @classmethod
    def _check_imo(cls, value: Any) -> str:
        cleaned = _clean_optional_imo(value)
        if cleaned is None:
            raise ValueError("imo is required")
        return cleaned

    @field_validator("mmsi", mode="before")
    @classmethod
    def _check_mmsi(cls, value: Any) -> str:
        return _clean_mmsi(value)


Message = AisMessage | ComplianceEvent | InspectionResult


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def message_id(stream: str, message: Message) -> str:
    """Build the stable key used for idempotent raw and clean inserts.

    Compliance events already carry an event id. Inspection results are unique
    per day, plan and hull. AIS messages have no id in the stream, so a hash of
    the validated payload is used; replaying the same message yields the same
    hash and therefore the same row.
    """
    if stream == "ais" and isinstance(message, AisMessage):
        digest = hashlib.sha256(_canonical_json(message.model_dump(mode="json")).encode())
        return f"ais:{digest.hexdigest()}"
    if stream == "compliance" and isinstance(message, ComplianceEvent):
        return f"compliance:{message.event_id}"
    if stream == "results" and isinstance(message, InspectionResult):
        return f"results:{message.day}:{message.plan_id}:{message.imo}:{message.mmsi}"
    raise ValueError(f"message type does not match stream {stream!r}")


def payload_digest(payload: dict[str, Any]) -> str:
    """Stable hash of an arbitrary payload, used to deduplicate quarantine rows."""
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()
