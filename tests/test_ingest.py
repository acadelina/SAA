"""Tests for the standalone validation entry point."""

from __future__ import annotations

from shifting_hulls.ingest import Rejection, validate_message
from shifting_hulls.schemas import AisMessage, ComplianceEvent, InspectionResult
from tests.test_schemas import ais_payload, compliance_payload, result_payload


def test_valid_messages_return_the_matching_model() -> None:
    assert isinstance(validate_message("ais", ais_payload()), AisMessage)
    assert isinstance(validate_message("compliance", compliance_payload()), ComplianceEvent)
    assert isinstance(validate_message("results", result_payload()), InspectionResult)


def test_invalid_message_returns_a_rejection_with_the_raw_payload() -> None:
    raw = ais_payload(mmsi="12345")

    result = validate_message("ais", raw)

    assert isinstance(result, Rejection)
    assert "mmsi" in result.reason
    assert result.raw == raw
    assert result.raw is not raw


def test_rejection_reason_names_the_failed_rule() -> None:
    result = validate_message("results", result_payload(imo=9319704))

    assert isinstance(result, Rejection)
    assert "check digit" in result.reason


def test_unknown_stream_is_rejected_instead_of_raising() -> None:
    raw = {"anything": 1}

    result = validate_message("positions", raw)

    assert isinstance(result, Rejection)
    assert "unknown stream" in result.reason
    assert result.raw == raw


def test_rejection_is_frozen_and_carries_no_model() -> None:
    result = validate_message("ais", ais_payload(lat=100))

    assert isinstance(result, Rejection)
    assert set(result.raw) == set(ais_payload())
    assert not hasattr(result, "lat")
