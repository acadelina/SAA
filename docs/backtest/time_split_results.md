# Time split backtest: results

Produced by the code in `scripts/backtest/` against the data described in
[data_access.md](data_access.md), following [time_split_design.md](time_split_design.md),
which was written before any control was fetched.

Data: OpenSanctions maritime export of 17 November 2026, and Global Fishing Watch
vessel identity records fetched the same day.

## Cohort

| Group | Hulls | Usable before the cutoff |
| --- | --- | --- |
| Positives, shadow tagged with a dated listing | 182 | 182 |
| Controls, detained and never listed | 800 | 766 |
| Tankers | 303 | 182 positives, 121 controls |

Every positive is listed after the cutoff of 2024-05-31. 34 controls are dropped
because they have no identity history before it.

## The falsifier

The design fixed one test in advance: if the lower 95 percent bound of the
primary feature's AUC within tankers were below 0.60, the claim would not stand.

**It is below 0.60. The falsifier fires.**

`mmsi_rate` within tankers scores 0.557, with a 95 percent interval of
[0.484, 0.629].

## Single features, tankers only

This is the fair comparison, because 182 of 182 positives carry a tanker class
and only 121 of 766 controls do.

| Feature | AUC | 95 percent interval |
| --- | --- | --- |
| `n_callsigns` | 0.651 | [0.584, 0.716] |
| `n_flags` | 0.649 | [0.584, 0.713] |
| `n_mmsi` | 0.620 | [0.553, 0.686] |
| `years_observed` | 0.594 | [0.529, 0.662] |
| `flag_rate` | 0.589 | [0.518, 0.660] |
| `n_names` | 0.565 | [0.502, 0.628] |
| `mmsi_rate` | 0.557 | [0.484, 0.629] |
| `name_rate` | 0.497 | [0.430, 0.566] |

History length alone, which is not a behavioural signal at all, scores 0.594.
Every churn count scores within about 0.06 of it. Within one ship type, churn
as of the cutoff barely separates the two groups better than simply knowing how
long each hull has been observed.

## Single features, all hulls

| Feature | AUC | 95 percent interval |
| --- | --- | --- |
| `n_flags` | 0.733 | [0.700, 0.765] |
| `n_callsigns` | 0.723 | [0.688, 0.755] |
| `n_mmsi` | 0.695 | [0.659, 0.729] |
| `flag_rate` | 0.662 | [0.625, 0.697] |
| `mmsi_rate` | 0.622 | [0.583, 0.660] |
| `years_observed` | 0.609 | [0.568, 0.649] |
| `n_names` | 0.607 | [0.563, 0.651] |
| `name_rate` | 0.535 | [0.488, 0.580] |

These are higher, but the contrast is mostly ship type: it is not a measure of
how well the ranking would work.

## Temporal holdout

Train on positives listed 2024-06 to 2024-12, test on positives listed 2025-02.

| Model | All hulls | Tankers only |
| --- | --- | --- |
| Logistic, all features | 0.776 [0.707, 0.834] | 0.642 [0.540, 0.737] |
| Churn rate only | 0.663 [0.606, 0.720] | 0.579 [0.478, 0.683] |
| History length only | 0.592 [0.528, 0.655] | 0.609 [0.509, 0.703] |

Across all hulls a model using everything reaches 0.78. Within tankers it
reaches 0.64, and its interval covers the 0.61 that history length alone gets.
Only the positives are split by date here, so this is a weak temporal test.

## Where the pilot figure comes from

Exploratory, not part of the pre-registered design: the same features computed
over each hull's whole identity history, with no cutoff.

| Feature | All hulls | Tankers only |
| --- | --- | --- |
| `n_mmsi` | 0.901 [0.876, 0.926] | 0.862 [0.819, 0.900] |
| `n_flags` | 0.920 [0.896, 0.942] | 0.877 [0.837, 0.913] |
| `mmsi_rate` | **0.840 [0.812, 0.866]** | **0.776 [0.716, 0.831]** |

The plan reports an AUC of 0.84 for the churn count, falling to 0.77 within
tankers. Dropping the cutoff reproduces 0.840 and 0.776.

Two different cohorts and two different control sets landing on the same two
numbers is unlikely to be a coincidence. The most plausible reading is that the
pilot's churn figures were computed over each hull's full identity history,
which for a positive includes everything since it was listed. That is the
mechanism the design set out to test, and this is what it looks like when the
cutoff is removed.

It is not proof: the cohorts differ (149 hulls per side in the pilot, 182 and
766 here) and the reproduction is descriptive.

## What this means

1. **The 0.84 should not be quoted as a prospective number.** It is what the
   rule scores when it is allowed to see the period after listing. At the time a
   decision would have to be made, the same rule scores about 0.56 to 0.62
   within tankers.
2. **The plan's timing caveat is now quantified.** Section 1 already said churn
   may be a consequence of listing rather than a precursor. This measures the
   gap: roughly 0.20 to 0.25 AUC.
3. **The simulator should not be calibrated to 0.65 against 0.18 per
   vessel-year** as if that contrast were visible in advance. Whatever the
   simulator plants, its churn feature should not be stronger than the real
   signal supports, or the agent will be given an easier problem than the office
   has.
4. **It does not settle whether the agent idea is good.** The same result leaves
   more room for a method that uses things this backtest cannot see: text,
   cross stream links, and the interaction with an inspector who adapts. That is
   an argument, not a measurement, and the plan should say so.

## Threats to this result

- **Retro-linking.** GFW links an MMSI to an IMO using knowledge it has today,
  so old records may be attached to a hull only because of later information.
  The direction of the bias is unknown and cannot be sized from this data.
- **Different populations.** Controls are hulls that were detained and never
  listed. Positives are hulls a named authority listed. A detector for the
  office's real fleet is not the same object as a detector for this contrast.
- **Batch listings.** Positives arrive in a few groups, so 182 hulls are not 182
  independent events and the intervals are optimistic.
- **One cutoff**, and a single control draw with a fixed seed.
- **The label** means "a named authority listed this hull", not "this hull is
  shadow fleet".
