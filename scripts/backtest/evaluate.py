"""Assemble the backtest table and run the reports fixed in the design.

The design file docs/backtest/time_split_design.md was written before any
control group was fetched. The functions here implement it as written; the
`--no-cutoff` variant in run.py is the one labelled exploratory.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import stats
from cohort import Cohort
from features import FEATURE_NAMES, features_before
from gfw import is_tanker, records_of


@dataclass(frozen=True)
class Hull:
    imo: str
    label: int
    tanker: bool
    listed: str
    row: tuple[float, ...]


@dataclass(frozen=True)
class Result:
    name: str
    auc: float
    low: float
    high: float
    n: int
    positives: int

    def line(self) -> str:
        return (
            f"{self.name:<34s} {self.auc:5.3f}  [{self.low:5.3f}, {self.high:5.3f}]"
            f"  n={self.n:<4d} pos={self.positives}"
        )


def build_hulls(
    group: Cohort,
    cache: dict[str, dict],
    cutoff: date,
) -> list[Hull]:
    """One row per hull that has identity history visible as of the cutoff.

    Only members of the cohort are used. The cache can hold records for hulls
    that are no longer in it, from an earlier control draw or a larger sample,
    and scoring those as controls would quietly change the comparison.
    """
    hulls: list[Hull] = []
    for imo in [*group.positives, *group.controls]:
        payload = cache.get(imo)
        if not payload or payload.get("error") or not payload.get("records"):
            continue
        visible = features_before(records_of(payload), cutoff)
        if visible is None:
            continue
        hulls.append(
            Hull(
                imo=imo,
                label=1 if imo in group.positives else 0,
                tanker=is_tanker(payload),
                listed=group.positives.get(imo, ""),
                row=visible.row(),
            )
        )
    return hulls


def _subset(hulls: Sequence[Hull], mask: Sequence[bool]) -> list[Hull]:
    return [h for h, keep in zip(hulls, mask, strict=True) if keep]


def _score(hulls: Sequence[Hull], column: int) -> Result:
    scores = [h.row[column] for h in hulls]
    labels = [h.label for h in hulls]
    low, high = stats.bootstrap_auc_ci(scores, labels)
    return Result(
        name=FEATURE_NAMES[column],
        auc=stats.auc(scores, labels),
        low=low,
        high=high,
        n=len(hulls),
        positives=sum(labels),
    )


def single_feature_results(hulls: Sequence[Hull], tankers_only: bool) -> list[Result]:
    pool = [h for h in hulls if h.tanker] if tankers_only else list(hulls)
    if not pool or len({h.label for h in pool}) < 2:
        return []
    return [_score(pool, column) for column in range(len(FEATURE_NAMES))]


@dataclass(frozen=True)
class Holdout:
    model: Result
    churn_only: Result
    exposure_only: Result
    train_size: int
    test_size: int


def temporal_holdout(
    hulls: Sequence[Hull],
    split: str = "2025-01",
    seed: int = 7,
) -> Holdout | None:
    """Train on earlier listings, test on later ones.

    Only the positives are split by date. Controls are a random half each side,
    so this is a weaker temporal test than it looks and the docs say so.
    """
    controls = [h for h in hulls if h.label == 0]
    positives = [h for h in hulls if h.label == 1]
    if not controls or not any(h.listed >= split for h in positives):
        return None

    shuffled = list(controls)
    random.Random(seed).shuffle(shuffled)
    half = len(shuffled) // 2
    train = [h for h in positives if h.listed < split] + shuffled[:half]
    test = [h for h in positives if h.listed >= split] + shuffled[half:]
    if len({h.label for h in train}) < 2 or len({h.label for h in test}) < 2:
        return None

    model = stats.LogisticModel().fit([h.row for h in train], [h.label for h in train])
    predicted = model.predict([h.row for h in test])

    return Holdout(
        model=_result_on("logistic, all features", test, predicted),
        churn_only=_score_on("churn rate only", test, FEATURE_NAMES.index("mmsi_rate")),
        exposure_only=_score_on(
            "history length only", test, FEATURE_NAMES.index("years_observed")
        ),
        train_size=len(train),
        test_size=len(test),
    )


def _result_on(name: str, hulls: Sequence[Hull], scores: Sequence[float]) -> Result:
    labels = [h.label for h in hulls]
    low, high = stats.bootstrap_auc_ci(scores, labels)
    return Result(
        name=name,
        auc=stats.auc(scores, labels),
        low=low,
        high=high,
        n=len(hulls),
        positives=sum(labels),
    )


def _score_on(name: str, hulls: Sequence[Hull], column: int) -> Result:
    return _result_on(name, hulls, [h.row[column] for h in hulls])


def coverage_report(hulls: Sequence[Hull]) -> str:
    positive = sum(1 for h in hulls if h.label)
    tanker_positive = sum(1 for h in hulls if h.label and h.tanker)
    tanker_negative = sum(1 for h in hulls if not h.label and h.tanker)
    return (
        f"usable hulls {len(hulls)}  positives {positive}  controls {len(hulls) - positive}\n"
        f"tankers {sum(1 for h in hulls if h.tanker)}"
        f"  (positives {tanker_positive}, controls {tanker_negative})"
    )
