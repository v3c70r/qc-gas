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
| Learned data | `data/update-profile.json` | per-UTC-hour change statistics |
| Derived plan | `data/fetch-schedule.json` | concrete daily UTC poll slots |
| Gate state | `data/fetch-state.json` | last actual fetch timestamp |
| Tests | `scripts/test_fetch_strategy.py`, `scripts/test_fetch_pipeline.py` | unit + CLI coverage |

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
    "11": {"samples": 12, "observed_hours": 6.5, "change_events": 3, "changed_stations": 812}
  },
  "last_observed": {"Montréal": {"regular": 170.5, "super": 188.0, "diesel": 180.0}},
  "last_observed_at": "2026-09-25T11:20:00Z"
}
```

- `samples` — fetches that happened in that hour
- `observed_hours` — wall-clock time those fetches *cover* (gap since the
  previous observation, capped at 2 h so a long outage cannot distort the rate)
- `change_events` — fetches that saw at least one real price change
- `changed_stations` — number of stations in the affected regions

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
course stay under the half-a-tenth threshold — that is the acceptable cost of
keeping the profile tiny and the stats region-level).

`hour_weights()` blends that rate with the research prior below. The prior is
weighted by `prior_alpha = 168` observed-hours (one week of hourly coverage):

```
weight_h ∝ (1 - w) · learned_rate_h + w · prior_h
w = 168 / (168 + total_observed_hours)
```

With no data at all the weights *are* the prior; after a week the prior and the
data weigh the same; after a month the prior is a small correction.

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

`plan_slots(weights, budget, min_per_hour=1, max_per_hour=4)` picks quarter-hour
UTC slots:

1. every hour keeps `min_per_hour` slot (default 1) while the budget allows, so
   the learner keeps unbiased coverage of all 24 hours;
2. the remaining budget is greedily water-filled into the highest-weight hours,
   at most 4 slots per hour (one per quarter-hour mark `:00`, `:15`, `:30`,
   `:45`, spread coarse-first);
3. `budget` is the hard **maximum upstream downloads per day** (default 36 =
   24 hourly floor + 12 extras, override with the `FETCH_DAILY_BUDGET` env var
   or `--budget`).

Example output (`data/fetch-schedule.json`):

```json
{
  "v": 1,
  "generated_at": "2026-09-25T11:20:00Z",
  "source": "learned",
  "prior": "research-2026-09",
  "budget_per_day": 36,
  "allocated_per_day": 36,
  "min_per_hour": 1,
  "max_per_hour": 4,
  "slots": ["00:00", "00:15", "00:30", "01:00", "..."],
  "hour_weights": [0.0288, "..."]
}
```

The plan is rebuilt on every successful fetch. `hour_weights()` uses cumulative
counters, so the schedule reflects the whole observed history rather than a
rolling window: it stabilises over a few weeks instead of chasing single noisy
days. A decayed/rolling window would adapt faster and is an obvious next step
if the learned peaks drift.

## The workflow gate

`static.yml` now wakes on `*/15 * * * *` instead of hourly. Every wake-up runs
`scripts/should_fetch.py`, which downloads **only** when it is due:

1. no previous fetch recorded → fetch (bootstrap / after deploy);
2. last fetch < 10 min ago → **skip** (overlapping triggers cannot double-poll);
3. a scheduled slot fell inside `(last_fetch, now]` within a 45-minute grace
   window → fetch (the grace stops a delayed runner from firing on a slot that
   is hours stale);
4. nothing for more than 3 h → fetch anyway (**freshness safety net**, so a bad
   or empty schedule can never leave the data stale);
5. otherwise → skip until the next slot.

A non-scheduled run (`push`, `workflow_dispatch`) always fetches. The gate writes
`due=true|false` to `$GITHUB_OUTPUT`; `update-data.yml` gates the download,
size-check and commit steps on it, and `static.yml` skips the Pages build/deploy
when `due=false`. So a not-due wake-up costs a checkout plus the gate — upstream
is not touched and no identical snapshot is redeployed.

If `should_fetch.py` itself fails, the workflow shell fallback sets `due=true`:
fail-open, because stale prices are worse than one extra download.

## Scheduled triggers are not precise

Measured on this repository (via the Actions API), the previous `0 * * * *`
cron actually produced only **5–6 runs/day**, at irregular minutes (`:01`,
`:12`, `:18`, `:44`, …): GitHub delays and drops scheduled triggers under load.
The strategy is built around that reality instead of pretending it can hit an
exact minute:

- the plan keeps an **hourly freshness floor** (`min_per_hour=1`) so any honoured
  run inside an hour is due and staleness stays around an hour;
- the 45-minute grace window means a run delayed by 10–40 minutes still serves
  its slot instead of being thrown away;
- the 3 h freshness net guarantees a download even if every scheduled run is
  dropped for hours.

Extra density in the learned windows pays off when the platform does honour the
15-minute triggers; the floor keeps the worst case no worse than the old
hourly poll.

## Request budget

| Situation | Upstream downloads/day |
| --- | --- |
| before this change | 24 (fixed hourly) |
| default plan | ≤ 36 (24 hourly floor + 12 concentrated in learned windows) |
| with `FETCH_DAILY_BUDGET=24` | ≤ 24 while still concentrating on learned windows |
| empty/corrupt schedule | ≤ 8 from the freshness net (one every 3 h) |
| code pushes (`push` event) | unbounded by the plan, but only as often as humans push |

The planned part is structurally bounded: the gate only fires on planned slots
and the plan can never exceed `budget_per_day` quarter-hour slots. The freshness
net can add at most one extra fetch every `max_age_minutes` (8/day at the 3 h
default) when the plan is empty or broken.

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
