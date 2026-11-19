"""Tests for envelope parsing, routing, idempotency, recovery and containment."""

from __future__ import annotations

import logging

import pytest

from shifting_hulls.consumer import (
    CONFLICTING_DUPLICATE,
    ENVELOPE_ERROR,
    NOT_AN_OBJECT_ERROR,
    Consumer,
    parse_fields,
    process_message,
    quarantine_key,
    run_consumer,
)
from shifting_hulls.ingest import Rejection
from shifting_hulls.interfaces import PendingRecord, StreamRecord
from shifting_hulls.schemas import AisMessage, message_id, payload_digest
from tests.fakes import FakeStore, FakeStreamSource
from tests.test_schemas import ais_payload, compliance_payload, result_payload


def test_valid_message_writes_raw_by_entry_id_and_clean_by_content() -> None:
    store = FakeStore()

    result = process_message("ais", "1-0", ais_payload(), store)

    assert isinstance(result, AisMessage)
    assert store.raw == {("ais", "1-0"): ais_payload()}
    assert store.raw_hashes[("ais", "1-0")] == payload_digest(ais_payload())
    assert store.clean == {("ais", message_id("ais", result)): result.model_dump()}
    assert store.quarantine == {}


def test_invalid_message_writes_only_a_quarantine_row() -> None:
    store = FakeStore()

    result = process_message("ais", "1-1", ais_payload(sog=-5), store)

    assert isinstance(result, Rejection)
    assert store.raw == {}
    assert store.clean == {}
    assert len(store.quarantine) == 1
    _, (payload, reason) = next(iter(store.quarantine.items()))
    assert payload == ais_payload(sog=-5)
    assert "sog" in reason


def test_replaying_the_same_entry_does_not_duplicate_rows() -> None:
    store = FakeStore()
    payload = ais_payload()

    process_message("ais", "100-0", payload, store)
    process_message("ais", "100-0", payload, store)

    assert len(store.raw) == 1
    assert len(store.clean) == 1
    assert store.quarantine == {}


def test_distinct_entries_with_the_same_payload_keep_both_raw_rows() -> None:
    store = FakeStore()
    payload = ais_payload()

    process_message("ais", "100-0", payload, store)
    process_message("ais", "100-1", payload, store)

    assert len(store.raw) == 2
    assert len(store.clean) == 1
    assert store.quarantine == {}


def test_two_timestamp_spellings_of_one_instant_share_one_clean_row() -> None:
    store = FakeStore()

    process_message("ais", "1-0", ais_payload(ts="2026-03-04T10:15:22Z"), store)
    process_message(
        "ais", "1-1", ais_payload(ts="2026-03-04T12:15:22+02:00"), store
    )

    assert len(store.raw) == 2
    assert len(store.clean) == 1


def test_raw_keeps_untruncated_name_while_clean_is_truncated() -> None:
    store = FakeStore()
    long_name = "A" * 130

    process_message("ais", "1-0", ais_payload(name=long_name), store)

    assert store.raw[("ais", "1-0")]["name"] == long_name
    _, clean_row = next(iter(store.clean.items()))
    assert clean_row["name"] == "A" * 120


def test_same_entry_id_with_different_content_is_a_conflict() -> None:
    store = FakeStore()

    process_message("ais", "1-0", ais_payload(), store)
    result = process_message("ais", "1-0", ais_payload(mmsi=273456781), store)

    assert isinstance(result, Rejection)
    assert result.reason == CONFLICTING_DUPLICATE
    assert list(store.raw) == [("ais", "1-0")]
    assert len(store.clean) == 1
    _, (_, reason) = next(iter(store.quarantine.items()))
    assert reason == CONFLICTING_DUPLICATE


def test_replaying_a_rejected_message_does_not_duplicate_quarantine() -> None:
    store = FakeStore()
    payload = ais_payload(lat=100)

    process_message("ais", "101-0", payload, store)
    process_message("ais", "101-0", payload, store)

    assert len(store.quarantine) == 1


def test_quarantine_key_includes_the_reason() -> None:
    payload = ais_payload(lat=100)

    assert quarantine_key(payload, "reason one") != quarantine_key(
        payload, "reason two"
    )
    assert quarantine_key(payload, "reason one") == quarantine_key(
        payload, "reason one"
    )


