# Price history data — schema, granularity, retention & size budget

The price history is **real region-level data** recorded automatically by the
GitHub Actions data workflow (`.github/workflows/update-data.yml`). Which runs
may download is decided by the learned, budget-bounded schedule described in
[`fetch-strategy.md`](./fetch-strategy.md) — since #91 an explicit local
**timetable** (busy 20 min, local midnight 10 min, night 1/h ≈ 65 fetches/day).
The workflow is woken every 5 minutes by a **local `crontab`**
(`scripts/install_dispatch_cron.sh` → `repository_dispatch`) and by one hourly
fallback cron; the gate admits exactly the timetable's slots, so an off-schedule
wake-up costs a checkout + gate and nothing else (see
[Fetch rhythm](#fetch-rhythm-final)). The upstream snapshot itself is
regenerated every 5 minutes. There is no external database:
everything is committed back into this repository.

## How a snapshot is produced

1. `scripts/should_fetch.py` gates the run (adaptive schedule + freshness net).
2. `scripts/download_data.py` downloads the latest `stations.geojson.gz`.
3. `scripts/process_data.py` normalizes it into `data/stations.json`.
4. `scripts/update_profile.py` diffs the new snapshot against the previous
   observation and rewrites `data/update-profile.json` (learned change hours),
   `data/history/station-changes.json` (per-station `last_changed_at` /
   `last_seen_at`, issue #68) and `data/fetch-schedule.json` (plan for the next
   runs).
5. `scripts/append_history.py` appends one **region-level** 6h bucket and one
   **station-level daily** snapshot to the history store.
6. `scripts/metrics.py` folds the station change store, the snapshot and the
   previous ledger into `data/metrics.json` — the latency / freshness numbers
   below.
7. The workflow commits the **persistent** files and pushes them back to the
   repo; the **derived** snapshots travel to the Pages build as a workflow
   artifact instead (issue #103, see [Derived snapshots vs persistent
   state](#derived-snapshots-vs-persistent-state-issue-103)).

## Derived snapshots vs persistent state (issue #103)

Every file under `data/` belongs to exactly one of **two layers**. Before #103
the workflow did `git add data/`, so each admitted fetch (~65/day) committed
**19 files**, and most of them are regenerated from scratch on the next run.
The measured pack growth was ≈24.5 MiB/day (`git count-objects -vH` after
`git gc`), i.e. only ~32 days of headroom under the 1 GB soft limit.

| Layer | Files | Fate |
| --- | --- | --- |
| **Derived snapshots** | `data/stations.json`, `data/stats.json`, `data/metrics.json`, `data/history.json` | written by the pipeline, uploaded as the `data-snapshot` workflow artifact, restored into `data/` by the Pages build — **never committed** |
| **Build output** | every `*.json.br` sibling | artifact only, never committed (nothing reads them; see [Storage format](#storage-format--sharding)) |
| **Persistent state** | `data/history/**` (`YYYY-MM` raw shards, `daily.json`, `monthly.json`, `index.json`, `station-history.json`, `station-changes.json`), `data/update-profile.json`, `data/fetch-log.json`, `data/fetch-schedule.json`, `data/fetch-state.json`, `data/metrics-ledger.json`, `data/regie-margin.json`, `data/fetch-interval.json`, `data/trigger-interval.json` | committed on every fetch — this is the only copy that exists |

Why each derived file is derived:

- `data/stations.json` / `data/stats.json` — rebuilt from the upstream snapshot
  by `download_data.py` + `process_data.py` on every admitted poll;
- `data/history.json` — rebuilt on **every** run from the tier files
  (`append_history.py` → `build_merged`), i.e. a frontend mirror;
- `data/metrics.json` — recomputed digest; the only state it carried (the
  rolling latency ledger) moved to the persistent `data/metrics-ledger.json`,
  otherwise every run would report a single-sample latency again.

The partition itself is code, not prose: `scripts/data_layers.py` holds the two
lists, the workflow gets its `git add` pathspecs and its artifact paths from it
(`--pathspec-file`, `--github-output`), `--check` fails the run when a file
under `data/` belongs to neither layer, and `scripts/test_data_layers.py` pins
the wiring (no derived file tracked by git, no `.br` tracked by git, artifact
uploaded in `update-data.yml`, restored in `static.yml` before `cp -r data dist/`).

### Measured effect (per data commit)

Attributed with `git verify-pack -v` (size-in-packfile of the blobs each data
commit added) over the last Actions data commits before the change:

| File | Pack bytes per commit | Layer |
| --- | --- | --- |
| `data/stations.json.br` | 42.0 KiB | build output |
| `data/history/station-history.json.br` | 38.9 KiB | build output |
| `data/history/station-history.json` | 18.8 KiB | **persistent** |
| `data/history/station-changes.json.br` | 13.6 KiB | build output |
| `data/stations.json` | 12.2 KiB | derived |
| `data/history/station-changes.json` | 12.0 KiB | **persistent** |
| `data/history.json.br` | 9.2 KiB | build output |
| `data/history/2026-10.json.br` | 6.9 KiB | build output |
| `data/history.json` | 4.2 KiB | derived |
| `data/history/2026-10.json` | 3.1 KiB | **persistent** |
| `data/metrics.json` + `.br` | 1.4 KiB | derived |
| `data/update-profile.json` | 0.9 KiB | **persistent** |
| `data/stats.json` + `.br`, `data/history/index.json` + `.br` | 0.6 KiB | derived / persistent |
| `data/fetch-schedule.json`, `data/fetch-state.json` | 0.2 KiB | **persistent** |

Replaying the last **44 data commits** (2026-10-17 12:22Z → 19:40Z, 7.3 h) into
a scratch repository and measuring `git gc --prune=now` + `git count-objects -v`
after the first snapshot makes the two layers directly comparable:

| | Before #103 (commit everything) | After #103 (persistent only) |
| --- | --- | --- |
| Files per data commit | 19 | **7** (`8` when a month shard or the Régie bulletin rolls over) |
| Pack growth over the 7.3 h window | 8 257 KiB | **858 KiB** |
| Extrapolated per day (same commit rate) | ≈26.5 MiB/day (matches the 24.5 MiB/day measured in #103) | **≈2.8 MiB/day** |
| Pack bytes per commit | ≈225 KiB | **≈35 KiB** |

Half of the old growth was the `.br` siblings, which is why the fix is "stop
committing them" rather than "compress the text better". `data/stations.json` is
still ~886 KB per snapshot in the workspace and in the artifact — it is simply
no longer in git history 65 times a day.

### Why the site still shows the newest snapshot

- The Pages build (`static.yml` → `build-and-deploy`) only runs when the gate
  said `due=true` (`needs.update-data.outputs.due == 'true'`), i.e. exactly when
  a fresh snapshot was produced. `update-data.yml` uploads it with
  `actions/upload-artifact` and the build restores it into `data/` before
  `cp -r data dist/` (`actions/download-artifact`), so the deployed
  `data/stations.json` is the one from that run (`metadata.generated_at` = the
  upstream timestamp that triggered it).
- A non-due wake-up deploys nothing (unchanged), so "no `stations.json` in the
  repository" never means "stale site".
- Because the snapshots are no longer in git, a failed step can no longer fall
  back to the committed copy. The data job fails loudly (`Require the derived
  snapshots the site awaits`) when `data/stations.json` or `data/history.json`
  is missing — the two files the frontend awaits without a catch
  (`js/map.js`, `js/dashboard.js` → `loadHistoryData()`). Failing the job also
  skips the Pages job, so the **previous** deployment keeps serving the last good
  snapshot instead of a site whose map/dashboard 404s. `stats.json` and
  `metrics.json` are *not* required: no frontend code reads them and
  `metrics.py` is deliberately `continue-on-error` (#70), so a metrics bug must
  not be able to block every deploy.

### Adding a new data file

1. Write it inside `data/` from one of the pipeline scripts.
2. Classify it in `scripts/data_layers.py`: a **derived** snapshot (regenerated
   every fetch / rebuilt from another committed file) goes into
   `DERIVED_FILES`; anything that cannot be recomputed goes into
   `PERSISTENT_FILES` (or `PERSISTENT_DIRS`). Add its `.br` to the workflow's
   compression list if the frontend should be able to fetch a compressed copy.
3. Document it in this file (the table above and, if it has a retention rule,
   the [tiered retention](#tiered-retention) table).
4. Run `python3 -m unittest scripts.test_data_layers` — an unclassified file
   fails both the unit test and `python3 scripts/data_layers.py --check` in the
   workflow.

### Rollout note

The first `git pull` in a long-lived worktree (the probe's `master` worktree, an
agent checkout) deletes the snapshots that used to be tracked while they carry
local modifications. The probe recovers on its own: `sync_with_remote()` sees
only `data/`-local changes, resets to upstream, and regenerates the derived
files in the next cycle (`.agent/probe.log` shows one `pull --rebase failed`
line). Nothing is committed from a stale snapshot either way.

### Tests and local development without the snapshot

A fresh clone or `git worktree` has neither `data/stations.json` nor
`data/history.json` (both derived). The browser tests therefore go through
`tests/fixtures/data-snapshots.js`: when a file is absent it restores a committed
gzip of one real snapshot (`stations.sample.json.gz` ~100 KB,
`history.sample.json.gz` ~26 KB, both static) into `data/`, so the specs still
see the real 2 463-station set and all 18 regions — density and breadth matter
("≤ 25 km around Montréal" needs > 200 stations; the ranking specs need every
region). `data/history.json` is required for the same reason the deploy guard
requires it: `js/dashboard.js` awaits `loadHistoryData()` without a catch, so
without the file the dashboard panel never opens (that was the functional-test
failure in #103 review round 2). `stats.json` / `metrics.json` are not restored —
no frontend code reads them.

Local development can regenerate the real files by running the pipeline once
(`python3 scripts/download_data.py && python3 scripts/process_data.py &&
python3 scripts/append_history.py`), which is what the probe does every 15
minutes anyway.

Criterion 2 of #103 (pack growth < 10 MiB/day measured with
`git count-objects -vH` after `git gc --prune=now`) is a **production**
measurement: it needs ≈ a day of real Actions commits on `master` after this
change, not a scratch replay. The replay above is the pre-merge estimate.

## Fetch rhythm (final)

### The shipped rhythm

| Knob | Value | Where | Why |
| --- | --- | --- | --- |
| Timetable (the plan) | busy 06:00–23:00 every **20 min**, local 00:00 every 10 min, 01:00–05:00 hourly ≈ **65 fetches/day** | `data/fetch-schedule.json` `timetable` (written by `scripts/update_profile.py` / `scripts/schedule_plan.py`) | #91: night carries ~1/121 of the day's re-prices, so an hourly night floor is correct; the busy window is what the staleness metric is made of |
| **Local wake-up** | `*/5 * * * *` (every 5 min) | `scripts/install_dispatch_cron.sh` (local `crontab`, marked `# qc-gas-dispatch`) | #93: a crontab line is all it takes, and a timetable change needs **no** crontab edit — the gate reads the committed timetable |
| Primary trigger | `POST /repos/:owner/:repo/dispatches` with `event_type: fetch-data` | `.github/workflows/static.yml` (`repository_dispatch`) | A dispatch is an external wake-up: the gate judges it against the timetable (off-slot ⇒ `due=false`, no download/commit/deploy) but it does **not** consume the plan budget |
| Fallback trigger | `cron: '17 * * * *'` (hourly, off the hour) | `.github/workflows/static.yml` (`schedule`) | GitHub throttles scheduled runs (measured below); the hourly wake only has to catch up when the machine is off |
| Plan budget | `FETCH_DAILY_BUDGET = 65` | `env:` of the `update-data` job | = the timetable's slots/day (#91/#93). It must not be smaller, otherwise the next successful fetch rewrites `fetch-schedule.json` with a coarser plan and breaks the busy-hour cadence the dispatch exists for |
| Freshness net | `FETCH_MAX_AGE_MINUTES = 120` | same `env:` block | = 2 × the **fallback** cron interval: the net only fires after two consecutive fallback wakes were dropped (the 5-minute dispatch wakes are far denser and are gated by the timetable, not by this net) |
| Upstream regeneration | every 5 minutes (quarter-hour grid, measured 2026-10-11: `04:45:01 → 04:50:01 → 04:55:01`) | upstream `metadata.generated_at` | A 20-minute busy cadence does not hit the upstream ceiling |

`scripts/test_trigger_interval_report.py::ShippedRhythmTest` pins this table: if
one knob moves without the others, the suite fails (`budget == timetable
slots/day`, `max_age == 2 × fallback cron interval`, `wake-up ≤ busy p90`), and
`scripts/test_install_dispatch_cron.py` pins the installer itself (idempotent,
removable, preflight-failing, token never written into the crontab).

### Evidence: the actions cron is throttled (measured, not inferred)

The **needed** interval comes from #76's staleness study
([`fetch-interval-report.md`](./fetch-interval-report.md), driven by
`scripts/interval_report.py`); this section is the complementary measurement of
whether the shipped triggers actually deliver #91's timetable.

`scripts/trigger_interval_report.py` reads **only committed records**
(the commit history of `data/stations.json` — historical era — plus
`data/fetch-state.json`, which the gate rewrites on every admitted run and the
local probe never touches; the probe ledger `data/fetch-log.json`; and the
committed timetable `data/fetch-schedule.json`) and reports the *measured*
interval distribution per trigger source. Issue #103 removed the snapshot from
git, hence the second path: several days of `fetch-state.json` history is a
cleaner record than the snapshot ever was (it is written even when the upstream
snapshot did not change). Since #93 the criterion is
**busy hours (local 06:00–23:59) p90 ≤ 20 minutes over a window of ≥ 12 hours**,
plus a **stale share ≤ 5 %** for the timetable source (simulated by #91's
`scripts/schedule_eval.py` on the measured probe series). The old global
"p90 ≤ 30 min" rule failed a correct plan *by construction*, because the
01:00–05:00 night floor is deliberately hourly.

```bash
python3 scripts/trigger_interval_report.py           # -> data/trigger-interval.json
python3 scripts/trigger_interval_report.py --since 2026-09-25T17:00:00Z   # one rhythm era
```

The report is **not** run by the fetch workflow: it needs the full commit
history, while the workflow checks out with `fetch-depth: 1` (a full clone here
is ~170 MB on every wake-up). Refresh the committed evidence by running the
command after any trigger change and again ≥ 12 h later — the PM agent cites
`data/trigger-interval.json`, not the plan's intentions.

Measured on 2026-10-16 (this repository, before the local crontab was installed):

| Source | Fetches | Span | p50 | busy p90 | max | > 20 min (busy) | Stale share | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| actions (full history) | 1605 | 3984 h | 121.3 min | 267.0 min | 941 min | 99 % | — | **misses** |
| actions since 2026-09-25 (cron `*/15`) | 90 | 399 h | 257 min | 405.7 min | 537.9 min | 99 % | — | **misses** |
| local probe | 402 | 100 h | 15 min | 15 min | 15 min | 0 % | — | **meets** |
| local dispatch (committed timetable) | 65/day | 114.6 h | 20 min | 20 min | 20 min | 0 % | 1.25 % | **meets** |

The first two rows are the bulk of the repository's history, i.e. the era when
the cron *was* hourly and then `*/15`: even then the real cadence was ~2 h
(p50), and `*/15` made it *worse* (p50 257 min) — GitHub throttles
high-frequency schedules harder. **Conclusion: no `schedule` shape on this
repository can reach the busy criterion, so the cadence has to come from the
dispatch trigger.**

The `local-dispatch` row judges the chain built in #93: the local crontab wakes
the workflow every 5 minutes, the gate admits only the timetable's slots, so the
source's *cadence is the committed timetable* (busy p90 exactly 20 min), and the
staleness share is `schedule_eval.py`'s simulation over the measured series
(114.6 h, 20 718 re-prices, 2 465 stations): **1.25 %**, far inside the 5 %
budget. It is "plan guarantee + measured staleness", not a 12-hour commit
window; once real dispatch commits have accumulated, the `actions` row is its
measured counterpart (run the script again).

### External trigger: step-by-step setup

Nothing in the repository stores a secret; the token lives only in the
external scheduler's environment.

1. **Create a minimal fine-grained PAT** — GitHub → *Settings → Developer
   settings → Personal access tokens → Fine-grained tokens*. Scope it to **this
   repository only** and grant **Contents: Read and write** (`contents:write`).
   That is the only permission `repository_dispatch` needs (it is a write to
   the repo); do **not** add Actions, Issues, Secrets or `repo` scope. Set the
   shortest expiry you can live with and rotate it.
2. **Trigger the workflow once by hand** to prove the wiring:

   ```bash
   export GH_DISPATCH_TOKEN=github_pat_...        # never committed, never echoed
   export GH_DISPATCH_REPO=<owner>/<repo>         # optional: defaults to origin
   scripts/dispatch_fetch.sh                      # -> POST .../dispatches {"event_type":"fetch-data"}
   ```

   `scripts/dispatch_fetch.sh` exits `2` with the minimal-scope reminder when the
   token is unset, `3` on unusable input (repo name or event type), and `4` when
   the dispatch request itself fails — a wrong-scope PAT, a renamed repo or a
   network blip therefore shows up as a **cron failure** instead of a silent
   no-op that only the 12-hour evidence report would catch. It never prints the
   token. `static.yml` accepts the dispatch
   (`repository_dispatch: types: [fetch-data]`).
3. **Schedule it**: use the repository's own installer — it puts exactly one
   marked line (`# qc-gas-dispatch`) into your local `crontab`, waking the
   workflow every 5 minutes, and the gate decides which of those wake-ups may
   actually download (see [Local wake-up](#local-wake-up-crontab-issue-93)):

   ```bash
   scripts/install_dispatch_cron.sh --dry-run   # show the line, touch nothing
   scripts/install_dispatch_cron.sh             # install (idempotent)
   scripts/install_dispatch_cron.sh --remove    # uninstall (removes only that line)
   ```

   Any other scheduler works too (cron-job.org, EasyCron, a cron in another
   repo): *URL* `https://api.github.com/repos/<owner>/<repo>/dispatches`,
   *method* `POST`, headers `Authorization: Bearer <PAT>`,
   `Accept: application/vnd.github+json`, `Content-Type: application/json`,
   body `{"event_type": "fetch-data"}`, every 5 minutes.

   `scripts/install_dispatch_cron.sh` exits non-zero (**2**) instead of
   installing when the self-check fails (no `crontab`, no usable token, or
   `scripts/dispatch_fetch.sh` not executable) — a cron entry that can never
   work must not be installed silently.

   Nothing to deploy on our side: the dispatch wakes the *existing* workflow
   (data fetch + Pages deploy) and the gate skips the download, the commit and
   the deploy when it decides no poll is due. A wake-up costs one
   checkout + gate (~20–30 s); an admitted poll is at most one upstream request.
4. **Verify with evidence** (do not trust the cron service's own logs): run
   `python3 scripts/trigger_interval_report.py` and check that the
   `local-dispatch` source is reported `meets` (busy-hour p90 ≤ 20 min, stale
   share ≤ 5 %), then re-run it after ≥ 12 h so the observed `actions` row can
   confirm the real dispatch cadence; commit the refreshed
   `data/trigger-interval.json`. For a single admitted run, check
   `gh run view --json jobs`: the gate step must print `due=true` and the run's
   `event` must be `repository_dispatch`.

### Local wake-up (crontab, issue #93)

The local invoker is deliberately dumb: **wake every 5 minutes, let the gate
decide**. Why not write the timetable into crontab itself:

- one line, and a timetable change needs **zero** crontab edits (the gate reads
  `data/fetch-schedule.json` from the checkout);
- an off-slot wake-up only costs a checkout + the gate — no upstream download,
  no data commit, no Pages deploy (idempotent and cheap);
- Actions minutes on a public repository are free, so ~288 wake-ups/day
  (~20–30 s each) are not a budget problem.

**Install / inspect / uninstall** (`--dry-run` prints the exact line without
touching crontab; the installer is idempotent — running it twice still leaves
one marked line):

```bash
crontab -l | grep qc-gas-dispatch            # the installed line (exactly one)
scripts/install_dispatch_cron.sh --remove    # uninstall (no token needed)
scripts/install_dispatch_cron.sh --remove --dry-run   # show what would survive
```

Uninstalling is deliberately **not** gated by the install preflight: a broken
`gh` login, a deleted token file or a `dispatch_fetch.sh` without its execute bit
are exactly the situations in which the installed line is useless and has to go,
so `--remove` only needs `crontab` itself. `--dry-run --remove` prints the
surviving crontab and touches nothing.

The installed line (token fetched at run time, never stored in crontab):

```cron
*/5 * * * * cd <repo> && mkdir -p .agent && { [ ! -s .agent/dispatch-cron.log ] || [ "$(wc -c < .agent/dispatch-cron.log)" -lt 1048576 ] || mv -f .agent/dispatch-cron.log .agent/dispatch-cron.log.1 ; } && GH_DISPATCH_REPO=<owner>/<repo> GH_DISPATCH_TOKEN="$(gh auth token --user <owner>)" scripts/dispatch_fetch.sh >> .agent/dispatch-cron.log 2>&1 ; rc=$? ; echo "[$(date -u -Iseconds)] exit=$rc (qc-gas-dispatch)" >> .agent/dispatch-cron.log # qc-gas-dispatch
```

- **Token**: `$(gh auth token ...)` is resolved on every wake-up, so no secret
  is written into the crontab. If `gh` cannot serve a token from a cron
  environment (no keyring/DBUS, several accounts), the installer falls back to
  reading a **600-permission** local file (`~/.config/qc-gas/dispatch-token`,
  override with `QC_GAS_TOKEN_FILE`), and refuses to install if that file's mode
  is not `600`. Neither path ever puts the token in the repository, the crontab
  or the log.
- **Log**: `.agent/dispatch-cron.log` (already covered by `.gitignore`'s
  `.agent/**/*.log`). Each wake-up appends `[<UTC time>] exit=<code>` plus the
  script's own output, so a missing token shows up as `exit=2` with the explicit
  "GH_DISPATCH_TOKEN 未设置" message instead of a silent no-op. The file is
  rotated to `.agent/dispatch-cron.log.1` once it passes `QC_GAS_LOG_MAX_BYTES`
  (default 1 MiB), so local logging stays bounded.
- **Move the repository**: re-run the installer after a `git clone` to another
  path — the line embeds the absolute path (and `--repo` / `QC_GAS_DISPATCH_REPO`
  if `origin` is not a GitHub remote).

### How to tune it

Three places, changed together (the consistency test enforces the coupling):

| What | Where | Rule |
| --- | --- | --- |
| Local wake-up | `scripts/install_dispatch_cron.sh` (`DEFAULT_WAKE_MINUTES`) | must be **no sparser** than the busy-hour timetable gap (5 min ≤ 20 min); re-run the installer to apply |
| `FETCH_DAILY_BUDGET` | `.github/workflows/update-data.yml` `env:` | `= the committed timetable's slots/day` (65 since #91); a smaller value would make the next fetch rewrite the plan coarser |
| `FETCH_MAX_AGE_MINUTES` | same `env:` block | `= 2 × the fallback cron interval` (120 at the hourly fallback) |

The timetable itself is written by `scripts/update_profile.py` from
`scripts/schedule_plan.py` (gap caps: busy 20 min, midnight 10 min, night 60 min);
running it with a different `FETCH_DAILY_BUDGET` picks a different shape, and the
report then fails its consistency check until the env var matches again.

Both env vars are the single source of truth for the gate and the plan writer
(`scripts/should_fetch.py` / `scripts/update_profile.py`); the constants in
`scripts/fetch_strategy.py` are only the local fallback for tests and manual
runs, and are kept in sync with the workflow. Changing any of them means running
`python3 -m unittest scripts.test_trigger_interval_report` and refreshing
`data/trigger-interval.json` (the report fails its consistency check by design
when the knobs disagree).

`data/fetch-schedule.json` is derived state and only picks a new budget up on
the next successful fetch (`update_profile.py`); until then the gate keeps
evaluating the file's existing `slots`, so nothing has to be edited by hand. To
rebuild it immediately from the committed profile, see
[`fetch-strategy.md` §How to tune it](./fetch-strategy.md#how-to-tune-it).

One upstream request per admitted run is unchanged (`download_data.py` does not
retry-storm; the `User-Agent` is untouched), and the plan budget equals the
committed timetable's slots/day — the 5-minute wake-ups are gated by that
timetable, so they cannot add upstream load on their own.

## 本机高频探针（**长期补充**，issue #74 / #77）

Actions 的 `schedule` 被 GitHub 严重节流（实测：全历史 p50 120 分钟、`*/15` 时期
p50 257 分钟，见 [Fetch rhythm](#fetch-rhythm-final)），而外部触发器
（`repository_dispatch`）需要 owner 自己配一个外部 cron + PAT。在两个条件都落地
之前，**本机探针是唯一能达到 15 分钟新鲜度的机制**，因此它的角色从
「临时/学习用」定为「**长期补充**」（#77 的决定）：

- 它保证**数据新鲜度**（15 分钟），并在上游/闸门出问题时提供可对照的第二条链路；
- 它继续为 #76 的「必要间隔」评估和本脚本的实测间隔提供 ground truth
  （`data/fetch-log.json`）；
- **它不计入 Actions 抓取预算，也不写 `data/fetch-state.json`**（#93 明确保留）：
  探针走自己的提交主题、自己的 `data/fetch-log.json` 台账，从不调用
  `should_fetch.py`，也不改 `fetch-state`，所以「上游请求预算是多少」的唯一
  真值源仍然是 Actions 侧闸门的状态文件，不会因为本机探针而变得无法解释。
- **退场条件**（写死在这里，避免“临时”变成永久）：本机 dispatch 链路（#93）
  就绪后，`python3 scripts/trigger_interval_report.py` 连续 ≥ 3 天把
  `actions` 判为 `meets`（忙时 p90 ≤ 20 分钟、陈旧占比 ≤ 5 %、窗口 ≥ 12 小时）
  → 用 `tmux kill-session -t probe` 停掉探针，并新建一条 issue 记录停用时的证据。
  在探针停用前，Actions 侧的数据提交继续保留（两边都只在有变化时提交）。

探针的具体行为：

- `scripts/local_fetch_loop.sh`（tmux 常驻）：每 ~15 分钟跑一遍与
  `update-data.yml` 相同的数据链路 —— `download_data.py` →
  `process_data.py` → `update_profile.py` → `scripts/probe_events.py append` →
  `append_history.py` → `metrics.py` —— 再跑一次**只报不堵**的
  `check_data_size.py`，然后复用工作流**同一组** brotli 文件（`stations` / `stats`
  / `history` / `regie-margin` / `metrics` + `data/history/*.json`，不用通配，
  以免为 `fetch-log` / `fetch-state` / `fetch-schedule` / `update-profile`
  生成永不使用的 `.br`），最后只提交**小账本**（见下）。
- **样本外置（issue #92）**：探针**不再提交快照** —— `stations.json`(+`.br`)、
  `history.json`、`history/station-changes.json`(+`.br`)、`stats.json`(+`.br`)、
  `metrics.json`(+`.br`) 照旧由链路写盘（本地分析仍可用完整快照），但**永不
  `git add`**；它们过去占**约 39 MiB/天**的仓库增长，而探针的推送不会触发站点
  部署（`static.yml` 的 `push.paths` 不含 `data/**`），对用户零价值。
- **每轮真相 → 本地事件日志** `data/probe-local/events.jsonl`（目录已被
  `.gitignore` 覆盖）：每轮追加一行
  `{"t": "2026-10-16T20:04:25Z", "changed": 135, "keys": [...], "stations": 2465}`
  —— `t` = 本轮抓取时刻（`station-changes.json` 的 `last.at`）、`changed` =
  本轮重定价站点数（就是 `update-profile.json` 的 `changed_stations_exact`）、
  `keys` = 变更站点坐标键（截断到 100 个）、`stations` = 本轮观测站点数（陈旧
  占比的分母）。体积目标 < 300 KB/天；逻辑在 `scripts/probe_events.py`，
  被 `interval_report.py` / `schedule_eval.py` 通过 `--events-file` 直接消费
  （文件缺失时二者回退 git 修订历史，CI 不报错）。
- **提交节流**：仓库里**每小时最多提交一次**、只在内容确有变化时提交，且只提交
  小文件 `data/update-profile.json`、`data/fetch-log.json`、
  `data/fetch-schedule.json`（`PROBE_COMMIT_INTERVAL_SECONDS`，默认 3600）。
  上一轮推送若给了，本地提交保留、下一轮继续重推，不受节流影响。
- **与 Actions 的差异**：不调用 `fetch_bulletin.py`（每周 PDF 周报，与高频探测
  无关）与 `should_fetch.py`；`check_data_size.py` 只记录警告，不会阻断探针
  （工作流里它是阻断提交的）。除这些之外二者共用同一批脚本，没有第二套抓取实现。
- **周期节奏**：计时从周期开始算，`sleep` 只补足 `PROBE_INTERVAL_SECONDS`
  的**剩余**时间（`work=…s sleep=…s`）；固定 `sleep 900` 会把观测间隔拉成
  `900 s + 链路耗时`，从而歪偏 #76 的评估。
- 它**故意绕过** `scripts/should_fetch.py`（本机这一轮要的是无条件的高频采样，
  不是闸门决策），也**不复制任何抓取逻辑**：所有上游请求仍然只由
  `download_data.py` 发出；`should_fetch.py` 的判定逻辑与 Actions 侧行为**不变**。
  `#93` 的 5 分钟 dispatch 唤醒是**另一条链路**（它确实过闸门、过时刻表），两者
  互不代替：探针给的是 15 分钟的无条件新鲜度，dispatch 给的是按时刻表的低成本
  采样，探针台账从不进 Actions 的预算或 `fetch-state`。
- 每次完成的周期会把时间戳追加到 `data/fetch-log.json`（时间戳数组，保留最近
  2000 条 ≈ 15 天），并记录按天的 `ok` / `failed` / `retries` 计数与
  `summary_sent`（当日是否已发健康汇报）。这些文件随 `data/` 一起提交，所以
  间隔分布是**真实记录**，可被 #76（必要间隔）与 #77
  （`scripts/trigger_interval_report.py`，实测触发间隔）直接计算，不需要人工估算。
- 探针每天（本地日期）最多发一条健康摘要到跟踪 issue #78
  （`gh issue comment`，字段见下）；发送失败只写日志，不影响抓取循环。
- 运行日志写在 `.agent/probe.log`（`.gitignore` 已忽略 `.agent/**/*.log`）。

### 独立工作树（**必须**，issue #92）

探针与 agent **不得共用工作区**：实测 agent 在 `agent/*` 分支改文件时，探针的
数据提交会落到 agent 分支上（连续 3 轮推送失败 ⇒ 账本进不了 master），而
`sync_with_remote()` 的 `reset --hard '@{u}'` 会直接抹掉 agent 未提交的工作。
因此：

1. **运行时分支守卫**：当前分支 ≠ `PROBE_BASE_BRANCH`（默认 `master`）时，探针
   **立即以退出码 2 退出**并写明原因（绝不静默把数据提交到功能分支）；
2. **用 `git worktree add` 建独立工作树**（共享对象库、几乎不额外占盘）：

```bash
# 在仓库副本旁创建独立的 master 工作树
cd <repo> && git worktree add ../qc-gas-probe master

# 启动（固定会话名 probe）
tmux new -d -s probe -c ../qc-gas-probe \
  && tmux send-keys -t probe 'bash scripts/local_fetch_loop.sh' Enter

# 查看进度
tmux attach -t probe          # Ctrl-b d 脱离；cat ../qc-gas-probe/.agent/probe.log 也可
tmux kill-session -t probe    # 停止
```

3. `sync_with_remote()` 里唯一具有破坏性的 `git reset --hard '@{u}'` 只在**未提交
   改动全部位于 `data/`**（探针自己的足迹）时才执行；只要出现 `data/` 之外的
   脏文件（agent 的在途改动），它**拒绝回退**并把原因写进日志。

机器休眠/重启后循环自然停止，重新执行启动命令即可恢复；探针**不会**自动
拉起，也不会修改任何 workflow。

> **改脚本后必须重启循环**：`local_fetch_loop.sh` 是常驻的 bash 进程，已经启动的
> 会话继续执行**加载时**的版本。合入改动（例如 #92 停止提交快照）后要
> `tmux kill-session -t probe` 再按上面的命令启动，否则探针仍按旧逻辑每 15 分钟
> 提交一次全量 `data/` 快照。

### 可用环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `PROBE_BASE_BRANCH` | `master` | 允许运行的分支；其它分支立即退出（码 2） |
| `PROBE_INTERVAL_SECONDS` | `900` | 周期间隔（15 分钟） |
| `PROBE_COMMIT_INTERVAL_SECONDS` | `3600` | 两次数据提交之间的最小间隔（每小时最多一次） |
| `PROBE_MAX_PUSH_RETRIES` | `3` | 推送被拒后 `pull --rebase` 重试次数（退避 15 s） |
| `PROBE_MAX_CYCLES` | `0` | `0` = 常驻；`1` 可用于冒烟测试 |
| `PROBE_SKIP_PUSH` | 未设 | 非空则只本地提交、不推送 |
| `PROBE_SKIP_SUMMARY` | 未设 | 非空则不评论健康摘要 |
| `PROBE_TRACKING_ISSUE` | `78` | 健康摘要发到哪个 issue |
| `PROBE_EVENTS_FILE` | `data/probe-local/events.jsonl` | 本地事件日志（gitignore） |
| `PROBE_LOG_FILE` | `.agent/probe.log` | 日志文件 |
| `PROBE_PYTHON` | `python3` | 解释器 |
| `PROBE_CHECK_ONLY` | 未设 | 非空则只跑启动校验后退出（测试用） |

### 与 Actions 的并发关系

Actions 侧的数据提交仍在继续，所以本机推送必须让远端优先：

- 每个周期开始前先 `git pull --rebase`，遇冲突则 `rebase --abort`，并且**只有在
  未提交改动全部位于 `data/`**（探针自己的足迹）时才 `reset --hard '@{u}'`；
  否则拒绝回退、把原因写进日志（保护 agent 的在途工作）；
- 探针只提交 `data/update-profile.json` / `data/fetch-log.json` /
  `data/fetch-schedule.json`，每小时最多一次；未推送成功的本地提交在下一轮继续
  重推（推送被拒时 `pull --rebase` + 重试，最多 `PROBE_MAX_PUSH_RETRIES`，
  默认 3 次）；仍失败则保留本地提交、记一次失败，循环继续；
- 任何一步失败都**不会**终止循环（脚本没有 `set -e`，每个 stage 都是
  `continue-on-error` 语义），失败与重试计数进入当天的 `days` 记录。

### `data/fetch-log.json`

```json
{
  "v": 1,
  "fetches": ["2026-10-12T08:00:12Z", "2026-10-12T08:15:09Z"],
  "days": {"2026-10-12": {"ok": 90, "failed": 1, "retries": 2}},
  "summary_sent": "2026-10-12"
}
```

- `fetches` — 每次**完整跑完链路**的抓取时刻（UTC ISO），供 #76 计算实际间隔；
  只保留最近 `MAX_ENTRIES = 2000` 条。
- `days` — 每次抓取的 **UTC 日期** 的 `ok` / `failed` / `retries` 计数，保留 30 天；
  推送重试在**下一个周期**记入（本周期的时间戳必须早于它所在的提交）。
- `summary_sent` — 最后一条健康摘要的**本地日期**，用于「每天最多一条」去重。

读写逻辑在 `scripts/fetch_log.py`（纯函数 + CLI：`append` / `summary` /
`needs-summary` / `mark-summary`），单测见 `scripts/test_fetch_log.py`，
探针脚本自身的契约测试见 `scripts/test_local_fetch_loop.py`。

## Exploration vs exploitation (ε-greedy)

A purely learned schedule can get stuck: `data/update-profile.json` showed
`observed_hours == 0` for **03:00 and 08:00 UTC** even though `plan_slots()`
scheduled both — the gate's `min_gap`/grace/freshness logic kept landing the
runs on the busy windows, so the learner never saw those hours and their weight
stayed frozen at the research prior. That is *exploitation without
exploration*: the sampler cannot discover whether an unvisited hour matters.

To fix it, `plan_schedule()` reserves a fraction **ε** of the daily budget for
**explore** slots and only allocates the remaining `1 − ε` to the learned
peaks (**exploit** slots):

- the `round(budget · ε)` explore slots go to the hours with the **least
  `observed_hours`**, and within an hour to a quarter-hour mark the
  exploitation plan is *not* already using (`:15` → `:30` → `:45` → `:00`), so
  a run delayed by the 45-minute grace window still lands on a slot;
- explore slots are a **subset** of `slots` and can never duplicate an
  exploitation slot; when the least-observed hours are already full, the
  probes spill to the next-least-observed hour;
- `ε` defaults to **0.15** (`15 %`) and is clamped to `[0, 1]`. Every probe
  found is recorded in `data/fetch-schedule.json` as `explore_slots` (plus
  `explore_frac` / `explore_count`), and `should_fetch.py --dry-run` tags a due
  hit with `[explore]`, so a poll can be audited as exploration vs exploitation;
- the budget is unchanged: `len(slots) ≤ budget_per_day`, so the busiest hours
  give up at most the ε share that exploration takes (acceptance: hot-hour
  coverage loss ≤ ε).

The exploration results are folded into `update-profile.json` exactly like any
other fetch, so an hour's weight gradually stops being pure prior. With the #91
timetable the budget is spent on the gap caps themselves (`FETCH_DAILY_BUDGET=65`
= busy 20 min + midnight 10 min + hourly night), so there is **no slack left to
probe with** and the committed plan carries no `explore_slots`: every hour is
covered by construction (the night floor), which gives the learner better hour
coverage than ε-greedy probing ever did — and both the 5-minute dispatch wakes
and the local probe record their observation through the same
`update_profile.py` path. Raising `FETCH_DAILY_BUDGET` above 65 brings in-plan
exploration back, but only pays off if the plan grows tighter than the caps
(`FETCH_EXPLORE_FRAC=0.15` stays a no-op until then, which is why it is kept
explicitly in the workflow env).

**Turning it off:** set `FETCH_EXPLORE_FRAC=0` (or `--explore-frac 0`); the
plan is then byte-for-byte the pre-#69 plan. Set it in the `env:` block of the
`update-data` job like the other knobs. Exploration is skipped automatically
when there is no learned profile yet (the research-prior fallback) or when the
budget only covers the hourly `min_per_hour` floor — there is no slack to
probe with.

## Fetch profile (`update-profile.json`)

The fetch profile is the learned "when do stations re-price" state that
`update_profile.py` maintains and `fetch_strategy.hour_weights()` turns into the
poll schedule ([`fetch-strategy.md`](./fetch-strategy.md#layered-weights-regions-expected-volume-quarter-hours-issue-71)).
It is derived state, not history: it has **no retention window** — it is
cumulative by design, so the schedule reflects the whole observed history
instead of chasing single days.

```json
{
  "v": 1,
  "hours": {
    "11": {"samples": 12, "observed_hours": 6.5, "change_events": 3,
           "changed_stations": 812, "changed_stations_exact": 47,
           "stations_observed": 2463, "exact_samples": 12}
  },
  "regions": {
    "Montréal": {"stations": 452, "hours": {
      "11": {"samples": 12, "observed_hours": 6.5,
             "change_events": 3, "changed_stations": 18}
    }}
  },
  "marks": {
    "11": {"0": {"samples": 3, "changed_stations": 21},
           "1": {"samples": 3, "changed_stations": 9}}
  },
  "last_observed": {"Montréal": {"regular": 170.5, "super": 188.0}},
  "last_observed_at": "2026-09-25T11:20:00Z",
  "last_station_diff": {"at": "2026-09-25T11:20:00Z", "observed": 2463, "changed": 47}
}
```

- `hours` — province-wide per-UTC-hour counters (the pre-#71 schema, unchanged;
  `changed_stations` is the region-average estimate, `changed_stations_exact`
  the per-station count of issue #68 and `exact_samples` how many fetches fed
  the later).
- `regions` — the same accounting per region (issue #71), **sparse**: only hours
  that were actually observed are written, and `changed_stations` is always the
  exact per-station count. `stations` is the region's station count at the last
  fetch.
- `marks` — per quarter-hour counter (`0` = `:00` … `3` = `:45`) of fetches and
  exact re-prices; it only starts ordering the marks of an hour once that hour
  has been sampled enough (`MARK_MIN_SAMPLES = 32`).
- `last_observed` / `last_station_diff` — the diff baselines of the next fetch.

### Size & growth

All three blocks are bounded by the calendar, not by time:

- `hours`: 24 counters, ~100 B each → **~2.5 KB**;
- `regions`: 18 regions x 24 hours x ~75 B → **~33 KB** when every hour of the
  day has been sampled in every region; only observed hours are stored, so a
  partially covered day (the common case early on) is a few hundred bytes per
  region;
- `marks`: 24 hours x 4 marks x ~40 B → **~4 KB** once every mark has samples.

Measured on an offline replay of the real snapshot (two days of 15-minute
fetches, all 18 regions, 432 region-hour cells — i.e. saturated):
**43.9 KB** uncompressed for the whole file, ≈ 9 KB as `.br`.

The file does **not grow with time**: the counters are cumulative, so they grow
in *value*, not in size. The 40 MB `data/` budget of
`scripts/check_data_size.py` is therefore unaffected.

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
| station changes | `data/history/station-changes.json` | last change / last observation | last **180 days seen** | station freshness (`last_changed_at`) + station card "observed X ago" / "price changed X ago" (#70 / #96) |
| metrics | `data/metrics.json` | summary (derived, not committed since #103) | overwritten on every run | CI / PM agent freshness evidence |
| latency ledger | `data/metrics-ledger.json` | rolling latency samples (persistent since #103) | last **300** samples | p50/p90/p95 over more than the current run |
| trigger intervals | `data/trigger-interval.json` | measured interval distribution per trigger | overwritten on every run | evidence that the shipped rhythm reaches the busy-hour criterion (#77 / #93) |
| probe ledger | `data/fetch-log.json` | per-cycle timestamp + per-day counters | last **2000** cycles ≈ 15 days | local probe ground truth for the necessary-interval evaluation (#74 / #76) |
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
workflow (`data/*.json.br`, `data/history/*.json.br`). Those siblings are
**build output for the Pages artifact only**: nothing in `js/` fetches a
`*.json.br`, GitHub Pages does not negotiate precompressed files, and each one
is a full new blob per commit — so since issue #103 **no** `.br` file is
committed to git (see below).

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

## Station-level change detection

Region averages hide two things (issue #68): a +5¢ and a −5¢ move inside the
same region cancel out, and a 140-station region dilutes a single re-price
below the `PRICE_EPSILON = 0.05` threshold. The upstream `Prices[]` payload has
no per-station update time, so freshness has to come from our own observation
history: every fetch now diffs the snapshot **station by station**, keyed by the
same 5-decimal coordinate key as the station daily tier, and records when each
key last actually moved.

### Why a separate file (tradeoff)

The alternative was to append the change columns to `station-history.json`.
A separate store was chosen because:

- `station-history.json` is a **column-oriented, day-aligned** matrix whose
  insertion logic reindexes every existing array when an out-of-order source
  day arrives; adding two per-station scalars per row would have to ride that
  reindexing and would bloat every one of the ~2.5k station rows.
- the change store has a different retention rule (prune by *last seen*, not by
day) and a different reader (freshness queries, not charting), so keeping it
  apart means the station card path cannot regress when this schema changes.
- the row layout lets a single entry answer both questions without scanning the
  whole matrix: `t` is freshness, `p` is the diff baseline.

The cost is one extra small file (~0.2 MB) next to the daily tier.

### Schema

```json
{
  "v": 1,
  "generated_at": "2026-10-12T00:50:01Z",
  "tiers": {"max_age_days": 180},
  "last": {"at": "2026-10-12T00:50:01Z", "observed": 2463, "changed": 0},
  "s": {
    "-79.01957,48.24676": {
      "t": "2026-10-05T13:15:03Z",
      "u": "2026-10-12T00:50:01Z",
      "p": [199.4, 223.9, 295.9]
    }
  }
}
```

- `s[coord_key]` — one entry per station, key `"lng,lat"` rounded to 5 decimals
  (identical to the station daily tier's key).
- `t` — **`last_changed_at`**: fetch timestamp of the last real price change
  (`null` until a change has been observed for that key).
- `u` — **`last_seen_at`**: fetch timestamp the station last appeared in the
  upstream feed (updated on every fetch, even when nothing moved).
- `p` — `[g, s, d]` last observed prices (regular/super/diesel, `null` for a
  fuel the station did not report); this is the baseline the next diff compares
  against.
- `last` — summary of the most recent fetch. The stations that moved in that
  fetch are exactly the keys whose `t == last.at`
  (`update_profile.stations_changed_at(store, store['last']['at'])`).

A fuel counts as changed when both the previous and current snapshot have a
value and they differ by at least `PRICE_EPSILON` (half a tenth). A new key has
no baseline and is not a change; a fuel appearing or disappearing is an
availability change, not a re-price; a station vanishing from the feed keeps its
last entry until it stops being seen for the retention window.

### What becomes measurable

- **exact change set** — the changed stations and their count for each fetch
  (`update_profile.py` prints the list; the schedule's learned fields keep the
  region-level `changed_stations` for compatibility and add
  `changed_stations_exact` / `stations_observed` next to it)
- **freshness distribution** — `now - u` over all entries
- **stale share** — share of keys with `now - t > 7 days`
- **real change rate** — changed keys per fetch, per 15 min or per hour

`scripts/metrics.py` (below) turns these into a committed summary instead of
leaving them as raw fields to recompute by hand.

### Size & retention

Measured on the live snapshot (2 463 stations):

- whole store: **~200 KB** uncompressed / **~21 KB** `.br`
- per entry: **~85 B** (5-decimal key + `t` + `u` + three prices)
- it stores only the *latest* change/observation per station, and entries not
  seen for 180 days are pruned, so it reaches a steady size with **~0 MB/year**
  ongoing growth at a fixed station count.

`scripts/check_data_size.py` reports the file explicitly and fails the job if it
passes its own **8 MB** budget (`MAX_STATION_CHANGES_MB`), in addition to the
40 MB `data/` total budget.

## Freshness metrics (`data/metrics.json`)

`scripts/metrics.py` (standard library only, issue #70) answers "is the data we
show actually fresh?" with numbers instead of impressions. It runs once after
`append_history.py` in the workflow (`continue-on-error`, so it can never block
the data commit) and reads only committed files:

| Input | Used for |
| --- | --- |
| `data/stations.json` | upstream `metadata.generated_at`, station count |
| `data/history/station-changes.json` | per-station `last_seen_at` / `last_changed_at` |
| `data/fetch-state.json`, `update-profile.json` | fetch time, exact hourly change counters |
| `data/metrics.json` (previous) | the rolling latency ledger |

It writes `data/metrics.json` (and prints a human summary; `--json` prints the
same payload machine-readably for the PM agent). Schema:

```json
{
  "v": 1,
  "generated_at": "2026-10-12T07:38:08Z",
  "source_generated_at": "2026-10-12T06:50:01Z",
  "observed_at": "2026-10-12T06:51:13Z",
  "stations": 2463,
  "latency_seconds": {"p50": 72.0, "p90": 72.0, "p95": 72.0, "max": 72.0, "count": 1,
                      "samples": [{"at": "2026-10-12T06:51:13Z", "s": 71.887}]},
  "freshness_seconds": {"p50": 2815.9, "p90": 2815.9, "max": 2815.9, "count": 2463},
  "stale": {"total": 2463, "never_changed": 2009, "over_7d": 1, "over_30d": 0,
            "over_7d_share": 0.0004, "over_30d_share": 0.0,
            "examples": [{"key": "-73.50000,45.50000", "days": 11.0}]},
  "hourly_changes": {"hours": [{"hour": 6, "changed": 454, "observed_hours": 1.0, "rate": 454.0}],
                     "total_changed": 454, "mean_rate": 2.649}
}
```

- `latency_seconds` — **end-to-end delay** `observed_at - source_generated_at`:
  how long after upstream published the snapshot it was fetched and recorded.
  Because only the *last* fetch timestamp is committed elsewhere, the script
  keeps a bounded rolling ledger of samples in this same file
  (`MAX_LATENCY_SAMPLES = 300`, ~3 days of hourly fetching); p50/p90/p95/max are
  computed over that ledger, so the number is a real distribution, not one value.
- `freshness_seconds` — `now - last_seen_at` over the 2 463 station entries:
  how far the on-screen prices can lag upstream. The card reader uses the same
  `last_seen_at` to render "observed X minutes ago".
- `stale` — share of stations whose last real price change is older than 7 / 30
  days (frozen-source candidates). `t: null` entries count as `never_changed`,
  not as stale, because no change has been observed for them yet.
- `hourly_changes` — per-UTC-hour `changed_stations_exact / observed_hours` from
  the learned profile, i.e. the real station re-price rate (#68 signal).

`scripts/check_data_size.py` reports the file and fails if it passes
**50 KB** (`MAX_METRICS_KB`), on top of the 40 MB `data/` total budget.

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

Station change tier:

- Whole file at 2 463 stations: **~200 KB** uncompressed / **~21 KB** `.br`
- No time dimension: the size follows the *station count*, not the retention
  length, so 180-day pruning keeps it flat at **~0 MB/year**.

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

The station change store is read by the frontend only for the honesty label on
the station card / detail panel ("price observed X minutes ago"); it does not
feed prices or charts. `loadStationChangesData()` tolerates a missing or
malformed file (the label is simply not rendered), and `update_profile.py`
treats a missing one as an empty store, so it is safe to delete: the next fetch
recreates it and simply has no `last_changed_at` baseline yet.
