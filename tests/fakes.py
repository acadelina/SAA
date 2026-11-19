"""Test doubles for the consumer's Redis and storage interfaces."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from shifting_hulls.interfaces import (
    InsertOutcome,
    PendingRecord,
    StreamRecord,
)


@dataclass
class FakeStreamSource:
    """Return prepared batches, one per call, then empty batches.

    Pending batches simulate entries this consumer read but did not ack.
    Stale batches simulate entries claimed from another consumer.
    """

    batches: list[list[StreamRecord]]
    pending_batches: list[list[PendingRecord]] = field(default_factory=list)
    stale_batches: list[list[PendingRecord]] = field(default_factory=list)
    acks: list[tuple[str, str]] = field(default_factory=list)
    calls: int = field(default=0, init=False)
    pending_calls: int = field(default=0, init=False)
    claim_calls: int = field(default=0, init=False)

    def read(
        self, streams: Sequence[str], block_ms: int, count: int
    ) -> list[StreamRecord]:
        if self.calls >= len(self.batches):
            return []
        batch = self.batches[self.calls]
        self.calls += 1
        return list(batch)

    def read_pending(
        self, streams: Sequence[str], count: int
    ) -> list[PendingRecord]:
        if self.pending_calls >= len(self.pending_batches):
            return []
        batch = self.pending_batches[self.pending_calls]
        self.pending_calls += 1
        return list(batch)

    def claim_stale(
        self, streams: Sequence[str], min_idle_ms: int, count: int
    ) -> list[PendingRecord]:
        if self.claim_calls >= len(self.stale_batches):
            return []
        batch = self.stale_batches[self.claim_calls]
        self.claim_calls += 1
        return list(batch)

    def ack(self, stream: str, entry_id: str) -> None:
        self.acks.append((stream, entry_id))

    def close(self) -> None:
        pass


@dataclass
class FakeStore:
    """In-memory store with the same contract as PostgreSQL.

    Raw rows are keyed on (stream, entry_id) and clean rows on (stream, clean
    key). A transaction snapshots the tables and restores them if the block
    raises, so atomicity can be tested without a database.
    """

    raw: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    raw_hashes: dict[tuple[str, str], str] = field(default_factory=dict)
    clean: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    quarantine: dict[tuple[str, str], tuple[dict[str, Any], str]] = field(
        default_factory=dict
    )
    transactions: int = field(default=0, init=False)
    fail_clean_next: bool = field(default=False, init=False)
    fail_quarantine_next: bool = field(default=False, init=False)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        snapshot = (
            dict(self.raw),
            dict(self.raw_hashes),
            dict(self.clean),
            dict(self.quarantine),
        )
        try:
            yield
        except Exception:
            self.raw, self.raw_hashes, self.clean, self.quarantine = snapshot
            raise
        self.transactions += 1

    def insert_raw(
        self,
        stream: str,
        entry_id: str,
        payload_sha256: str,
        payload: Mapping[str, Any],
    ) -> InsertOutcome:
        key = (stream, entry_id)
        if key in self.raw:
            if self.raw_hashes[key] == payload_sha256:
                return InsertOutcome.duplicate
            return InsertOutcome.conflict
        self.raw[key] = dict(payload)
        self.raw_hashes[key] = payload_sha256
        return InsertOutcome.inserted

    def insert_clean(
        self, stream: str, message_id: str, row: Mapping[str, Any]
    ) -> InsertOutcome:
        if self.fail_clean_next:
            self.fail_clean_next = False
            raise RuntimeError("simulated clean insert failure")
        key = (stream, message_id)
        if key not in self.clean:
            self.clean[key] = dict(row)
            return InsertOutcome.inserted
        if self.clean[key] == dict(row):
            return InsertOutcome.duplicate
        return InsertOutcome.conflict

    def insert_quarantine(
        self,
        stream: str,
        message_id: str,
        payload: Mapping[str, Any],
        reason: str,
    ) -> None:
        if self.fail_quarantine_next:
            self.fail_quarantine_next = False
            raise RuntimeError("simulated quarantine failure")
        self.quarantine.setdefault((stream, message_id), (dict(payload), reason))

    def close(self) -> None:
        pass
