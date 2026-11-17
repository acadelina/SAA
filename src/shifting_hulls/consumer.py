"""Redis Streams consumer that validates messages and writes them to PostgreSQL.

The producer contract is one entry per stream message: exactly one field named
``data`` whose value is a JSON object. Flat entries, extra sibling fields and
malformed envelopes are quarantined rather than guessed at.

Delivery is at-least-once. A message is acknowledged only after its database
transaction commits. On startup the consumer drains its own pending list and
claims entries that other consumers left idle for longer than the configured
minimum, so a crash between read and ack does not lose data. Each record is
processed in its own transaction and wrapped in a last-resort try/except that
quarantines unexpected failures instead of stopping the loop.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

import psycopg
import redis

from shifting_hulls.ingest import Rejection, ValidationResult, validate_message
from shifting_hulls.interfaces import (
    InsertOutcome,
    MessageStore,
    PendingRecord,
    StreamRecord,
    StreamSource,
)
from shifting_hulls.schemas import (
    STREAMS,
    message_id,
    payload_digest,
)
from shifting_hulls.stores import PostgresStore

logger = logging.getLogger(__name__)

DEFAULT_BLOCK_MS = 5000
DEFAULT_COUNT = 100
DEFAULT_MIN_IDLE_MS = 60_000
DEFAULT_PENDING_WARN_S = 300.0

ENVELOPE_ERROR = "entry must contain exactly one data field holding a JSON object"
INVALID_JSON_ERROR = "data field is not valid JSON"
NOT_AN_OBJECT_ERROR = "data field must be a JSON object"
CONFLICTING_DUPLICATE = "conflicting duplicate"


def parse_fields(fields: Mapping[str, str]) -> tuple[dict[str, Any], str | None]:
    """Decode one Redis entry, returning the payload and an envelope error.

    The only accepted shape is ``{"data": "<json object>"}``. Values are never
    sniffed from strings, because that turns a name like ``"123"`` into a
    number and ``"null"`` into None. When the envelope is wrong, the original
    fields are returned so quarantine can keep exactly what arrived.
    """
    if set(fields) != {"data"}:
        return dict(fields), ENVELOPE_ERROR
    raw = fields["data"]
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return dict(fields), INVALID_JSON_ERROR
    if not isinstance(parsed, dict):
        return dict(fields), NOT_AN_OBJECT_ERROR
    return parsed, None


def quarantine_key(payload: Mapping[str, Any], reason: str) -> str:
    """Build a quarantine key that includes both the payload and the reason."""
    reason_digest = hashlib.sha256(reason.encode()).hexdigest()
    return f"q:{payload_digest(dict(payload))}:{reason_digest}"


def process_message(
    stream: str,
    entry_id: str,
    payload: dict[str, Any],
    store: MessageStore,
    parse_error: str | None = None,
) -> ValidationResult:
    """Route one message to clean, raw or quarantine storage in one transaction.

    ``entry_id`` is the raw idempotency key: every delivered entry is kept in
    the raw table under (stream, entry_id). The clean key is derived from the
    validated content, so replays deduplicate but different raw payloads do
    not collapse. A clean row that already exists with different content is
    written to quarantine as ``conflicting duplicate`` instead of being
    silently ignored.
    """
    if parse_error is not None:
        _write_quarantine(store, stream, payload, parse_error)
        logger.warning("quarantined %s/%s: %s", stream, entry_id, parse_error)
        return Rejection(reason=parse_error, raw=dict(payload))

    result = validate_message(stream, payload)
    if isinstance(result, Rejection):
        _write_quarantine(store, stream, payload, result.reason)
        logger.warning("quarantined %s/%s: %s", stream, entry_id, result.reason)
        return result

    clean_id = message_id(stream, result)
    quarantined: Rejection | None = None
    with store.transaction():
        raw_outcome = store.insert_raw(
            stream, entry_id, payload_digest(payload), payload
        )
        if raw_outcome is InsertOutcome.conflict:
            quarantined = _conflicting_duplicate(store, stream, payload)
        else:
            clean_outcome = store.insert_clean(
                stream, clean_id, result.model_dump(mode="python")
            )
            if clean_outcome is InsertOutcome.conflict:
                quarantined = _conflicting_duplicate(store, stream, payload)

    if quarantined is not None:
        logger.warning(
            "conflicting duplicate for %s/%s (clean key %s)",
            stream,
            entry_id,
            clean_id,
        )
        return quarantined
    return result


def _write_quarantine(
    store: MessageStore,
    stream: str,
    payload: Mapping[str, Any],
    reason: str,
) -> None:
    with store.transaction():
        store.insert_quarantine(
            stream,
            quarantine_key(payload, reason),
            payload,
            reason,
        )


def _conflicting_duplicate(
    store: MessageStore, stream: str, payload: Mapping[str, Any]
) -> Rejection:
    store.insert_quarantine(
        stream,
        quarantine_key(payload, CONFLICTING_DUPLICATE),
        payload,
        CONFLICTING_DUPLICATE,
    )
    return Rejection(reason=CONFLICTING_DUPLICATE, raw=dict(payload))


class Consumer:
    """Process stream records with recovery, acknowledgement and containment."""

    def __init__(
        self,
        source: StreamSource,
        store: MessageStore,
        streams: Sequence[str] = STREAMS,
        block_ms: int = DEFAULT_BLOCK_MS,
        count: int = DEFAULT_COUNT,
        min_idle_ms: int = DEFAULT_MIN_IDLE_MS,
        pending_warn_after_s: float = DEFAULT_PENDING_WARN_S,
        stop_event: threading.Event | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._source = source
        self._store = store
        self._streams = tuple(streams)
        self._block_ms = block_ms
        self._count = count
        self._min_idle_ms = min_idle_ms
        self._pending_warn_after_s = pending_warn_after_s
        self._stop_event = stop_event
        self._clock = clock
        self._unacked_since: dict[tuple[str, str], float] = {}
        self._acked = 0

    @property
    def unacknowledged(self) -> tuple[tuple[str, str], ...]:
        """Keys of records that failed processing and were left pending."""
        return tuple(self._unacked_since)

    def recover(self) -> int:
        """Drain this consumer's pending entries and claim stale ones.

        This runs before new reads so a previous crash is resolved first. A
        record that cannot be processed stays pending and is retried later; the
        loop stops rather than spinning on a permanent failure.
        """
        acked = 0
        while True:
            own = self._source.read_pending(self._streams, self._count)
            if not own:
                break
            processed = self.handle_records([item.record for item in own])
            acked += processed
            if processed == 0:
                break

        stale = self._source.claim_stale(
            self._streams, self._min_idle_ms, self._count
        )
        for pending in stale:
            logger.warning(
                "reclaiming pending entry %s/%s idle for at least %d ms",
                pending.record.stream,
                pending.record.entry_id,
                pending.idle_ms,
            )
        acked += self.handle_records([item.record for item in stale])
        return acked

    def process_record(self, record: StreamRecord) -> bool:
        """Process and acknowledge one record. Return True when acknowledged.

        A validation or envelope failure is quarantined by ``process_message``
        and counts as success, because it is no longer pending work. An
        unexpected exception is also quarantined when possible. If even that
        fails (for example because PostgreSQL is down), the record is left
        unacknowledged so Redis can redeliver it.
        """
        try:
            process_message(
                record.stream,
                record.entry_id,
                record.payload,
                self._store,
                record.parse_error,
            )
        except Exception as exc:  # noqa: BLE001 - containment is the point
            self._note_unacknowledged(record)
            reason = f"internal error: {type(exc).__name__}: {exc}"
            try:
                _write_quarantine(self._store, record.stream, record.payload, reason)
            except Exception:  # noqa: BLE001 - storage is unavailable
                logger.exception(
                    "could not quarantine %s/%s; leaving it pending",
                    record.stream,
                    record.entry_id,
                )
                return False
            logger.exception(
                "quarantined %s/%s after an unexpected error",
                record.stream,
                record.entry_id,
            )
            self._unacked_since.pop(_record_key(record), None)
        return self._ack(record)

    def handle_records(self, records: Sequence[StreamRecord]) -> int:
        """Process records until done or stopped. Return how many were acked."""
        acked = 0
        for record in records:
            if self._stop_requested():
                break
            if self.process_record(record):
                acked += 1
        self._acked += acked
        return acked

    def run(self, max_batches: int | None = None) -> int:
        """Recover, then read batches until max_batches or a stop signal."""
        if not self._recover_with_retry():
            return self._acked
        batches = 0
        attempt = 0
        while not self._stop_requested():
            if max_batches is not None and batches >= max_batches:
                break
            self._warn_about_old_pending()
            if self._unacked_since:
                try:
                    pending = self._source.read_pending(
                        self._streams, self._count
                    )
                    progressed = self.handle_records(
                        [item.record for item in pending]
                    )
                except (redis.ConnectionError, redis.TimeoutError) as exc:
                    delay = _backoff_delay(attempt)
                    attempt += 1
                    logger.error(
                        "pending retry failed (%s); retrying in %.1f s",
                        exc,
                        delay,
                    )
                    self._wait(delay)
                    continue
                if self._unacked_since and progressed == 0:
                    # Do not drain new entries into the pending list while
                    # storage is unavailable. Retry the old ones first.
                    delay = _backoff_delay(attempt)
                    attempt += 1
                    logger.error(
                        "no pending record could be processed; "
                        "retrying in %.1f s",
                        delay,
                    )
                    self._wait(delay)
                    continue
            try:
                records = self._source.read(
                    self._streams,
                    block_ms=self._block_ms,
                    count=self._count,
                )
            except (redis.ConnectionError, redis.TimeoutError) as exc:
                delay = _backoff_delay(attempt)
                attempt += 1
                logger.error(
                    "stream read failed (%s); retrying in %.1f s", exc, delay
                )
                self._wait(delay)
                continue
            attempt = 0
            batches += 1
            processed = self.handle_records(records)
            if records and processed == 0:
                delay = _backoff_delay(attempt)
                attempt += 1
                logger.error(
                    "could not process any of %d records; retrying in %.1f s",
                    len(records),
                    delay,
                )
                self._wait(delay)
        return self._acked

    def _recover_with_retry(self) -> bool:
        """Run startup recovery, retrying Redis failures until stopped."""
        attempt = 0
        while not self._stop_requested():
            try:
                self.recover()
                return True
            except (redis.ConnectionError, redis.TimeoutError) as exc:
                delay = _backoff_delay(attempt)
                attempt += 1
                logger.error(
                    "startup recovery failed (%s); retrying in %.1f s",
                    exc,
                    delay,
                )
                self._wait(delay)
        return False

    def _ack(self, record: StreamRecord) -> bool:
        try:
            self._source.ack(record.stream, record.entry_id)
        except Exception:  # noqa: BLE001 - Redis is unavailable, retry later
            logger.exception(
                "could not acknowledge %s/%s; it will be retried",
                record.stream,
                record.entry_id,
            )
            self._note_unacknowledged(record)
            return False
        self._unacked_since.pop(_record_key(record), None)
        return True

    def _note_unacknowledged(self, record: StreamRecord) -> None:
        key = _record_key(record)
        self._unacked_since.setdefault(key, self._clock())
        logger.error(
            "processing failed for %s/%s; leaving it pending for retry",
            record.stream,
            record.entry_id,
        )

    def _warn_about_old_pending(self) -> None:
        now = self._clock()
        for (stream, entry_id), since in self._unacked_since.items():
            age = now - since
            if age >= self._pending_warn_after_s:
                logger.error(
                    "PENDING TOO LONG: %s/%s has been unacknowledged for %.0f s",
                    stream,
                    entry_id,
                    age,
                )

    def _stop_requested(self) -> bool:
        return self._stop_event is not None and self._stop_event.is_set()

    def _wait(self, delay: float) -> None:
        if self._stop_event is not None:
            self._stop_event.wait(delay)
        else:
            time.sleep(delay)


def _record_key(record: StreamRecord) -> tuple[str, str]:
    return (record.stream, record.entry_id)


def _backoff_delay(attempt: int) -> float:
    return min(0.5 * (2**attempt), 30.0)


def _records_from_response(response: list[Any]) -> list[StreamRecord]:
    records: list[StreamRecord] = []
    for stream_name, entries in response:
        for entry_id, fields in entries:
            payload, error = parse_fields(fields)
            records.append(StreamRecord(stream_name, entry_id, payload, error))
    return records


class RedisStreamSource:
    """Read all named streams through one Redis consumer group."""

    def __init__(
        self,
        url: str,
        group: str = "ingest",
        consumer: str = "ingest-1",
        socket_timeout: float = 5.0,
        connect_timeout: float = 5.0,
    ) -> None:
        self._client: redis.Redis = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_timeout=socket_timeout,
            socket_connect_timeout=connect_timeout,
            health_check_interval=30,
        )
        self._group = group
        self._consumer = consumer
        self._ready: set[str] = set()

    def close(self) -> None:
        self._client.close()

    def _ensure_groups(self, streams: Sequence[str]) -> None:
        for stream in streams:
            if stream in self._ready:
                continue
            try:
                self._client.xgroup_create(
                    stream, self._group, id="0", mkstream=True
                )
            except redis.ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise
            self._ready.add(stream)

    def _read_group(
        self,
        streams: Sequence[str],
        start: str,
        block_ms: int | None,
        count: int,
    ) -> list[Any]:
        self._ensure_groups(streams)
        ids: dict[Any, Any] = {stream: start for stream in streams}
        try:
            response = self._xreadgroup(ids, block_ms, count)
        except redis.ResponseError as exc:
            # A group can disappear if Redis is flushed. Recreate it once and
            # retry rather than crashing the consumer.
            if "NOGROUP" not in str(exc):
                raise
            self._ready.clear()
            self._ensure_groups(streams)
            response = self._xreadgroup(ids, block_ms, count)
        return response

    def _xreadgroup(
        self, ids: dict[Any, Any], block_ms: int | None, count: int
    ) -> list[Any]:
        # redis-py's stubs model the client as possibly asynchronous, so the
        # return type is an Awaitable union. The synchronous client always
        # returns a list or None here; cast at this narrow boundary.
        response = cast(
            "list[Any] | None",
            self._client.xreadgroup(
                self._group,
                self._consumer,
                ids,
                count=count,
                block=block_ms,
            ),
        )
        return response or []

    def read(
        self, streams: Sequence[str], block_ms: int, count: int
    ) -> list[StreamRecord]:
        return _records_from_response(
            self._read_group(streams, ">", block_ms, count)
        )

    def read_pending(
        self, streams: Sequence[str], count: int
    ) -> list[PendingRecord]:
        records = _records_from_response(
            self._read_group(streams, "0", None, count)
        )
        # XREADGROUP does not expose idle time; these entries are ours and are
        # processed immediately, so zero is an honest lower bound.
        return [PendingRecord(record=record, idle_ms=0) for record in records]

    def claim_stale(
        self, streams: Sequence[str], min_idle_ms: int, count: int
    ) -> list[PendingRecord]:
        self._ensure_groups(streams)
        claimed: list[PendingRecord] = []
        for stream in streams:
            claimed.extend(
                self._claim_stream(stream, min_idle_ms, count, retry=True)
            )
        return claimed

    def _claim_stream(
        self,
        stream: str,
        min_idle_ms: int,
        count: int,
        *,
        retry: bool,
    ) -> list[PendingRecord]:
        records: list[PendingRecord] = []
        start = "0-0"
        while True:
            try:
                response = cast(
                    "Any",
                    self._client.xautoclaim(
                        stream,
                        self._group,
                        self._consumer,
                        min_idle_ms,
                        start_id=start,
                        count=count,
                    ),
                )
            except redis.ResponseError as exc:
                if "NOGROUP" in str(exc) and retry:
                    self._ready.clear()
                    self._ensure_groups((stream,))
                    return self._claim_stream(
                        stream, min_idle_ms, count, retry=False
                    )
                raise
            if not response:
                break
            cursor, entries = response[0], response[1]
            for entry_id, fields in entries:
                payload, error = parse_fields(fields)
                records.append(
                    PendingRecord(
                        record=StreamRecord(
                            stream, entry_id, payload, error
                        ),
                        idle_ms=min_idle_ms,
                    )
                )
            if cursor in ("0-0", 0, b"0-0") or cursor == start:
                break
            start = cursor
        return records

    def ack(self, stream: str, entry_id: str) -> None:
        self._client.xack(stream, self._group, entry_id)


def run_consumer(
    source: StreamSource,
    store: MessageStore,
    streams: Sequence[str] = STREAMS,
    block_ms: int = DEFAULT_BLOCK_MS,
    count: int = DEFAULT_COUNT,
    max_batches: int | None = None,
    min_idle_ms: int = DEFAULT_MIN_IDLE_MS,
    pending_warn_after_s: float = DEFAULT_PENDING_WARN_S,
    stop_event: threading.Event | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Run the consumer until stopped or until max_batches is reached."""
    consumer = Consumer(
        source,
        store,
        streams=streams,
        block_ms=block_ms,
        count=count,
        min_idle_ms=min_idle_ms,
        pending_warn_after_s=pending_warn_after_s,
        stop_event=stop_event,
        clock=clock,
    )
    return consumer.run(max_batches=max_batches)


