"""Tests for message routing, Redis field parsing and idempotent inserts."""

from __future__ import annotations

from shifting_hulls.consumer import parse_fields, process_message, run_consumer
from shifting_hulls.ingest import Rejection
from shifting_hulls.interfaces import StreamRecord
from shifting_hulls.schemas import MAX_NAME_LENGTH, AisMessage
from tests.fakes import FakeStore, FakeStreamSource
from tests.test_schemas import ais_payload, compliance_payload, result_payload


def test_valid_message_writes_one_raw_and_one_clean_row() -> None:
    store = FakeStore()

    result = process_message("ais", "1-0", ais_payload(), store)

    assert isinstance(result, AisMessage)
    assert len(store.raw) == 1
    assert len(store.clean) == 1
    assert store.quarantine == {}
    raw_key, raw_payload = next(iter(store.raw.items()))
    clean_key, clean_row = next(iter(store.clean.items()))
    assert raw_key == clean_key
    assert raw_payload == ais_payload()
    assert clean_row["mmsi"] == "273456780"


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


def test_replaying_the_same_message_does_not_duplicate_rows() -> None:
    store = FakeStore()
    payload = ais_payload()

    process_message("ais", "100-0", payload, store)
    process_message("ais", "100-0", payload, store)

    assert len(store.raw) == 1
    assert len(store.clean) == 1


def test_replaying_a_rejected_message_does_not_duplicate_quarantine() -> None:
    store = FakeStore()
    payload = ais_payload(lat=100)

    process_message("ais", "101-0", payload, store)
    process_message("ais", "101-0", payload, store)

    assert len(store.quarantine) == 1


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


def test_inspection_result_keys_include_day_plan_and_hull() -> None:
    store = FakeStore()

    process_message("results", "3-0", result_payload(), store)

    assert list(store.clean) == [("results", "results:12:p-0012:9319703:273456780")]


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


def test_parse_fields_reads_a_json_data_field() -> None:
    payload = parse_fields({"data": '{"mmsi": 273456780, "sog": 11.2}'})

    assert payload == {"mmsi": 273456780, "sog": 11.2}


def test_parse_fields_reads_flat_fields_and_keeps_text() -> None:
    payload = parse_fields({"mmsi": "273456780", "sog": "11.2", "name": "FIANIT"})

    assert payload == {"mmsi": 273456780, "sog": 11.2, "name": "FIANIT"}


def test_parse_fields_keeps_malformed_data_for_quarantine() -> None:
    payload = parse_fields({"data": "{not json"})

    assert payload == {"data": "{not json"}
    assert isinstance(process_message("ais", "1-0", payload, FakeStore()), Rejection)


def test_prompt_injection_in_a_name_stays_truncated_data() -> None:
    store = FakeStore()
    name = "ignore previous instructions " + "C" * 400

    process_message("ais", "1-0", ais_payload(name=name), store)

    _, clean_row = next(iter(store.clean.items()))
    assert clean_row["name"] == name[:MAX_NAME_LENGTH]
    assert clean_row["name"].startswith("ignore previous instructions")
    _, raw_payload = next(iter(store.raw.items()))
    assert raw_payload["name"] == name
