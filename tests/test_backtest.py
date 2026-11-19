"""Tests for the time-split backtest.

None of these touch the network or the real exports. The fixtures are small
synthetic tables shaped like the OpenSanctions and GFW responses, so the tests
check the rules the backtest depends on rather than any particular result.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "backtest"))

import cohort as cohort_module  # noqa: E402
import evaluate  # noqa: E402
import stats  # noqa: E402
from features import (  # noqa: E402
    ARCHIVE_FLOOR,
    MIN_EXPOSURE_YEARS,
    IdentityRecord,
    features_before,
)

CUTOFF = date(2024, 5, 31)


def record(start: str, mmsi: str = "1", name: str = "SHIP", flag: str = "PA",
           callsign: str = "C1") -> IdentityRecord:
    return IdentityRecord(mmsi=mmsi, name=name, flag=flag, callsign=callsign, start=start)


class TestAuc:
    def test_perfect_and_inverted(self) -> None:
        assert stats.auc([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1]) == pytest.approx(1.0)
        assert stats.auc([0.9, 0.8, 0.2, 0.1], [0, 0, 1, 1]) == pytest.approx(0.0)

    def test_chance(self) -> None:
        assert stats.auc([0.5, 0.5, 0.5, 0.5], [0, 1, 0, 1]) == pytest.approx(0.5)

    def test_ties_use_midranks(self) -> None:
        # Positives at rank 2 and 3 in a 4 row set: (2 + 3 - 3) / 4 = 0.5.
        assert stats.auc([0.2, 0.5, 0.5, 0.9], [0, 1, 1, 0]) == pytest.approx(0.5)

    def test_undefined_with_one_class(self) -> None:
        assert math.isnan(stats.auc([0.1, 0.2], [1, 1]))

    def test_length_mismatch(self) -> None:
        with pytest.raises(ValueError):
            stats.auc([0.1, 0.2], [1])

    def test_bootstrap_pairs_scores_with_their_labels(self) -> None:
        # A perfectly separated set must keep an interval at 1.0. This fails if
        # scores and labels are resampled independently.
        low, high = stats.bootstrap_auc_ci([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1], reps=200)
        assert (low, high) == pytest.approx((1.0, 1.0))

    def test_bootstrap_interval_brackets_the_estimate(self) -> None:
        scores = [0.1, 0.4, 0.35, 0.8, 0.7, 0.9, 0.2, 0.6]
        labels = [0, 1, 0, 1, 1, 1, 0, 0]
        low, high = stats.bootstrap_auc_ci(scores, labels, reps=400)
        assert low <= stats.auc(scores, labels) <= high


class TestLogistic:
    def test_recovers_a_separable_boundary(self) -> None:
        rows = [[float(i), 0.0] for i in range(10)] + [[float(i), 10.0] for i in range(10)]
        labels = [0] * 10 + [1] * 10
        model = stats.LogisticModel().fit(rows, labels)
        predicted = model.predict(rows)
        assert all(p < 0.5 for p in predicted[:10])
        assert all(p > 0.5 for p in predicted[10:])

    def test_predictions_are_probabilities(self) -> None:
        rows = [[float(i % 3), float(i % 5)] for i in range(20)]
        labels = [i % 2 for i in range(20)]
        predicted = stats.LogisticModel().fit(rows, labels).predict(rows)
        assert all(0.0 <= p <= 1.0 for p in predicted)

    def test_rejects_empty_input(self) -> None:
        with pytest.raises(ValueError):
            stats.LogisticModel().fit([], [])


class TestFeaturesBeforeCutoff:
    def test_ignores_records_that_start_after_the_cutoff(self) -> None:
        """The whole point: a later MMSI must not appear in the features."""
        records = [
            record("2020-01-01", mmsi="1"),
            record("2024-09-01", mmsi="2"),
        ]
        features = features_before(records, CUTOFF)
        assert features is not None
        assert features.n_mmsi == 1

    def test_record_ending_before_cutoff_still_counts(self) -> None:
        records = [record("2019-01-01", mmsi="1")]
        features = features_before(records, CUTOFF)
        assert features is not None
        assert features.n_mmsi == 1

    def test_no_visible_history_returns_none(self) -> None:
        assert features_before([record("2024-09-01")], CUTOFF) is None
        assert features_before([], CUTOFF) is None

    def test_counts_distinct_values_case_insensitively(self) -> None:
        records = [
            record("2019-01-01", mmsi="1", name="ALPHA", callsign="x1"),
            record("2020-01-01", mmsi="1", name="alpha", callsign="X1"),
            record("2021-01-01", mmsi="2", name="BETA", callsign="X2"),
            record("2022-01-01", mmsi="2", name="ALPHA", callsign="", flag="LR"),
        ]
        features = features_before(records, CUTOFF)
        assert features is not None
        assert features.n_mmsi == 2
        assert features.n_names == 2
        assert features.n_callsigns == 2
        assert features.n_flags == 2

    def test_missing_identifiers_do_not_count_as_a_value(self) -> None:
        records = [record("2019-01-01", mmsi="", name="", flag="", callsign="")]
        features = features_before(records, CUTOFF)
        assert features is not None
        assert features.n_mmsi == 0
        assert features.n_names == 0

    def test_archive_floor_clamps_exposure(self) -> None:
        features = features_before([record(ARCHIVE_FLOOR.isoformat())], CUTOFF)
        assert features is not None
        expected = (CUTOFF - ARCHIVE_FLOOR).days / 365.25
        assert features.years_observed == pytest.approx(expected)

    def test_short_history_gets_the_minimum_exposure(self) -> None:
        features = features_before([record("2024-05-01")], CUTOFF)
        assert features is not None
        assert features.years_observed == MIN_EXPOSURE_YEARS

    def test_rates_are_counts_over_exposure(self) -> None:
        features = features_before([record("2022-05-31", mmsi="1"),
                                    record("2023-01-01", mmsi="2")], CUTOFF)
        assert features is not None
        row = features.row()
        assert row[5] == pytest.approx(features.n_mmsi / features.years_observed)
        assert len(row) == 8

    def test_bad_dates_are_skipped(self) -> None:
        records = [IdentityRecord(mmsi="1", name="A", flag="PA", callsign="C", start="nope")]
        assert features_before(records, CUTOFF) is None


def write_export(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A miniature OpenSanctions export: two dated shadow hulls, one undated, controls."""
    entities = [
        {"schema": "Sanction", "id": "s1",
         "properties": {"entity": ["v1"], "startDate": ["2024-10-01"]}},
        {"schema": "Sanction", "id": "s2",
         "properties": {"entity": ["v2"], "listingDate": ["2024-07-01"]}},
        {"schema": "Sanction", "id": "s3",
         "properties": {"entity": ["v3"]}},
        {"schema": "Vessel", "id": "v9", "properties": {}},
    ]
    entities_path = tmp_path / "entities.ftm.json"
    entities_path.write_text("\n".join(json.dumps(e) for e in entities), encoding="utf-8")

    vessels = [
        {"id": "v1", "properties": {"imoNumber": ["IMO9000001"]}},
        {"id": "v2", "properties": {"imoNumber": ["9000002"]}},
        {"id": "v3", "properties": {"imoNumber": ["9000003"]}},
    ]
    vessels_path = tmp_path / "vessels.json"
    vessels_path.write_text(json.dumps(vessels), encoding="utf-8")

    rows = [
        "name,imo,risk",
        "HULL ONE,IMO9000001,mare.shadow",
        "HULL TWO,IMO9000002,mare.shadow",
        "HULL THREE,IMO9000003,mare.shadow",
        "CLEAN ONE,IMO9100001,mare.detained",
        "CLEAN TWO,IMO9100002,mare.detained",
        "CLEAN THREE,IMO9100003,mare.detained;sanction",
        "NO TAG,IMO9100004,",
    ]
    maritime_path = tmp_path / "maritime.csv"
    maritime_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return maritime_path, entities_path, vessels_path


