# Simulator rules: catches, adaptation, signal strength and seeds

Status: design specification for the Shifting Hulls simulator. Every number
marked *default* or *assumption* is a project choice, not a measurement from
data. Measured values come from the Danish AIS, Global Fishing Watch and
OpenSanctions work described in Section 2 of the Milestone 2 plan.

This file removes ambiguity in four places: what counts as a catch, how hulls
adapt, how strong the agent's advantage channels are, and how random seeds are
used. It is the reference for the simulator code and for the evaluation
scripts. If the code and this file disagree, the code is wrong.

**Notation.** N = hulls in the fleet (default 1000). S = shadow hulls at day 0
(default 5% of N = 50). K = inspection budget per day (default 20). D =
simulated days (default 30). s = signal strength, from 0 to 1.

---

## 1. Inspections, catches and replacement

### 1.1 One inspection, one outcome

Each day the system submits exactly K hulls. For each hull the simulator
returns one outcome: `shadow` or `clean`. That is the only ground truth the
running system ever sees. The hidden fleet table stays inside the simulator and
is read only by the evaluation scripts.

### 1.2 A caught shadow hull leaves the active fleet

A hull with outcome `shadow` is **caught**. At the end of that simulated day it
leaves the active fleet. It is not inspected again and it is not counted again.
We model the catch as a detention that takes the vessel out of service. Its
full history remains in the database so that the evaluation can attribute the
catch, but the hull no longer sends AIS and cannot be inspected.

A replacement shadow hull enters the active fleet **before the decision** on
day d + 1 (the next simulated day, `replacement_delay_days = 1`). The
replacement keeps the active shadow count at S = 50, so every daily decision
sees the full pool. It is a new hull (new IMO) with a **warm start**: its
identity history is drawn from the same distribution as the day-0 shadow hulls
(number of prior MMSIs, age and initial AIS-off rate). Without the warm start a
replacement is nearly invisible for several days, and catches would measure
simulation age rather than agent skill.

Replacement is triggered by a catch, not by the agent's score. The agent cannot
create replacements. It can only catch the hulls that are present. A clean hull
is never removed and is never replaced. Fleet size N stays constant. The single
supported value is `caught_shadow_action: remove_and_replace`. A sensitivity
run may raise `replacement_delay_days`; the rules below then still hold.

**Why this rule.** A fixed pool of 50 shadow hulls cannot support 600
inspection slots over 30 days. Unique catches would stop after about 50
catches, and the metric would collapse in the last two thirds of every run.
Steady replacement keeps the task constant for the whole run and makes every
catch a distinct hull, so there is no question about double counting. The cost
is that the simulator is a steady-state testbed, not a model of how the real
shadow fleet grows or shrinks. We state that cost, we do not hide it.

**Rejected alternative.** Keep caught hulls in the fleet and flag them. Then
the scorer and the agent could spend slots on known hulls, "catches per 20
inspections" would mix new catches and repeat catches, and the result would
depend on the counting rule more than on the decisions. The main experiment
does not use the flagging variant.

### 1.3 Cooldown for clean hulls

An inspected clean hull cannot be inspected again for 30 days, which is the
length of a run. In practice every hull is inspected at most once per run. This
mirrors the real port-state-control re-inspection cycle, which is longer than
our run, and it stops an agent from filling slots with the same clean hulls.

A plan that names a hull on cooldown, a hull that has left the fleet, or the
same hull twice is invalid. The plan validator rejects it, and the system falls
back to the scorer's top-K for that day (Section 3.1 of the Milestone 2 plan).
The invalid plan and the fallback are logged.

### 1.4 When the shadow pool is temporarily small

With the default replacement delay of 1 day, replacements arrive before the next
decision, so no plan ever faces a depleted pool. If a sensitivity run uses a
longer `replacement_delay_days`, the active shadow count can dip. For example,
with a delay of 2 days and catches of 20 shadow hulls on two consecutive days,
the decision on the second day sees about 30 active shadow hulls, not 50.

The metric does not change its denominator when the pool is small. Every day
contributes exactly K = 20 inspection slots, even if fewer shadow hulls are
available. This is conservative and treats every method the same. The
evaluation logs `active_shadow_count` for each day as a diagnostic.

