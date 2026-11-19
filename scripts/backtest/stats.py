"""Ranking statistics for the time-split backtest, standard library only.

The point of the backtest is a small, reproducible comparison, so it does not
justify pulling numpy and scikit-learn into the project. This module holds the
three pieces needed: AUC with a bootstrap interval, a small logistic regression
solved by IRLS, and the helpers that pair them up on a subset of rows.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence


def auc(scores: Sequence[float], labels: Sequence[int]) -> float:
    """Area under the ROC curve, ties handled with mid-ranks.

    Returns nan when only one class is present, because the curve is undefined.
    """
    if len(scores) != len(labels):
        raise ValueError("scores and labels must have the same length")
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return math.nan

    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        mid = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = mid
        i = j + 1

    positive_rank_sum = sum(r for r, label in zip(ranks, labels, strict=True) if label)
    return (positive_rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives)


def bootstrap_auc_ci(
    scores: Sequence[float],
    labels: Sequence[int],
    reps: int = 2000,
    seed: int = 0,
    level: float = 0.95,
) -> tuple[float, float]:
    """Percentile bootstrap interval over rows, resampling labels and scores together."""
    rng = random.Random(seed)
    n = len(scores)
    if n == 0:
        return math.nan, math.nan
    draws: list[float] = []
    for _ in range(reps):
        # One index draw, used for scores and labels alike, or the resample
        # would pair each score with an unrelated label.
        idx = [rng.randrange(n) for _ in range(n)]
        value = auc([scores[i] for i in idx], [labels[i] for i in idx])
        if not math.isnan(value):
            draws.append(value)
    if not draws:
        return math.nan, math.nan
    draws.sort()
    tail = (1.0 - level) / 2.0
    return _percentile(draws, tail), _percentile(draws, 1.0 - tail)


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        return math.nan
    pos = q * (len(sorted_values) - 1)
    low = int(math.floor(pos))
    high = min(low + 1, len(sorted_values) - 1)
    frac = pos - low
    return sorted_values[low] * (1.0 - frac) + sorted_values[high] * frac


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting for a small dense system."""
    n = len(vector)
    a = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ValueError("singular matrix")
        a[col], a[pivot] = a[pivot], a[col]
        lead = a[col][col]
        for r in range(col + 1, n):
            factor = a[r][col] / lead
            if factor == 0.0:
                continue
            for c in range(col, n + 1):
                a[r][c] -= factor * a[col][c]
    solution = [0.0] * n
    for r in range(n - 1, -1, -1):
        residual = a[r][n] - sum(a[r][c] * solution[c] for c in range(r + 1, n))
        solution[r] = residual / a[r][r]
    return solution


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


class LogisticModel:
    """Logistic regression with an intercept, ridge penalty and IRLS fitting.

    Features are standardised inside the model so the penalty is comparable
    across columns and the fit is well conditioned.
    """

    def __init__(self, ridge: float = 1e-3, iterations: int = 100) -> None:
        self.ridge = ridge
        self.iterations = iterations
        self.means: list[float] = []
        self.scales: list[float] = []
        self.weights: list[float] = []
        self.intercept = 0.0

    def fit(self, rows: Sequence[Sequence[float]], labels: Sequence[int]) -> LogisticModel:
        if len(rows) != len(labels):
            raise ValueError("rows and labels must have the same length")
        if not rows:
            raise ValueError("cannot fit on no rows")
        width = len(rows[0])
        self.means = [sum(r[c] for r in rows) / len(rows) for c in range(width)]
        self.scales = []
        for c in range(width):
            var = sum((r[c] - self.means[c]) ** 2 for r in rows) / len(rows)
            self.scales.append(math.sqrt(var) or 1.0)

        design = [[1.0] + [(r[c] - self.means[c]) / self.scales[c] for c in range(width)]
                  for r in rows]
        params = [0.0] * (width + 1)
        for _ in range(self.iterations):
            probs = [_sigmoid(sum(a * b for a, b in zip(row, params, strict=True)))
                     for row in design]
            weights = [max(p * (1.0 - p), 1e-6) for p in probs]
            grad = [
                sum(row[i] * (label - prob) for row, prob, label
                    in zip(design, probs, labels, strict=True))
                - (0.0 if i == 0 else self.ridge * params[i])
                for i in range(width + 1)
            ]
            hess = []
            for i in range(width + 1):
                row = []
                for j in range(width + 1):
                    value = sum(design[r][i] * weights[r] * design[r][j]
                                for r in range(len(design)))
                    if i == j and i > 0:
                        value += self.ridge
                    row.append(value)
                hess.append(row)
            try:
                step = _solve(hess, grad)
            except ValueError:
                break
            params = [p + s for p, s in zip(params, step, strict=True)]
            if max(abs(s) for s in step) < 1e-8:
                break

        self.intercept = params[0]
        self.weights = params[1:]
        return self

    def predict(self, rows: Sequence[Sequence[float]]) -> list[float]:
        return [
            _sigmoid(self.intercept + sum(
                w * ((r[c] - self.means[c]) / self.scales[c])
                for c, w in enumerate(self.weights)
            ))
            for r in rows
        ]
