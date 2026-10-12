# Adaptive price-fetch strategy — polling when stations actually re-price

Drivers were occasionally arriving at a station whose price no longer matched
the app: the data workflow polled the upstream Régie Essence snapshot on a fixed
hourly interval, so a station that re-priced at 13:15 was only picked up by the
14:00 run (or later, if the run was delayed).

This document describes the replacement: a **learned, budget-bounded poll
schedule**. The app now studies *when* Québec stations change their prices
(multi-round diffing of successive snapshots) and concentrates its upstream
requests around those windows — without hammering the Régie serveur.

## Where it lives

| Piece | File | Role |
| --- | --- | --- |
| Pure strategy logic | `scripts/fetch_strategy.py` | weights, slot planning, diffing, gate decision |
| Learner / planner (CLI) | `scripts/update_profile.py` | diff new snapshot → update profile → rewrite schedule |
| Workflow gate (CLI) | `scripts/should_fetch.py` | decide whether *this* workflow run may download |
| Learned data | `data/update-profile.json` | per-hour change statistics — global, per region and per quarter-hour mark |
| Derived plan | `data/fetch-schedule.json` | concrete daily UTC poll slots (+ pinned region hours) |
| Gate state | `data/fetch-state.json` | last actual fetch timestamp |
| Tests | `scripts/test_fetch_strategy.py`, `scripts/test_fetch_pipeline.py` | unit + CLI coverage |

