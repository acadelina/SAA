"""Time-split backtest for the identity churn signal.

Answer it is testing: using only identity history dated before a cutoff, can
churn rank the hulls a named authority lists after that cutoff, above hulls
that were detained and never listed?

Three steps, each run separately so the data and the analysis can be inspected
apart:

    python -I scripts/backtest/run.py cohort --data-dir data/opensanctions
    python -I scripts/backtest/run.py fetch  --work work --cohort work/cohort.json
    python -I scripts/backtest/run.py report --work work --cutoff 2024-05-31

See docs/backtest/time_split_design.md for the design, which was written before
any control was fetched, and docs/backtest/data_access.md for where the inputs
come from. Raw API responses stay in --work, which .gitignore excludes.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cohort as cohort_module  # noqa: E402
import evaluate  # noqa: E402
from features import FEATURE_NAMES  # noqa: E402
from gfw import fetch_all  # noqa: E402

EXPLORATORY_CUTOFF = "2026-12-31"


def cmd_cohort(args: argparse.Namespace) -> int:
    data = Path(args.data_dir)
    built = cohort_module.build_cohort(
        maritime_csv=data / "maritime.csv",
        entities_path=data / "entities.ftm.json",
        vessels_path=data / "vessels.json",
        control_sample=args.controls,
        seed=args.seed,
    )
    out = Path(args.cohort)
    out.parent.mkdir(parents=True, exist_ok=True)
    cohort_module.save(built, out)
    print(built.summary())
    print(f"written to {out}")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    built = cohort_module.load(Path(args.cohort))
    wanted = cohort_module.dedupe(list(built.positives) + built.controls)
    if args.limit:
        wanted = wanted[: args.limit]
    cache = fetch_all(wanted, Path(args.work) / "gfw_cache.json")
    print(f"cached {len(cache)} hulls in {Path(args.work) / 'gfw_cache.json'}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    built = cohort_module.load(Path(args.cohort))
    cache = json.loads((Path(args.work) / "gfw_cache.json").read_text(encoding="utf-8"))
    cutoff = date.fromisoformat(args.cutoff)

    late = cohort_module.positives_after(built, args.cutoff)
    if len(late) != len(built.positives):
        print(
            f"WARNING: {len(built.positives) - len(late)} positives were listed on or "
            f"before the cutoff and cannot be used; rebuild the cohort.",
            file=sys.stderr,
        )

    hulls = evaluate.build_hulls(built, cache, cutoff)
    print(evaluate.coverage_report(hulls))
    print()
    print("Features, all hulls")
    for result in evaluate.single_feature_results(hulls, tankers_only=False):
        print("  " + result.line())
    print()
    print("Features, tankers only (positives are mostly tankers, so this is the fair cut)")
    for result in evaluate.single_feature_results(hulls, tankers_only=True):
        print("  " + result.line())
    print()

    for tankers_only in (False, True):
        pool = [h for h in hulls if h.tanker] if tankers_only else list(hulls)
        holdout = evaluate.temporal_holdout(pool)
        if holdout is None:
            continue
        scope = "tankers" if tankers_only else "all hulls"
        print(f"Temporal holdout, {scope} (train {holdout.train_size}, test {holdout.test_size})")
        for result in (holdout.model, holdout.churn_only, holdout.exposure_only):
            print("  " + result.line())
        print()

    if args.exploratory_no_cutoff:
        print(
            "EXPLORATORY, not pre-registered: the same features with all history and no "
            "cutoff, to compare against the pilot's reported AUC."
        )
        wide = evaluate.build_hulls(built, cache, date.fromisoformat(EXPLORATORY_CUTOFF))
        for tankers_only in (False, True):
            scope = "tankers" if tankers_only else "all hulls"
            for result in evaluate.single_feature_results(wide, tankers_only=tankers_only):
                if result.name in {"n_mmsi", "mmsi_rate", "n_flags"}:
                    print(f"  {scope:<8s} " + result.line())
        print()

    print("Feature order: " + ", ".join(FEATURE_NAMES))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_cohort = sub.add_parser("cohort", help="build positives and controls")
    p_cohort.add_argument("--data-dir", default="data/opensanctions")
    p_cohort.add_argument("--cohort", default="work/cohort.json")
    p_cohort.add_argument("--controls", type=int, default=800)
    p_cohort.add_argument("--seed", type=int, default=7)
    p_cohort.set_defaults(func=cmd_cohort)

    p_fetch = sub.add_parser("fetch", help="fetch identity history (needs GFW_TOKEN)")
    p_fetch.add_argument("--work", default="work")
    p_fetch.add_argument("--cohort", default="work/cohort.json")
    p_fetch.add_argument("--limit", type=int, default=0)
    p_fetch.set_defaults(func=cmd_fetch)

    p_report = sub.add_parser("report", help="run the pre-registered reports")
    p_report.add_argument("--work", default="work")
    p_report.add_argument("--cohort", default="work/cohort.json")
    p_report.add_argument("--cutoff", default="2024-05-31")
    p_report.add_argument("--exploratory-no-cutoff", action="store_true")
    p_report.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
