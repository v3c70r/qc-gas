#!/usr/bin/env python3
"""「最大可接受抓取间隔」评估 (issue #76) — 陈旧度 vs 间隔 + 结论。

回答一个决策问题：**为了达到「可接受的新鲜度」，最短必须多久抓一次上游快照？**
（#67/#74 测得 GitHub Actions schedule 已被节流：中位 293 分钟、最大 538 分钟，
所以这个问题的答案直接决定 *能不能* 只靠 Actions cron 兜底。）

输入（全部为已提交的真实数据，纯标准库）：

* **站点观测序列** —— ``data/history/station-changes.json`` 的 **git 修订历史**。
  探针（``scripts/local_fetch_loop.sh``，#74）每 ~15 分钟跑完整数据链并提交一次
  该文件，所以它的「每个修订 = 一次真实抓取后的站点级价格快照」，等价于一份
  15 分钟分辨率的真值序列（Actions 侧同样每次成功抓取都会提交一次）。
* ``data/fetch-log.json`` —— 探针台账（#74）：实际抓取次数、实际间隔分布、
  已覆盖天数，用来判断样本够不够、以及「当前实际间隔下的陈旧度」。
* ``data/stations.json`` —— 坐标键 → 区域/品牌 映射（分组统计用）。
* ``data/history/station-history.json``（可选）—— 日分辨率站点价格，用来做
  区域/品牌分组的**粗估**（日分辨率，与 15 分钟精细分析分开标注）。

产出：

* ``docs/fetch-interval-report.md`` —— 人类可读报告（结论段 + 表格 + 偏差标注）
* ``data/fetch-interval.json`` —— 机器可读，供 PM agent（#C）直接引用

## 口径（与 #68/#70 保持一致）

* 变更检测复用 ``fetch_strategy.diff_stations``（同一 ``PRICE_EPSILON`` 阈值、
  同一「新站点/燃料消失不算变更」规则）。
* 「过期展示时长」= 站点价格真实变更之后，被**下一次模拟抓取**修正之前的时长；
  期间 App 展示的是过期价格，年龄从 0 涨到这个值。因此
  ``p90(过期时长) ≈ 0.9 × T``（变更时刻相对抓取网格近似均匀分布）。
* 「陈旧站占比」= 时间加权：``Σ 过期展示时长 / (站点数 × 窗口时长)``，
  即「随机时刻有多少比例的站点正展示过期价格」。判据取 5%。
* 从没在窗口内变更过的站点不计入年龄分布（它们的展示价格与上游一致，不陈旧），
  但仍计入站点总数的分母。

## 诚实标注（报告必须原样保留）

15 分钟（或 Actions 的 ~小时级）采样看不到子分辨率抖动，所以

1. 观测到的变更**次数**是下界（快速改回原价的抖动完全不可见）；
2. 变更**时刻**只能定位到「新价格首次出现的观测点」，晚于真实变更时刻
   ⇒ 过期展示时长被**低估**。

两者方向一致：数据让现状看起来比真实更好 ⇒ **结论是乐观上界，实际所需间隔
≤ 本报告给出的值**。样本不足（< 3 天）时脚本明确报「样本不足 + 还需多少天」，
只输出标注为「初值」的表格，不给结论。
"""

import argparse
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_log import SAMPLE_MIN_DAYS, load_log, summary as probe_summary  # noqa: E402
from fetch_strategy import PRICE_EPSILON, diff_stations, prices_from_row  # noqa: E402
from metrics import load_json, parse_iso, percentile, to_iso  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / 'data'

STATION_CHANGES_REL = Path('history') / 'station-changes.json'
STATION_HISTORY_REL = Path('history') / 'station-history.json'
GIT_CHANGES_PATH = 'data/history/station-changes.json'
PROBE_EVENTS_REL = Path('probe-local') / 'events.jsonl'
STATIONS_FILE = 'stations.json'
PROBE_LOG_FILE = 'fetch-log.json'

GIT_OBSERVATIONS_SOURCE = 'git revision history of data/history/station-changes.json'

REPORT_REL = Path('docs') / 'fetch-interval-report.md'
JSON_FILE = 'fetch-interval.json'

# 候选抓取间隔（分钟）：15 = 探针实测分辨率，往上每次翻倍到 8 小时
# （覆盖 Actions 被节流后的实测中位间隔 ~293 分钟）。
INTERVAL_GRID_MINUTES = (15, 30, 60, 120, 240, 480)
# 判据 A：p90 过期展示时长 ≤ 30 分钟。
P90_AGE_BUDGET_SECONDS = 30 * 60
# 判据 B：任一时刻展示过期价格的站点占比 ≤ 5%。
STALE_SHARE_BUDGET = 0.05
# 「陈旧」的样本阈值：过期展示时长 > 60 分钟视为明显陈旧。
STALE_AGE_SECONDS = 60 * 60
# 样本充足的最少覆盖天数：复用 #74 探针台账的判据（同一条线，避免两个标准）。
MIN_SAMPLE_DAYS = SAMPLE_MIN_DAYS
# 把间隔减半后 p90 改善 < 10% 视为收益递减。
KNEE_REDUCTION = 0.10
# 变更事件少于这个数量时，p99 之类的高分位不可信 → 样本不足。
MIN_CHANGE_EVENTS = 30
# git 修订历史最多回看多少个提交（15 分钟一提交 ≈ 20 天），保持脚本有界。
MAX_REVISIONS = 2000
UNKNOWN_REGION = 'Inconnue'
UNKNOWN_BRAND = '—'