Schema, retention and size of the learned profile are documented in
[`history-data.md`](./history-data.md#fetch-profile-update-profilejson).

Workflow wiring lives in `.github/workflows/static.yml` (schedule + deploy
gating) and `.github/workflows/update-data.yml` (gate + steps).

## How the learning works

Each successful fetch (after `process_data.py`, before/with `append_history.py`)
`update_profile.py` diffs the observed per-region average price per fuel against
the previously observed one (`fetch_strategy.diff_regions`, half-a-tenth
tolerance, so rounding noise is not a change). For the UTC hour of the fetch it
records:

```json
{
  "hours": {
    "11": {"samples": 12, "observed_hours": 6.5, "change_events": 3, "changed_stations": 812,
           "changed_stations_exact": 47, "stations_observed": 2463, "exact_samples": 12}
  },
  "regions": {
    "Montréal": {"stations": 452, "hours": {
      "11": {"samples": 12, "observed_hours": 6.5, "change_events": 3, "changed_stations": 18}
    }}
  },
  "marks": {
    "11": {"0": {"samples": 3, "changed_stations": 21}, "1": {"samples": 3, "changed_stations": 9}}
  },
  "last_observed": {"Montréal": {"regular": 170.5, "super": 188.0, "diesel": 180.0}},
  "last_observed_at": "2026-09-25T11:20:00Z",
  "last_station_diff": {"at": "2026-09-25T11:20:00Z", "observed": 2463, "changed": 47}
}
```

- `samples` — fetches that happened in that hour
- `observed_hours` — wall-clock time those fetches *cover* (gap since the
  previous observation, capped at 2 h so a long outage cannot distort the rate)
- `change_events` — fetches that saw at least one real price change
- `changed_stations` — number of stations in the affected regions
- `changed_stations_exact` — stations that really re-priced in that hour,
  counted per station (issue #68); `stations_observed` is how many stations the
  snapshot held and `exact_samples` how many fetches fed those two counters
- `regions` — the same accounting per region (issue #71), **sparse** (only
  observed hours are stored) and always the exact per-station count
- `marks` — per quarter-hour counter (`0` = `:00` … `3` = `:45`) of fetches and
  exact re-prices, so the intra-hour signal stays measurable

The learned signal is the **rate** `changed_stations / observed_hours`
("stations re-pricing per hour at this hour of day"). A rate rather than a raw
count means denser sampling in an hour does not make that hour look better: the
estimate is unbiased as the budget shifts around. The unit of detection is the
region average (the same granularity as the stored history), weighted by how
many stations the region holds, so "most stations changed" is what drives the
weights.

Detection sensitivity was checked against the already-recorded 6 h history: in
all 57 buckets of September 2026, 14–53 region-grid fuel averages moved by up to
13 ¢ between buckets (a single-station move inside a very large region can of
course stay under the half-a-tenth threshold — that is why issue #68 added the
per-station diff below, which reports exactly which stations re-priced).

### Station-level diff (issue #68)

The region average is a lossy signal: a +5¢ and a −5¢ move inside one region
cancel out, and a single re-price inside the ~140-station Montréal region is
diluted far below `PRICE_EPSILON`. `fetch_strategy.diff_stations()` therefore
compares the two snapshots **station by station**, using the same 5-decimal
coordinate key as `data/history/station-history.json`, and
`update_profile.py` folds the result into `data/update-profile.json` (exact
counts) and `data/history/station-changes.json` (`last_changed_at` /
`last_seen_at` per station — see
[`history-data.md`](./history-data.md#station-level-change-detection)).

The learned schedule now reads both channels (see "Weighted by expected changed
stations" below): the exact per-station counter is used once it has covered the
day, and the legacy region-average rate until then.

`hour_weights()` blends that rate with the research prior below. The prior is
weighted by `prior_alpha = 168` observed-hours (one week of hourly coverage):

```
weight_h ∝ (1 - w) · learned_rate_h + w · prior_h
w = 168 / (168 + total_observed_hours)
```

With no data at all the weights *are* the prior; after a week the prior and the
data weigh the same; after a month the prior is a small correction.

## Layered weights: regions, expected volume, quarter-hours (issue #71)

One global 24-hour distribution averages regions and brands together. Measured
on the live profile, the per-hour spread of the global signal was only
**745–1235 changed stations (1.65×)** — barely above noise — which is exactly
what you get when Montréal, Laval and Gaspésie are averaged into one curve. The
profile and the planner now carry three extra layers.

### 1. Expected changed stations, not "did something change"

`hour_rates(profile, channel=...)` returns the **expected number of stations
re-pricing per hour of day**, and `hour_weights()` weights the budget by that
volume:

| channel | signal | behaviour |
| --- | --- | --- |
| `region` (legacy) | `changed_stations / observed_hours`: a region counts its *whole* station count as soon as one of its fuel averages moved | event-based, saturates — a 6h replay of the committed history shows nearly every region "moving" in nearly every bucket, spread only **1.14×** |
| `exact` (#68) | `changed_stations_exact / observed_hours`: stations that really re-priced, one per pump | count-based, keeps the hourly contrast |
| `auto` (default) | `exact` once `exact_channel_ready()` is true, else `region` | never mixes units inside one distribution |

The switch is deliberately coarse — `EXACT_READY_SAMPLES = 48` fetches and at
least one exact sample in *every* observed hour — because the two channels have
different units. The exact counters only start recording after the region
average channel already had weeks of history, so the exact side is ignored
until it can cover the whole day; until then the schedule behaves exactly as
before.

### 2. Per-region hourly weights (hierarchical shrinkage)

`update_profile.py` attributes every re-priced station to its region
(`station_regions()` + `diff_stations()`) and folds `{region: {hour: ...}}` into
the profile — sparse, only observed hours, with the exact per-station count.

```
rate_{region,hour} = changed_stations_{region,hour} / observed_hours_{region,hour}
province_rate_hour = Σ_region rate_{region,hour}      # expected re-prices/hour
exposure_region    = Σ_hour  observed_hours_{region,hour}

region_weights   = shrink(rate_region, exposure_region, toward=global_weights,
                          alpha=REGION_PRIOR_HOURS)   # 24 observed hours
province_weights = shrink(province_rate, Σ exposure_region, toward=global_weights,
                          alpha=168)
```

`shrink()` is a one-line hierarchical blend: with no exposure the result *is*
the global distribution, with `exposure == alpha` (one day for a region) the
region and the province weigh the same. Nord-du-Québec has 36 stations and
Côte-Nord 177; a handful of noisy fetches cannot move their weights, while a
metropolis that really has a distinct window eventually dominates its own
distribution. This is the "global × region" mix the issue asked for, and it
degrades to the pre-#71 behaviour whenever regional data is missing.

### 3. Regional coverage constraint

`coverage_hours(region_weights, baseline=province_weights)` pins the hours a
region would otherwise lose to the metropolises. An hour is pinned for a region
when **both**

- it is a genuine hot hour for that region: its weight is at least
  `REGION_PEAK_RATIO = 1.25` times the region's uniform share `1/24` (a flat
  rhythm therefore pins nothing), and
- the province's own share there is at least 1.25 times smaller, i.e. the global
  plan does not already concentrate on that hour.

At most `REGION_PEAK_HOURS = 2` hours per region survive, and
`coverage_hours()` returns the province-wide union in that ranked order.
`plan_schedule(..., coverage_hours=...)` allocates them **after** the hourly
floor and **before** the water-fill, capped at `COVERAGE_BUDGET_SHARE = 0.5` of
the budget that the floor left over: the floor (issue #77) survives, a pin can
never eat the whole plan, and the volume-based water-fill always keeps its
share. The ranking is deliberately *not* weighted by station count — the pin is
about fairness (the water-fill already serves the big regions), so a 1-station
region may hold a pin a big city loses.

The schedule records the result for review:

```json
"coverage_hours": [20, 22, 6, 23, 19, 5, 4, 10, 14, 7, 15, 17, 13, 16, 11, 12, 18],
"region_coverage": {
  "Nord-du-Québec": {"peaks": [5], "covered": true, "stations": 26},
  "Montréal": {"peaks": [18], "covered": true, "stations": 226}
}
```

Replaying 1–2 days of 15-minute fetches against the real snapshot with
region-specific moves (offline harness, `FETCH_DAILY_BUDGET` lowered so the
constraint actually bites — at the shipped 24 the hourly floor already covers
every hour, so the pins are inert by construction):

| budget/day | pre-#71 plan | #71 plan |
| --- | --- | --- |
| 12 | 9–10 of 18 regions covered | 8 of 18, but a different set: Côte-Nord, Gaspésie, Abitibi, Municipalités hors MRC and Outaouais keep their own window |
| 18 | 11 of 18 | **12–13 of 18** |
| 24 (shipped default) | 18/18 | 18/18 (the floor covers the day) |
| 48 | 18/18 | 18/18 |

Below the hourly floor the plan is a sparse subset of hours, so the pins and the
volume hours compete for the same slots: at 18 slots the region-specific pins
win clearly, at 12 they trade places with the province's busiest hours (a wash
in count). Without any pin, a reduced budget simply clones the province pattern
and a station in Nord-du-Québec (26 stations, local-midnight reset) or Gaspésie
(59 stations, 22:00 UTC) never gets polled in its own window.

### 4. Quarter-hour marks (optional refinement)

`record_observation()` also counts samples and exact re-prices per
quarter-hour mark (`marks`, `0` = `:00` … `3` = `:45`), and
`learned_mark_orders()` ranks the marks of an hour by learned density once that
hour has at least `MARK_MIN_SAMPLES = 32` recorded fetches. `plan_schedule()`
uses that ranking when it has to choose *which* marks of an hour to keep,
instead of the coarse-first default (`:00`, `:30`, `:15`, `:45`). Whenever the
plan holds more than one slot in an hour, it lands on the quarter-hour that
historically carried the most re-prices.

### Not done: brand layer

The issue also mentions brands (Costco vs independents). It is deliberately left
out of this change: the snapshot carries **49** brands (Esso 453, Petro-Canada
422 … down to a long tail below 10 stations), and with that little data per
brand the shrinkage prior would dominate every brand distribution anyway, while
adding a third dimension to a profile that is still accumulating its first
regional observations. Once the exact counters have a month of data, a brand
layer can reuse exactly the same helpers (`_record_regions`, `_region_rates`,
`hour_weights(..., region=...)`) keyed on a bucketed brand.

### Old vs new

| | before #71 | after #71 |
| --- | --- | --- |
| weight source | one global 24-hour rate | province sum of regional rates, shrunk to global |
| detector | region average (saturating) | exact per-station count once ready, else region average |
| per-hour spread (measurable) | 1.65× live, **1.14×** on a 6h replay of the committed history | exact channel keeps the real contrast (region-specific replay: **39.6×**); unit tests pin the flat-vs-sharp property |
| region guarantee | none | each region's distinctive hours pinned and ranked (`coverage_hours`, `region_coverage`), never more than half of the discretionary budget |
| intra-hour | coarse-first marks | learned mark order per hour when sampled |
| old fields | `hours`, `last_observed`, `last_station_diff` | unchanged, still written (additive schema) |

The committed `data/update-profile.json` predates the regional block and has
exact counters for only part of the day (hours 06/08/15), so the schedule keeps
using the legacy path until the exact channel covers every observed hour.

## Initial windows (researched, `RESEARCH_PRIOR`)

The issue explicitly allows seeding the first windows with what is publicly
known about Canadian/Québec fuel pricing. The prior therefore elevates:

- **Local midnight** (stations re-set the price for the next day; Ontario
  reporting describes prices climbing at midnight and floating down through the
  day) → ~04:00–07:00 UTC.
- **Wednesday evening → Thursday 00:01**, when the Régie de l'énergie floor
  prices (published Wednesday morning) take effect; large jumps land Wednesday
  evening / Thursday morning → late UTC hours and early UTC morning.
- **Morning commute increases** (06:00–09:00 local) → ~10:00–14:00 UTC.

Sources consulted: `gasquebec.ca/en/cities` (Régie floor-price cadence),
`insidehalton.com` ("What is the best time to buy gas in Ontario" — prices climb
back at midnight), `insauga.com` (stations "re-establish the price" from
midnight and float downwards), `gaswizard.ca` / `r/ontario` discussions on
time-of-day patterns. Montréal is UTC-4 (EDT) / UTC-5 (EST); the prior is
expressed directly in UTC and only matters for the first days of operation.

Real observations replace it automatically — nothing else needs to be edited.

## From weights to a bounded schedule

`plan_schedule(weights, budget, min_per_hour=1, max_per_hour=4,
observed_hours=<profile>, explore_frac=0.15, coverage_hours=<region peaks>,
mark_orders=<learned marks>)` picks quarter-hour UTC slots:

1. every hour keeps `min_per_hour` slot (default 1) while the budget allows, so
   the learner keeps unbiased coverage of all 24 hours (the hourly freshness
   floor of issue #77);
2. the union of the regional peak hours (`coverage_hours`, issue #71) is pinned
   next, in the ranked order `coverage_hours()` returns, for at most half of
   what the floor left over (`COVERAGE_BUDGET_SHARE`);
3. a fraction `explore_frac` (ε, default **0.15**) of the budget is reserved for
   **explore** slots in the hours with the least `observed_hours`, on a
   quarter-hour mark the exploitation plan does not already use — this is what
   stops the learner from being locked onto its prior (issue #69; see
   [`history-data.md` §Exploration vs exploitation](./history-data.md#exploration-vs-exploitation-ε-greedy)).
   Exploration is reserved *before* the pins, so the regional constraint can
   never freeze the sampler's own learning;
4. the remaining budget is greedily water-filled into the highest-weight hours,
   at most 4 slots per hour, using the learned quarter-hour order of that hour
   (:func:`learned_mark_orders`) or the coarse-first default `:00`, `:30`,
   `:15`, `:45`;
5. `budget` is the hard **maximum upstream downloads per day** from the
   *scheduled plan* (default **24** = one request per hourly wake-up, i.e. the
   hourly fallback cron's capacity; override with the `FETCH_DAILY_BUDGET` env
   var or `--budget`). Below 24 the learned weights decide which slots survive;
   at 24 the budget equals the hourly floor, so the plan is one slot per hour
   (00:00, 01:00, …) and both the ε share and the pins have no slack left —
   hour coverage now comes from the uniform external trigger instead (issue
   #77). `plan_slots(...)` is the exploration-free wrapper
   used where there is no learned profile yet.

Sub-hourly cadence is **not** the plan's job any more: it comes from the
external `repository_dispatch` trigger, which the gate treats as "fetch
unconditionally" and which does not consume this budget (see
[Fetch rhythm](#fetch-rhythm-vs-upstream-regeneration)).

Example output (`data/fetch-schedule.json`):

```json
{
  "v": 1,
  "generated_at": "2026-09-25T11:20:00Z",
  "source": "learned",
  "prior": "research-2026-09",
  "budget_per_day": 24,
  "allocated_per_day": 24,
  "min_per_hour": 1,
  "max_per_hour": 4,
  "explore_frac": 0.15,
  "explore_count": 0,
  "explore_slots": [],
  "slots": ["00:00", "01:00", "02:00", "...", "23:00"],
  "hour_weights": [0.0288, "..."],
  "coverage_hours": [],
  "region_coverage": {}
}
```

`coverage_hours` / `region_coverage` stay empty until the profile has regional
observations (the committed profile predates #71); the first fetch after the
merge fills both, exactly like the exact counters of #68.

`explore_slots` is a subset of `slots` (never a duplicate); `should_fetch.py`
uses it to tag a due hit with `[explore]` in its output, so each poll is
attributable to exploration or exploitation (issue #69). At the shipped budget
of 24 it is empty by design (the budget only covers the hourly floor).

The plan is rebuilt on every successful fetch. `hour_weights()` uses cumulative
counters, so the schedule reflects the whole observed history rather than a
rolling window: it stabilises over a few weeks instead of chasing single noisy
days. A decayed/rolling window would adapt faster and is an obvious next step
if the learned peaks drift.

## The workflow gate

`static.yml` wakes on the **external trigger** (`repository_dispatch`, target
cadence 30 min) plus an hourly fallback cron (`17 * * * *`). Every wake-up runs
`scripts/should_fetch.py`, which downloads **only** when it is due:

1. no previous fetch recorded → fetch (bootstrap / after deploy);
2. last fetch < 10 min ago → **skip** (overlapping triggers cannot double-poll);
3. a scheduled slot fell inside `(last_fetch, now]` within a 45-minute grace
   window → fetch (the grace stops a delayed runner from firing on a slot that
   is hours stale);
4. nothing for more than `max_age_minutes` (default **120 min** = two hourly
   wake-ups, env `FETCH_MAX_AGE_MINUTES`) → fetch anyway (**freshness safety
   net**, so a bad or empty schedule can never leave the data stale);
5. otherwise → skip until the next slot.

A non-scheduled run (`push`, `workflow_dispatch`, `repository_dispatch`) always
fetches: one dispatch = at most one poll. The gate writes
`due=true|false` to `$GITHUB_OUTPUT`; `update-data.yml` gates the download,
size-check and commit steps on it, and `static.yml` skips the Pages build/deploy
when `due=false`. So a not-due wake-up costs a checkout plus the gate — upstream
is not touched and no identical snapshot is redeployed.
If `should_fetch.py` itself fails, the workflow shell fallback sets `due=true`:
fail-open, because stale prices are worse than one extra download.

## Fetch rhythm vs upstream regeneration

The rhythm is now defined by **#76/#77** and documented in full (evidence table,
external-trigger setup, tuning rules) in
[`history-data.md` §Fetch rhythm](./history-data.md#fetch-rhythm-final-issue-77).
Short version:

- the upstream snapshot is **regenerated every 5 minutes** on the quarter-hour
grid (measured 2026-10-11: `04:45:01 → 04:50:01 → 04:55:01`);
- **24.5 % of stations re-price on any given day** (603 of 2463 comparable
stations moved between adjacent days in `data/history/station-history.json`);
- #76's staleness study asks for a **30-minute** interval (p90 stale lag ≈ 0.9 ×
T ≤ 30 min);
- GitHub's `schedule` cannot deliver that here: measured over 15 days
(`data/trigger-interval.json`, `scripts/trigger_interval_report.py`), even the
hourly cron actually ran every ~2 h (p50 120 min) and the `*/15` cron every
~4.3 h (p50 257 min). So the sub-hourly cadence comes from an **external
trigger** (`repository_dispatch` type `fetch-data`, see the setup steps in
`history-data.md`), and the cron is only an hourly best-effort fallback.

Upstream load is unchanged in kind: the same single request per admitted run,
the same `User-Agent`, the same retry backoff in `download_data.py`. A dispatch
is one poll (the gate fetches unconditionally on non-schedule events), and the
planned budget can never exceed the wake-up cadence.

### How to tune it

`FETCH_DAILY_BUDGET`, `FETCH_MAX_AGE_MINUTES` and `FETCH_EXPLORE_FRAC` are the
**single knobs** for the strategy: `scripts/should_fetch.py` (the gate) and
`scripts/update_profile.py` (the plan writer) read the same env vars, falling
back to `fetch_strategy.DEFAULT_DAILY_BUDGET` /
`DEFAULT_MAX_AGE_MINUTES` / `DEFAULT_EXPLORE_FRAC` when unset (local runs,
tests). In CI they are set once in the `env:` block of the `update-data` job in
`.github/workflows/update-data.yml` — edit that block, not the scripts.
`FETCH_EXPLORE_FRAC=0` switches exploration off and reproduces the pre-#69
plan exactly.

They must stay coupled to the **cron in `static.yml`** (issue #77):
`FETCH_DAILY_BUDGET = 1440 / cron_interval_minutes` (24 at the hourly fallback)
and `FETCH_MAX_AGE_MINUTES = 2 × cron_interval_minutes` (120). Otherwise the
fallback is either denser than the plan (the plan stops meaning anything) or
sparser (one dropped run eats two hours of freshness).
`python3 -m unittest scripts.test_trigger_interval_report` fails when they
drift apart.

`data/fetch-schedule.json` is derived state: its `budget_per_day` only follows
the env var after the **next successful fetch** rewrites it
(`update_profile.py`). Until then it keeps the old budget — if the file and the
env disagree, the gate still uses the file's `slots`, so no manual edit is
needed. To rebuild it immediately from the committed profile (no network, no
new observation recorded), re-run the planner over the committed profile with
the new budget, e.g.:

```bash
python3 - <<'PY'
import json, pathlib
from scripts import update_profile as up
from scripts.fetch_strategy import (coverage_hours, hour_weights, learned_mark_orders,
                                    observed_hour_totals, plan_schedule,
                                    region_coverage, region_hour_weights)

d = pathlib.Path('data')
profile = json.loads((d / 'update-profile.json').read_text())
weights = hour_weights(profile)
per_region = region_hour_weights(profile)
guarded = coverage_hours(per_region, baseline=weights)
slots, explore = plan_schedule(weights, budget=24, min_per_hour=1, max_per_hour=4,
                               observed_hours=observed_hour_totals(profile),
                               explore_frac=0.15, coverage_hours=guarded,
                               mark_orders=learned_mark_orders(profile))
coverage = region_coverage(per_region, slots, baseline=weights)
for name, rec in coverage.items():
    rec['stations'] = int((profile.get('regions', {}).get(name) or {}).get('stations') or 0)
schedule = up.build_schedule(profile, slots, weights, 24, 1, 4,
                             up.parse_iso(profile['updated_at']),
                             explore_slots=explore, explore_frac=0.15,
                             coverage=guarded, regions=coverage)
(d / 'fetch-schedule.json').write_text(json.dumps(schedule, separators=(',', ':')) + '\n')
PY
```

Then `allocated_per_day` matches `budget_per_day` (24) again. A lower `--budget`
shows the regional coverage constraint at work: the pinned `coverage_hours`
survive the water-fill even when the hourly floor cannot.

## Necessary fetch interval (measured, issue #76)

`scripts/interval_report.py` answers "how often *must* we fetch?" from the
15-minute ground truth of the local probe (#74). It replays the git revisions of
`data/history/station-changes.json` (the probe commits one per cycle) as a
station-price series, measures the change-arrival distribution (p50/p90/p99, by
region and brand), simulates polling every T minutes and reports the stale-lag
distribution (p50/p90/p99) plus the share of stations showing an out-of-date
price at a random instant.

```bash
python3 scripts/interval_report.py       # -> docs/fetch-interval-report.md + data/fetch-interval.json
```

Decision rule: the longest T whose **p90 stale lag ≤ 30 minutes** *and*
**stale share ≤ 5 %**. Until the probe has ≥ 3 days of observations *and* ≥ 30
change events, the script reports *样本不足 / needs N more days* and marks every
table preliminary instead of inventing a recommendation. 15-minute sampling
cannot see sub-interval flapping and can only locate a change at the observation
that first showed the new price, so the measured change rate is a **lower bound**
and the eventual recommendation is an **optimistic ceiling** (the truly required
interval is ≤ it). T ≤ the sampling resolution is flagged "不可测" and excluded
from the conclusion. See
[`fetch-interval-report.md`](./fetch-interval-report.md) for the latest run.

## Scheduled triggers are not precise (issue #77)

Measured on this repository, GitHub delays and drops scheduled triggers under
load, so the strategy is built around that reality instead of pretending it can
hit an exact minute:

| Trigger | Measured real cadence |
| --- | --- |
| `0 * * * *` (older hourly cron, the bulk of the history) | p50 **120 min**, p90 278.8 min, max 754.6 min |
| `*/15 * * * *` (issues #67–#76) | p50 **257 min**, p90 405.7 min, max 537.9 min |

Source: `scripts/trigger_interval_report.py` over the committed history
(evidence table in [`history-data.md`](./history-data.md#fetch-rhythm-final-issue-77),
machine-readable in `data/trigger-interval.json`). Making the cron *denser* made
the real cadence **worse**, so scheduling is now treated as a safety net and the
cadence comes from the external trigger:

- the plan keeps an **hourly freshness floor** (`min_per_hour=1`) so any honoured
  run inside an hour is due and staleness stays around an hour;
- the 45-minute grace window means a run delayed by 10–40 minutes still serves
  its slot instead of being thrown away;
- the 120-minute freshness net (= two hourly wake-ups) guarantees a download
  even if every scheduled run is dropped for hours;
- the **external trigger** (`repository_dispatch` type `fetch-data`) is the only
  way to actually get the 30-minute cadence #76 asks for; `static.yml` accepts
  it and `scripts/dispatch_fetch.sh` is the ready-made caller (setup steps and
the minimal PAT scope live in
[`history-data.md`](./history-data.md#external-trigger-step-by-step-setup)).

## Request budget

| Situation | Upstream downloads/day |
| --- | --- |
| default plan | ≤ 24 (one per hourly fallback wake-up) |
| external trigger (dispatch) | ≤ 48 at a 30-minute cadence, unbounded by the plan but bounded by the external cron |
| empty/corrupt schedule | ≤ 12 from the freshness net (one every 120 min) |
| code pushes (`push` event) | unbounded by the plan, but only as often as humans push |

The planned part is structurally bounded: the gate only fires on planned slots
and the plan can never exceed `budget_per_day` slots. The freshness
net can add at most one extra fetch every `max_age_minutes` (12/day at the
120-minute default) when the plan is empty or broken; a dispatch is one poll
each.

## Files are disposable

`data/update-profile.json` and `data/fetch-schedule.json` are derived state. If
either is missing or corrupt the gate falls back to a research-prior plan and
`update_profile.py` rebuilds both on the next fetch. `data/fetch-state.json` is
regenerated by the gate; a corrupt/absent value only causes one bootstrap fetch.

## Running the pieces locally

```bash
# unit + CLI tests
python3 -m unittest scripts.test_fetch_strategy scripts.test_fetch_pipeline

# learn from the committed snapshot and print the resulting plan (sandboxed)
cp data/stations.json /tmp/prof/stations.json
python3 scripts/update_profile.py --data-dir /tmp/prof
python3 scripts/should_fetch.py --data-dir /tmp/prof --dry-run
```

Both CLIs accept `--now <ISO>` so decisions can be reproduced for any instant.
Both also honour `FETCH_DAILY_BUDGET` / `FETCH_MAX_AGE_MINUTES`, and
`update_profile.py` honours `FETCH_EXPLORE_FRAC` (see "How to tune it" above),
so a local run reproduces the CI rhythm when those are exported.