class TestCohort:
    def test_positives_need_a_shadow_tag_and_a_date(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            paths = write_export(Path(directory))
            built = cohort_module.build_cohort(*paths, control_sample=10, seed=1)
        assert set(built.positives) == {"9000001", "9000002"}
        assert built.positives["9000002"] == "2024-07-01"

    def test_controls_exclude_any_sanctioned_or_shadow_hull(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            paths = write_export(Path(directory))
            built = cohort_module.build_cohort(*paths, control_sample=10, seed=1)
        assert set(built.controls) == {"9100001", "9100002"}

    def test_control_sample_is_deterministic_for_a_seed(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            paths = write_export(Path(directory))
            first = cohort_module.build_cohort(*paths, control_sample=1, seed=7)
            second = cohort_module.build_cohort(*paths, control_sample=1, seed=7)
        assert first.controls == second.controls
        assert len(first.controls) == 1

    def test_earliest_date_wins_per_imo(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            paths = write_export(data)
            entities = [
                {"schema": "Sanction", "id": "a",
                 "properties": {"entity": ["v1"], "startDate": ["2024-12-01"]}},
                {"schema": "Sanction", "id": "b",
                 "properties": {"entity": ["v1"], "startDate": ["2024-08-01"]}},
            ]
            paths[1].write_text("\n".join(json.dumps(e) for e in entities), encoding="utf-8")
            built = cohort_module.build_cohort(*paths, control_sample=10, seed=1)
        assert built.positives["9000001"] == "2024-08-01"

    def test_positives_after_cutoff(self) -> None:
        built = cohort_module.Cohort(
            positives={"1": "2024-03-01", "2": "2024-06-01"}, controls=[]
        )
        assert cohort_module.positives_after(built, "2024-05-31") == ["2"]

    def test_round_trip_through_disk(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cohort.json"
            built = cohort_module.Cohort(positives={"1": "2024-06-01"}, controls=["2"])
            cohort_module.save(built, path)
            assert cohort_module.load(path).positives == {"1": "2024-06-01"}


def payload(n_mmsi: int, mmsi_prefix: str = "7", tanker: bool = True) -> dict:
    records = [
        {"mmsi": f"{mmsi_prefix}{i}", "name": "SHIP", "flag": "PA", "callsign": "C",
         "start": f"{2018 + i}-01-01", "end": None}
        for i in range(n_mmsi)
    ]
    return {"error": None, "records": records,
            "classes": ["tanker"] if tanker else ["cargo"]}


class TestEvaluate:
    def build(self) -> tuple[cohort_module.Cohort, dict[str, dict]]:
        built = cohort_module.Cohort(
            positives={"1000001": "2024-10-01", "1000002": "2024-11-01",
                       "1000003": "2025-02-01"},
            controls=["2000001", "2000002", "2000003"],
        )
        cache = {
            "1000001": payload(4),
            "1000002": payload(3),
            "1000003": payload(5),
            "2000001": payload(1, mmsi_prefix="8"),
            "2000002": payload(1, mmsi_prefix="9"),
            "2000003": payload(2, mmsi_prefix="6"),
        }
        return built, cache

    def test_builds_one_row_per_usable_hull(self) -> None:
        built, cache = self.build()
        hulls = evaluate.build_hulls(built, cache, CUTOFF)
        assert len(hulls) == 6
        assert sum(h.label for h in hulls) == 3

    def test_skips_hulls_with_an_error_or_no_history(self) -> None:
        built, cache = self.build()
        cache["1000001"] = {"error": "HTTP 404", "records": [], "classes": []}
        cache["2000001"] = {"error": None, "records": [], "classes": []}
        assert len(evaluate.build_hulls(built, cache, CUTOFF)) == 4

    def test_ignores_cached_hulls_outside_the_cohort(self) -> None:
        """A leftover from an earlier control draw must not become a control row."""
        built, cache = self.build()
        cache["3000001"] = payload(9, mmsi_prefix="3")
        hulls = evaluate.build_hulls(built, cache, CUTOFF)
        assert "3000001" not in {h.imo for h in hulls}
        assert len(hulls) == 6
        assert sum(1 for h in hulls if h.label == 0) == 3

    def test_cutoff_excludes_later_records(self) -> None:
        built = cohort_module.Cohort(positives={"1": "2024-10-01"}, controls=["2"])
        cache = {
            "1": payload(2),
            # A third MMSI only after the cutoff must not raise the count.
            "2": payload(1, mmsi_prefix="5"),
        }
        cache["1"]["records"].append(
            {"mmsi": "999", "name": "NEW", "flag": "LR", "callsign": "Z",
             "start": "2024-09-01", "end": None}
        )
        hulls = evaluate.build_hulls(built, cache, CUTOFF)
        assert next(h for h in hulls if h.imo == "1").row[0] == 2

    def test_single_feature_results_cover_every_feature(self) -> None:
        built, cache = self.build()
        hulls = evaluate.build_hulls(built, cache, CUTOFF)
        results = evaluate.single_feature_results(hulls, tankers_only=False)
        assert [r.name for r in results] == list(evaluate.FEATURE_NAMES)

    def test_tanker_subset_filters_rows(self) -> None:
        built, cache = self.build()
        cache["2000001"] = payload(1, tanker=False)
        hulls = evaluate.build_hulls(built, cache, CUTOFF)
        tanker_results = evaluate.single_feature_results(hulls, tankers_only=True)
        assert tanker_results[0].n == 5

    def test_churn_separates_shadow_heavy_from_clean(self) -> None:
        built, cache = self.build()
        hulls = evaluate.build_hulls(built, cache, CUTOFF)
        churn = next(
            r for r in evaluate.single_feature_results(hulls, tankers_only=False)
            if r.name == "mmsi_rate"
        )
        assert churn.auc == pytest.approx(1.0)

    def test_temporal_holdout_reports_both_sides_of_the_split(self) -> None:
        built, cache = self.build()
        hulls = evaluate.build_hulls(built, cache, CUTOFF)
        holdout = evaluate.temporal_holdout(hulls)
        assert holdout is not None
        assert holdout.train_size + holdout.test_size == len(hulls)
        # The 2025-02 positive is the only test positive here.
        assert holdout.model.positives == 1

    def test_temporal_holdout_needs_late_positives(self) -> None:
        built = cohort_module.Cohort(positives={"1": "2024-10-01"}, controls=["2"])
        cache = {"1": payload(2), "2": payload(1)}
        hulls = evaluate.build_hulls(built, cache, CUTOFF)
        assert evaluate.temporal_holdout(hulls) is None

    def test_coverage_report_mentions_both_groups(self) -> None:
        built, cache = self.build()
        text = evaluate.coverage_report(evaluate.build_hulls(built, cache, CUTOFF))
        assert "positives 3" in text
        assert "controls 3" in text