# ── 基础工具 ────────────────────────────────────────────────────────────────

def normalize_observations(entries):
    """原始观测记录 -> 按时间排序、逐站点价格展开的观测序列。

    ``entries`` 每项形如 ``{"at": ISO, "s": {coord_key: [g, s, d]}}``（即
    ``station-changes.json`` 修订里的 ``last.at`` + ``s``）。同一个时间戳出现
    多次时保留**最后一个**状态（后写的提交才是最新事实）。无法解析的条目、
    空站点表、没有时间戳的记录一律丢弃——脏输入只能让样本变小，不能抛异常。
    """
    by_stamp = {}
    for item in entries or []:
        if not isinstance(item, dict):
            continue
        at = parse_iso(item.get('at'))
        stations = item.get('s')
        if at is None or not isinstance(stations, dict):
            continue
        prices = {}
        for key, row in stations.items():
            fuels = prices_from_row((row or {}).get('p') if isinstance(row, dict) else row)
            if fuels:
                prices[str(key)] = fuels
        if not prices:
            continue
        by_stamp[to_iso(at)] = {'at': at, 'prices': prices}
    return [by_stamp[stamp] for stamp in sorted(by_stamp)]


def load_events_file(path):
    """本地探针事件日志 ``data/probe-local/events.jsonl`` -> ``(observations, events)``。

    #92 之后探针不再提交快照，样本落在本机（gitignore）的事件日志里，每行
    ``{"t": ISO, "changed": N, "keys": [...], "stations": M}``：

    * 有 ``t`` 的行 = 本周期的一次真实抓取 ⇒ 一个观测点（只带时间戳与站点总数，
      没有价格，所以站点数由 ``stations`` 自报，见 :func:`observation_stations`）；
    * ``keys`` 里的每个坐标键 = 该站点在该时刻的一次真实重定价 ⇒ 一个变更事件
      （与 git 历史路径同口径：变更时刻就是首次看到新价的观测时刻）。

    脏行、缺 ``t`` 的行、文件不存在一律跳过：只有本地日志的环境（无 git 快照历史）
    必须能算出覆盖天数与事件量级，而不是报错。
    """
    path = Path(path)
    if not path.exists():
        return [], []
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return [], []

    by_stamp = {}
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        at = parse_iso(entry.get('t'))
        if at is None:
            continue
        try:
            stations = int(entry.get('stations') or 0)
        except (TypeError, ValueError):
            stations = 0
        by_stamp[to_iso(at)] = {'at': at, 'stations': max(0, stations), 'prices': {}}
        keys = entry.get('keys')
        for key in (keys if isinstance(keys, list) else []):
            events.append({'at': at, 'key': str(key), 'fuels': {},
                           'region': UNKNOWN_REGION, 'brand': UNKNOWN_BRAND})

    # 同一个时间戳只算一次抓取（重复行保留后写的那条），事件按时间排序。
    observations = [by_stamp[stamp] for stamp in sorted(by_stamp)]
    events.sort(key=lambda event: event['at'])
    return observations, events


def observation_stations(observations):
    """观测序列的站点数：价格键的并集；没有价格时用日志自报的 ``stations``。

    站点数只是陈旧站占比的分母；本地事件日志没有价格表，所以 #92 让每行自报
    ``stations``（= 当轮 ``last.observed``）。
    """
    keys = {key for obs in observations or [] for key in (obs.get('prices') or {})}
    if keys:
        return len(keys)
    counts = [int(obs['stations']) for obs in observations or []
              if isinstance(obs.get('stations'), int)]
    return max(counts) if counts else 0


def station_labels(stations):
    """坐标键 -> ``{"region": ..., "brand": ...}``（来自当前 stations.json）。"""
    labels = {}
    for feat in ((stations or {}).get('features') or []):
        coords = ((feat or {}).get('geometry') or {}).get('coordinates') or []
        if len(coords) < 2:
            continue
        props = (feat or {}).get('properties') or {}
        try:
            key = f'{float(coords[0]):.5f},{float(coords[1]):.5f}'
        except (TypeError, ValueError):
            continue
        labels[key] = {
            'region': str(props.get('region') or UNKNOWN_REGION),
            'brand': str(props.get('brand') or UNKNOWN_BRAND),
        }
    return labels