def test_two_different_messages_produce_two_rows() -> None:
    store = FakeStore()

    process_message("ais", "1-0", ais_payload(mmsi=273456780), store)
    process_message("ais", "1-1", ais_payload(mmsi=273456781), store)

    assert len(store.raw) == 2
    assert len(store.clean) == 2


def test_compliance_event_ids_are_natural_keys() -> None:
    store = FakeStore()

    process_message("compliance", "2-0", compliance_payload(), store)
    process_message("compliance", "2-1", compliance_payload(), store)

    assert list(store.clean) == [("compliance", "compliance:evt-0001")]
    assert store.quarantine == {}


def test_corrected_compliance_event_is_quarantined_not_dropped() -> None:
    store = FakeStore()

    process_message("compliance", "2-0", compliance_payload(), store)
    result = process_message(
        "compliance",
        "2-1",
        compliance_payload(event_type="detention"),
        store,
    )

    assert isinstance(result, Rejection)
    assert result.reason == CONFLICTING_DUPLICATE
    assert len(store.clean) == 1
    _, (_, reason) = next(iter(store.quarantine.items()))
    assert reason == CONFLICTING_DUPLICATE


def test_inspection_result_keys_include_day_plan_and_hull() -> None:
    store = FakeStore()

    process_message("results", "3-0", result_payload(), store)

    assert list(store.clean) == [
        ("results", "results:12:p-0012:9319703:273456780")
    ]


def test_corrected_inspection_outcome_is_quarantined_not_dropped() -> None:
    store = FakeStore()

    process_message("results", "3-0", result_payload(), store)
    result = process_message(
        "results", "3-1", result_payload(outcome="clean"), store
    )

    assert isinstance(result, Rejection)
    assert result.reason == CONFLICTING_DUPLICATE
    assert len(store.clean) == 1


def test_run_consumer_processes_records_from_all_streams() -> None:
    source = FakeStreamSource(
        [
            [
                StreamRecord("ais", "1-0", ais_payload()),
                StreamRecord("compliance", "2-0", compliance_payload()),
                StreamRecord("results", "3-0", result_payload()),
                StreamRecord("ais", "1-1", ais_payload(lon=200)),
            ]
        ]
    )
    store = FakeStore()

    processed = run_consumer(source, store, max_batches=1)

    assert processed == 4
    assert len(store.raw) == 3
    assert len(store.clean) == 3
    assert len(store.quarantine) == 1
    assert source.acks == [
        ("ais", "1-0"),
        ("compliance", "2-0"),
        ("results", "3-0"),
        ("ais", "1-1"),
    ]


def test_run_consumer_stops_after_the_requested_batches() -> None:
    source = FakeStreamSource(
        [
            [StreamRecord("ais", "1-0", ais_payload())],
            [StreamRecord("ais", "1-1", ais_payload(mmsi=273456781))],
        ]
    )
    store = FakeStore()

    processed = run_consumer(source, store, max_batches=1)

    assert processed == 1
    assert source.calls == 1
    assert source.acks == [("ais", "1-0")]


def test_poison_message_is_quarantined_and_the_consumer_continues() -> None:
    source = FakeStreamSource(
        [
            [
                StreamRecord("ais", "1-0", ais_payload(sog=-5)),
                StreamRecord("ais", "1-1", ais_payload(mmsi=273456781)),
            ]
        ]
    )
    store = FakeStore()

    processed = run_consumer(source, store, max_batches=1)

    assert processed == 2
    assert len(store.quarantine) == 1
    assert len(store.clean) == 1
    assert source.acks == [("ais", "1-0"), ("ais", "1-1")]


def test_startup_drains_this_consumers_pending_entries() -> None:
    record = StreamRecord("ais", "9-0", ais_payload())
    source = FakeStreamSource(
        [], pending_batches=[[PendingRecord(record=record, idle_ms=0)]]
    )
    store = FakeStore()

    processed = run_consumer(source, store, streams=("ais",), max_batches=0)

    assert processed == 1
    assert len(store.clean) == 1
    assert source.acks == [("ais", "9-0")]


def test_startup_claims_stale_entries_from_other_consumers() -> None:
    record = StreamRecord("ais", "9-1", ais_payload())
    source = FakeStreamSource(
        [], stale_batches=[[PendingRecord(record=record, idle_ms=60_000)]]
    )
    store = FakeStore()

    processed = run_consumer(source, store, streams=("ais",), max_batches=0)

    assert processed == 1
    assert len(store.clean) == 1
    assert source.acks == [("ais", "9-1")]


