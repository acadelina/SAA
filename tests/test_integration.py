"""Integration tests against a real PostgreSQL and Redis started by Docker.

These tests are marked ``integration`` and are not part of ``make test``. Run
them with ``make test-integration``. They start an isolated Compose project on
free host ports, apply the real schema, and remove the containers, networks and
volumes afterwards. If Docker or the Compose plugin is unavailable, the whole
module skips with a message that says why.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest
import redis

from shifting_hulls.consumer import RedisStreamSource, process_message, run_consumer
from shifting_hulls.ingest import Rejection
from shifting_hulls.interfaces import StreamRecord
from shifting_hulls.stores import PostgresStore
from tests.test_schemas import ais_payload, compliance_payload

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
SCHEMA_FILE = REPO_ROOT / "sql" / "001_init.sql"


@dataclass(frozen=True)
class Services:
    """Connection details for one isolated Compose project."""

    redis_url: str
    postgres_dsn: str
    project: str
    env: dict[str, str]
    user: str
    database: str


def _docker_unavailable_reason() -> str | None:
    if shutil.which("docker") is None:
        return "the docker executable was not found"
    try:
        daemon = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"docker info could not run: {exc}"
    if daemon.returncode != 0:
        return f"the Docker daemon is not reachable: {daemon.stderr.strip()}"
    try:
        compose = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"docker compose could not run: {exc}"
    if compose.returncode != 0:
        return "the docker compose plugin is not available"
    return None


def _free_port(used: set[int]) -> int:
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port not in used:
            used.add(port)
            return port


def _compose(
    project: str,
    args: list[str],
    env: dict[str, str],
    *,
    input_text: str | None = None,
    timeout: int = 240,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            project,
            "-f",
            str(COMPOSE_FILE),
            *args,
        ],
        cwd=REPO_ROOT,
        env=env,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _teardown(project: str, env: dict[str, str]) -> None:
    try:
        result = _compose(
            project, ["down", "-v", "--remove-orphans"], env, timeout=120
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if result.returncode != 0:
        print(f"compose teardown warning: {result.stderr.strip()}")


def _wait_for_postgres(dsn: str, attempts: int = 60) -> None:
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            with psycopg.connect(dsn, connect_timeout=3):
                return
        except psycopg.OperationalError as exc:
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"PostgreSQL did not become ready: {last_error}")


def _wait_for_redis(url: str, attempts: int = 60) -> None:
    last_error: Exception | None = None
    for _ in range(attempts):
        client = redis.Redis.from_url(url, socket_connect_timeout=3)
        try:
            client.ping()
            return
        except redis.RedisError as exc:
            last_error = exc
            time.sleep(0.5)
        finally:
            client.close()
    raise RuntimeError(f"Redis did not become ready: {last_error}")


@pytest.fixture(scope="session")
def services() -> Iterator[Services]:
    reason = _docker_unavailable_reason()
    if reason is not None:
        pytest.skip(f"integration tests skipped: {reason}")

    used_ports: set[int] = set()
    redis_port = int(
        os.environ.get("IT_REDIS_PORT") or _free_port(used_ports)
    )
    postgres_port = int(
        os.environ.get("IT_POSTGRES_PORT") or _free_port(used_ports)
    )
    used_ports.update({redis_port, postgres_port})

    project = f"shifting-hulls-it-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    user = "shifting_hulls_it"
    password = "integration-only"
    database = "shifting_hulls_it"
    env = os.environ.copy()
    env.update(
        {
            "REDIS_PORT": str(redis_port),
            "POSTGRES_PORT": str(postgres_port),
            "POSTGRES_USER": user,
            "POSTGRES_PASSWORD": password,
            "POSTGRES_DB": database,
        }
    )
    redis_url = f"redis://127.0.0.1:{redis_port}/0"
    postgres_dsn = (
        f"postgresql://{user}:{password}@127.0.0.1:{postgres_port}/{database}"
    )

    up = _compose(
        project,
        ["up", "-d", "--wait", "--wait-timeout", "120"],
        env,
    )
    if up.returncode != 0:
        _teardown(project, env)
        pytest.skip(
            "integration services could not start: "
            f"{(up.stderr or up.stdout).strip()}"
        )

    try:
        _wait_for_postgres(postgres_dsn)
        _wait_for_redis(redis_url)
        # The image also applies the schema on first init. Running it again
        # here proves that 001_init.sql stays idempotent.
        applied = _compose(
            project,
            [
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                user,
                "-d",
                database,
                "-v",
                "ON_ERROR_STOP=1",
            ],
            env,
            input_text=SCHEMA_FILE.read_text(),
        )
        if applied.returncode != 0:
            pytest.fail(
                "could not apply sql/001_init.sql: "
                f"{(applied.stderr or applied.stdout).strip()}"
            )
        yield Services(
            redis_url=redis_url,
            postgres_dsn=postgres_dsn,
            project=project,
            env=env,
            user=user,
            database=database,
        )
    finally:
        _teardown(project, env)


@pytest.fixture()
def db(services: Services) -> Iterator[psycopg.Connection]:
    connection = psycopg.connect(services.postgres_dsn, autocommit=True)
    connection.execute(
        "TRUNCATE raw_messages, clean_ais, clean_compliance, "
        "clean_results, quarantine"
    )
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture()
def redis_client(services: Services) -> Iterator[redis.Redis]:
    client: redis.Redis = redis.Redis.from_url(
        services.redis_url, decode_responses=True
    )
    client.flushdb()
    try:
        yield client
    finally:
        client.close()


@pytest.fixture()
def store(services: Services, db: psycopg.Connection) -> Iterator[PostgresStore]:
    with PostgresStore(services.postgres_dsn) as postgres_store:
        yield postgres_store


def _add(client: redis.Redis, stream: str, payload: dict[str, object]) -> str:
    return client.xadd(stream, {"data": json.dumps(payload)})


def _pending(client: redis.Redis, stream: str, group: str) -> int:
    info = client.xpending(stream, group)
    return int(info["pending"])


def test_values_land_in_the_right_columns_and_raw_keeps_the_entry(
    services: Services,
    redis_client: redis.Redis,
    store: PostgresStore,
    db: psycopg.Connection,
) -> None:
    long_name = "A" * 130
    payload = ais_payload(lat=44.1, lon=28.7, name=long_name)
    entry_id = _add(redis_client, "ais", payload)
    source = RedisStreamSource(
        services.redis_url, group="g-columns", consumer="c1"
    )
    try:
        processed = run_consumer(
            source,
            store,
            streams=("ais",),
            block_ms=100,
            count=10,
            max_batches=1,
            min_idle_ms=0,
        )
    finally:
        source.close()

    assert processed == 1
    row = db.execute("SELECT lat, lon, name FROM clean_ais").fetchone()
    assert row == (44.1, 28.7, "A" * 120)

    raw = db.execute(
        "SELECT stream, entry_id, payload_sha256, payload FROM raw_messages"
    ).fetchone()
    assert raw[0] == "ais"
    assert raw[1] == entry_id
    assert len(raw[2]) == 64
    assert raw[3]["lat"] == 44.1
    assert raw[3]["lon"] == 28.7
    assert raw[3]["name"] == long_name
    assert _pending(redis_client, "ais", "g-columns") == 0


def test_replay_deduplicates_clean_rows_and_ack_clears_pending(
    services: Services,
    redis_client: redis.Redis,
    store: PostgresStore,
    db: psycopg.Connection,
) -> None:
    payload = ais_payload()
    entry_id = _add(redis_client, "ais", payload)
    source = RedisStreamSource(
        services.redis_url, group="g-replay", consumer="c1"
    )
    try:
        assert (
            run_consumer(
                source,
                store,
                streams=("ais",),
                block_ms=100,
                count=10,
                max_batches=1,
                min_idle_ms=0,
            )
            == 1
        )
    finally:
        source.close()
    assert _pending(redis_client, "ais", "g-replay") == 0

    # Same entry id delivered twice must not add a second raw or clean row.
    record = StreamRecord("ais", entry_id, payload)
    process_message("ais", record.entry_id, record.payload, store)
    process_message("ais", record.entry_id, record.payload, store)
    assert db.execute("SELECT count(*) FROM raw_messages").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM clean_ais").fetchone()[0] == 1

    # The producer resends the content under a new entry id: keep both raw
    # deliveries, but deduplicate the clean row by validated content.
    _add(redis_client, "ais", payload)
    source = RedisStreamSource(
        services.redis_url, group="g-replay", consumer="c1"
    )
    try:
        run_consumer(
            source,
            store,
            streams=("ais",),
            block_ms=100,
            count=10,
            max_batches=1,
            min_idle_ms=0,
        )
    finally:
        source.close()
    assert db.execute("SELECT count(*) FROM raw_messages").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM clean_ais").fetchone()[0] == 1
    assert _pending(redis_client, "ais", "g-replay") == 0


def test_a_crash_between_read_and_ack_is_recovered(
    services: Services,
    redis_client: redis.Redis,
    store: PostgresStore,
    db: psycopg.Connection,
) -> None:
    _add(redis_client, "ais", ais_payload(mmsi=273456781))
    group = "g-crash"

    crashed = RedisStreamSource(services.redis_url, group=group, consumer="c1")
    try:
        records = crashed.read(("ais",), block_ms=100, count=10)
    finally:
        crashed.close()
    assert len(records) == 1
    assert _pending(redis_client, "ais", group) == 1

    recovered = RedisStreamSource(
        services.redis_url, group=group, consumer="c1"
    )
    try:
        processed = run_consumer(
            recovered,
            store,
            streams=("ais",),
            block_ms=100,
            count=10,
            max_batches=0,
            min_idle_ms=0,
        )
    finally:
        recovered.close()

    assert processed == 1
    assert _pending(redis_client, "ais", group) == 0
    assert db.execute("SELECT count(*) FROM clean_ais").fetchone()[0] == 1


def test_stale_entry_from_another_consumer_is_claimed(
    services: Services,
    redis_client: redis.Redis,
    store: PostgresStore,
    db: psycopg.Connection,
) -> None:
    _add(redis_client, "ais", ais_payload(mmsi=273456782))
    group = "g-stale"

    first = RedisStreamSource(services.redis_url, group=group, consumer="a")
    try:
        assert len(first.read(("ais",), block_ms=100, count=10)) == 1
    finally:
        first.close()
    assert _pending(redis_client, "ais", group) == 1

    second = RedisStreamSource(services.redis_url, group=group, consumer="b")
    try:
        processed = run_consumer(
            second,
            store,
            streams=("ais",),
            block_ms=100,
            count=10,
            max_batches=0,
            min_idle_ms=0,
        )
    finally:
        second.close()

    assert processed == 1
    assert _pending(redis_client, "ais", group) == 0
    assert db.execute("SELECT count(*) FROM clean_ais").fetchone()[0] == 1


def test_poison_messages_are_quarantined_and_the_consumer_continues(
    services: Services,
    redis_client: redis.Redis,
    store: PostgresStore,
    db: psycopg.Connection,
) -> None:
    _add(redis_client, "ais", ais_payload(mmsi=273456783))
    _add(redis_client, "ais", ais_payload(name="BAD\x00NAME"))
    _add(redis_client, "ais", ais_payload(lat=float("nan")))
    redis_client.xadd("ais", {"mmsi": "273456784"})  # no data envelope
    redis_client.xadd(
        "ais", {"data": json.dumps(ais_payload()), "extra": "sibling"}
    )

    source = RedisStreamSource(
        services.redis_url, group="g-poison", consumer="c1"
    )
    try:
        processed = run_consumer(
            source,
            store,
            streams=("ais",),
            block_ms=100,
            count=10,
            max_batches=1,
            min_idle_ms=0,
        )
    finally:
        source.close()

    assert processed == 5
    assert db.execute("SELECT count(*) FROM clean_ais").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM quarantine").fetchone()[0] == 4
    assert _pending(redis_client, "ais", "g-poison") == 0

    reasons = [
        row[0]
        for row in db.execute("SELECT reason FROM quarantine").fetchall()
    ]
    assert any("control characters" in reason for reason in reasons)
    assert any("finite number" in reason for reason in reasons)
    assert any("data field" in reason or "data" in reason for reason in reasons)

    payloads = [
        json.loads(row[0])
        for row in db.execute("SELECT payload FROM quarantine").fetchall()
    ]
    nan_payloads = [
        payload
        for payload in payloads
        if isinstance(payload.get("lat"), float)
        and math.isnan(payload["lat"])
    ]
    assert len(nan_payloads) == 1


def test_raw_and_clean_rows_are_atomic(
    services: Services,
    store: PostgresStore,
    db: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated failure after the raw insert")

    monkeypatch.setattr(store, "insert_clean", explode)

    with pytest.raises(RuntimeError, match="simulated failure"):
        process_message("ais", "1-0", ais_payload(), store)

    assert db.execute("SELECT count(*) FROM raw_messages").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM clean_ais").fetchone()[0] == 0


def test_conflicting_duplicate_is_quarantined_not_dropped(
    services: Services,
    redis_client: redis.Redis,
    store: PostgresStore,
    db: psycopg.Connection,
) -> None:
    _add(redis_client, "compliance", compliance_payload())
    _add(
        redis_client,
        "compliance",
        compliance_payload(event_type="detention"),
    )
    source = RedisStreamSource(
        services.redis_url, group="g-conflict", consumer="c1"
    )
    try:
        processed = run_consumer(
            source,
            store,
            streams=("compliance",),
            block_ms=100,
            count=10,
            max_batches=1,
            min_idle_ms=0,
        )
    finally:
        source.close()

    assert processed == 2
    assert (
        db.execute("SELECT count(*) FROM clean_compliance").fetchone()[0] == 1
    )
    row = db.execute("SELECT reason FROM quarantine").fetchone()
    assert row is not None
    assert row[0] == "conflicting duplicate"
    assert len(db.execute("SELECT * FROM raw_messages").fetchall()) == 2


def test_conflicting_duplicate_returns_a_rejection(
    services: Services,
    store: PostgresStore,
) -> None:
    process_message("compliance", "2-0", compliance_payload(), store)
    result = process_message(
        "compliance", "2-1", compliance_payload(event_type="detention"), store
    )

    assert isinstance(result, Rejection)
    assert result.reason == "conflicting duplicate"
