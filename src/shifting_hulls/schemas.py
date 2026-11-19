"""Schemas for the three simulated data streams.

Each stream has one Pydantic model. ``validate_message`` in ``ingest`` is the
single entry point that applies these rules; the rules themselves live here.

Validation rules, in plain English:

* MMSI is exactly nine digits. JSON integers are accepted and converted to
  text because some producers emit identifiers as numbers. A leading zero is
  never recovered: an eight-digit number is rejected rather than padded.
* MMSI ranges are deliberately not restricted to vessel MIDs (200 to 799).
  Real AIS also uses service prefixes such as 970 for SAR aircraft, 98 for
  auxiliary craft and 99 for aids to navigation, and the simulator may use
  them. Only the nine-digit shape is enforced here.
* IMO is optional for AIS and compliance events, but when present it must have
  seven digits, be at least 1000000, and have a correct IMO check digit. The
  check digit rule: multiply the first six digits by 7, 6, 5, 4, 3 and 2 from
  left to right, add the results, and the last digit must equal the sum modulo
  10.
* Latitude is between -90 and 90 degrees, longitude between -180 and 180.
* Speed over ground is not negative and not above the AIS maximum of 102.2
  knots, above which the real system reports "102.3 or higher".
* Every float rejects NaN and infinity. Every integer is strict: booleans,
  floats and numeric strings are not accepted in integer fields.
* Numeric AIS fields are bounded to their real domains: course 0 to 360,
  heading 0 to 359 or the "not available" value 511, navigational status 0 to
  15, ship type 0 to 99. The inspection day is non-negative and below 100000.
* Every timestamp carries a timezone and lies between 2000-01-01 and
  2100-01-01. ``publish_time`` in a compliance event cannot be earlier than
  ``event_time``.
* Free text must be a string. NUL and other control characters are rejected
  so PostgreSQL cannot be poisoned with values it refuses. Note may contain
  tabs, carriage returns and newlines; names and identifiers may not.
* Zero-width and bidirectional override characters are stripped from every
  text field. The clean row is what the agent reads, so invisible characters
  that could hide or reorder text have no reason to survive. The full original
  text is still kept in the raw table.
* ``name`` and ``note`` are truncated to their maximum length. They are never
  parsed or executed: a ship name that looks like an instruction stays a
  string. Truncation only affects the clean row.
* A compliance event must carry at least one identifier (IMO, name or MMSI).
* Unknown fields are rejected, so a change in the producer is visible as a
  quarantine row instead of being silently ignored.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationInfo,
    field_validator,
    model_validator,
)

STREAMS: tuple[str, ...] = ("ais", "compliance", "results")

MAX_NAME_LENGTH = 120
MAX_NOTE_LENGTH = 500
MAX_SOG_KNOTS = 102.2
MAX_DAY = 100_000

MIN_TIMESTAMP = datetime(2000, 1, 1, tzinfo=UTC)
MAX_TIMESTAMP = datetime(2100, 1, 1, tzinfo=UTC)

MIN_IMO = 1_000_000

_MMSI_RE = re.compile(r"[0-9]{9}")
_IMO_RE = re.compile(r"[0-9]{7}")
_IMO_WEIGHTS = (7, 6, 5, 4, 3, 2)

# Format characters that have no visible width or reorder the text around
# them. They are removed rather than rejected because names may legitimately
# arrive from producers that use them as separators.
_INVISIBLE_CHARACTERS = frozenset(
    "​‌‍⁠﻿"
    + "".join(chr(code) for code in range(0x202A, 0x202F))
    + "".join(chr(code) for code in range(0x2066, 0x206A))
)

# Note is free prose and may be multiline. Nothing else may contain these.
_NOTE_WHITESPACE = frozenset("\n\r\t")

_REJECTED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


def is_valid_imo(value: str) -> bool:
    """Return True when value is a valid IMO number with a correct check digit."""
    if _IMO_RE.fullmatch(value) is None:
        return False
    if int(value) < MIN_IMO:
        return False
    digits = [int(character) for character in value]
    total = sum(
        digit * weight
        for digit, weight in zip(digits[:6], _IMO_WEIGHTS, strict=True)
    )
    return total % 10 == digits[6]


def _to_text(value: Any) -> Any:
    """Convert a JSON integer to text, but leave every other type alone.

    Booleans are subclasses of int in Python, so they are excluded explicitly.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return value


