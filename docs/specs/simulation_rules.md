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

A replacement shadow hull enters the active fleet on day d + 1 (the next
simulated day). The replacement keeps the active shadow count at S = 50. It is
a new hull (new IMO) with a **warm start**: its identity history is drawn from
the same distribution as the day-0 shadow hulls (number of prior MMSIs, age and
initial AIS-off rate). Without the warm start a replacement is nearly invisible
for several days, and catches would measure simulation age rather than agent
skill.

Replacement is triggered by a catch, not by the agent's score. The agent cannot
create replacements. It can only catch the hulls that are present. A clean hull
is never removed and is never replaced. Fleet size N stays constant.

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

Catches remove hulls before their replacements arrive, so the active shadow
count can dip below 50 on the day after a large catch day. The worst case is
catching 20 shadow hulls on two consecutive days, which leaves about 30 active
shadow hulls. The count returns to 50 on the following day.

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
