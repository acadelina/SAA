"""Small interfaces that keep the consumer independent of Redis and PostgreSQL."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class InsertOutcome(StrEnum):
    """What happened when a row was inserted under an idempotency key."""

    inserted = "inserted"
    duplicate = "duplicate"
    conflict = "conflict"


@dataclass(frozen=True)
class StreamRecord:
    """One decoded entry read from a stream.

    ``parse_error`` is set when the envelope itself is invalid. In that case
    ``payload`` holds the original entry fields so quarantine can preserve
    them.
    """

    stream: str
    entry_id: str
    payload: dict[str, Any]
    parse_error: str | None = None


@dataclass(frozen=True)
class PendingRecord:
    """A pending entry and how long it has been waiting, when known."""

    record: StreamRecord
    idle_ms: int


class StreamSource(Protocol):
    """A source of decoded stream entries that supports acknowledgement.

    ``read`` returns new entries. ``read_pending`` returns entries already
    delivered to this consumer but not acknowledged. ``claim_stale`` takes over
    entries that other consumers left pending for at least ``min_idle_ms``.
    """

    def read(
        self, streams: Sequence[str], block_ms: int, count: int
    ) -> list[StreamRecord]:
        """Return up to count new records, waiting up to block_ms."""
        ...

    def read_pending(
        self, streams: Sequence[str], count: int
    ) -> list[PendingRecord]:
        """Return up to count pending records owned by this consumer."""
        ...

    def claim_stale(
        self, streams: Sequence[str], min_idle_ms: int, count: int
    ) -> list[PendingRecord]:
        """Take over pending records idle for at least min_idle_ms."""
        ...

    def ack(self, stream: str, entry_id: str) -> None:
        """Acknowledge one entry. Call only after the database commit."""
        ...

    def close(self) -> None:
        """Release the underlying connection."""
        ...


class MessageStore(Protocol):
    """Storage operations needed by the consumer.

    Every insert is idempotent. ``insert_raw`` is keyed on (stream, entry_id),
    so a redelivered entry leaves one raw row. ``insert_clean`` is keyed on the
    deterministic clean key and reports whether an existing row is identical
    or different. ``transaction`` must make the inserts for one message commit
    or roll back together.
    """

    def transaction(self) -> AbstractContextManager[None]: ...

    def insert_raw(
        self,
        stream: str,
        entry_id: str,
        payload_sha256: str,
        payload: Mapping[str, Any],
    ) -> InsertOutcome: ...

    def insert_clean(
        self, stream: str, message_id: str, row: Mapping[str, Any]
    ) -> InsertOutcome: ...

    def insert_quarantine(
        self,
        stream: str,
        message_id: str,
        payload: Mapping[str, Any],
        reason: str,
    ) -> None: ...

    def close(self) -> None: ...
