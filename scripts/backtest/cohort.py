"""Build the labelled cohort from an OpenSanctions export.

Two groups, both keyed by IMO so the same key survives an MMSI change:

* positives: hulls tagged as shadow fleet that also carry a real designation
  date from a named authority. The date is what makes a time split possible.
  Note the label is "a named authority listed this hull", which is not the same
  thing as "this hull is shadow fleet"; see docs/backtest/time_split_design.md.
* controls: hulls that appear in a port-state detention register and were never
  sanctioned or shadow tagged. They are a different population from the
  positives, which is a limitation of the comparison, not a detail.

The export layout this reads is described in docs/backtest/data_access.md.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

SANCTION_TAGS = frozenset({"sanction", "mare.shadow"})
CONTROL_TAGS = frozenset({"mare.detained"})


@dataclass
class Cohort:
    positives: dict[str, str] = field(default_factory=dict)
    controls: list[str] = field(default_factory=list)

    def listing_months(self) -> list[tuple[str, int]]:
        return sorted(Counter(d[:7] for d in self.positives.values()).items())

    def summary(self) -> str:
        months = ", ".join(f"{m}: {n}" for m, n in self.listing_months())
        return (
            f"positives {len(self.positives)} ({months})\n"
            f"controls {len(self.controls)}"
        )


def _iter_jsonl(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _imo_from(value: str) -> str:
    """Normalise both 'IMO9123456' and '9123456' to bare digits."""
    return value.replace("IMO", "").strip()


def listing_dates(entities_path: Path, vessels_path: Path) -> dict[str, str]:
    """Earliest designation date per IMO, from Sanction records attached to vessels."""
    by_entity: dict[str, list[str]] = {}
    for entity in _iter_jsonl(entities_path):
        if entity.get("schema") != "Sanction":
            continue
        dates = [
            value
            for prop in ("startDate", "listingDate")
            for value in entity.get("properties", {}).get(prop, [])
        ]
        if not dates:
            continue
        for target in entity.get("properties", {}).get("entity", []):
            by_entity.setdefault(target, []).extend(dates)

    imo_by_entity: dict[str, str] = {}
    for vessel in json.loads(vessels_path.read_text(encoding="utf-8")):
        properties = vessel.get("properties", {})
        imos = properties.get("imoNumber") or []
        if imos:
            imo_by_entity[vessel["id"]] = _imo_from(imos[0])

    out: dict[str, str] = {}
    for entity_id, dates in by_entity.items():
        imo = imo_by_entity.get(entity_id)
        if not imo:
            continue
        earliest = min(dates)
        previous = out.get(imo)
        out[imo] = earliest if previous is None else min(previous, earliest)
    return out


def risk_tags(maritime_csv: Path) -> Iterator[tuple[str, set[str]]]:
    """Yield (IMO, tags) for every row of the simple maritime export."""
    with maritime_csv.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            imo = _imo_from(row.get("imo") or "")
            if not imo:
                continue
            yield imo, {t for t in (row.get("risk") or "").split(";") if t}


def valid_imo(imo: str) -> bool:
    return len(imo) == 7 and imo.isdigit()


def build_cohort(
    maritime_csv: Path,
    entities_path: Path,
    vessels_path: Path,
    control_sample: int = 800,
    seed: int = 7,
) -> Cohort:
    """Positives with a dated listing, plus a random sample of never-listed controls."""
    dates = listing_dates(entities_path, vessels_path)

    shadow: set[str] = set()
    sanctioned_or_shadow: set[str] = set()
    detained: set[str] = set()
    for imo, tags in risk_tags(maritime_csv):
        if tags & SANCTION_TAGS:
            sanctioned_or_shadow.add(imo)
        if "mare.shadow" in tags:
            shadow.add(imo)
        if tags & CONTROL_TAGS:
            detained.add(imo)

    positives = {imo: date for imo, date in dates.items() if imo in shadow}
    candidates = sorted(
        imo for imo in detained - sanctioned_or_shadow - set(dates) if valid_imo(imo)
    )

    import random

    rng = random.Random(seed)
    controls = sorted(rng.sample(candidates, min(control_sample, len(candidates))))
    return Cohort(positives=positives, controls=controls)


def save(cohort: Cohort, path: Path) -> None:
    path.write_text(json.dumps(cohort.__dict__, indent=1), encoding="utf-8")


def load(path: Path) -> Cohort:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Cohort(positives=raw["positives"], controls=raw["controls"])


def positives_after(cohort: Cohort, cutoff: str) -> list[str]:
    """Positives listed strictly after the cutoff; the only honest ones for a time split."""
    return sorted(imo for imo, when in cohort.positives.items() if when[:10] > cutoff)


def dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out
