"""Redis Streams consumer that validates messages and writes them to PostgreSQL.

The producer contract is one entry per stream message. The entry may hold a
single ``data`` field with a JSON object, or the message fields directly. In
both cases a malformed payload ends up in quarantine instead of stopping the
loop.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from typing import Any

import redis

from shifting_hulls.ingest import Rejection, ValidationResult, validate_message
from shifting_hulls.interfaces import MessageStore, StreamRecord, StreamSource
from shifting_hulls.schemas import STREAMS, message_id, payload_digest
from shifting_hulls.stores import PostgresStore

DEFAULT_BLOCK_MS = 5000
DEFAULT_COUNT = 100


def parse_fields(fields: Mapping[str, str]) -> dict[str, Any]:
    """Turn Redis entry fields into a payload dictionary.

    Values in a flat entry are JSON-decoded when possible, so ``"11.2"`` becomes
    a number while a plain name such as ``"FIANIT"`` stays text.
    """
    if "data" in fields:
        try:
            parsed = json.loads(fields["data"])
        except (TypeError, json.JSONDecodeError):
            return dict(fields)
        if isinstance(parsed, dict):
            return parsed
        return {"data": parsed}

    payload: dict[str, Any] = {}
    for key, value in fields.items():
        try:
            payload[key] = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            payload[key] = value
    return payload


def process_message(
    stream: str,
    entry_id: str,
    payload: dict[str, Any],
    store: MessageStore,
) -> ValidationResult:
    """Validate one message and route it to clean, raw or quarantine storage.

    entry_id is accepted for logging and tracing; idempotency is keyed on the
    message content so a replay of the same entry maps to the same rows.
    """
    result = validate_message(stream, payload)
    if isinstance(result, Rejection):
        store.insert_quarantine(
            stream, f"q:{payload_digest(payload)}", payload, result.reason
        )
        return result

    key = message_id(stream, result)
    store.insert_raw(stream, key, payload)
    store.insert_clean(stream, key, result.model_dump(mode="python"))
    return result


def run_consumer(
    source: StreamSource,
    store: MessageStore,
    streams: Sequence[str] = STREAMS,
    block_ms: int = DEFAULT_BLOCK_MS,
    count: int = DEFAULT_COUNT,
    max_batches: int | None = None,
) -> int:
    """Read and process records until stopped or until max_batches is reached."""
    processed = 0
    batches = 0
    while max_batches is None or batches < max_batches:
        records = source.read(streams, block_ms=block_ms, count=count)
        batches += 1
        for record in records:
            process_message(record.stream, record.entry_id, record.payload, store)
            processed += 1
    return processed


class RedisStreamSource:
    """Read all named streams through one Redis consumer group."""

    def __init__(self, url: str, group: str = "ingest", consumer: str = "ingest-1") -> None:
        self._client: redis.Redis = redis.Redis.from_url(url, decode_responses=True)
        self._group = group
        self._consumer = consumer
        self._ready: set[str] = set()

    def _ensure_groups(self, streams: Sequence[str]) -> None:
        for stream in streams:
            if stream in self._ready:
                continue
            try:
                self._client.xgroup_create(stream, self._group, id="0", mkstream=True)
            except redis.ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise
            self._ready.add(stream)

    def _read_group(
        self, streams: Sequence[str], block_ms: int, count: int
    ) -> list[Any]:
        response = self._client.xreadgroup(
            self._group,
            self._consumer,
            {stream: ">" for stream in streams},
            count=count,
            block=block_ms,
        )
        return response or []

    def read(
        self, streams: Sequence[str], block_ms: int, count: int
    ) -> list[StreamRecord]:
        self._ensure_groups(streams)
        try:
            response = self._read_group(streams, block_ms, count)
        except redis.ResponseError as exc:
            # A group can disappear if Redis is flushed. Recreate it once and
            # retry rather than crashing the consumer.
            if "NOGROUP" not in str(exc):
                raise
            self._ready.clear()
            self._ensure_groups(streams)
            response = self._read_group(streams, block_ms, count)

        records: list[StreamRecord] = []
        for stream_name, entries in response:
            for entry_id, fields in entries:
                records.append(
                    StreamRecord(stream_name, entry_id, parse_fields(fields))
                )
        return records


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set")
    redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
    source = RedisStreamSource(
        redis_url,
        group=os.environ.get("CONSUMER_GROUP", "ingest"),
        consumer=os.environ.get("CONSUMER_NAME", "ingest-1"),
    )
    with PostgresStore(database_url) as store:
        run_consumer(source, store)


if __name__ == "__main__":
    main()
