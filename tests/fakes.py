"""Test doubles for the consumer's Redis and storage interfaces."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from shifting_hulls.interfaces import StreamRecord


@dataclass
class FakeStreamSource:
    """Return prepared batches, one per read call, then empty batches."""

    batches: list[list[StreamRecord]]
    calls: int = field(default=0, init=False)

    def read(
        self, streams: Sequence[str], block_ms: int, count: int
    ) -> list[StreamRecord]:
        if self.calls >= len(self.batches):
            return []
        batch = self.batches[self.calls]
        self.calls += 1
        return list(batch)


@dataclass
class FakeStore:
    """In-memory store with the same idempotency contract as PostgreSQL.

    Each table is keyed on (stream, message_id), mirroring the primary keys and
    ON CONFLICT DO NOTHING clauses in sql/001_init.sql.
    """

    raw: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    clean: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    quarantine: dict[tuple[str, str], tuple[dict[str, Any], str]] = field(
        default_factory=dict
    )

    def insert_raw(
        self, stream: str, message_id: str, payload: Mapping[str, Any]
    ) -> None:
        self.raw.setdefault((stream, message_id), dict(payload))

    def insert_clean(
        self, stream: str, message_id: str, row: Mapping[str, Any]
    ) -> None:
        self.clean.setdefault((stream, message_id), dict(row))

    def insert_quarantine(
        self,
        stream: str,
        message_id: str,
        payload: Mapping[str, Any],
        reason: str,
    ) -> None:
        self.quarantine.setdefault((stream, message_id), (dict(payload), reason))
