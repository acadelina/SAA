"""PostgreSQL implementation of the consumer's storage interface."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

_RAW_TABLES = {
    "ais": "raw_ais",
    "compliance": "raw_compliance",
    "results": "raw_results",
}

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


def _table(tables: dict[str, str], stream: str) -> str:
    try:
        return tables[stream]
    except KeyError as exc:
        raise ValueError(f"unknown stream {stream!r}") from exc


class PostgresStore:
    """Write raw, clean and quarantine rows with ON CONFLICT DO NOTHING."""

    def __init__(self, conninfo: str) -> None:
        self._conn = psycopg.connect(conninfo, autocommit=True)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> PostgresStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def insert_raw(
        self, stream: str, message_id: str, payload: Mapping[str, Any]
    ) -> None:
        table = _table(_RAW_TABLES, stream)
        self._conn.execute(
            f"INSERT INTO {table} (message_id, payload) VALUES (%s, %s) "
            "ON CONFLICT (message_id) DO NOTHING",
            (message_id, Jsonb(dict(payload))),
        )

    def insert_clean(
        self, stream: str, message_id: str, row: Mapping[str, Any]
    ) -> None:
        table = _table(_CLEAN_TABLES, stream)
        columns = _CLEAN_COLUMNS[stream]
        names = ", ".join(("message_id", *columns))
        placeholders = ", ".join(["%s"] * (len(columns) + 1))
        values = [message_id, *(row.get(column) for column in columns)]
        self._conn.execute(
            f"INSERT INTO {table} ({names}) VALUES ({placeholders}) "
            "ON CONFLICT (message_id) DO NOTHING",
            values,
        )

    def insert_quarantine(
        self,
        stream: str,
        message_id: str,
        payload: Mapping[str, Any],
        reason: str,
    ) -> None:
        self._conn.execute(
            "INSERT INTO quarantine (stream, message_id, reason, payload) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (stream, message_id) DO NOTHING",
            (stream, message_id, reason, Jsonb(dict(payload))),
        )