A day on which all remaining shadow hulls are caught yields c_d equal to the
pool size, not 20. That day is included without rescaling.

### 1.5 Identities do not create extra catches

The agent inspects hulls, not identifiers. If one hull appears under two MMSIs
on the same day, it is still one hull and one outcome. Picking both identities
in one plan is a duplicate and makes the plan invalid. The identity resolver
exists to join those records before a plan is submitted.

---

## 2. Primary and secondary metrics

### 2.1 Primary metric

For one run (one seed, one method, one signal strength):

```
P = (20 / K) * (1 / D) * sum over days d of c_d
```

where c_d is the number of shadow hulls caught on day d. Because a caught hull
leaves the fleet, every catch is a unique hull, so c_d is both the unique count
and the raw catch count. With K = 20 the factor is 1 and P is the mean number
of catches per day. With a different K the factor expresses the result per 20
inspection slots.

We average over days rather than over inspections because the budget is fixed
at K per day. A day with fewer available shadow hulls is still a full day of
duty. Report P for every method, seed and signal strength. Never pool across
signal strengths.

### 2.2 Secondary metrics

| Metric | Definition | Why it matters |
|---|---|---|
| Precision | (sum c_d) / (K * D) | Share of inspections that catch a shadow hull |
| Wasted clean inspections | K * D - sum c_d | Direct cost to legitimate operators |
| Outside top-K share | Share of the agent's picks that are not in the scorer's top-K that day | Shows whether the agent adds information rather than re-ranking |
| New-hull yield | Share of catches on hulls that entered after day 0 | Falls if the agent only catches the original fleet |
| Post-replacement catch time | Median days from a replacement entering to being caught | Measures how quickly the agent adapts to new hulls |

---

## 3. Hull adaptation (drift)

Adaptation is the mechanism that creates drift. It is an **assumption** about
how an adversary reacts to enforcement, not a measurement.

### 3.1 Rule

- **Trigger:** at least one shadow catch on day d.
- **Who adapts:** every uncaught shadow hull in the active fleet is a
  candidate. Clean hulls never adapt.
- **Probability:** each candidate adapts with `p_adapt = 0.20`, independently.
  The draw is seeded from the run seed and the day.
- **Delay:** an adapted hull shows its new behaviour from day d + 2. The delay
  models the time needed to repaint, re-register or change procedure.
- **What changes:** the hull's MMSI churn rate (new MMSIs per hull per day) is
  multiplied by 0.30, and its daily probability of an AIS-off gap is
  multiplied by 2.0. Name and flag changes follow the same churn multiplier.
- **Persistence:** the new behaviour lasts for the rest of the run and does not
  decay. There is no reset and no second adaptation state.
- **Spread:** it applies to the whole uncaught shadow fleet, not only to the
  hulls near the catch. We assume operators learn from observed enforcement.
  This also means drift does not depend on which specific hull the agent
  happened to pick.
- **Cap:** at most 80% of the active shadow fleet may be in the adapted state
  at once. After that, no further hull adapts. A fleet that never changes
  identity and never transmits is unsolvable, and the comparison between
  methods would stop meaning anything.

A caught hull cannot adapt because it has left the fleet. This is why the rule
is fleet-wide and not inspected-hull-only. The inspected hull is removed, so
there is no "the caught hull changes its behaviour" state.

### 3.2 Defaults and rationale

All values in this table are **defaults and assumptions**. None is measured.

| Parameter | Default | Rationale |
|---|---|---|
| `p_adapt` | 0.20 | One catch in five reaches each surviving hull. High enough to create drift within a run, low enough to leave some hulls catchable |
| `delay_days` | 2 | Behaviour change and re-registration are not instant. Also separates cause and effect in the logs |
| `mmsi_churn_multiplier` | 0.30 | Churn is the visible signal in the churn rule, so hiding means changing less often |
| `ais_off_prob_multiplier` | 2.0 | The alternative to changing identity is going dark. Doubling the gap rate trades one visible signal for another |
| `persistent` | true | Drift should accumulate over 30 days, not wash out. Simpler to explain and to analyse |
| `max_adapted_fraction` | 0.80 | Keeps the task solvable and every method comparable |

The design intent is that the churn rule loses most of its signal within about
a week of regular catches, while a catch rate above random remains possible.
Whether that happens is checked in calibration and reported, not assumed.