def load_git_observations(root, rel_path=GIT_CHANGES_PATH, max_revisions=MAX_REVISIONS):
    """从 ``station-changes.json`` 的 git 修订历史重建观测序列。

    每个修订是一次真实抓取后的站点级快照，所以修订序列就是「15 分钟分辨率真值」
    （探针 #74 每周期提交一次）。任何 git 失败（不是仓库、没有该文件、没装 git）
    都退化成空列表：脚本必须能在干净 checkout 上跑出「样本不足」报告而不是崩掉。
    """
    root = Path(root)
    try:
        log = subprocess.run(
            ['git', '-C', str(root), 'log', f'--format=%H%x1f%cI',
             f'-{max_revisions}', '--', str(rel_path)],
            capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return []

    entries = []
    for line in reversed(log.stdout.splitlines()):
        rev = line.split('\x1f')[0].strip()
        if not rev:
            continue
        try:
            blob = subprocess.run(['git', '-C', str(root), 'show', f'{rev}:{rel_path}'],
                                  capture_output=True, text=True, check=True)
            store = json.loads(blob.stdout)
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
            continue
        if not isinstance(store, dict):
            continue
        at = ((store.get('last') or {}).get('at')) or store.get('generated_at')
        entries.append({'at': at, 's': store.get('s') or {}})
    return normalize_observations(entries)


# ── 变更到达分布 ────────────────────────────────────────────────────────────

def change_events(observations, labels=None):
    """观测序列 -> 排序后的「站点发生真实变更」事件列表。

    相邻两次观测之间用 :func:`fetch_strategy.diff_stations` 做站点级 diff
    （与 #68 的 ``last_changed_at`` 完全同口径），事件时刻取**新价格首次出现的
    观测点**（因为真实变更时刻落在两次观测之间，只能定位到这个上界）。
    """
    labels = labels or {}
    events = []
    for prev, cur in zip(observations or [], (observations or [])[1:]):
        changed = diff_stations(prev['prices'], cur['prices'])
        for key in sorted(changed):
            info = labels.get(key) or {}
            events.append({
                'at': cur['at'],
                'key': key,
                'fuels': changed[key],
                'region': info.get('region') or UNKNOWN_REGION,
                'brand': info.get('brand') or UNKNOWN_BRAND,
            })
    return events


def station_intervals(events):
    """同一站点两次相邻变更之间的间隔（秒），总体 + 按区域/品牌分组。"""
    out = {'overall': [], 'by_region': {}, 'by_brand': {}}
    last = {}
    for ev in events or []:
        key = ev.get('key')
        if key is None:
            continue
        region = ev.get('region') or UNKNOWN_REGION
        brand = ev.get('brand') or UNKNOWN_BRAND
        out['by_region'].setdefault(region, [])
        out['by_brand'].setdefault(brand, [])
        prev = last.get(key)
        if prev is not None:
            seconds = (ev['at'] - prev).total_seconds()
            if seconds > 0:
                out['overall'].append(seconds)
                out['by_region'][region].append(seconds)
                out['by_brand'][brand].append(seconds)
        last[key] = ev['at']
    return out


def stats_of(values):
    """p50/p90/p99 + count（空集合时只有 count=0，其余为 ``None``）。"""
    vals = [float(v) for v in (values or []) if v is not None]
    if not vals:
        return {'count': 0, 'p50': None, 'p90': None, 'p99': None}
    return {
        'count': len(vals),
        'p50': round(percentile(vals, 50), 1),
        'p90': round(percentile(vals, 90), 1),
        'p99': round(percentile(vals, 99), 1),
    }


def grouped_stats(intervals):
    """``station_intervals`` 的输出 -> 可直接进 JSON 的分位数字典（丢弃空分组）。"""
    return {
        'overall': stats_of(intervals.get('overall')),
        'by_region': {region: stats_of(vals)
                      for region, vals in sorted((intervals.get('by_region') or {}).items())
                      if vals},
        'by_brand': {brand: stats_of(vals)
                     for brand, vals in sorted((intervals.get('by_brand') or {}).items())
                     if vals},
    }


def daily_change_intervals(store, labels=None, epsilon=PRICE_EPSILON):
    """``station-history.json``（日分辨率）-> 站点级变更间隔（秒）。

    值的可比性规则是**同一个燃料**：某天该燃料有新值、且前一天（同一燃料）也有
    值、且差 ≥ ``epsilon``，才算一次变更。``null``（当天没有观测到该站）把基线
    清空，所以「消失又出现」不会被误判成一次变更。这是粗估：一天内发生的多次
    变更只看到一次，间隔必然是整数天的倍数。
    """
    labels = labels or {}
    days = [str(d) for d in ((store or {}).get('t') or [])]
    sites = (store or {}).get('s') or {}
    if not days or not isinstance(sites, dict):
        return {'overall': [], 'by_region': {}, 'by_brand': {}}

    out = {'overall': [], 'by_region': {}, 'by_brand': {}}
    for key, site in sites.items():
        if not isinstance(site, dict):
            continue
        info = labels.get(key) or {}
        region = info.get('region') or UNKNOWN_REGION
        brand = info.get('brand') or UNKNOWN_BRAND
        out['by_region'].setdefault(region, [])
        out['by_brand'].setdefault(brand, [])
        event_days = set()
        for arr in site.values():
            if not isinstance(arr, list):
                continue
            baseline = None
            for i, day in enumerate(days):
                value = arr[i] if i < len(arr) else None
                if value is None:
                    baseline = None
                    continue
                if baseline is not None and abs(float(value) - baseline) >= epsilon:
                    event_days.add(day)
                baseline = float(value)
        ordered = sorted(event_days)
        for before, after in zip(ordered, ordered[1:]):
            seconds = (parse_iso(after + 'T00:00:00Z') - parse_iso(before + 'T00:00:00Z')).total_seconds()
            out['overall'].append(seconds)
            out['by_region'][region].append(seconds)
            out['by_brand'][brand].append(seconds)
    return out


# ── 陈旧度模拟 ──────────────────────────────────────────────────────────────

def simulate_interval(events, observations, interval_minutes, stale_seconds=STALE_AGE_SECONDS,
                      source='grid'):
    """模拟「每 T 分钟抓一次」，返回该间隔下的陈旧度指标。

    抓取网格从窗口起点按 T 对齐，并**把窗口终点当作一次抓取**（最后一个观测点
    本身就是一次真实抓取，否则窗口尾部的变更会因为「下一次抓取在窗口之外」而
    被整体丢掉）。对每个真实变更事件取 ``fix = 第一个 ≥ 变更时刻的模拟抓取时刻``；
    该站在 ``[变更, fix)`` 区间展示过期价格，过期展示时长 = ``fix - 变更时刻``。

    如果窗口内一个变更事件都没有（样本太短或太快），所有分位数/占比都是 ``None``
    而不是 0：0 会被读成「没有陈旧」，而事实是「测不出来」。
    """
    observations = observations or []
    if not observations:
        return _empty_sim(interval_minutes, source)

    window_start = observations[0]['at']
    window_end = observations[-1]['at']
    window_seconds = (window_end - window_start).total_seconds()
    step = max(1e-9, float(interval_minutes) * 60.0)

    lags = []
    for ev in events or []:
        delta = (ev['at'] - window_start).total_seconds()
        fix_offset = min(math.ceil(delta / step - 1e-9) * step, window_seconds)
        lags.append(max(0.0, fix_offset - delta))

    stations = observation_stations(observations)
    share = None
    if lags and stations and window_seconds > 0:
        share = sum(lags) / (stations * window_seconds)
    over = ([lag for lag in lags if lag > stale_seconds] if lags else [])

    stats = stats_of(lags)
    return {
        'interval_minutes': interval_minutes,
        'source': source,
        'events': len(lags),
        'p50': stats['p50'],
        'p90': stats['p90'],
        'p99': stats['p99'],
        'stale_share': round(share, 6) if share is not None else None,
        'stale_share_over_threshold': round(len(over) / len(lags), 4) if lags else None,
        'stale_threshold_seconds': stale_seconds,
        'stations': stations,
        'window_seconds': round(window_seconds, 1),
    }


def _empty_sim(interval_minutes, source='grid'):
    return {
        'interval_minutes': interval_minutes, 'source': source, 'events': 0,
        'p50': None, 'p90': None, 'p99': None,
        'stale_share': None, 'stale_share_over_threshold': None,
        'stale_threshold_seconds': STALE_AGE_SECONDS, 'stations': 0,
        'window_seconds': 0.0,
    }


def staleness_table(events, observations, grid=INTERVAL_GRID_MINUTES,
                    stale_seconds=STALE_AGE_SECONDS):
    """对每个候选间隔 T 生成一行陈旧度指标（恒为 ``len(grid)`` 行）。"""
    return [simulate_interval(events, observations, minutes, stale_seconds)
            for minutes in grid]


# ── 结论 ────────────────────────────────────────────────────────────────────

def recommend(rows, p90_budget=P90_AGE_BUDGET_SECONDS, share_budget=STALE_SHARE_BUDGET):
    """按判据给出「最大可接受间隔」+ 收益递减拐点。

    判据（两条都要满足）：

    * ``p90 过期展示时长 ≤ P90_AGE_BUDGET_SECONDS``（默认 30 分钟）
    * ``陈旧站占比 ≤ STALE_SHARE_BUDGET``（默认 5%）

    只在**网格**行里做结论（``source='observed'`` 的实测间隔行仅用于参照），
    并且**跳过分辨率下限档位**（``at_or_below_resolution``）：T 等于观测分辨率时，
    模拟出的过期时长恒为 0（变更时刻只能定位到观测点），那是测量下限而不是「没有陈旧」。
    没有任何档位满足判据时返回 ``None``：宁可不给建议，也不给误导性建议。
    """
    grid_rows = [row for row in rows or []
                 if row.get('source') != 'observed' and not row.get('at_or_below_resolution')]
    acceptable = [
        row for row in grid_rows
        if row.get('p90') is not None and row['p90'] <= p90_budget
        and row.get('stale_share') is not None and row['stale_share'] <= share_budget
    ]
    if not acceptable:
        return None

    best = max(acceptable, key=lambda row: row['interval_minutes'])

    by_minutes = {row['interval_minutes']: row for row in grid_rows}
    knee = None
    for row in sorted(grid_rows, key=lambda r: r['interval_minutes']):
        half = by_minutes.get(row['interval_minutes'] / 2)
        if not half or not row.get('p90'):
            continue
        gain = (row['p90'] - (half.get('p90') or 0.0)) / row['p90']
        if gain >= KNEE_REDUCTION:
            knee = row['interval_minutes']

    summary = (
        f"最大可接受间隔 = {best['interval_minutes']:g} 分钟"
        f"（判据：p90 过期展示时长 ≤ {p90_budget / 60:g} 分钟"
        f"（实测 {_minutes(best['p90'])}）且 陈旧站占比 ≤ {share_budget * 100:g}%"
        f"（实测 {(best['stale_share'] or 0) * 100:.2f}%））"
    )
    if knee:
        summary += (f"；收益递减拐点 ≈ {knee:g} 分钟：比它更密地抓取"
                    f"（间隔减半）已无法把 p90 再降 ≥ {KNEE_REDUCTION * 100:g}%")
    else:
        summary += (f"；在本窗口内 p90 与间隔近似线性（每减半 T 就把 p90 减半），"
                    f"没有出现收益递减拐点——判据本身就是拐点")

    return {
        'max_acceptable_interval_minutes': best['interval_minutes'],
        'criteria': {
            'p90_age_seconds_max': p90_budget,
            'stale_share_max': share_budget,
        },
        'p90_at_max_acceptable': best['p90'],
        'stale_share_at_max_acceptable': best['stale_share'],
        'diminishing_returns_from_minutes': knee,
        'summary': summary,
    }


# ── 报告装配 ────────────────────────────────────────────────────────────────

def sampling_minutes(observations):
    """观测间隔的中位数（分钟）= 这份真值的实际分辨率。"""
    gates = [(cur['at'] - prev['at']).total_seconds() / 60.0
             for prev, cur in zip(observations or [], (observations or [])[1:])]
    gates = [g for g in gates if g > 0]
    return round(percentile(gates, 50), 2) if gates else None


def build_report(data_dir, root, now, observations=None, events=None,
                 observations_source=None, probe=None, grid=INTERVAL_GRID_MINUTES,
                 min_days=MIN_SAMPLE_DAYS, stale_seconds=STALE_AGE_SECONDS):
    """读取已提交数据 + 观测序列，装配报告 payload（人类/机器共用一份）。

    ``observations`` 缺省时从 git 修订历史重建；``events`` 缺省时由观测序列做
    站点级 diff 得到（``--events-file`` 走另一条路：本地日志直接给出时间戳 + 变更键，
    两者都由调用方按同一口径传入）。
    """
    data_dir = Path(data_dir)
    root = Path(root)
    now = now or datetime.now(timezone.utc)

    stations = load_json(data_dir / STATIONS_FILE)
    labels = station_labels(stations)
    change_store = load_json(data_dir / STATION_CHANGES_REL) or {}
    history_store = load_json(data_dir / STATION_HISTORY_REL) or {}

    if observations is None:
        observations = load_git_observations(root)
    if observations_source is None:
        observations_source = GIT_OBSERVATIONS_SOURCE
    if probe is None:
        probe = probe_summary(load_log(data_dir / PROBE_LOG_FILE), now)

    if events is None:
        events = change_events(observations, labels)
    coverage_days = 0
    span_hours = 0.0
    if observations:
        first, last = observations[0]['at'], observations[-1]['at']
        coverage_days = (last.date() - first.date()).days + 1
        span_hours = round((last - first).total_seconds() / 3600.0, 2)

    stations_observed = observation_stations(observations)
    missing_days = max(0, int(min_days) - coverage_days)
    sufficient = (coverage_days >= int(min_days) and len(events) >= MIN_CHANGE_EVENTS
                  and len(observations) >= 2)

    rows = staleness_table(events, observations, grid, stale_seconds)
    resolution = sampling_minutes(observations)
    for row in rows:
        # T 不超过观测分辨率时，变更时刻的分辨率下限会让过期时长恒为 0，
        # 那是测量假象（见 bias），不能参与结论。
        row['at_or_below_resolution'] = (resolution is not None
                                         and row['interval_minutes'] <= resolution)

    observed_interval = (probe or {}).get('interval_p50')
    if observed_interval and observations:
        rows = rows + [simulate_interval(events, observations,
                                         round(observed_interval / 60.0, 1),
                                         stale_seconds, source='observed')]

    daily = daily_change_intervals(history_store, labels)

    return {
        'v': 1,
        'generated_at': to_iso(now),
        'status': 'ok' if sufficient else 'insufficient',
        'preliminary': not sufficient,
        'source': {
            'observations': observations_source,
            'probe': 'data/fetch-log.json',
            'daily_history': 'data/history/station-history.json',
            'revisions_used': len(observations),
            'change_store_generated_at': change_store.get('generated_at'),
        },
        'sample': {
            'observations': len(observations),
            'coverage_days': coverage_days,
            'span_hours': span_hours,
            'first_at': to_iso(observations[0]['at']) if observations else None,
            'last_at': to_iso(observations[-1]['at']) if observations else None,
            'stations_observed': stations_observed,
            'change_events': len(events),
            'stations_changed': len({ev['key'] for ev in events}),
            'required_days': int(min_days),
            'missing_days': missing_days,
            'required_change_events': MIN_CHANGE_EVENTS,
        },
        'probe': probe,
        'change_arrival': dict(grouped_stats(station_intervals(events)),
                               units='seconds'),
        'change_arrival_daily': dict(grouped_stats(daily),
                                     units='seconds', days_covered=len(history_store.get('t') or [])),
        'staleness': {
            'definition': ('过期展示时长 = 价格真实变更后到下一次模拟抓取之间的时长'
                           '（抓取网格从窗口起点对齐，窗口终点自身也算一次抓取）；'
                           '陈旧站占比 = Σ过期展示时长 /(站点数×窗口时长)'),
            'stale_threshold_seconds': stale_seconds,
            'rows': rows,
        },
        'criteria': {
            'p90_age_seconds_max': P90_AGE_BUDGET_SECONDS,
            'stale_share_max': STALE_SHARE_BUDGET,
        },
        'recommendation': recommend(rows) if sufficient else None,
        'bias': {
            'sampling_minutes': resolution,
            'notes': [
                '采样分辨率以内（< 观测间隔）的价格抖动完全不可见 ⇒ 变更次数是**下界**。',
                '变更时刻只能定位到「新价格首次出现的观测点」，晚于真实变更时刻'
                ' ⇒ 过期展示时长被**低估**。',
                '两个偏差方向一致：现状比数据看起来更差 ⇒ 结论是**乐观上界**，'
                '实际所需抓取间隔 ≤ 本报告的建议值。',
                '因此 T ≤ 观测分辨率的档位（表中标「≤ 分辨率，不可测」）不参与结论：'
                '那些数值只反映网格对齐误差，是测量下限，不是真实陈旧度。',
            ],
        },
    }


def _minutes(seconds):
    if seconds is None:
        return '—'
    if seconds < 5400:
        return f'{seconds / 60:.1f} 分钟'
    if seconds < 48 * 3600:
        return f'{seconds / 3600:.1f} 小时'
    return f'{seconds / 86400:.1f} 天'


def _pct(value):
    return '—' if value is None else f'{value * 100:.2f}%'


def _stats_cell(stats):
    if not stats or not stats.get('count'):
        return '—'
    return (f"{_minutes(stats['p50'])} / {_minutes(stats['p90'])} / "
            f"{_minutes(stats['p99'])} (n={stats['count']})")


def _group_table(title, groups, limit=12):
    lines = [title, '', '| 分组 | p50 / p90 / p99 (n) |', '| --- | --- |']
    shown = 0
    for name, stats in groups.items():
        if not stats.get('count'):
            continue
        lines.append(f'| {name} | {_stats_cell(stats)} |')
        shown += 1
        if shown >= limit:
            break
    if not shown:
        lines.append('| — | 无足够样本 |')
    lines.append('')
    return lines


def format_report(payload):
    """payload -> ``docs/fetch-interval-report.md`` 的 Markdown 正文。"""
    sample = payload['sample']
    rows = payload['staleness']['rows']
    bias = payload['bias']
    rec = payload['recommendation']
    probe = payload.get('probe') or {}

    lines = [
        '# 最大可接受抓取间隔评估（陈旧度 vs 间隔）',
        '',
        f"_由 `scripts/interval_report.py` 生成于 {payload['generated_at']}"
        f"（纯标准库，无新依赖）。_",
        '',
        '## 结论摘要',
        '',
    ]

    if rec:
        lines += [
            f"- **{rec['summary']}**",
            f"- 判据：p90 过期展示时长 ≤ {payload['criteria']['p90_age_seconds_max'] / 60:g} 分钟"
            f" 且 陈旧站占比 ≤ {payload['criteria']['stale_share_max'] * 100:g}%",
        ]
    else:
        lines += ['- **样本不足，本次不给出结论**（只输出标注为「初值」的表格）。']

    if probe.get('interval_p50'):
        lines.append(
            f"- 实测抓取间隔（#74 探针）：p50 {_minutes(probe['interval_p50'])} / "
            f"p90 {_minutes(probe.get('interval_p90'))}"
            + ('；共 %s 次、覆盖 %s 天' % (probe.get('total_fetches'), probe.get('days_covered'))
               if probe.get('total_fetches') else ''))
    else:
        lines.append('- 实测抓取间隔（#74 探针）：暂无可用的本机探针数据。')

    lines += [
        '',
        '## 1. 样本',
        '',
        '| 项 | 值 |',
        '| --- | --- |',
        f"| 观测点数（= 抓取后的站点快照数） | {sample['observations']} |",
        f"| 覆盖天数 | {sample['coverage_days']}（窗口 {sample['span_hours']} 小时） |",
        f"| 观测间隔中位数（真值分辨率） | "
        f"{_minutes((bias['sampling_minutes'] or 0) * 60) if bias['sampling_minutes'] else '—'} |",
        f"| 站点数（窗口内见过的坐标键） | {sample['stations_observed']} |",
        f"| 变更事件数 / 发生变更的站点数 | {sample['change_events']} / {sample['stations_changed']} |",
        f"| 窗口 | {sample['first_at']} → {sample['last_at']} |",
        f"| 数据来源 | `{payload['source']['observations']}`；台账 `data/fetch-log.json` |",
        '',
    ]

    if payload['preliminary']:
        lines += [
            f"> ⚠️ **样本不足**：目前 {sample['coverage_days']} 天 / "
            f"{sample['change_events']} 个变更事件，"
            f"满足判据需要 ≥ {sample['required_days']} 天且 ≥ "
            f"{sample['required_change_events']} 个变更事件。"
            f"**还需 {sample['missing_days']} 天**（探针每 15 分钟积累一次，无需人工干预）。",
            '> 下表数值为**初值**，只用于验证口径，不构成结论；样本达标后重跑本脚本即可。',
            '',
        ]

    lines += ['## 2. 变更到达分布（站点级：两次价格变更之间的间隔）', '']
    arrival = payload['change_arrival']
    lines.append(f"总体：{_stats_cell(arrival['overall'])}")
    lines.append('')
    lines += _group_table('按区域：', arrival['by_region'])
    lines += _group_table('按品牌：', arrival['by_brand'])

    daily = payload['change_arrival_daily']
    lines += [
        '### 2b. 日分辨率粗估（`data/history/station-history.json`，'
        f"{daily.get('days_covered')} 天）",
        '',
        '一天之内发生多次变更只能看到一次，间隔必然是整数天的倍数，仅作量级参考。',
        '',
        f"总体：{_stats_cell(daily['overall'])}",
        '',
    ]
    lines += _group_table('按区域：', daily['by_region'], limit=6)
    lines += _group_table('按品牌：', daily['by_brand'], limit=6)

    lines += [
        '## 3. 陈旧度 vs 抓取间隔 T',
        '',
        f"**口径**：{payload['staleness']['definition']}"
        f"「陈旧」阈值 = {_minutes(payload['staleness']['stale_threshold_seconds'])}。"
        '从没在窗口内变更过的站点不计入年龄分布（它们展示的仍是上游当前价）。',
        '',
        '| T（分钟） | 变更事件 | p50 过期时长 | p90 过期时长 | p99 过期时长 | 陈旧站占比 | 过期 > 60 分钟的变更占比 |',
        '| --- | --- | --- | --- | --- | --- | --- |',
    ]
    for row in rows:
        label = f"{row['interval_minutes']:g}"
        if row.get('source') == 'observed':
            label += '（实测）'
        elif row.get('at_or_below_resolution'):
            label += '（≤ 分辨率，不可测）'
        lines.append(
            f"| {label} | {row['events']} | "
            f"{_minutes(row['p50'])} | {_minutes(row['p90'])} | {_minutes(row['p99'])} | "
            f"{_pct(row['stale_share'])} | {_pct(row['stale_share_over_threshold'])} |")
    lines.append('')

    lines += ['## 4. 拐点与判据', '']
    lines += _marginal_lines(rows, preliminary=payload['preliminary'])
    lines.append('')
    if rec:
        lines += [
            f"- **{rec['summary']}**",
            f"- 判据：p90 ≤ {rec['criteria']['p90_age_seconds_max'] / 60:g} 分钟"
            f"（实测 {_minutes(rec['p90_at_max_acceptable'])}）"
            f" 且 陈旧站占比 ≤ {rec['criteria']['stale_share_max'] * 100:g}%"
            f"（实测 {_pct(rec['stale_share_at_max_acceptable'])}）",
        ]
    else:
        lines += ['- 样本不足，暂无可判定的最大可接受间隔（见第 1 节所需天数）。']
    lines.append('')

    lines += ['## 5. 测量偏差（必读）', '']
    if bias['sampling_minutes']:
        lines.append(f"本报告的观测分辨率（抓取间隔中位数）= {bias['sampling_minutes']:g} 分钟。")
        lines.append('')
    lines += ['- ' + note for note in bias['notes']]
    lines += [
        '',
        '因此本报告的结论是**乐观上界**：真实所需的抓取间隔只会更短。'
        '要让结论更保守，只能靠更细的采样（缩短探针周期）或更长的窗口，'
        '而不是调整判据。',
        '',
    ]
    return '\n'.join(lines)


def _marginal_lines(rows, preliminary=False):
    """相邻档位（T 减半）的 p90 改善表 + 拐点结论。"""
    by_minutes = {row['interval_minutes']: row for row in rows
                  if row.get('source') != 'observed' and not row.get('at_or_below_resolution')}
    lines = ['| T（分钟） | 比 T/2 更密的收益（p90 降低比例） | 陈旧站占比变化 |',
             '| --- | --- | --- |']
    gains = []
    for minutes in sorted(by_minutes):
        row = by_minutes[minutes]
        half = by_minutes.get(minutes / 2)
        if not half or not row.get('p90') or row['p90'] <= 0:
            continue
        gain = (row['p90'] - (half.get('p90') or 0.0)) / row['p90']
        share_gain = ((row.get('stale_share') or 0.0) - (half.get('stale_share') or 0.0))
        lines.append(f'| {minutes:g} → {minutes / 2:g} | {gain * 100:.1f}% | '
                     f'{share_gain * 100:+.3f} 个百分点 |')
        gains.append((minutes, gain))
    if not gains:
        lines.append('| — | 样本不足 | — |')
        return lines
    lines.append('')
    if preliminary:
        lines.append('样本不足：上表只是口径验证用的初值，**不足以判定拐点**'
                     '（所需天数见第 1 节）；样本达标后重跑本脚本。')
        return lines
    verdict = [g for g in gains if g[1] < KNEE_REDUCTION]
    if verdict:
        lines.append(f"按当前判据（p90 越密越好），把 T 减半后 p90 改善 < "
                     f"{KNEE_REDUCTION * 100:g}% 的档位从 **{verdict[0][0]:g} 分钟** 开始——"
                     f"那就是收益递减的拐点：比它更长的间隔没必要，比它更密的间隔买不到多少新鲜度。")
    else:
        lines.append('在本窗口内 p90 与 T 近似线性（每减半 T 就把 p90 减半），'
                     '没有出现收益递减拐点；决定性的拐点就是判据本身。')
    return lines


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default=str(DEFAULT_DATA_DIR))
    parser.add_argument('--root', default=str(ROOT),
                        help='仓库根（git 修订历史从这里读）')
    parser.add_argument('--now', help='UTC ISO 时间戳覆盖（测试/复现）')
    parser.add_argument('--observations', help='直接用一份观测序列 JSON 代替 git 历史')
    parser.add_argument('--events-file',
                        help='本地探针事件日志 JSONL（`data/probe-local/events.jsonl`）；'
                             '文件缺失时回退 git 修订历史，不报错')
    parser.add_argument('--report', help=f'Markdown 输出路径（默认 {REPORT_REL}）')
    parser.add_argument('--json-out', help=f'JSON 输出路径（默认 data/{JSON_FILE}）')
    parser.add_argument('--min-days', type=int, default=MIN_SAMPLE_DAYS,
                        help='样本充足所需的最少覆盖天数')
    parser.add_argument('--stale-minutes', type=int, default=STALE_AGE_SECONDS // 60,
                        help='「陈旧」阈值（分钟）')
    parser.add_argument('--no-write', action='store_true', help='只打印，不写文件')
    parser.add_argument('--json', action='store_true', help='打印机器可读 JSON')
    args = parser.parse_args(argv)

    observations = None
    events = None
    observations_source = None
    if args.observations:
        observations = normalize_observations(load_json(args.observations) or [])
    elif args.events_file:
        events_path = Path(args.events_file)
        if events_path.exists():
            observations, events = load_events_file(events_path)
            observations_source = f'{args.events_file}（本地探针事件日志）'
        else:
            print(f'note: {args.events_file} not found; falling back to the git '
                  'revision history', file=sys.stderr)

    now = parse_iso(args.now) or datetime.now(timezone.utc)
    payload = build_report(args.data_dir, args.root, now,
                           observations=observations, events=events,
                           observations_source=observations_source,
                           min_days=args.min_days,
                           stale_seconds=args.stale_minutes * 60)
    markdown = format_report(payload)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
    else:
        print(f"status={payload['status']} "
              f"days={payload['sample']['coverage_days']}/{payload['sample']['required_days']} "
              f"events={payload['sample']['change_events']} "
              f"sampling={payload['bias']['sampling_minutes']}min")
        print(payload['recommendation']['summary'] if payload['recommendation']
              else f"样本不足：还需 {payload['sample']['missing_days']} 天")
        for row in payload['staleness']['rows']:
            print(f"  T={row['interval_minutes']:g}min "
                  f"p50={_minutes(row['p50'])} p90={_minutes(row['p90'])} "
                  f"p99={_minutes(row['p99'])} stale_share={_pct(row['stale_share'])}")

    if args.no_write:
        return 0

    report_path = Path(args.report) if args.report else Path(args.root) / REPORT_REL
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(markdown + '\n', encoding='utf-8')

    json_path = Path(args.json_out) if args.json_out else Path(args.data_dir) / JSON_FILE
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + '\n',
                         encoding='utf-8')

    if not args.json:
        print(f'wrote {report_path} and {json_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
