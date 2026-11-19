"""PostgreSQL implementation of the consumer's storage interface."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from shifting_hulls.interfaces import InsertOutcome

_CLEAN_TABLES = {
    "ais": "clean_ais",
    "compliance": "clean_compliance",
    "results": "clean_results",
}

_CLEAN_COLUMNS: dict[str, tuple[str, ...]] = {
    "ais": (
        "ts",
        "mmsi",
        "imo",
        "name",
        "flag",
        "call_sign",
        "lat",
        "lon",
        "sog",
        "cog",
        "heading",
        "nav_status",
        "draft",
        "ship_type",
    ),
    "compliance": (
        "event_id",
        "event_type",
        "imo",
        "name",
        "mmsi",
        "event_time",
        "publish_time",
        "note",
    ),
    "results": (
        "day",
        "plan_id",
        "imo",
        "mmsi",
        "outcome",
        "reported_at",
    ),
}

_NATURAL_KEY_QUERIES: dict[str, str] = {
    "compliance": "event_id = %s",
    "results": "day = %s AND plan_id = %s AND imo = %s AND mmsi = %s",
}


def _table(tables: dict[str, str], stream: str) -> str:
    try:
        return tables[stream]
    except KeyError as exc:
        raise ValueError(f"unknown stream {stream!r}") from exc


def _natural_key(stream: str, row: Mapping[str, Any]) -> tuple[Any, ...]:
    if stream == "compliance":
        return (row.get("event_id"),)
    if stream == "results":
        return (
            row.get("day"),
            row.get("plan_id"),
            row.get("imo"),
            row.get("mmsi"),
        )
    raise ValueError(f"stream {stream!r} has no natural key")


def _payload_text(payload: Mapping[str, Any]) -> str:
    """Serialise a quarantine payload as JSON text.

    The column is text rather than jsonb because quarantine must accept
    anything the producer sent, including NaN and infinity. Standard jsonb
    refuses those tokens, and losing the payload would break the rule that
    nothing is silently dropped.
    """
    return json.dumps(dict(payload), sort_keys=True, default=str)


class PostgresStore:
    """Write raw, clean and quarantine rows in explicit transactions.

    The connection is not autocommit. ``transaction`` commits when its block
    exits normally and rolls back on an exception, which keeps the raw and
    clean rows for one message atomic.
    """

    def __init__(self, conninfo: str, connect_timeout: int = 5) -> None:
        self._conn = psycopg.connect(
            conninfo,
            autocommit=False,
            connect_timeout=connect_timeout,
        )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> PostgresStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._conn.transaction():
            yield

    def insert_raw(
        self,
        stream: str,
        entry_id: str,
        payload_sha256: str,
        payload: Mapping[str, Any],
    ) -> InsertOutcome:
        """Store one delivered entry under (stream, entry_id).

        Redis entry ids are unique per stream, so a redelivery is a duplicate.
        A different payload under the same id is a producer bug; report it as a
        conflict instead of overwriting the first payload.
        """
        _table(_CLEAN_TABLES, stream)
        cursor = self._conn.execute(
            "INSERT INTO raw_messages "
            "(stream, entry_id, payload_sha256, payload) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (stream, entry_id) DO NOTHING",
            (stream, entry_id, payload_sha256, Jsonb(dict(payload))),
        )
        if cursor.rowcount:
            return InsertOutcome.inserted
        existing = self._conn.execute(
            "SELECT payload_sha256 FROM raw_messages "
            "WHERE stream = %s AND entry_id = %s",
            (stream, entry_id),
        ).fetchone()
        if existing is None:
            return InsertOutcome.conflict
        return (
            InsertOutcome.duplicate
            if existing[0] == payload_sha256
            else InsertOutcome.conflict
        )

    def insert_clean(
        self, stream: str, message_id: str, row: Mapping[str, Any]
    ) -> InsertOutcome:
        """Insert one clean row and compare any existing row with the same key.

        ``ON CONFLICT DO NOTHING`` is deliberately targetless: the compliance
        and results tables also have natural unique constraints, and every one
        of them means the same thing here. If a row already exists, it is
        compared to the new row. Identical means a harmless replay; different
        means the producer corrected content under an existing key, which the
        caller must quarantine rather than drop.
        """
        table = _table(_CLEAN_TABLES, stream)
        columns = _CLEAN_COLUMNS[stream]
        names = ", ".join(("message_id", *columns))
        placeholders = ", ".join(["%s"] * (len(columns) + 1))
        values = [message_id, *(row.get(column) for column in columns)]
        cursor = self._conn.execute(
            f"INSERT INTO {table} ({names}) VALUES ({placeholders}) "
            "ON CONFLICT DO NOTHING",
            values,
        )
        if cursor.rowcount:
            return InsertOutcome.inserted
        existing = self._select_clean(stream, table, columns, message_id, row)
        if existing is None:
            return InsertOutcome.conflict
        identical = all(
            existing[column] == row.get(column) for column in columns
        )
        return (
            InsertOutcome.duplicate if identical else InsertOutcome.conflict
        )

    def _select_clean(
        self,
        stream: str,
        table: str,
        columns: tuple[str, ...],
        message_id: str,
        row: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        selection = ", ".join(columns)
        found = self._conn.execute(
            f"SELECT {selection} FROM {table} WHERE message_id = %s",
            (message_id,),
        ).fetchone()
        if found is None and stream in _NATURAL_KEY_QUERIES:
            found = self._conn.execute(
                f"SELECT {selection} FROM {table} "
                f"WHERE {_NATURAL_KEY_QUERIES[stream]}",
                _natural_key(stream, row),
            ).fetchone()
        if found is None:
            return None
        return dict(zip(columns, found, strict=True))

    def insert_quarantine(
        self,
        stream: str,
        message_id: str,
        payload: Mapping[str, Any],
        reason: str,
    ) -> None:
        """Keep one rejected payload with its reason.

        The caller includes the reason in message_id, so the same payload can
        be quarantined for two different rules without one hiding the other.
        """
        self._conn.execute(
            "INSERT INTO quarantine (stream, message_id, reason, payload) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (stream, message_id) DO NOTHING",
            (stream, message_id, reason, _payload_text(payload)),
        )
