#!/usr/bin/env python3
"""抓取触发方式的**实测**间隔分布（issue #77）——「选定的节奏真的能达到吗？」

#76 的问题（`scripts/interval_report.py`）是「为了新鲜度**需要多快**」；
本脚本的问题是「我们发货的触发方式**实际**有多快，够不够」。两者都是决策证据，
但一个都不能靠推断：这个脚本只读**已经提交**的真实记录，纯标准库，无新依赖。

数据来源（二选一，都会用到）：

* **数据提交** —— ``data/stations.json`` 的 git 提交历史。每次抓取只要上游快照
  有变化就会产生一次提交，提交主题区分来源：
  ``Update gas prices data [skip ci]`` = GitHub Actions（``static.yml`` 的
  schedule / repository_dispatch / push），带 ``(local probe)`` 标记的 = 本机探针
  （``scripts/local_fetch_loop.sh``）。既有的 Agent 提交、文档提交不是抓取，忽略。
* **探针台账** —— ``data/fetch-log.json``（#74）：每次**完整跑完链路**的时刻，
  不依赖快照是否变化，是比提交历史更精确的探针真值。

产出：``data/trigger-interval.json``（机器可读，PM agent 直接引用）+ 终端摘要。

## 判据

* 目标间隔 ``TARGET_INTERVAL_MINUTES`` = **30 分钟**，由 #76 的判据反推：
  p90 过期展示时长 ≈ 0.9 × T ≤ 30 分钟 ⇒ T ≤ 33 分钟，取 30（见
  ``docs/fetch-interval-report.md`` 与 ``docs/history-data.md``）。
* 一个来源「达标」= 它的**历史窗口**里 p90 间隔 ≤ 目标，且窗口跨 ≥ 12 小时、
  至少有 2 个间隔。少于 12 小时一律 ``insufficient-window``：这个脚本拒绝用
  「短窗口看着还行」当证据（验收标准要求 ≥ 12 小时）。

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

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / 'data'

SNAPSHOT_REL = 'data/stations.json'
PROBE_LOG_FILE = 'fetch-log.json'
JSON_FILE = 'trigger-interval.json'

STATIC_WORKFLOW_REL = Path('.github') / 'workflows' / 'static.yml'
UPDATE_WORKFLOW_REL = Path('.github') / 'workflows' / 'update-data.yml'
DISPATCH_EVENT_TYPE = 'fetch-data'

# #76 的判据反推出的目标间隔（分钟）：p90 过期展示时长 ≈ 0.9 × T ≤ 30 分钟。
TARGET_INTERVAL_MINUTES = 30
# 验收标准：证据窗口至少 12 小时，且至少 2 个间隔，才允许给结论。
MIN_WINDOW_HOURS = 12.0
MIN_INTERVALS = 2
# git 历史最多回看多少个提交（保持脚本有界，和 interval_report 同一量级）。
MAX_COMMITS = 2000

SOURCE_ACTIONS = 'actions'
SOURCE_PROBE = 'local-probe'
DATA_COMMIT_PREFIX = 'Update gas prices data'
PROBE_MARKER = '(local probe)'

# 时长单位：Actions 侧的正常间隔是小时级，用分钟做统一单位。
_SECONDS_PER_MINUTE = 60.0
_MINUTES_PER_HOUR = 60.0


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


def verdict(stats, target_minutes=TARGET_INTERVAL_MINUTES):
    """``meets`` / ``misses`` / ``insufficient-window``（见模块 docstring）。"""
    if not stats or stats.get('intervals', 0) < MIN_INTERVALS:
        return 'insufficient-window'
    if (stats.get('span_hours') or 0.0) < MIN_WINDOW_HOURS:
        return 'insufficient-window'
    p90 = stats.get('p90')
    if p90 is None:
        return 'insufficient-window'
    return 'meets' if p90 <= target_minutes else 'misses'


def interval_stats(times, target_minutes=TARGET_INTERVAL_MINUTES, since=None):
    """一个来源的实测间隔分布 + 是否达标（间隔与分位数单位都是分钟）。"""
    stamps = sorted(dt for dt in (times or []) if dt is not None)
    kept = [dt for dt in stamps if since is None or dt >= since]
    gaps = intervals_minutes(stamps, since=since)
    span_hours = 0.0
    if len(kept) > 1:
        span_hours = round((kept[-1] - kept[0]).total_seconds() / 3600.0, 2)

    def _round(value):
        return None if value is None else round(value, 1)

    over = sum(1 for gap in gaps if gap > target_minutes)
    stats = {
        'fetches': len(kept),
        'intervals': len(gaps),
        'span_hours': span_hours,
        'first_at': to_iso(kept[0]) if kept else None,
        'last_at': to_iso(kept[-1]) if kept else None,
        'p50': _round(percentile(gaps, 50)),
        'p90': _round(percentile(gaps, 90)),
        'p99': _round(percentile(gaps, 99)),
        'min': _round(min(gaps)) if gaps else None,
        'max': _round(max(gaps)) if gaps else None,
        'over_target': over,
        'over_target_share': round(over / len(gaps), 4) if gaps else None,
        'window_since': to_iso(since) if since is not None else None,
    }
    stats['verdict'] = verdict(stats, target_minutes)
    return stats


def decide(verdicts):
    """按「基础设施越少越好」挑出可达标的来源。

    Actions 的 schedule 不需要任何外部设施，所以它达标就是首选；否则退到本机
    探针 / 外部触发器；两者都还没攒够 12 小时证据时明确报 ``undecided``，
    而不是从短窗口里编一个结论。
    """
    if verdicts.get(SOURCE_ACTIONS) == 'meets':
        return SOURCE_ACTIONS, ('Actions 的 cron 自身就达到目标间隔：外部触发器只是可选加速。')
    for source in (SOURCE_PROBE, SOURCE_ACTIONS):
        if verdicts.get(source) == 'meets':
            return source, (f'{source} 达到目标间隔（Actions cron 被节流），'
                            '外部触发器/本机探针必须常驻。')
    return 'undecided', (f'证据不足：没有任何触发方式在 ≥ {MIN_WINDOW_HOURS:g} 小时的窗口里'
                        '达到目标间隔（或还没攒够样本），先按现状运行并重跑本脚本。')


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


def shipped_rhythm(root=ROOT):
    """读发货的工作流 -> 实际节奏（cron 间隔、预算、兜底）+ 是否达标所需。"""
    root = Path(root)
    static = _read(root / STATIC_WORKFLOW_REL)
    update = _read(root / UPDATE_WORKFLOW_REL)
    crons = re.findall(r'cron:\s*\'([^\']+)\'', static)
    cron = crons[0] if crons else None
    cron_interval = cron_interval_minutes(cron) if cron else None
    return {
        'cron': cron,
        'cron_interval_minutes': cron_interval,
        'daily_budget': _workflow_value(update, 'FETCH_DAILY_BUDGET'),
        'max_age_minutes': _workflow_value(update, 'FETCH_MAX_AGE_MINUTES'),
        'target_interval_minutes': TARGET_INTERVAL_MINUTES,
        'external_trigger_required': (cron_interval is None
                                      or cron_interval > TARGET_INTERVAL_MINUTES),
        'dispatch_event_type': DISPATCH_EVENT_TYPE,
    }


def rhythm_errors(shipped):
    """发货节奏的内部一致性问题（空列表 = 一致）。

    验收标准 #77：触发间隔与闸门参数必须同步 —— 兜底不能比排班还密（那样计划就
    形同虚设），也不能比排班还疏（那样一次被丢的运行就吃掉两个小时的新鲜度）。
    """
    errors = []
    cron_interval = shipped.get('cron_interval_minutes')
    budget = shipped.get('daily_budget')
    max_age = shipped.get('max_age_minutes')
    if not cron_interval:
        errors.append('static.yml 里没有可解析的 cron（或形状不被支持）')
        return errors
    expected_budget = int(round(24 * _MINUTES_PER_HOUR / cron_interval))
    if budget != expected_budget:
        errors.append(f'FETCH_DAILY_BUDGET={budget} 与 cron 每天 {expected_budget} '
                      f'次觉醒不一致：计划槽位要么用不完，要么永远排不满')
    if max_age != 2 * cron_interval:
        errors.append(f'FETCH_MAX_AGE_MINUTES={max_age} 不是 cron 间隔 '
                      f'{cron_interval} 分钟的 2 倍：兜底会比排班还密或还疏')
    return errors


# ── 报告装配 ────────────────────────────────────────────────────────────────

def build_payload(data_dir, root, now, target_minutes=TARGET_INTERVAL_MINUTES,
                  window_hours=MIN_WINDOW_HOURS, fetches=None, shipped=None,
                  since=None):
    """装配 ``data/trigger-interval.json`` 的 payload。

    ``fetches`` 可注入 ``{source: [datetime, ...]}``（测试 / 离线复现）；给定时
    不碰 git 与台账。``history`` = 全部可用样本（≥12 小时才算数，``since`` 可以
    只量某一个节奏时期），``recent`` = 最近 ``window_hours`` 小时的当前状态。
    """
    data_dir = Path(data_dir)
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    window_since = now - timedelta(hours=float(window_hours))
    if fetches is None:
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

    chosen, message = decide({name: rec['verdict'] for name, rec in sources.items()})
    return {
        'v': 1,
        'generated_at': to_iso(now),
        'target_interval_minutes': target_minutes,
        'window_hours': float(window_hours),
        'criteria': {
            'p90_max_minutes': target_minutes,
            'min_window_hours': MIN_WINDOW_HOURS,
            'min_intervals': MIN_INTERVALS,
        },
        'source': {
            'actions': f'git commit history of {SNAPSHOT_REL} (subject filter)',
            'local-probe': f'{SNAPSHOT_REL} commits + data/{PROBE_LOG_FILE}',
        },
        'sources': sources,
        'decision': {'chosen': chosen, 'message': message},
        'shipped': shipped if shipped is not None else shipped_rhythm(root),
        'notes': [
            '只看得见「内容变化」的抓取：快照与上次完全相同时没有提交，'
            '所以提交推出的间隔是实际触发间隔的**上界**（探针台账无此偏差）。',
            '提交时间 = 触发时刻 + checkout/下载/处理/提交耗时（分钟级），不是触发时刻本身。',
            'Actions 的 schedule 会被节流；「不达标」通常意味着触发方式本身不行，'
            '不是这份数据碰巧不好 —— 所以验收要求 ≥ 12 小时的窗口而不是单次样本。',
        ],
    }


def format_summary(payload):
    """终端摘要（人类可读，与 JSON 同源）。"""
    lines = [f"目标间隔 {payload['target_interval_minutes']} 分钟 / "
             f"窗口 ≥ {payload['criteria']['min_window_hours']:g} 小时"
             f"（生成于 {payload['generated_at']}）", '']
    lines.append('| 来源 | 抓取 | 窗口 | p50 | p90 | max | > 目标 | 判定 |')
    lines.append('| --- | --- | --- | --- | --- | --- | --- | --- |')
    for name, rec in (payload.get('sources') or {}).items():
        for label, stats in (('历史', rec['history']), ('最近窗口', rec['recent'])):
            fmt = lambda value: '—' if value is None else f'{value:g} 分钟'
            lines.append(
                f"| {name} {label} | {stats['fetches']} | "
                f"{stats['span_hours']:g} h | {fmt(stats['p50'])} | {fmt(stats['p90'])} | "
                f"{fmt(stats['max'])} | "
                f"{(stats['over_target_share'] or 0) * 100:.0f}% | {stats['verdict']} |")
    decision = payload.get('decision') or {}
    lines += ['', f"结论：{decision.get('chosen')} — {decision.get('message')}"]
    shipped = payload.get('shipped') or {}
    lines += [
        '',
        f"发货节奏：cron `{shipped.get('cron')}` "
        f"（{shipped.get('cron_interval_minutes')} 分钟）/ "
        f"budget {shipped.get('daily_budget')} / "
        f"max-age {shipped.get('max_age_minutes')} 分钟"
        + ('（cron 自身达不到目标 ⇒ 需要外部触发器）'
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
    parser.add_argument('--target-minutes', type=int, default=TARGET_INTERVAL_MINUTES,
                        help=f'目标间隔（默认 {TARGET_INTERVAL_MINUTES} 分钟，#76 判据）')
    parser.add_argument('--window-hours', type=float, default=MIN_WINDOW_HOURS,
                        help=f'「最近窗口」小时数（验收要求 ≥ {MIN_WINDOW_HOURS:g}）')
    parser.add_argument('--since',
                        help='只统计这个 UTC ISO 时刻之后的抓取（量某一个节奏时期）')
    parser.add_argument('--json-out', help=f'JSON 输出路径（默认 data/{JSON_FILE}）')
    parser.add_argument('--no-write', action='store_true', help='只打印，不写文件')
    parser.add_argument('--json', action='store_true', help='打印机器可读 JSON')
    args = parser.parse_args(argv)

    now = parse_iso(args.now) or datetime.now(timezone.utc)
    payload = build_payload(args.data_dir, args.root, now,
                            target_minutes=args.target_minutes,
                            window_hours=args.window_hours,
                            since=parse_iso(args.since))

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
