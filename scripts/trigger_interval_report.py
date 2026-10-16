#!/usr/bin/env python3
"""抓取触发方式的**实测**间隔分布（issue #77）——「选定的节奏真的能达到吗？」

#76 的问题（`scripts/interval_report.py`）是「为了新鲜度**需要多快**」；
本脚本的问题是「我们发货的触发方式**实际**有多快，够不够」。两者都是决策证据，
但一个都不能靠推断：这个脚本只读**已经提交**的真实记录，纯标准库，无新依赖。

数据来源（三种，都会用到）：

* **数据提交** —— ``data/stations.json`` 的 git 提交历史。每次抓取只要上游快照
  有变化就会产生一次提交，提交主题区分来源：
  ``Update gas prices data [skip ci]`` = GitHub Actions（``static.yml`` 的
  schedule / repository_dispatch / push），带 ``(local probe)`` 标记的 = 本机探针
  （``scripts/local_fetch_loop.sh``）。既有的 Agent 提交、文档提交不是抓取，忽略。
* **探针台账** —— ``data/fetch-log.json``（#74）：每次**完整跑完链路**的时刻，
  不依赖快照是否变化，是比提交历史更精确的探针真值。
* **已提交时刻表** —— ``data/fetch-schedule.json`` 的 ``timetable``（#91）：本机
  crontab 每 5 分钟 dispatch，闸门（``should_fetch.py``）只放行时刻表的槽位，所以
  **本机 dispatch 源的抓取节奏就是时刻表**；陈旧占比由 ``schedule_eval.py`` 在
  实测探针序列上模拟（#93）。

产出：``data/trigger-interval.json``（机器可读，PM agent 直接引用）+ 终端摘要。

## 判据（issue #93 更新）

* **忙时（本地 06:00–23:59）间隔 p90 ≤ 20 分钟** —— #91 的显式时刻表在忙时就是
  20 分钟一格，夜间（01:00–05:00）故意只保底 1 次/小时、本地 00:00 每 10 分钟。
  旧的「所有间隔 p90 ≤ 30 分钟」按构造就对夜间 60 分钟间隔误判失败（#93），所以
  判据只看**忙时**：那是「价格真的会动」的窗口，也是陈旧度指标的主导项。
* **全窗口陈旧占比 ≤ 5 %** —— 由 #91 的 `scripts/schedule_eval.py` 在实测探针序列
  上模拟得出（时间加权的「随机时刻看到旧价」比例）。这个数字**只对时刻表
  （= 本机 dispatch 源）**可算，因为它衡量的是「计划能不能覆盖真实的改价时刻」；
  本机 dispatch 源正是「5 分钟唤醒 + 闸门按时刻表放行」这条链路。
* 一个来源「达标」= 窗口跨 ≥ 12 小时、至少有 2 个（忙时）间隔，且忙时 p90 ≤ 20
  分钟（时刻表源还要陈旧占比 ≤ 5 %）。少于 12 小时一律 `insufficient-window`：
  这个脚本拒绝用「短窗口看着还行」当证据（验收标准要求 ≥ 12 小时）。

## 来源

| 来源 | 真值 |
| --- | --- |
| `actions` | `data/stations.json` 的提交历史里由 Actions 提交的抓取 |
| `local-probe` | 同上带 `(local probe)` 标记的提交，加 `data/fetch-log.json` 台账 |
| `local-dispatch` | **已提交的时刻表**（`data/fetch-schedule.json` 的 `timetable`）+ `schedule_eval.py` |

`local-dispatch` 是「本机 crontab 每 5 分钟 dispatch + 闸门按时刻表放行」这条链路的
判定：闸门只放行槽位，所以它的**抓取节奏就是时刻表**；忙时间隔由时刻表自身
（`scripts/install_dispatch_cron.sh` 的 `*/5` 只是唤醒节奏，不是抓取节奏）、陈旧占比
由 `schedule_eval.py` 在实测序列上模拟。这是**计划保证 + 实测陈旧度**，不是等 12
小时提交历史；等真实 dispatch 提交攒够窗口后，`actions` 源就是它的实测对照。

## 诚实标注

1. 提交历史只看得见**内容变化**的抓取：两次抓取之间快照一模一样时不会有提交，
   所以提交推出的间隔是实际触发间隔的**上界**（探针台账没有这个问题）。
2. 提交时间 = 触发时间 + checkout/下载/处理/提交耗时（分钟级），不是触发时刻本身。
3. Actions 的 schedule 会被节流（本仓库实测 p50 ≈ 4 小时，见报告输出），
   所以「不达标」通常意味着触发方式本身不行，而不是这一份数据碰巧不好。
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from metrics import load_json, parse_iso, percentile, to_iso  # noqa: E402
from fetch_strategy import LOCAL_TZ, format_timetable, local_zone, parse_timetable  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / 'data'

SNAPSHOT_REL = 'data/stations.json'
PROBE_LOG_FILE = 'fetch-log.json'
JSON_FILE = 'trigger-interval.json'
SCHEDULE_FILE = 'fetch-schedule.json'
INSTALLER_REL = Path('scripts') / 'install_dispatch_cron.sh'

STATIC_WORKFLOW_REL = Path('.github') / 'workflows' / 'static.yml'
UPDATE_WORKFLOW_REL = Path('.github') / 'workflows' / 'update-data.yml'
DISPATCH_EVENT_TYPE = 'fetch-data'

# 旧的全局目标（#76）：p90 过期展示时长 ≈ 0.9 × T ≤ 30 分钟。保留为**参考**，
# 判定本身已经改成忙时判据（#93），因为夜间 60 分钟间隔按构造会让全局 p90 判据失败。
TARGET_INTERVAL_MINUTES = 30
# #91 时刻表的忙时间隔硬上限，也是新的判定阈值：忙时（本地 06:00–23:59）p90 ≤ 20 分钟。
BUSY_P90_MAX_MINUTES = 20
BUSY_FIRST_HOUR = 6
BUSY_LAST_HOUR = 23
BUSY_HOURS_LABEL = f'{BUSY_FIRST_HOUR:02d}:00-{BUSY_LAST_HOUR:02d}:59'
# #93 的第二个判据：时间加权陈旧占比（由 #91 的 schedule_eval.py 模拟实测）。
STALE_SHARE_MAX = 0.05
# #93 的唤醒节奏（`scripts/install_dispatch_cron.sh` 默认 `*/5 * * * *`）。
DEFAULT_DISPATCH_WAKE_MINUTES = 5
# 验收标准：证据窗口至少 12 小时，且至少 2 个间隔，才允许给结论。
MIN_WINDOW_HOURS = 12.0
MIN_INTERVALS = 2
# git 历史最多回看多少个提交（保持脚本有界，和 interval_report 同一量级）。
MAX_COMMITS = 2000

SOURCE_ACTIONS = 'actions'
SOURCE_PROBE = 'local-probe'
SOURCE_DISPATCH = 'local-dispatch'
DATA_COMMIT_PREFIX = 'Update gas prices data'
PROBE_MARKER = '(local probe)'

# 时长单位：Actions 侧的正常间隔是小时级，用分钟做统一单位。
_SECONDS_PER_MINUTE = 60.0


# ── 来源判定 / git 历史 ─────────────────────────────────────────────────────

def fetch_source(subject):
    """提交主题 -> 触发来源；不是抓取提交则 ``None``。"""
    text = str(subject or '').strip()
    if not text.startswith(DATA_COMMIT_PREFIX):
        return None
    return SOURCE_PROBE if PROBE_MARKER in text else SOURCE_ACTIONS


def parse_git_log(output):
    """``git log --format=%cI%x1f%s`` 输出 -> 抓取记录（旧到新）。

    格式错误的行（缺分隔符、时间戳不可解析）直接丢弃：脏输入只能让样本变小，
    不能让报告崩掉或编出一个时间戳。
    """
    out = []
    for line in str(output or '').splitlines():
        if '\x1f' not in line:
            continue
        stamp, subject = line.split('\x1f', 1)
        source = fetch_source(subject)
        at = parse_iso(stamp.strip())
        if source is None or at is None:
            continue
        out.append({'at': at, 'source': source})
    out.sort(key=lambda rec: rec['at'])
    return out


def load_git_fetches(root, rel=SNAPSHOT_REL, max_commits=MAX_COMMITS):
    """从 ``data/stations.json`` 的提交历史重建真实抓取序列。

    任何 git 失败（不是仓库、没装 git、文件不在历史里）都退化成空列表 ——
    报告必须能在一个干净 checkout 上跑出「没有证据」，而不是抛异常。
    """
    try:
        log = subprocess.run(
            ['git', '-C', str(root), 'log', f'--format=%cI%x1f%s',
             f'-{int(max_commits)}', '--', str(rel)],
            capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return []
    return parse_git_log(log.stdout)


def probe_fetch_times(fetch_log):
    """``data/fetch-log.json`` -> 探针抓取时刻（UTC，升序）。"""
    times = [parse_iso(raw) for raw in (fetch_log or {}).get('fetches') or []]
    return sorted(dt for dt in times if dt is not None)


def source_times(fetches, source=None):
    """抓取记录（或 datetime 列表）里某个来源的时刻列表。"""
    out = []
    for rec in fetches or []:
        if isinstance(rec, datetime):
            if source is None:
                out.append(rec)
            continue
        if not isinstance(rec, dict):
            continue
        if source is not None and rec.get('source') != source:
            continue
        at = rec.get('at')
        if at is not None:
            out.append(at)
    return sorted(out)


# ── 间隔分布 ────────────────────────────────────────────────────────────────

def intervals_minutes(times, since=None):
    """相邻抓取的时间差（分钟）；``since`` 只保留**起点在窗口内**的间隔。

    与 ``fetch_log.intervals_seconds`` 同一口径：跨越窗口起点的那个间隔（例如
    机器睡了一夜）不计入，避免一次长断点污染「当前节奏」。
    """
    stamps = sorted(dt for dt in (times or []) if dt is not None)
    out = []
    for prev, cur in zip(stamps, stamps[1:]):
        if since is not None and prev < since:
            continue
        out.append((cur - prev).total_seconds() / _SECONDS_PER_MINUTE)
    return out


def busy_intervals_minutes(times, since=None, tz=LOCAL_TZ,
                           first_hour=BUSY_FIRST_HOUR, last_hour=BUSY_LAST_HOUR):
    """只保留**起点落在本地忙时**的间隔（分钟），即 #93 的判定口径。

    夜间 01:00–05:00 的 60 分钟保底间隔是**故意的**（#91：夜间的改价量是白天的
    1/121，全天候 20 分钟采样只是浪费请求），所以全局 p90 判据会把一个正确的计划
    判失败。忙时才是有价格活动、且陈旧度指标主导的窗口。
    """
    stamps = sorted(dt for dt in (times or []) if dt is not None)
    out = []
    for prev, cur in zip(stamps, stamps[1:]):
        if since is not None and prev < since:
            continue
        local_hour = prev.astimezone(local_zone(tz)).hour
        if first_hour <= local_hour <= last_hour:
            out.append((cur - prev).total_seconds() / _SECONDS_PER_MINUTE)
    return out


def gap_stats(gaps, target_minutes=BUSY_P90_MAX_MINUTES):
    """一组间隔（分钟）的分布：分位数、极值、超阈值个数。"""
    gaps = [float(gap) for gap in (gaps or [])]

    def _round(value):
        return None if value is None else round(value, 1)

    over = sum(1 for gap in gaps if gap > target_minutes)
    return {
        'intervals': len(gaps),
        'p50': _round(percentile(gaps, 50)),
        'p90': _round(percentile(gaps, 90)),
        'p99': _round(percentile(gaps, 99)),
        'min': _round(min(gaps)) if gaps else None,
        'max': _round(max(gaps)) if gaps else None,
        'over_target': over,
        'over_target_share': round(over / len(gaps), 4) if gaps else None,
    }


def verdict(stats, target_minutes=BUSY_P90_MAX_MINUTES,
            stale_share_budget=STALE_SHARE_MAX):
    """``meets`` / ``misses`` / ``insufficient-window``（见模块 docstring）。

    #93 的判据：**忙时间隔 p90 ≤ 20 分钟**（+ 时刻表源另加陈旧占比 ≤ 5 %，由
    ``schedule_eval.py`` 提供）。全局 p90 只作参考，不再决定判定。
    """
    if not stats or stats.get('intervals', 0) < MIN_INTERVALS:
        return 'insufficient-window'
    if (stats.get('span_hours') or 0.0) < MIN_WINDOW_HOURS:
        return 'insufficient-window'
    busy_p90 = stats.get('busy_p90')
    if busy_p90 is None:
        return 'insufficient-window'
    if busy_p90 > target_minutes:
        return 'misses'
    share = stats.get('stale_share')
    if share is not None and share > stale_share_budget:
        return 'misses'
    return 'meets'


def interval_stats(times, target_minutes=BUSY_P90_MAX_MINUTES, since=None,
                   tz=LOCAL_TZ, stale_share=None, span_hours=None):
    """一个来源的实测间隔分布 + 是否达标（间隔与分位数单位都是分钟）。

    ``p50/p90/p99`` 是**全部间隔**的历史形状（参考），``busy_*`` 是判定口径
    （起点在本地忙时的间隔）。``stale_share``（只有时刻表源有）来自
    ``schedule_eval.py``；``span_hours`` 可覆盖（时刻表源的窗口是探针序列的长度）。
    """
    stamps = sorted(dt for dt in (times or []) if dt is not None)
    kept = [dt for dt in stamps if since is None or dt >= since]
    gaps = intervals_minutes(stamps, since=since)
    busy = busy_intervals_minutes(stamps, since=since, tz=tz)
    if span_hours is None:
        span_hours = 0.0
        if len(kept) > 1:
            span_hours = round((kept[-1] - kept[0]).total_seconds() / 3600.0, 2)

    stats = {
        'fetches': len(kept),
        'span_hours': span_hours,
        'first_at': to_iso(kept[0]) if kept else None,
        'last_at': to_iso(kept[-1]) if kept else None,
        'stale_share': stale_share,
        'stale_share_budget': STALE_SHARE_MAX if stale_share is not None else None,
        'window_since': to_iso(since) if since is not None else None,
    }
    stats.update(gap_stats(gaps, target_minutes))
    busy_stats = gap_stats(busy, target_minutes)
    stats['busy_intervals'] = busy_stats['intervals']
    stats['busy_p50'] = busy_stats['p50']
    stats['busy_p90'] = busy_stats['p90']
    stats['busy_max'] = busy_stats['max']
    stats['verdict'] = verdict(stats, target_minutes)
    return stats


def decide(verdicts):
    """按「基础设施越少越好」挑出可达标的来源。

    Actions 的 schedule 不需要任何外部设施，所以它达标就是首选；否则看本机
    dispatch 链路（#93：5 分钟唤醒 + 闸门按时刻表放行），再退到本机探针；
    都没攒够 12 小时证据时明确报 ``undecided``，而不是从短窗口里编一个结论。
    """
    if verdicts.get(SOURCE_ACTIONS) == 'meets':
        return SOURCE_ACTIONS, ('Actions 的 cron 自身就达到忙时目标间隔：外部触发器只是可选加速。')
    for source in (SOURCE_DISPATCH, SOURCE_PROBE):
        if verdicts.get(source) == 'meets':
            return source, (f'{source} 达到忙时目标间隔（p90 ≤ {BUSY_P90_MAX_MINUTES} 分钟，'
                            f'陈旧占比 ≤ {STALE_SHARE_MAX * 100:g} %）：Actions cron 被节流，'
                            '这条外部触发链路必须常驻。')
    return 'undecided', (f'证据不足：没有任何触发方式在 ≥ {MIN_WINDOW_HOURS:g} 小时的窗口里'
                        f'达到忙时间隔 p90 ≤ {BUSY_P90_MAX_MINUTES} 分钟（或还没攒够样本），'
                        '先按现状运行并重跑本脚本。')


# ── 本机 dispatch 源（issue #93）─────────────────────────────────────────────


def load_shipped_timetable(data_dir):
    """已提交的时刻表（``data/fetch-schedule.json`` 的本地 ``timetable``）。"""
    schedule = load_json(Path(data_dir) / SCHEDULE_FILE)
    if not isinstance(schedule, dict):
        return []
    return format_timetable(parse_timetable(schedule.get('timetable') or ()))


def timetable_busy_gaps(timetable):
    """时刻表在本地忙时内的相邻间隔（分钟）——本机 dispatch 链路的抓取节奏。

    闸门（``should_fetch.py``）只放行时刻表的槽位，所以本机 dispatch 源的抓取
    节奏**就是**时刻表：唤醒频率（``*/5``）比它密，被闸门在非到点时闸掉。
    """
    marks = sorted({(int(hour) % 24) * 60 + int(minute)
                    for hour, minute in parse_timetable(timetable)})
    if len(marks) < 2:
        return []
    gaps = []
    for start, end in zip(marks, marks[1:] + [marks[0] + 24 * 60]):
        if BUSY_FIRST_HOUR <= (start // 60) % 24 <= BUSY_LAST_HOUR:
            gaps.append(float(end - start))
    return sorted(gaps)


def schedule_eval_metrics(timetable, root=ROOT, observations_path=None, tz=LOCAL_TZ):
    """让 #91 的 ``schedule_eval.py`` 在实测探针序列上模拟这个时刻表。

    返回的 ``p90_seconds`` 是**过期展示时长**（改价时刻到下一次抓取）的 p90，
    ``stale_share`` 是时间加权的「随机时刻看到旧价」比例（#93 的第二个判据）。
    """
    import schedule_eval  # 延迟导入：只有本机 dispatch 源需要它

    observations = schedule_eval.load_observations(
        root=root, observations_path=observations_path)
    window = schedule_eval.build_window(observations)
    metrics = schedule_eval.simulate_timetable(list(timetable), window, tz=tz)
    lag_p90 = metrics.get('p90')
    return {
        'events': metrics.get('events'),
        'stations': metrics.get('stations'),
        'window_hours': round((metrics.get('window_seconds') or 0.0) / 3600.0, 2),
        'window_start': metrics.get('window_start'),
        'window_end': metrics.get('window_end'),
        'stale_age_p90_minutes': None if lag_p90 is None else round(lag_p90 / 60.0, 1),
        'stale_share': metrics.get('stale_share'),
        'stale_share_budget': STALE_SHARE_MAX,
        'verdict': schedule_eval.verdict(metrics),
    }


def dispatch_evidence(data_dir, root=ROOT, observations_path=None, tz=LOCAL_TZ,
                      target_minutes=BUSY_P90_MAX_MINUTES):
    """本机 dispatch 源（#93）的判定记录；没有已提交时刻表时返回 ``None``。

    ``history`` 里的间隔是**时刻表的间隔**（design），陈旧占比是 ``schedule_eval``
    在实测序列上的**模拟量**；两者都不是等 12 小时提交历史，所以 ``method`` 里写
    清楚：这是「计划保证 + 实测陈旧度」。真实的 dispatch 提交熛够窗口后，
    ``actions`` 源是它的实测对照。
    """
    timetable = load_shipped_timetable(data_dir)
    if not timetable:
        return None
    metrics = schedule_eval_metrics(timetable, root=root,
                                    observations_path=observations_path, tz=tz)
    gaps = timetable_busy_gaps(timetable)
    stats = {
        'fetches': len(timetable),
        'planned_fetches_per_day': len(timetable),
        'span_hours': metrics.get('window_hours'),
        'first_at': metrics.get('window_start'),
        'last_at': metrics.get('window_end'),
        'stale_share': metrics.get('stale_share'),
        'stale_share_budget': STALE_SHARE_MAX,
        'stale_age_p90_minutes': metrics.get('stale_age_p90_minutes'),
        'window_since': None,
    }
    stats.update(gap_stats(gaps, target_minutes))
    stats['busy_intervals'] = len(gaps)
    stats['busy_p50'] = stats['p50']
    stats['busy_p90'] = stats['p90']
    stats['busy_max'] = stats['max']
    stats['verdict'] = verdict(stats, target_minutes)
    return {
        'method': ('已提交时刻表（data/fetch-schedule.json 的 timetable）'
                   ' + scripts/schedule_eval.py 的陈旧度模拟'),
        'intervals_source': 'timetable gaps（计划保证，非实测提交历史）',
        'timetable': timetable,
        'history': stats,
        'verdict': stats['verdict'],
        'meets_target': stats['verdict'] == 'meets',
        'schedule_eval': metrics,
    }


# ── 仓库里实际发货的节奏 ────────────────────────────────────────────────────

def cron_interval_minutes(expr):
    """基本 cron 表达式 -> 名义间隔（分钟）。

    只支持仓库实际使用的形状（``M * * * *``、``M */N * * *``、``M H * * *``），
    其它形状返回 ``None``：宁可让一致性检查跳过，也不要瞎算一个数出来。
    """
    fields = str(expr or '').split()
    if len(fields) != 5:
        return None
    minute, hour = fields[0], fields[1]
    if not re.fullmatch(r'\d{1,2}', minute):
        return None
    if hour == '*':
        return 60
    multiple = re.fullmatch(r'\*/(\d{1,2})', hour)
    if multiple:
        step = int(multiple.group(1))
        return 60 * step if step > 0 else None
    if re.fullmatch(r'\d{1,2}', hour):
        return 24 * 60
    return None


def _read(path):
    try:
        return Path(path).read_text(encoding='utf-8')
    except OSError:
        return ''


def _workflow_value(text, name):
    match = re.search(rf'{name}:\s*\'?(\d+)\'?', text or '')
    return int(match.group(1)) if match else None


def dispatch_wake_minutes(root=ROOT):
    """本机唤醒间隔（分钟），从安装器里读，保证两边不会默默走偏。"""
    match = re.search(r'^DEFAULT_WAKE_MINUTES=(\d+)', _read(Path(root) / INSTALLER_REL),
                      re.M)
    return int(match.group(1)) if match else None


def shipped_rhythm(root=ROOT):
    """读发货的工作流 + 时刻表 -> 实际节奏（唤醒、计划槽位、兜底）+ 是否达标所需。"""
    root = Path(root)
    static = _read(root / STATIC_WORKFLOW_REL)
    update = _read(root / UPDATE_WORKFLOW_REL)
    crons = re.findall(r'cron:\s*\'([^\']+)\'', static)
    cron = crons[0] if crons else None
    cron_interval = cron_interval_minutes(cron) if cron else None
    timetable = load_shipped_timetable(root / 'data')
    busy = timetable_busy_gaps(timetable)
    busy_p90 = percentile(busy, 90) if busy else None
    return {
        'cron': cron,
        'cron_interval_minutes': cron_interval,
        'daily_budget': _workflow_value(update, 'FETCH_DAILY_BUDGET'),
        'max_age_minutes': _workflow_value(update, 'FETCH_MAX_AGE_MINUTES'),
        'dispatch_wake_minutes': dispatch_wake_minutes(root),
        'dispatch_event_type': DISPATCH_EVENT_TYPE,
        'timetable_fetches_per_day': len(timetable) or None,
        'timetable_busy_p90_minutes': None if busy_p90 is None else round(busy_p90, 1),
        'busy_p90_max_minutes': BUSY_P90_MAX_MINUTES,
        'busy_hours': BUSY_HOURS_LABEL,
        'target_interval_minutes': TARGET_INTERVAL_MINUTES,
        'external_trigger_required': (cron_interval is None
                                      or cron_interval > BUSY_P90_MAX_MINUTES),
    }


def rhythm_errors(shipped):
    """发货节奏的内部一致性问题（空列表 = 一致）。

    #93 的规则（取代 #77 的「预算 = 全天唤醒次数」）：闸门按**已提交时刻表**
    放行，所以 `FETCH_DAILY_BUDGET`（计划写入器的预算）必须与时刻表的槽位数
    一致，否则下一次抓取会把计划改成另一个形状；未到点的 dispatch 唤醒不消耗
    计划预算（它不在排班里），但唤醒频率必须**不稀于**忙时间隔，否则该抓的槽位
    会被漏掉。
    """
    errors = []
    cron_interval = shipped.get('cron_interval_minutes')
    budget = shipped.get('daily_budget')
    max_age = shipped.get('max_age_minutes')
    wake = shipped.get('dispatch_wake_minutes')
    slots = shipped.get('timetable_fetches_per_day')
    busy_p90 = shipped.get('timetable_busy_p90_minutes')
    if not cron_interval:
        errors.append('static.yml 里没有可解析的 cron（或形状不被支持）')
        return errors
    if budget is None or slots is None:
        errors.append('读不出 FETCH_DAILY_BUDGET 或已提交时刻表的槽位数：无法确认'
                      '计划写入器会不会把闸门读的时刻表改掉')
    elif budget != slots:
        errors.append(f'FETCH_DAILY_BUDGET={budget} 与已提交时刻表的 {slots} 槽位/天 '
                      '不一致：闸门按时刻表放行，计划写入器会用这个预算把它改掉')
    if max_age != 2 * cron_interval:
        errors.append(f'FETCH_MAX_AGE_MINUTES={max_age} 不是兜底 cron 间隔 '
                      f'{cron_interval} 分钟的 2 倍：兜底会比排班还密或还疏')
    if wake is None:
        errors.append('install_dispatch_cron.sh 里读不出 DEFAULT_WAKE_MINUTES：'
                      '无法确认本机唤醒者的频率')
    elif busy_p90 is not None and wake > busy_p90:
        errors.append(f'本机唤醒每 {wake} 分钟，比忙时时刻表间隔 {busy_p90:g} 分钟还稀：'
                      '闸门会错过该抓的槽位')
    return errors


# ── 报告装配 ────────────────────────────────────────────────────────────────

def build_payload(data_dir, root, now, target_minutes=BUSY_P90_MAX_MINUTES,
                  window_hours=MIN_WINDOW_HOURS, fetches=None, shipped=None,
                  since=None, dispatch=None, observations_path=None,
                  with_dispatch=None):
    """装配 ``data/trigger-interval.json`` 的 payload。

    ``fetches`` 可注入 ``{source: [datetime, ...]}``（测试 / 离线复现）；给定时
    不碰 git 与台账。``history`` = 全部可用样本（≥12 小时才算数，``since`` 可以
    只量某一个节奏时期），``recent`` = 最近 ``window_hours`` 小时的当前状态。

    ``dispatch`` 是本机 dispatch 源（#93）的记录；默认在真跑（``fetches is None``）
    时从已提交时刻表 + ``schedule_eval.py`` 算出来，可以用 ``with_dispatch=False``
    关掉（离线测试）。如果没有已提交时刻表，这个源就缺席（而不是编一个判定）。
    """
    data_dir = Path(data_dir)
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    window_since = now - timedelta(hours=float(window_hours))
    real_run = fetches is None
    if real_run:
        records = load_git_fetches(root)
        fetches = {
            SOURCE_ACTIONS: source_times(records, SOURCE_ACTIONS),
            SOURCE_PROBE: source_times(records, SOURCE_PROBE),
        }
        # 探针台账比提交历史精确（不依赖快照变化），有就用台账。
        ledger = probe_fetch_times(load_json(data_dir / PROBE_LOG_FILE))
        if len(ledger) >= len(fetches[SOURCE_PROBE]):
            fetches[SOURCE_PROBE] = ledger

    sources = {}
    for name, times in (fetches or {}).items():
        history = interval_stats(times, target_minutes, since=since)
        recent = interval_stats(times, target_minutes, since=window_since)
        sources[name] = {
            'history': history,
            'recent': recent,
            'verdict': history['verdict'],
            'meets_target': history['verdict'] == 'meets',
        }

    compute_dispatch = real_run if with_dispatch is None else bool(with_dispatch)
    if dispatch is None and compute_dispatch:
        dispatch = dispatch_evidence(data_dir, root=root,
                                     observations_path=observations_path,
                                     target_minutes=target_minutes)
    if dispatch:
        sources[SOURCE_DISPATCH] = dispatch

    chosen, message = decide({name: rec['verdict'] for name, rec in sources.items()})
    return {
        'v': 2,
        'generated_at': to_iso(now),
        'target_interval_minutes': target_minutes,
        'window_hours': float(window_hours),
        'criteria': {
            'busy_p90_max_minutes': target_minutes,
            'p90_max_minutes': target_minutes,      # legacy 别名 = 忙时判据
            'busy_hours': BUSY_HOURS_LABEL,
            'stale_share_max': STALE_SHARE_MAX,
            'overall_p90_max_minutes': TARGET_INTERVAL_MINUTES,
            'min_window_hours': MIN_WINDOW_HOURS,
            'min_intervals': MIN_INTERVALS,
        },
        'source': {
            SOURCE_ACTIONS: f'git commit history of {SNAPSHOT_REL} (subject filter)',
            SOURCE_PROBE: f'{SNAPSHOT_REL} commits + data/{PROBE_LOG_FILE}',
            SOURCE_DISPATCH: (f'data/{SCHEDULE_FILE} timetable + '
                              'scripts/schedule_eval.py (issues #91/#93)'),
        },
        'sources': sources,
        'decision': {'chosen': chosen, 'message': message},
        'shipped': shipped if shipped is not None else shipped_rhythm(root),
        'notes': [
            '只看得见「内容变化」的抓取：快照与上次完全相同时没有提交，'
            '所以提交推出的间隔是实际触发间隔的**上界**（探针台账无此偏差）。',
            '提交时间 = 触发时刻 + checkout/下载/处理/提交耗时（分钟级），不是触发时刻本身。',
            f'判定口径改为**忙时（本地 {BUSY_HOURS_LABEL}）p90 ≤ '
            f'{BUSY_P90_MAX_MINUTES} 分钟**：夜间 01:00–05:00 的 60 分钟保底间隔是 #91 的故意'
            '设计，用全局 p90 会把正确的时刻表按构造判失败（#93）。',
            f'本机 dispatch 源（{SOURCE_DISPATCH}）的间隔来自**已提交时刻表**（闸门只放行'
            '槽位），陈旧占比来自 schedule_eval.py 在实测探针序列上的模拟：'
            '这是「计划保证 + 实测陈旧度」，不是等 12 小时的提交历史。',
        ],
    }


def format_stat(stats):
    """规模 / 窗口 / 忙时 p90 / 陈旧占比的公共格式化。"""
    def _minutes(value):
        return '—' if value is None else f'{value:g} 分钟'

    share = stats.get('stale_share')
    return (f"{stats['fetches']} | {stats['span_hours']:g} h | "
            f"{_minutes(stats.get('p50'))} | {_minutes(stats.get('busy_p90'))} | "
            f"{_minutes(stats.get('max'))} | "
            f"{(stats.get('over_target_share') or 0) * 100:.0f}% | "
            f"{'—' if share is None else f'{share * 100:.2f}%'} | {stats['verdict']} |")


def format_summary(payload):
    """终端摘要（人类可读，与 JSON 同源）。"""
    criteria = payload.get('criteria') or {}
    lines = [f"判定：忙时（{criteria.get('busy_hours')}）间隔 p90 ≤ "
             f"{criteria.get('busy_p90_max_minutes')} 分钟"
             f"（时刻表源还要陈旧占比 ≤ {criteria.get('stale_share_max', 0) * 100:g}%）/ "
             f"窗口 ≥ {criteria.get('min_window_hours'):g} 小时"
             f"（生成于 {payload['generated_at']}）", '']
    lines.append('| 来源 | 抓取 | 窗口 | p50 | 忙时 p90 | max | > 目标(忙时) | 陈旧占比 | 判定 |')
    lines.append('| --- | --- | --- | --- | --- | --- | --- | --- | --- |')
    for name, rec in (payload.get('sources') or {}).items():
        rows = [('历史', rec.get('history'))]
        if rec.get('recent'):
            rows.append(('最近窗口', rec['recent']))
        for label, stats in rows:
            if stats:
                lines.append(f"| {name} {label} | {format_stat(stats)}")
        if rec.get('method'):
            lines.append(f"| {name} | _{rec['method']}_ |")
    decision = payload.get('decision') or {}
    lines += ['', f"结论：{decision.get('chosen')} — {decision.get('message')}"]
    shipped = payload.get('shipped') or {}
    lines += [
        '',
        f"发货节奏：兜底 cron `{shipped.get('cron')}` "
        f"（{shipped.get('cron_interval_minutes')} 分钟）/ "
        f"本机唤醒 每 {shipped.get('dispatch_wake_minutes')} 分钟 / "
        f"计划 budget {shipped.get('daily_budget')} "
        f"（时刻表 {shipped.get('timetable_fetches_per_day')} 槽位/天）/ "
        f"max-age {shipped.get('max_age_minutes')} 分钟"
        + ('（cron 自身达不到忙时目标 ⇒ 需要本机 dispatch 链路）'
           if shipped.get('external_trigger_required') else ''),
    ]
    errors = rhythm_errors(shipped)
    if errors:
        lines += ['', '节奏不一致：'] + [f'- {err}' for err in errors]
    return '\n'.join(lines)


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default=str(DEFAULT_DATA_DIR))
    parser.add_argument('--root', default=str(ROOT),
                        help='仓库根（git 提交历史从这里读）')
    parser.add_argument('--now', help='UTC ISO 时间戳覆盖（测试/复现）')
    parser.add_argument('--target-minutes', type=int, default=BUSY_P90_MAX_MINUTES,
                        help=f'忙时间隔 p90 上限（默认 {BUSY_P90_MAX_MINUTES} 分钟，#91/#93 判据）')
    parser.add_argument('--window-hours', type=float, default=MIN_WINDOW_HOURS,
                        help=f'「最近窗口」小时数（验收要求 ≥ {MIN_WINDOW_HOURS:g}）')
    parser.add_argument('--since',
                        help='只统计这个 UTC ISO 时刻之后的抓取（量某一个节奏时期）')
    parser.add_argument('--observations',
                        help='探针观测序列 JSON，代替 git 修订历史（给 schedule_eval 用）')
    parser.add_argument('--no-dispatch', action='store_true',
                        help='不算本机 dispatch 源（离线 / 只量提交历史时）')
    parser.add_argument('--json-out', help=f'JSON 输出路径（默认 data/{JSON_FILE}）')
    parser.add_argument('--no-write', action='store_true', help='只打印，不写文件')
    parser.add_argument('--json', action='store_true', help='打印机器可读 JSON')
    args = parser.parse_args(argv)

    now = parse_iso(args.now) or datetime.now(timezone.utc)
    payload = build_payload(args.data_dir, args.root, now,
                            target_minutes=args.target_minutes,
                            window_hours=args.window_hours,
                            since=parse_iso(args.since),
                            observations_path=args.observations,
                            with_dispatch=False if args.no_dispatch else None)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
    else:
        print(format_summary(payload))

    if args.no_write:
        return 0

    json_path = (Path(args.json_out) if args.json_out
                 else Path(args.data_dir) / JSON_FILE)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False,
                                    separators=(',', ':')) + '\n',
                         encoding='utf-8')
    if not args.json:
        print(f'wrote {json_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
