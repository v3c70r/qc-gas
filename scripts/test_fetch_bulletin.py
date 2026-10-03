#!/usr/bin/env python3
"""Offline regression tests for the Régie weekly Bulletin parser.

The parser is exercised against a committed ``pdftotext -layout`` fixture so
the whole pipeline step can be verified without network access or the
``poppler-utils`` system package. The fixture is the real 2026-09-28 bulletin
(week of 2026-09-28), which keeps the tests honest about the exact text layout
the Régie's Excel-generated PDF produces:

  * 17 administrative regions × regular / super / diesel
  * ``n/d`` (not available) cells, including Nunavik's all-missing regular row
  * negative retail margins (diesel was negative that week)
  * the ``Nord-du-Québec`` parent row plus its ``Jamésie`` / ``Nunavik`` sub-rows
  * en-dash and accent-free region names (``Gaspésie–Iles-de-la-Madeleine``)
  * the Montreal rack-price series (Tableau 4)
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_bulletin  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'bulletin-2026-09-28.txt'


def load_fixture():
    return FIXTURE.read_text(encoding='utf-8')


class ParseBulletinTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = fetch_bulletin.parse_bulletin(load_fixture())

    def test_week_dates_and_published_at(self):
        week = self.doc['week']
        self.assertEqual(week['start'], '2026-09-21')
        self.assertEqual(week['end'], '2026-09-28')
        self.assertEqual(week['published_at'], '2026-10-02')

    def test_all_three_fuels_have_seventeen_regions(self):
        for fuel in ('regular', 'super', 'diesel'):
            regions = self.doc['fuels'][fuel]['regions']
            self.assertEqual(len(regions), 17, fuel)
            self.assertIn('Montréal', regions)

    def test_region_entry_keeps_prev_mean_mean_delta_margin(self):
        mtl = self.doc['fuels']['regular']['regions']['Montréal']
        self.assertEqual(mtl['prev_mean'], 199.6)
        self.assertEqual(mtl['mean'], 197.0)
        self.assertEqual(mtl['delta'], -2.7)
        self.assertEqual(mtl['margin'], 1.4)

    def test_negative_margin_keeps_its_sign(self):
        slsj = self.doc['fuels']['regular']['regions']['Saguenay–Lac-Saint-Jean']
        self.assertEqual(slsj['margin'], -6.9)
        self.assertEqual(slsj['delta'], -2.5)

    def test_zero_margin_stays_zero(self):
        out = self.doc['fuels']['super']['regions']['Outaouais']
        self.assertEqual(out['margin'], 0.0)

    def test_missing_values_become_null(self):
        ndq = self.doc['fuels']['regular']['regions']['Nord-du-Québec']
        self.assertIsNone(ndq['sub']['Nunavik']['mean'])
        self.assertIsNone(ndq['sub']['Nunavik']['margin'])
        # Super table has real Nunavik means but no margin.
        sup = self.doc['fuels']['super']['regions']['Nord-du-Québec']
        self.assertEqual(sup['sub']['Nunavik']['mean'], 230.9)
        self.assertIsNone(sup['sub']['Nunavik']['margin'])

    def test_quebec_weighted_row_is_stored(self):
        qc = self.doc['fuels']['regular']['quebec']
        self.assertEqual(qc['mean'], 192.0)
        self.assertEqual(qc['margin'], -0.6)

    def test_nord_du_quebec_parent_equals_jamesie_and_nunavik_is_separate(self):
        ndq = self.doc['fuels']['diesel']['regions']['Nord-du-Québec']
        # The parent row is the Jamésie value; Nunavik must never overwrite it.
        self.assertEqual(ndq['mean'], 308.7)
        self.assertEqual(ndq['margin'], 22.2)
        self.assertEqual(ndq['sub']['Jamésie']['mean'], 308.7)
        self.assertNotEqual(ndq['mean'], ndq['sub']['Nunavik']['mean'])

    def test_en_dash_region_name_is_kept_verbatim(self):
        gaspesie = self.doc['fuels']['regular']['regions']['Gaspésie–Iles-de-la-Madeleine']
        self.assertEqual(gaspesie['margin'], 7.1)

    def test_rack_series_from_tableau_4(self):
        rack = {row['week']: row for row in self.doc['rack']}
        self.assertEqual(len(rack), 4)
        self.assertEqual(rack['2026-09-07'],
                         {'week': '2026-09-07', 'regular': 138.4, 'super': 154.9,
                          'diesel': 218.8, 'mazout': 201.3})
        self.assertEqual(rack['2026-09-28']['regular'], 143.6)
        self.assertEqual(rack['2026-09-28']['mazout'], 212.1)


class ParseNumberTest(unittest.TestCase):
    def test_french_decimal_comma(self):
        self.assertEqual(fetch_bulletin.parse_number('194,9'), 194.9)

    def test_negative_sign(self):
        self.assertEqual(fetch_bulletin.parse_number('-5,1'), -5.1)

    def test_unicode_minus(self):
        self.assertEqual(fetch_bulletin.parse_number('\u22124,9'), -4.9)

    def test_missing_marker_is_none(self):
        self.assertIsNone(fetch_bulletin.parse_number('n/d'))
        self.assertIsNone(fetch_bulletin.parse_number(''))
        self.assertIsNone(fetch_bulletin.parse_number(None))


class ConditionalRequestTest(unittest.TestCase):
    def test_builds_if_none_match_and_if_modified_since(self):
        headers = fetch_bulletin.build_conditional_headers({
            'etag': '"abc"',
            'last_modified': 'Fri, 02 Oct 2026 15:17:34 GMT',
        })
        self.assertEqual(headers['If-None-Match'], '"abc"')
        self.assertEqual(headers['If-Modified-Since'], 'Fri, 02 Oct 2026 15:17:34 GMT')

    def test_empty_without_validators(self):
        self.assertEqual(fetch_bulletin.build_conditional_headers({}), {})
        self.assertEqual(fetch_bulletin.build_conditional_headers(None), {})

    def test_download_guard_allows_at_most_one_download_per_day(self):
        source = {'fetched_at': '2026-10-03T08:00:00Z'}
        same_day = datetime(2026, 10, 3, 20, 0, tzinfo=timezone.utc)
        next_day = datetime(2026, 10, 4, 1, 0, tzinfo=timezone.utc)
        self.assertTrue(fetch_bulletin.already_downloaded_today(source, same_day))
        self.assertFalse(fetch_bulletin.already_downloaded_today(source, next_day))
        self.assertFalse(fetch_bulletin.already_downloaded_today(None, same_day))


class ContentSignatureTest(unittest.TestCase):
    def test_signature_ignores_volatile_metadata(self):
        base = {'week': {'start': 'a'}, 'fuels': {'regular': {}}, 'rack': []}
        a = dict(base, generated_at='2026-10-01T00:00:00Z', source={'etag': 'x'})
        b = dict(base, generated_at='2026-10-02T00:00:00Z', source={'etag': 'y'})
        self.assertEqual(fetch_bulletin.content_signature(a),
                         fetch_bulletin.content_signature(b))

    def test_signature_changes_when_a_margin_changes(self):
        a = {'week': {'start': 'a'}, 'fuels': {'regular': {'regions': {'X': {'margin': 1.0}}}}, 'rack': []}
        b = {'week': {'start': 'a'}, 'fuels': {'regular': {'regions': {'X': {'margin': 2.0}}}}, 'rack': []}
        self.assertNotEqual(fetch_bulletin.content_signature(a),
                            fetch_bulletin.content_signature(b))


class MainCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.text_file = self.data_dir / 'fixture.txt'
        self.text_file.write_text(load_fixture(), encoding='utf-8')
        self.out = self.data_dir / 'regie-margin.json'

    def run_main(self, *extra, now='2026-10-03T10:00:00Z'):
        return fetch_bulletin.main([
            '--data-dir', str(self.data_dir),
            '--text', str(self.text_file),
            '--now', now,
            *extra,
        ])

    def test_writes_data_file_from_text_fixture(self):
        rc = self.run_main()
        self.assertEqual(rc, 0)
        doc = json.loads(self.out.read_text(encoding='utf-8'))
        self.assertEqual(doc['v'], 1)
        self.assertEqual(doc['source_url'], fetch_bulletin.BULLETIN_URL)
        self.assertEqual(doc['week']['end'], '2026-09-28')
        self.assertEqual(len(doc['fuels']['diesel']['regions']), 17)
        self.assertEqual(len(doc['rack']), 4)
        self.assertEqual(doc['generated_at'], '2026-10-03T10:00:00Z')

    def test_unchanged_week_does_not_rewrite_the_file(self):
        self.run_main(now='2026-10-03T10:00:00Z')
        first = self.out.read_bytes()
        self.run_main(now='2026-10-04T10:00:00Z')
        self.assertEqual(self.out.read_bytes(), first)

    def test_parse_failure_keeps_the_previous_file(self):
        self.run_main()
        good = self.out.read_bytes()
        self.text_file.write_text('this is not a bulletin', encoding='utf-8')
        rc = self.run_main(now='2026-10-04T10:00:00Z')
        self.assertEqual(rc, 0)
        self.assertEqual(self.out.read_bytes(), good)

    def test_missing_text_source_keeps_the_previous_file(self):
        self.run_main()
        good = self.out.read_bytes()
        self.text_file.unlink()
        rc = self.run_main(now='2026-10-04T10:00:00Z')
        self.assertEqual(rc, 0)
        self.assertEqual(self.out.read_bytes(), good)


if __name__ == '__main__':
    unittest.main()