### 3.3 Config sketch

```yaml
inspection:
  caught_shadow_action: remove_and_replace
  replacement_delay_days: 1
  replacement_warm_start: true
  clean_cooldown_days: 30
  shadow_cooldown_days: 0        # caught hulls leave before they could return
  invalid_plan_fallback: scorer_top_k

adaptation:
  enabled: true
  trigger: any_shadow_catch
  p_adapt: 0.20                      # assumption
  delay_days: 2                      # assumption
  applies_to: uncaught_shadow_fleet  # fleet-wide, not inspected hull
  mmsi_churn_multiplier: 0.30        # assumption
  ais_off_prob_multiplier: 2.0       # assumption
  persistent: true
  decay_half_life_days: null
  max_adapted_fraction: 0.80
```

The schema names may change during implementation. The semantics above may
not: a change to what adapts, when, or by how much is a design change and must
be recorded in the plan's change log.

---

## 4. Signal strength and the negative control

### 4.1 Why this exists

The agent can only beat the scorer through information the scorer cannot use:
free text (names, registry notes) and timing links between the AIS stream and
the compliance stream. The team controls how much such information the
simulator contains. If it is strong, the agent looks good. If it is absent,
the agent cannot win. A single result at one arbitrary strength would say more
about our settings than about the agent.

So signal strength is an explicit parameter, we sweep it, and we report the
agent's gain as a function of it. The zero-signal case is a negative control.

### 4.2 Two channels

Both parameters are in [0, 1] and are **assumptions**. Neither is measured in
real data: we do not know how much label information real ship names or event
timing carry.

| Parameter | What it controls | At 0 | At 1 |
|---|---|---|---|
| `text_signal` (s_text) | Share of shadow hulls whose name or registry note carries a **true** prior identity (a previous name token, a real prior MMSI, or an explicit re-registration remark) | No shadow hull carries a true hint | Every shadow hull carries a true hint |
| `coupling_signal` (s_couple) | Share of shadow hull identity changes (MMSI, name, flag) that also produce a matching compliance event within 1 day | Event timing is independent of identity changes for both groups | Every shadow identity change has a matching event within 1 day |

To keep text from being a perfect detector, 5% of **all** hulls (shadow and
clean) carry a generic registry remark drawn from the same template pool. That
remark is label-independent: it looks the same in both groups and names no real
prior identity. Clean hulls produce matching compliance events at a fixed base
rate of 5% within the same window.

At s = 0 neither channel gives shadow hulls more label information than clean
hulls: text remarks are generic for both groups, and event timing is
independent of identity changes for both groups. At s = 1 both channels are
strong. Any intermediate value is linear in the probability that a shadow hull
carries the tell.

### 4.3 Sweep

Run every method at four strengths:

| Sweep value | s_text | s_couple | Purpose |
|---|---|---|---|
| 0.00 | 0.00 | 0.00 | Negative control: no usable text or coupling signal |
| 0.25 | 0.25 | 0.25 | Weak signal |
| 0.50 | 0.50 | 0.50 | Moderate signal, the default reporting point |
| 1.00 | 1.00 | 1.00 | Strong signal, the best case for the agent |

If time allows, add the two off-diagonal corners (s_text = 0, s_couple = 1) and
(s_text = 1, s_couple = 0) to attribute a gain to one channel. These are
optional and are reported separately, not used as the main result.

### 4.4 Gain and negative control

For a strength s, let G(s) be the mean paired difference between the agent's
primary metric and the better baseline's primary metric at the same s, with a
95% interval. The better baseline is the higher of the churn rule and the
scorer on the development seeds at that strength, fixed before any evaluation
seed is run, so the choice cannot be steered by the evaluation results. The
headline result is the curve G(s) over the four values, not one number.

At s = 0 the agent has no information the scorer lacks. The expected gain is
zero or negative. Predefined rule: if the 95% paired interval of G(0) lies
entirely above 0, stop and investigate before reporting any positive gain at
another strength. A positive zero-signal result points to a simulator leak, an
evaluation bug, or tuning on evaluation seeds. An agent that is worse than the
scorer at s = 0 is acceptable and should be explained (for example, extra
sampling variance or fallbacks).

### 4.5 Limitation, stated plainly

