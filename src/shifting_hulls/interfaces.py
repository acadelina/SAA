"""Small interfaces that keep the consumer independent of Redis and PostgreSQL."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class StreamRecord:
    """One decoded entry read from a stream."""

    stream: str
    entry_id: str
    payload: dict[str, Any]


class StreamSource(Protocol):
    """A source of decoded stream entries."""

    def read(
        self, streams: Sequence[str], block_ms: int, count: int
    ) -> list[StreamRecord]:
        """Return up to count records, waiting up to block_ms for new entries."""
        ...


class MessageStore(Protocol):
    """Storage operations needed by the consumer.

    Implementations must treat message_id as the idempotency key: inserting the
    same stream and message_id twice leaves one row.
    """

    def insert_raw(
        self, stream: str, message_id: str, payload: Mapping[str, Any]
    ) -> None: ...

    def insert_clean(
        self, stream: str, message_id: str, row: Mapping[str, Any]
    ) -> None: ...

    def insert_quarantine(
        self,
        stream: str,
        message_id: str,
        payload: Mapping[str, Any],
        reason: str,
    ) -> None: ...
