#!/usr/bin/env python3
"""Tests for scripts/probe_events.py — the local probe event log (issue #92).

The probe used to commit every data snapshot (~39 MiB/day), which was pure repo
bloat: those pushes never trigger a site deploy. #92 moves the samples out of
the repo into a local, gitignored JSONL log, one line per probe cycle:

    {"t": "2026-10-16T20:04:25Z", "changed": 135, "keys": [...], "stations": 2465}

These tests pin the line contract (fetch time, change count, changed keys,
station total), the tolerance of the reader, and the size bounds that keep the
log well under 300 KB/day.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import probe_events as pe  # noqa: E402

AT = '2026-10-16T20:04:25Z'


def change_store(at=AT, changed=('1.00000,1.00000', '2.00000,2.00000'),
                 unchanged=('3.00000,3.00000',)):
    """The real fixture: update_profile's per-station change store (`last_changed_at`)."""
    stations = {}
    for key in changed:
        stations[key] = {'t': at, 'u': at, 'p': [150.0, None, None]}
    for key in unchanged:
        stations[key] = {'t': None, 'u': at, 'p': [150.0, None, None]}
    return {
        'v': 1,
        'generated_at': at,
        'tiers': {'max_age_days': 14},
        'last': {'at': at, 'observed': len(stations), 'changed': len(changed)},
        's': stations,
    }


class BuildEventTest(unittest.TestCase):
    def test_one_event_per_fetch_carries_time_count_keys_and_stations(self):
        event = pe.build_event(change_store())
        self.assertEqual(event['t'], AT)
        self.assertEqual(event['changed'], 2)
        self.assertEqual(event['keys'], ['1.00000,1.00000', '2.00000,2.00000'])
        self.assertEqual(event['stations'], 3)

    def test_the_count_is_the_stores_own_count_not_the_key_list_length(self):
        # update_profile counts every re-priced station; the key list can be
        # truncated for size, so `changed` must survive independently.
        event = pe.build_event(change_store(), max_keys=1)
        self.assertEqual(event['changed'], 2)
        self.assertEqual(event['keys'], ['1.00000,1.00000'])

    def test_a_store_without_a_fetch_time_is_not_an_event(self):
        store = change_store()
        store['last'] = {'observed': 1, 'changed': 0}
        self.assertIsNone(pe.build_event(store))

    def test_unusable_input_is_not_an_event(self):
        self.assertIsNone(pe.build_event(None))
        self.assertIsNone(pe.build_event({}))

    def test_station_total_falls_back_to_the_store_size(self):
        store = change_store()
        store['last'].pop('observed')
        self.assertEqual(pe.build_event(store)['stations'], 3)


class EventLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'probe-local' / 'events.jsonl'

    def read_lines(self):
        return [json.loads(line) for line in
                self.path.read_text(encoding='utf-8').splitlines() if line.strip()]

    def test_append_creates_the_directory_and_appends_one_line(self):
        self.assertTrue(pe.append_event(self.path, pe.build_event(change_store())))
        lines = self.read_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['t'], AT)
        self.assertEqual(lines[0]['changed'], 2)

    def test_appending_a_second_fetch_keeps_the_first_line(self):
        pe.append_event(self.path, pe.build_event(change_store()))
        pe.append_event(self.path,
                        pe.build_event(change_store(at='2026-10-16T20:19:25Z')))
        self.assertEqual([line['t'] for line in self.read_lines()],
                         [AT, '2026-10-16T20:19:25Z'])

    def test_a_replayed_fetch_time_is_not_appended_twice(self):
        pe.append_event(self.path, pe.build_event(change_store()))
        pe.append_event(self.path, pe.build_event(change_store()))
        self.assertEqual(len(self.read_lines()), 1)

    def test_the_log_is_truncated_to_the_newest_entries(self):
        stamps = [f'2026-10-16T20:{minute:02d}:00Z' for minute in (0, 15, 30, 45, 59)]
        for stamp in stamps:
            pe.append_event(self.path,
                            pe.build_event(change_store(at=stamp)),
                            max_entries=3)
        self.assertEqual([line['t'] for line in self.read_lines()], stamps[-3:])

    def test_load_events_skips_unusable_lines(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            '{"t": "2026-10-16T20:00:00Z", "changed": 1, "keys": ["a"], "stations": 3}\n'
            '\n'
            'not json\n'
            '[1, 2, 3]\n'
            '{"changed": 5}\n'
            '{"t": "2026-10-16T20:15:00Z"}\n',
            encoding='utf-8')
        events = pe.load_events(self.path)
        self.assertEqual([event['t'] for event in events],
                         ['2026-10-16T20:00:00Z', '2026-10-16T20:15:00Z'])
        self.assertEqual(events[0]['changed'], 1)

    def test_load_events_of_a_missing_file_is_empty(self):
        self.assertEqual(pe.load_events(self.path), [])

    def test_a_day_of_lines_stays_well_under_the_size_budget(self):
        # 96 cycles/day * the worst-case line (100 capped keys) < 300 KB.
        event = pe.build_event(change_store(
            changed=tuple(f'{i}.00000,{i}.00000' for i in range(400))))
        line = json.dumps(event, ensure_ascii=False, separators=(',', ':'))
        self.assertLess(len(line.encode('utf-8')), 3000)
        self.assertLess(len(line.encode('utf-8')) * 96, 300 * 1024)


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.changes = self.dir / 'station-changes.json'
        self.changes.write_text(json.dumps(change_store()), encoding='utf-8')
        self.events = self.dir / 'probe-local' / 'events.jsonl'

    def test_cli_appends_a_line_and_prints_it(self):
        rc = pe.main(['append', '--changes', str(self.changes),
                      '--events-file', str(self.events)])
        self.assertEqual(rc, 0)
        lines = pe.load_events(self.events)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['t'], AT)

    def test_cli_missing_change_store_fails_with_a_reason(self):
        import io
        from contextlib import redirect_stderr
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            rc = pe.main(['append', '--changes', str(self.dir / 'nope.json'),
                          '--events-file', str(self.events)])
        self.assertNotEqual(rc, 0)
        self.assertIn('station-changes', buffer.getvalue())


if __name__ == '__main__':
    unittest.main()