A gain at s = 1 is evidence that the agent can use text and cross-stream
coupling **when they exist in the simulator**. It is not evidence about the
real world, because we have not measured those strengths in real data. The
zero-signal control bounds the claim from below, and the sweep shows how the
claim depends on a setting we chose. The result must be reported this way in
the final document, not as a single headline number.

---

## 5. Calibration stance

Calibrate the simulator to **measured marginal rates**, not to the ordering of
the pilot baselines.

Set or fit from data:

- MMSI churn: 0.65 new MMSIs per vessel-year for shadow hulls, 0.18 for other
  hulls (pilot measurement).
- Two active MMSIs for the same hull in 15 to 20% of hulls (pilot
  measurement).
- Missing fields from one day of Danish AIS: draught 28%, navigational status
  17%, cargo type 83%, IMO 42%.
- AIS gap rates, gap lengths and position jump rates: measured on Danish AIS
  in weeks 1 and 2.

The pilot catch figures (random 1.0, churn rule 12.5, scorer 9.4 per 20
inspections) are a **plausibility range only**. Three reasons:

1. The pilot counted repeat catches on a fixed fleet. The main metric counts
   unique hulls under replacement, so the numbers are not directly comparable
   even on the same simulator.
2. The pilot was small. The paired top-20 difference between the churn rule
   and the scorer was -2.8, with a 95% interval of -7 to +1. The ordering is
   not established.
3. Tuning to an ordering turns a test into a fit: the simulator would be
   built so that one baseline wins, which is the thing the experiment is meant
   to test.

Do not tune any parameter to reproduce 12.5, 9.4, or the claim that the churn
rule beats the scorer. After calibration, check only that the figures are in
the same broad range (single digits to low teens on the repeat-catch
definition) and that neither baseline catches almost all or almost none of the
shadow hulls. Record the calibration run and the measured marginals next to the
config.

---

## 6. Random seeds and paired analysis

### 6.1 Seed sets

| Set | Seeds | Count | Use |
|---|---|---|---|
| Development | 1001 to 1005 | 5 | Prompt tuning, threshold choice, scorer training, debugging, any exploratory run |
| Evaluation | 2001 to 2010 | 10 | Main experiment and all reported results. Nothing else |

Rules:

- Both lists are fixed before the main experiment and recorded in the config
  and in the results files.
- The sets are disjoint. Evaluation seeds are never used to choose a prompt, a
  threshold, a baseline, a signal strength or a simulator setting. Development
  seeds are never reported as evaluation results.
- One base seed per run derives all simulator randomness, the adaptation
  draws and the LLM sampling seed. The base seed is stored with the run.
- Every method and every signal strength is run on every evaluation seed. The
  same fleet (same hulls, same initial identities) is used within a seed; the
  signal strength changes the text and event content, not the hull list.
- No evaluation seed is dropped or replaced after seeing its result. If a run
  crashes for technical reasons, rerun the same seed and log the rerun.
- The agent runs at temperature 0. vLLM batching can still cause small
  differences between runs, so the seed is a label for the run, not a promise
  of bit-identical output. We do not rerun seeds to pick a better result.

### 6.2 Paired differences and confidence interval

For evaluation seed i, with the same estimator and the same signal strength:

```
d_i = P_agent,i - P_baseline,i
```

where P is the primary metric from Section 2. Report the mean difference

```
d_bar = (1 / n) * sum(d_i),  n = 10
```

and the 95% paired t-interval:

```
d_bar +/- t(0.975, 9) * sd(d_i) / sqrt(n)
```

with 9 degrees of freedom and t(0.975, 9) = 2.262. This is a paired t-interval
on the 10 seed-level differences. It is the interval used in the success
criterion.

**Bootstrap check.** Also resample the 10 paired differences with replacement
10,000 times and take the 2.5th and 97.5th percentiles as a percentile
interval. If the t-interval and the bootstrap interval disagree about whether
zero (or the +2 threshold) is inside, report both and do not claim success.
With n = 10 the intervals are approximate; the bootstrap check shows whether
the t-interval is being driven by one unusual seed.

**What the interval covers.** It covers variation between seeds on this
simulator version, with this model version and these prompts. It does not cover
uncertainty about the simulator settings, the LLM version or the real world.
Report the 10 seed-level values as well as the summary, so a reader can see the
spread.

