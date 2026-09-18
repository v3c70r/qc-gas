# Price history data — schema, granularity, retention & size budget

The price history is **real region-level data** recorded automatically by the
hourly GitHub Actions workflow (`.github/workflows/update-data.yml`). There is
no external database: everything is committed back into this repository.

## How a snapshot is produced

1. `scripts/download_data.py` downloads the latest `stations.geojson.gz`.
2. `scripts/process_data.py` normalizes it into `data/stations.json`.
3. `scripts/append_history.py` appends one **region-level** 6h bucket and one
   **station-level daily** snapshot to the history store.
4. The workflow commits the new files and pushes them back to the repo.

## Granularity & downsampling

The UI's intraday granularity is 4–6 hours. We therefore store **6-hour UTC
buckets** (`00:00`, `06:00`, `12:00`, `18:00`), not raw hourly downloads.
Multiple workflow runs that fall inside the same 6h bucket are merged with a
weighted average (`count` weighted), plus the bucket's `min`/`max` extremes.

## Tiered retention

| Tier | File(s) | Granularity | Retention | Purpose |
| --- | --- | --- | --- | --- |
| raw | `data/history/YYYY-MM.json` | 6h bucket | last **14 days** | intraday region dashboard |
| station daily | `data/history/station-history.json` | 1 day (12:00 UTC) | last **180 days** | station card real history |
| daily | `data/history/daily.json` | 1 day (12:00 UTC) | **12 months** | 30–365 day region trend views |
| monthly | `data/history/monthly.json` | 1 month (day 1, 12:00 UTC) | **forever** | long-term trend view |
| merged | `data/history.json` | mixed (raw+daily+monthly) | mirror of above | frontend single-file read |
| index | `data/history/index.json` | — | — | regions, last source, tier config |

Tiers are **non-overlapping**: raw covers the newest 14 days, daily covers the
remainder of the 12-month window, and monthly covers everything older than
12 months. `data/history.json` is rebuilt on every run in the original
frontend format, so the dashboard and station cards keep working unchanged.

## Storage format & sharding

Raw snapshots are sharded by month (`data/history/YYYY-MM.json`) and use short
keys to keep repository growth small:

- `t` = bucket timestamp (UTC ISO 8601)
- `src` = exact source `generated_at` timestamp
- `r` = region map (region name → fuel map)
- fuel keys: `g` = regular, `s` = super, `d` = diesel
- metric keys: `a` = avg, `n` = min, `x` = max, `c` = count
- `__overall__` = province-wide aggregate

Example (one raw snapshot):

```json
{
  "t": "2026-09-10T18:00:00Z",
  "src": "2026-09-10T18:05:03Z",
  "r": {
    "Montréal": {
      "g": {"a": 170.5, "n": 169.0, "x": 172.0, "c": 220},
      "s": {"a": 188.0, "n": 187.0, "x": 190.0, "c": 220},
      "d": {"a": 180.0, "n": 179.0, "x": 181.5, "c": 180}
    },
    "__overall__": {"g": {"a": 171.2, "n": 169.0, "x": 172.0, "c": 2452}}
  }
}
```

The frontend-facing `data/history.json` uses the longer, original field names:

```json
{
  "regions": {"Montréal": {"points": [{"date": "...", "regular": {"avg": 170.5, "min": 169.0, "max": 172.0}, "...": "..."}]}},
  "overall": {"points": []},
  "metadata": {"generated_at": "...", "latest": "...", "interval_hours": 6, "regions": [], "source": "real", "tiers": {}}
}
```

Every JSON file under `data/` also gets a `.br` (Brotli) sibling during the
workflow (`data/*.json.br`, `data/history/*.json.br`), reusing the existing
Brotli step.

## Station-level history storage

Stations have no stable id in the source data, so the recorder keys each
station by its coordinates rounded to 5 decimals
(`lng,lat`, e.g. `-79.01957,48.24676`). Each day it stores one scalar price
per fuel (the latest snapshot of that UTC day) using the same short fuel keys
as the region store (`g`/`s`/`d`). The payload is column-oriented so the
coordinate key is written only once, not once per day:

```json
{
  "v": 1,
  "tiers": {"daily_days": 180, "monthly": "none"},
  "t": ["2026-09-10", "2026-09-11"],
  "s": {
    "-79.01957,48.24676": {
      "g": [179.9, 180.4],
      "s": [224.9, null],
      "d": [254.9, 255.1]
    }
  }
}
```

`t` is the sorted list of retained day keys. Each fuel array is aligned with
`t`; `null` means that station did not report on that day. Older than 180 days
is **pruned** (not rolled up), so the file reaches a steady size and does not
grow indefinitely.

`js/history.js` loads this file through `loadStationHistoryData()` and
`getStationHistory()` matches the currently rendered station to the same
coordinate key. It returns `null` when no recorded history exists, and the
station card shows the empty `noHistory` placeholder with no change percentage
and no extrapolated tail.

## Station-level vs region-level history

Region-level history is stored at 6h/daily/monthly tiers. Station-level
history is stored **once per day** and only contains prices actually observed
for that station. A station card therefore only draws a curve when real
recorded station data exists; it never falls back to the region average or a
per-station offset.

## Size expectations

Measured with 18 regions, three fuels, 2459 stations, and the short-key
schemas:

Region tier:

- One raw 6h bucket: **~2.9 KB** uncompressed (~0.8 KB `.br`)
- 14-day raw window: 56 buckets ≈ **~160 KB** uncompressed
- Daily tier (12 months): 365 points ≈ **~1.04 MB** uncompressed
- Monthly tier: grows **12 points/year** ≈ **~35 KB/year**

Station daily tier:

- First recorded day: **~139 KB** uncompressed / **~20 KB** `.br`
- Each additional day adds ~35 KB raw, so the 180-day steady state is
  **~7 MB** uncompressed / **~1 MB** `.br`
- Older days are pruned, so this file reaches a steady size and has **~0 MB/year**
  ongoing growth at a fixed station count.

The frontend-facing `data/history.json` (long field names, one series per
region) is the largest single file at ≈ **1.35 MB** uncompressed after a full
year, or ≈ **150–250 KB** as `data/history.json.br`. With the station daily
tier included, the whole `data/` directory should remain around **~12 MB** at
steady state, still far below the 40 MB guard threshold.

## Volume protection

`scripts/check_data_size.py` runs as a separate workflow step before commit.
It fails the job if the total `data/` size exceeds **40 MB** (configurable via
`MAX_DATA_MB`), and warns when any single file exceeds **90 MB** (GitHub's
per-file hard limit is 100 MB). This prevents silent repository bloat.

## Backward compatibility

`js/history.js` still parses the region shape (`regions[region].points` /
`overall.points`) and tolerates date-only strings for daily/monthly entries.
`loadStationHistoryData()` tolerates a missing
`data/history/station-history.json` (404 → no station chart). If either
history file is missing or empty, the dashboard and station cards degrade
gracefully (empty chart / "no history" message) instead of throwing.
