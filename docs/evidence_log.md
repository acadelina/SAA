# Evidence log

Every number the Milestone 2 plan relies on, with where it came from and what limits it. Sources are the pilot script, an external source, or "team count, unverified", meaning a figure reported by the team that has not been independently re-checked.

| Claim | Value | Source | Measured how | Caveat |
|---|---|---|---|---|
| Pilot group size, shadow-tagged hulls | 149 hulls | Pilot script | Global Fishing Watch identities joined to OpenSanctions *shadow fleet* labels | Labels are today's list; the group is not a random fleet sample |
| Pilot group size, comparison hulls | 149 hulls | Pilot script | Hulls tagged *detained* only, matched by IMO | Detained hulls are not a random fleet sample |
| New MMSIs per vessel-year, shadow group | 0.65 | Pilot script | MMSI changes in the GFW history divided by vessel-years | Measured today, after designation |
| New MMSIs per vessel-year, comparison group | 0.18 | Pilot script | Same method as the shadow group | Measured today, after designation |
| Churn ratio between groups | 3.6 times | Pilot script | 0.65 divided by 0.18 | Rounded; sensitive to the two group means |
| One-feature AUC for MMSI churn | 0.84 | Pilot script | Churn count used as the only score | Mixes ship types; see the tanker figure below |
| One-feature AUC within tankers | 0.77 | Pilot script | Same score, restricted to tanker hulls | Only 23 tanker controls |
| Tanker share of shadow group | 97% | Pilot script | Ship type in the GFW records | The groups differ strongly in ship type |
| Tanker share of comparison group | 15% | Pilot script | Ship type in the GFW records | Same |
| Tanker controls | 23 hulls | Pilot script | Count of comparison hulls typed as tankers | Small sample for the within-tanker AUC |
| Identity history of IMO 9319703 | 10 MMSIs, 7 names, 8 flags, 2012 to 2026 | External | GFW identity history for one hull | Stated in the plan, not re-checked in this pass |
| Pilot gradient-boosted AUC | 0.88 | Pilot script | Model using all eight features, including port arrivals and loitering | Not the plan's scorer; the plan drops those two features |
| Pilot top-20 catches, random | 1.0 per day | Pilot script | Simulation with N = 1000, K = 20, 5% prevalence | One pilot configuration, not the main experiment |
| Pilot top-20 catches, churn rule | 12.5 per day | Pilot script | Best threshold on MMSI count, same simulation | Same |
| Pilot top-20 catches, gradient-boosted scorer | 9.4 per day | Pilot script | Top-K by model score, same simulation | Model used all eight features |
| Wasted slots, churn rule | 7.5 of 20 | Pilot script | 20 minus 12.5 | Arithmetic on the pilot catch counts |
| Wasted slots, scorer | 10.6 of 20 | Pilot script | 20 minus 9.4 | Arithmetic on the pilot catch counts |
| Paired difference, churn rule minus scorer | 2.8 catches | Pilot script | Paired resampling of the pilot top-20 simulation | 95% interval -1 to +7, includes zero; only 149 control hulls resampled |
| Paired difference, scorer minus churn rule | -2.8 catches | Pilot script | Sign flip of the rule-minus-scorer difference | 95% interval -7 to +1; ordering not established |
| Concurrent MMSIs, GFW registry table | About 2 to 3 of 149 hulls | Pilot script | Two MMSIs overlapping over 30 days in the GFW registry table | Registry table only; not the source of the churn features |
| Concurrent MMSIs, self-reported table, shadow | 30 of 149 hulls | Pilot script | Two MMSIs active at the same time in the self-reported table | This is the table the churn features come from |
| Concurrent MMSIs, self-reported table, comparison | 23 of 149 hulls | Pilot script | Same method | Same table |
| OpenSanctions vessel count | 21,054 vessels | Team count, unverified | Team query of the maritime dataset, snapshot 15 Nov 2026 | Not re-checked |
| OpenSanctions shadow-fleet tags | 896 vessels | Team count, unverified | Team query of the maritime dataset, snapshot 15 Nov 2026 | Not re-checked |
| Danish AIS one day, volume | 16.1 million messages, 3,292 vessels | Team count, unverified | Count of rows and distinct MMSIs, 10 Nov 2026 file | Not re-checked |
| Danish AIS missing fields | Draught 28%, navigational status 17%, cargo type 83%, IMO 42% | Team count, unverified | Share of records with the field empty, 10 Nov 2026 file | Not re-checked; one day only |
| Danish shadow-tagged hulls per day | 11 to 19 per day | Team count, unverified | Hulls linked by IMO to a shadow tag | Not re-checked |
| Danish shadow-tagged hulls over two days | 28 hulls | Team count, unverified | Days 5 and 10 Nov 2026 combined | Not re-checked; days do not add to the daily range exactly |
| NOAA pilot day IMO matches | 203 hulls | Pilot script | IMO join between the NOAA file and OpenSanctions, 1 Jan 2023 | One day only |
| NOAA pilot day shadow-tagged matches | 26 hulls | Pilot script | Same join, restricted to shadow tags | One day only |
| NOAA shadow matches with a different MMSI | 25 of 26 hulls | Pilot script | Compared broadcast MMSI with the listed MMSI | Partly explained by time drift between the 2023 file and the current list |
| Age gap, NOAA pilot file to sanctions snapshot | About 3 years | External | January 2023 file against a 15 Nov 2026 snapshot | Uses the snapshot date, not the date each designation was made |

## Simulator design checks (our own computation, not data)

These come from `scripts/analysis/check_simulator_design.py`, which is in the repo and reproducible.

| Claim | Value | Source | How it was measured | Caveat |
|---|---|---|---|---|
| Random picker, 30-day mean, 7-day clean cooldown | 1.54 catches per day | Our Monte Carlo, 200 seeded runs | Uniform pick over eligible hulls, removal plus warm-start replacement | Simulator assumption, not data |
| Random picker, 30-day mean, 30-day clean cooldown | 1.96 catches per day | Same script | Same, cooldown set to 30 | This is why the 30-day cooldown was dropped |
| New MMSIs per shadow hull in 30 days at 0.65 per year | 0.053 per hull, 2.7 across the fleet | Arithmetic on the measured annual rate | 0.65 / 365 * 30, times 50 hulls | Within-run churn is too small to be a feature |
| Change in that count from the x0.30 adaptation | 1.9 events across the fleet | Arithmetic | 0.70 * 2.67 | Why adaptation acts on visible history, not the future rate |
| Pass rate of "lower 95% bound at or above +2", true gain +2 | 0.026 | Our simulation, 20,000 draws | Paired t with 10 seeds | The reason the success rule changed |
| Pass rate, true gain +2.5, SD 1.0 | 0.30 | Same | Same | 80% power needs about 34 seeds |