def _open_store_with_retry(conninfo: str, attempts: int = 5) -> PostgresStore:
    """Open PostgreSQL with capped exponential backoff for startup races."""
    delay = 1.0
    for attempt in range(1, attempts + 1):
        try:
            return PostgresStore(conninfo)
        except psycopg.OperationalError as exc:
            if attempt == attempts:
                raise
            logger.warning(
                "PostgreSQL connection failed (%s); retrying in %.1f s",
                exc,
                delay,
            )
            time.sleep(delay)
            delay = min(delay * 2, 30.0)
    raise RuntimeError("unreachable")


def _install_signal_handlers(stop: threading.Event) -> None:
    def request_stop(signum: int, _frame: Any) -> None:
        logger.info(
            "received signal %s; finishing the current record then stopping",
            signum,
        )
        stop.set()

    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, request_stop)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set")
    redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")

    stop = threading.Event()
    _install_signal_handlers(stop)
    store = _open_store_with_retry(database_url)
    source: RedisStreamSource | None = None
    try:
        source = RedisStreamSource(
            redis_url,
            group=os.environ.get("CONSUMER_GROUP", "ingest"),
            consumer=os.environ.get("CONSUMER_NAME", "ingest-1"),
            socket_timeout=float(
                os.environ.get("REDIS_SOCKET_TIMEOUT_S", "5")
            ),
            connect_timeout=float(
                os.environ.get("REDIS_CONNECT_TIMEOUT_S", "5")
            ),
        )
        run_consumer(
            source,
            store,
            stop_event=stop,
            min_idle_ms=int(
                os.environ.get("CONSUMER_MIN_IDLE_MS", DEFAULT_MIN_IDLE_MS)
            ),
            pending_warn_after_s=float(
                os.environ.get(
                    "CONSUMER_PENDING_WARN_S", DEFAULT_PENDING_WARN_S
                )
            ),
        )
    finally:
        if source is not None:
            source.close()
        store.close()


if __name__ == "__main__":
    main()