def test_unexpected_error_is_quarantined_and_acknowledged() -> None:
    source = FakeStreamSource(
        [[StreamRecord("ais", "1-0", ais_payload())]]
    )
    store = FakeStore()
    store.fail_clean_next = True

    consumer = Consumer(source, store, streams=("ais",))
    processed = consumer.run(max_batches=1)

    assert processed == 1
    assert store.raw == {}  # the transaction rolled back
    assert store.clean == {}
    assert len(store.quarantine) == 1
    _, (_, reason) = next(iter(store.quarantine.items()))
    assert reason.startswith("internal error: RuntimeError")
    assert source.acks == [("ais", "1-0")]
    assert consumer.unacknowledged == ()


def test_record_stays_pending_when_quarantine_also_fails() -> None:
    source = FakeStreamSource(
        [[StreamRecord("ais", "1-0", ais_payload())]]
    )
    store = FakeStore()
    store.fail_clean_next = True
    store.fail_quarantine_next = True

    consumer = Consumer(source, store, streams=("ais",))
    processed = consumer.run(max_batches=1)

    assert processed == 0
    assert source.acks == []
    assert consumer.unacknowledged == (("ais", "1-0"),)


def test_old_pending_entry_is_logged_loudly(caplog: pytest.LogCaptureFixture) -> None:
    class Clock:
        now = 0.0

        def __call__(self) -> float:
            return self.now

    clock = Clock()
    consumer = Consumer(
        FakeStreamSource([]),
        FakeStore(),
        pending_warn_after_s=10.0,
        clock=clock,
    )
    consumer._note_unacknowledged(StreamRecord("ais", "1-0", ais_payload()))
    clock.now = 11.0

    with caplog.at_level(logging.ERROR):
        consumer._warn_about_old_pending()

    assert "PENDING TOO LONG" in caplog.text
    assert "ais/1-0" in caplog.text


def test_parse_fields_reads_the_json_data_envelope() -> None:
    payload, error = parse_fields({"data": '{"mmsi": 273456780, "sog": 11.2}'})

    assert error is None
    assert payload == {"mmsi": 273456780, "sog": 11.2}


def test_parse_fields_does_not_sniff_types_from_strings() -> None:
    payload, error = parse_fields({"data": '{"name": "123", "note": "null"}'})

    assert error is None
    assert payload == {"name": "123", "note": "null"}
    assert isinstance(payload["name"], str)
    assert isinstance(payload["note"], str)


def test_parse_fields_rejects_flat_entries() -> None:
    fields = {"mmsi": "273456780", "sog": "11.2", "name": "FIANIT"}

    payload, error = parse_fields(fields)

    assert error == ENVELOPE_ERROR
    assert payload == fields
    assert isinstance(
        process_message("ais", "1-0", payload, FakeStore(), parse_error=error),
        Rejection,
    )


def test_parse_fields_rejects_sibling_fields_next_to_data() -> None:
    payload, error = parse_fields(
        {"data": '{"mmsi": 273456780}', "extra": "unexpected"}
    )

    assert error == ENVELOPE_ERROR
    assert payload["extra"] == "unexpected"


def test_parse_fields_keeps_malformed_data_for_quarantine() -> None:
    payload, error = parse_fields({"data": "{not json"})

    assert error is not None
    assert payload == {"data": "{not json"}


@pytest.mark.parametrize("value", ['[1, 2]', '"text"', "null", "42"])
def test_parse_fields_requires_a_json_object(value: str) -> None:
    payload, error = parse_fields({"data": value})

    assert error == NOT_AN_OBJECT_ERROR
    assert payload == {"data": value}
    assert isinstance(
        process_message("ais", "1-0", payload, FakeStore(), parse_error=error),
        Rejection,
    )


def test_prompt_injection_in_a_name_stays_truncated_data() -> None:
    store = FakeStore()
    name = "ignore previous instructions " + "C" * 400

    process_message("ais", "1-0", ais_payload(name=name), store)

    _, clean_row = next(iter(store.clean.items()))
    assert clean_row["name"] == name[:120]
    assert clean_row["name"].startswith("ignore previous instructions")
    assert store.raw[("ais", "1-0")]["name"] == name
