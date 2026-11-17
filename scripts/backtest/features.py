"""Identity features computed only from records that start before a cutoff.

This is the part that makes the backtest honest. Every feature here is a count
of something visible in a hull's identity history as of the cutoff, so nothing
that happens after the cutoff can leak in. The module is pure: it takes records
and a cutoff date and returns numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# GFW clamps the start of some identity records to the beginning of its AIS
# archive. Hulls whose history opens at the clamp cannot be told apart from
# hulls that genuinely started then, so exposure is measured from the floor for
# everyone rather than letting the clamp sort hulls by age.
ARCHIVE_FLOOR = date(2012, 1, 1)
MIN_EXPOSURE_YEARS = 0.25

FEATURE_NAMES = (
    "n_mmsi",
    "n_names",
    "n_flags",
    "n_callsigns",
    "years_observed",
    "mmsi_rate",
    "name_rate",
    "flag_rate",
)


@dataclass(frozen=True)
class IdentityRecord:
    """One self-reported identity period for a hull, as GFW returns it."""

    mmsi: str | None
    name: str | None
    flag: str | None
    callsign: str | None
    start: str | None
    end: str | None = None


@dataclass(frozen=True)
class IdentityFeatures:
    n_mmsi: int
    n_names: int
    n_flags: int
    n_callsigns: int
    years_observed: float

    def row(self) -> tuple[float, ...]:
        y = self.years_observed
        return (
            float(self.n_mmsi),
            float(self.n_names),
            float(self.n_flags),
            float(self.n_callsigns),
            y,
            self.n_mmsi / y,
            self.n_names / y,
            self.n_flags / y,
        )


def _start_of(record: IdentityRecord) -> date | None:
    if not record.start:
        return None
    try:
        return date.fromisoformat(record.start[:10])
    except ValueError:
        return None


def features_before(
    records: list[IdentityRecord], cutoff: date
) -> IdentityFeatures | None:
    """Features from records that begin on or before `cutoff`, or None if there are none.

    A hull with no pre-cutoff identity history cannot be scored prospectively,
    so it is dropped rather than scored as zero churn.
    """
    visible = [r for r in records if (start := _start_of(r)) is not None and start <= cutoff]
    if not visible:
        return None

    first = min(s for r in visible if (s := _start_of(r)) is not None)
    first = max(first, ARCHIVE_FLOOR)
    years = max((cutoff - first).days / 365.25, MIN_EXPOSURE_YEARS)

    return IdentityFeatures(
        n_mmsi=len({r.mmsi for r in visible if r.mmsi}),
        n_names=len({r.name.strip().upper() for r in visible if r.name and r.name.strip()}),
        n_flags=len({r.flag for r in visible if r.flag}),
        n_callsigns=len({r.callsign.strip().upper() for r in visible
                         if r.callsign and r.callsign.strip()}),
        years_observed=years,
    )
