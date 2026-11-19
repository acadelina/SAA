# Time split backtest: design, fixed before any result

This file records a design decision made before the analysis ran, so that the
result cannot be explained by choices made after seeing it.

## The question

The plan claims a churn based ranking. The pilot supporting it reported an AUC
of 0.84 for a rule that counts new MMSIs per hull-year, against 0.65 for hulls
that were later listed and 0.18 for hulls that were not.

That comparison does not say whether the ranking would have worked at the time.
Both sides of it are computed from identity history that includes everything
that happened after a hull was listed, so some of the churn it counts may be a
consequence of being listed, not a precursor of it.

The question this backtest answers:

> Using only identity history dated before a cutoff, can churn rank the hulls a
> named authority lists after that cutoff, above hulls that were detained and
> never listed?

## Labels

Both groups are keyed by IMO, which survives an MMSI change.

- **Positives (182 hulls).** Tagged `mare.shadow` in the OpenSanctions maritime
  export, and carrying a real designation date (`startDate` or `listingDate`) on
  a Sanction record from a named authority. The date is what makes a time split
  possible, which is why hulls without one are excluded.
  **The label means "a named authority listed this hull", not "this hull is
  shadow fleet".** Every conclusion is about the first thing, not the second.
- **Controls (800 hulls, seed 7).** Appearing in a port state detention register
  (`mare.detained`), and never sanctioned, never shadow tagged, and never
  carrying a designation date. They are therefore hulls that were inspected,
  found wanting, and not listed.

The two groups come from different populations. A detector tuned on this
contrast is not the same object as a detector for the fleet the office actually
sees. This is a limitation of the comparison and is reported as one.

## Cutoff

`2024-05-31`, the day before the first dated listing in the cohort. Every
positive is listed after it, so the whole positive set is genuinely out of
sample for the features.

## Features

Computed only from GFW identity records whose `transmissionDateFrom` is on or
before the cutoff. Nothing else is allowed in.

| Feature | What it counts |
| --- | --- |
| `n_mmsi` | distinct MMSIs |
| `n_names` | distinct ship names, upper-cased |
| `n_flags` | distinct flags |
| `n_callsigns` | distinct callsigns, upper-cased |
| `years_observed` | exposure from the first visible record to the cutoff |
| `mmsi_rate`, `name_rate`, `flag_rate` | the counts above divided by exposure |

Two details that matter:

- **Exposure floor.** GFW clamps the start of some identity records to the
  beginning of its AIS archive, 1 January 2012. Hulls whose history opens at the
  clamp cannot be told apart from hulls that genuinely started then, so exposure
  is measured from the clamp for everyone instead of letting the clamp sort
  hulls by age. Exposure is floored at three months so a hull with a very short
  history does not produce a rate that divides by nearly zero.
- **History length is scored on its own.** A hull with a longer history shows
  more identity changes whether or not it is shadow fleet. If `years_observed`
  scores about as well as churn, the churn result is mostly a history length
  effect and the finding is not about behaviour.

## Reports

1. Each feature alone, as an AUC with a 95 percent bootstrap interval over
   hulls (2000 resamples).
2. The same, within tankers only. Positives are mostly tankers and controls are
   mostly not, so the comparison across all hulls partly measures ship type.
   The tanker cut is the fair comparison.
3. A temporal holdout: train on positives listed 2024-06 to 2024-12, test on
   positives listed 2025-02. Only the positives are split by date, so this is a
   weaker temporal test than it looks.

## The falsifier, written in advance

If the lower 95 percent bound of the primary feature's AUC **within tankers** is
below 0.60, the claim that real data carries the ranking rests on the pilot
figure is not supported, and the plan should say so.

The primary feature is `mmsi_rate`, the rate version of the churn rule the plan
names. `n_mmsi` is reported alongside it because the plan's rule is a count.

## Known threats, reported and not fixed

- **Retro-linking.** GFW links MMSIs to an IMO using knowledge it has today. Old
  records may be attached to a hull only because of later information. This
  works in an unknown direction and cannot be sized from this data.
- **Different populations.** See the labels section.
- **Batch listings.** Positives arrive in a few large groups, so 182 hulls are
  not 182 independent events, and the bootstrap interval is optimistic.
- **Small tanker control group.** Only a fraction of controls are tankers.
- **One cutoff.** A single split, not a rolling one.