def _check_text(
    value: Any,
    field_name: str,
    *,
    max_length: int,
    allow_line_breaks: bool = False,
) -> str:
    """Clean one free-text field.

    Zero-width and bidi characters are stripped first. Any remaining control,
    format, surrogate or line/paragraph separator is rejected. The result is
    truncated to max_length, which matches the documented behaviour for names
    and notes.
    """
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    cleaned = "".join(
        character
        for character in value
        if character not in _INVISIBLE_CHARACTERS
    )
    for character in cleaned:
        if allow_line_breaks and character in _NOTE_WHITESPACE:
            continue
        if unicodedata.category(character) in _REJECTED_CATEGORIES:
            raise ValueError(f"{field_name} must not contain control characters")
    return cleaned[:max_length]


def _optional_text(
    value: Any,
    field_name: str,
    *,
    max_length: int,
    allow_line_breaks: bool = False,
) -> str | None:
    if value is None:
        return None
    return _check_text(
        value,
        field_name,
        max_length=max_length,
        allow_line_breaks=allow_line_breaks,
    )


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
    if int(text) < MIN_IMO:
        raise ValueError("imo must be at least 1000000")
    if not is_valid_imo(text):
        raise ValueError("imo has an invalid check digit")
    return text


def _require_timestamp_type(value: Any, field_name: str) -> Any:
    """Reject numbers and booleans before Pydantic tries to parse them."""
    if isinstance(value, datetime) or isinstance(value, str):
        return value
    raise ValueError(f"{field_name} must be an ISO 8601 string")


