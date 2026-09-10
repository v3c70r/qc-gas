# Price history data — schema, granularity, retention & size budget

The price history is **real region-level data** recorded automatically by the
hourly GitHub Actions workflow (`.github/workflows/update-data.yml`). There is
no external database: everything is committed back into this repository.

## How a snapshot is produced

1. `scripts/download_data.py` downloads the latest `stations.geojson.gz`.
2. `scripts/process_data.py` normalizes it into `data/stations.json`.
3. `scripts/append_history.py` aggregates the station-level prices into
   **region-level** series and appends one bucket snapshot to the history store.
4. The workflow commits the new files and pushes them back to the repo.

## Granularity & downsampling

The UI's intraday granularity is 4–6 hours. We therefore store **6-hour UTC
buckets** (`00:00`, `06:00`, `12:00`, `18:00`), not raw hourly downloads.
Multiple workflow runs that fall inside the same 6h bucket are merged with a
weighted average (`count` weighted), plus the bucket's `min`/`max` extremes.

## Tiered retention

| Tier | File(s) | Granularity | Retention | Purpose |
| --- | --- | --- | --- | --- |
| raw | `data/history/YYYY-MM.json` | 6h bucket | last **14 days** | intraday dashboard / station sparklines |
| daily | `data/history/daily.json` | 1 day (12:00 UTC) | **12 months** | 30–365 day trend views |
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

## Station-level vs region-level history

2460+ stations × high-frequency snapshots would quickly explode repository
size, so **only region-level aggregates are stored**. A station card's history
is still derived as:

```
station price ≈ region trend (avg/min/max) + per-station constant offset
```

The offset is the station's current price minus its region's latest average,
so each station's level is anchored to its **real current price** while the
shape follows the **real region trend**. When a requested range predates the
available history, `js/history.js` shows a flat-ish extrapolated tail so the
UI remains useful; this is explicitly a graceful-degradation fallback, not a
replacement for recorded data.

## Size expectations

Measured with 18 regions, three fuels, and the short-key raw schema:

- One raw 6h bucket: **~2.9 KB** uncompressed (~0.8 KB `.br`)
- 14-day raw window: 56 buckets ≈ **~160 KB** uncompressed
- Daily tier (12 months): 365 points ≈ **~1.04 MB** uncompressed
- Monthly tier: grows **12 points/year** ≈ **~35 KB/year**

So the short-key `data/history/` store reaches ≈ **1.25 MB** after one year
and grows by only a few tens of KB per year afterwards.

The frontend-facing `data/history.json` (long field names, one series per
region) is the largest single file at ≈ **1.35 MB** uncompressed after a full
year, or ≈ **150–250 KB** as `data/history.json.br`. The whole `data/`
directory should therefore remain around **4–5 MB** (dominated by
`data/stations.json`), still far below the 40 MB guard threshold.

## Volume protection

`scripts/check_data_size.py` runs as a separate workflow step before commit.
It fails the job if the total `data/` size exceeds **40 MB** (configurable via
`MAX_DATA_MB`), and warns when any single file exceeds **90 MB** (GitHub's
per-file hard limit is 100 MB). This prevents silent repository bloat.

## Backward compatibility

`js/history.js` still parses the old synthetic shape
(`regions[region].points` / `overall.points`) and tolerates date-only strings
for daily/monthly entries. If `data/history.json` is missing or empty, the
dashboard and station cards degrade gracefully (empty chart / "no history"
message) instead of throwing.
