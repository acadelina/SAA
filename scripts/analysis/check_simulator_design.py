"""Checks behind three design decisions in docs/specs/simulation_rules.md.

Run with:  python -I scripts/analysis/check_simulator_design.py

It answers three questions the spec depends on:

1. How does the clean cooldown change the random baseline? Removal plus
   replacement keeps the shadow pool at S = 50, but the cooldown removes
   inspected clean hulls from the eligible set, so the shadow share among
   eligible hulls rises during a run. That makes later days easier, not
   harder, and the random baseline above 1.0 per day.
2. How many MMSI changes does the real churn rate produce in 30 days? At
   0.65 new MMSIs per hull-year, a within-run churn count is close to zero,
   so the churn feature has to be historical and adaptation has to act on
   the visible history, not on the future rate.
3. What is the power of the success rule "the lower 95% bound is at or above
   +2" when the true gain is exactly +2?

The third check is the reason the success rule in the plan is written as a
one-sided test against zero with a separate smallest effect of interest.
"""

from __future__ import annotations

import random
import statistics

HULLS = 1000
SHADOW = 50
BUDGET = 20
DAYS = 30


def random_run(seed: int, cooldown: int) -> list[int]:
    """One run of a uniformly random picker over eligible hulls.

    Caught shadow hulls are replaced the next day by a fresh shadow hull, so
    the shadow count stays at SHADOW. Inspected clean hulls are blocked for
    `cooldown` days.
    """
    rng = random.Random(seed)
    is_shadow = [False] * HULLS
    for index in rng.sample(range(HULLS), SHADOW):
        is_shadow[index] = True
    blocked_until = [0] * HULLS
    catches: list[int] = []

    for day in range(1, DAYS + 1):
        eligible = [i for i in range(HULLS) if blocked_until[i] <= day]
        caught = 0
        for hull in rng.sample(eligible, BUDGET):
            if is_shadow[hull]:
                caught += 1
            else:
                blocked_until[hull] = day + cooldown
        # Replace each caught hull with a fresh shadow hull in a free slot.
        for _ in range(caught):
            free = [i for i in range(HULLS) if not is_shadow[i]]
            is_shadow[rng.choice(free)] = True
        catches.append(caught)
    return catches


def cooldown_effect() -> None:
    print("Random picker, mean catches per day over 30 days")
    runs = 200
    for cooldown in (30, 7, 5):
        means = [
            statistics.mean(random_run(seed, cooldown)) for seed in range(runs)
        ]
        single = [random_run(seed, cooldown)[0] for seed in range(runs)]
        print(
            f"  cooldown {cooldown:2d} days: 30-day mean {statistics.mean(means):.2f}"
            f"  (day 1: {statistics.mean(single):.2f})"
        )


def churn_arithmetic() -> None:
    per_year = 0.65
    per_day = per_year / 365
    fleet_30 = per_day * DAYS * SHADOW
    change = (1 - 0.30) * fleet_30
    print("\nChurn arithmetic at 0.65 new MMSIs per hull-year")
    print(f"  per hull per day: {per_day:.5f}")
    print(f"  per hull over 30 days: {per_day * DAYS:.4f}")
    print(f"  across 50 shadow hulls over 30 days: {fleet_30:.2f} events")
    print(f"  change caused by the x0.30 adaptation: {change:.2f} events")


def power_of_lower_bound() -> None:
    """P(mean - t * sd / sqrt(n) >= 2) for a true paired gain of `delta`."""
    rng = random.Random(0)
    t_crit = 2.262  # t(0.975, 9)
    reps = 20000
    print("\nPower of 'lower 95% bound at or above +2', 10 paired seeds")
    for sd in (0.5, 1.0):
        for delta in (2.0, 2.5, 3.0):
            passes = 0
            for _ in range(reps):
                diffs = [rng.gauss(delta, sd) for _ in range(10)]
                mean = statistics.mean(diffs)
                bound = mean - t_crit * statistics.stdev(diffs) / 10**0.5
                if bound >= 2:
                    passes += 1
            print(f"  true gain {delta:+.1f}, SD {sd:.1f}: {passes / reps:.3f}")


if __name__ == "__main__":
    cooldown_effect()
    churn_arithmetic()
    power_of_lower_bound()