def _check_timestamp(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone offset")
    if value < MIN_TIMESTAMP or value > MAX_TIMESTAMP:
        raise ValueError(
            f"{field_name} must be between 2000-01-01 and 2100-01-01"
        )
    return value


def _require_number(value: Any, field_name: str) -> Any:
    """Accept JSON numbers only. Strings and booleans are not numbers here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a JSON number")
    return value


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
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    sog: float = Field(ge=0, le=MAX_SOG_KNOTS, allow_inf_nan=False)
    cog: float = Field(ge=0, le=360, allow_inf_nan=False)
    heading: StrictInt = Field(ge=0, le=511)
    nav_status: StrictInt = Field(ge=0, le=15)
    draft: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    ship_type: StrictInt = Field(ge=0, le=99)

    @field_validator("ts", mode="before")
    @classmethod
    def _check_ts_type(cls, value: Any) -> Any:
        return _require_timestamp_type(value, "ts")

    @field_validator("ts")
    @classmethod
    def _check_ts(cls, value: datetime) -> datetime:
        return _check_timestamp(value, "ts")

    @field_validator("mmsi", mode="before")
    @classmethod
    def _check_mmsi(cls, value: Any) -> str:
        return _clean_mmsi(value)

    @field_validator("imo", mode="before")
    @classmethod
    def _check_imo(cls, value: Any) -> str | None:
        return _clean_optional_imo(value)

    @field_validator("lat", "lon", "sog", "cog", "draft", mode="before")
    @classmethod
    def _check_number(cls, value: Any, info: ValidationInfo) -> Any:
        if value is None:
            return None
        # Draft arrives blank in some real feeds; keep the old convenience.
        if info.field_name == "draft" and value == "":
            return None
        return _require_number(value, info.field_name or "value")

    @field_validator("heading")
    @classmethod
    def _check_heading(cls, value: int) -> int:
        if value == 511 or 0 <= value <= 359:
            return value
        raise ValueError("heading must be 0 to 359 or 511")

    @field_validator("name", mode="before")
    @classmethod
    def _clean_name(cls, value: Any) -> str:
        return _check_text(value, "name", max_length=MAX_NAME_LENGTH)

    @field_validator("flag", mode="before")
    @classmethod
    def _clean_flag(cls, value: Any) -> str:
        return _check_text(value, "flag", max_length=10)

    @field_validator("call_sign", mode="before")
    @classmethod
    def _clean_call_sign(cls, value: Any) -> str:
        return _check_text(value, "call_sign", max_length=20)


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

    @field_validator("event_time", "publish_time", mode="before")
    @classmethod
    def _check_time_type(cls, value: Any) -> Any:
        return _require_timestamp_type(value, "timestamp")

    @field_validator("event_time")
    @classmethod
    def _check_event_time(cls, value: datetime) -> datetime:
        return _check_timestamp(value, "event_time")

    @field_validator("publish_time")
    @classmethod
    def _check_publish_time(cls, value: datetime) -> datetime:
        return _check_timestamp(value, "publish_time")

    @field_validator("event_id", mode="before")
    @classmethod
    def _clean_event_id(cls, value: Any) -> str:
        return _check_text(value, "event_id", max_length=64)

    @field_validator("imo", mode="before")
    @classmethod
    def _check_imo(cls, value: Any) -> str | None:
        return _clean_optional_imo(value)

    @field_validator("mmsi", mode="before")
    @classmethod
    def _check_mmsi(cls, value: Any) -> str | None:
        return _clean_optional_mmsi(value)

    @field_validator("name", mode="before")
    @classmethod
    def _clean_name(cls, value: Any) -> str | None:
        return _optional_text(value, "name", max_length=MAX_NAME_LENGTH)

    @field_validator("note", mode="before")
    @classmethod
    def _clean_note(cls, value: Any) -> str | None:
        return _optional_text(
            value,
            "note",
            max_length=MAX_NOTE_LENGTH,
            allow_line_breaks=True,
        )

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

    day: StrictInt = Field(ge=0, le=MAX_DAY)
    plan_id: str = Field(min_length=1, max_length=64)
    imo: str
    mmsi: str
    outcome: InspectionOutcome
    reported_at: datetime

    @field_validator("reported_at", mode="before")
    @classmethod
    def _check_reported_at_type(cls, value: Any) -> Any:
        return _require_timestamp_type(value, "reported_at")

    @field_validator("reported_at")
    @classmethod
    def _check_reported_at(cls, value: datetime) -> datetime:
        return _check_timestamp(value, "reported_at")

    @field_validator("plan_id", mode="before")
    @classmethod
    def _clean_plan_id(cls, value: Any) -> str:
        return _check_text(value, "plan_id", max_length=64)

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


def _json_default(value: Any) -> str:
    """Serialise datetimes and enums so canonical JSON is stable across runs."""
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, StrEnum):
        return value.value
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def message_id(stream: str, message: Message) -> str:
    """Build the deterministic clean-row key for one validated message.

    The key is derived only from validated content, so replaying the same
    message produces the same key and ``ON CONFLICT`` can deduplicate it.
    Timestamps are normalised to UTC before hashing, so two spellings of the
    same instant (``Z`` and ``+02:00``) produce one key. AIS has no identifier
    in the stream, so its key is a SHA-256 of the normalised model. Compliance
    events use their event id and inspection results use the day, plan and
    hull, which are already natural keys. Raw storage is keyed separately by
    the Redis entry id, so distinct delivered payloads are never collapsed.
    """
    if stream == "ais" and isinstance(message, AisMessage):
        digest = hashlib.sha256(
            _canonical_json(message.model_dump(mode="python")).encode()
        )
        return f"ais:{digest.hexdigest()}"
    if stream == "compliance" and isinstance(message, ComplianceEvent):
        return f"compliance:{message.event_id}"
    if stream == "results" and isinstance(message, InspectionResult):
        return f"results:{message.day}:{message.plan_id}:{message.imo}:{message.mmsi}"
    raise ValueError(f"message type does not match stream {stream!r}")


def payload_digest(payload: dict[str, Any]) -> str:
    """Stable SHA-256 of an arbitrary payload, used for raw audit and quarantine."""
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()
