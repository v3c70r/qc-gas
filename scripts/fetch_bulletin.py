#!/usr/bin/env python3
"""Fetch and parse the Régie de l'énergie weekly petroleum Bulletin.

The Régie publishes a single, fixed-URL weekly PDF ("Bulletin") with, for each
of the 17 administrative regions and for regular / super / diesel:

  * the previous week's average pump price (``prev_mean``),
  * the current week's average pump price (``mean``),
  * the week-over-week change (``delta``),
  * the **retail margin, before sales taxes** (``margin``),
    "Marge de détail estimée (hors taxes)".

It also publishes the Montreal minimum rack ("rampe de chargement") prices
(Tableau 4). Parsing that gives the app an *official* explanation for regional
price differences instead of only our own reconstructed benchmarks.

Design notes
------------
* The PDF is rendered by Microsoft Excel, so ``pdftotext -layout`` yields a
  clean, column-aligned text layer; no OCR and no third-party dependency.
* Requests are conditional (``If-None-Match`` / ``If-Modified-Since``). The
  bulletin changes at most once a week, so a full download only happens when
  its validator changes — well inside the "at most one download per day" budget
  and without hammering the upstream Régie server.
* The output file is only rewritten when its ``week`` / ``fuels`` / ``rack``
  payload actually changes, so an unchanged bulletin produces no data commit.
* Any failure (network, missing ``pdftotext``, bad parse) keeps the previous
  file untouched and exits 0, so the workflow step never breaks the run.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BULLETIN_URL = (
    'https://www.regie-energie.qc.ca/storage/app/media/consommateurs/'
    'informations-pratiques/prix-petrole/publications/'
    'Publications-hebdomadaires/Bulletin/bulletin.pdf'
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / 'data'
OUTPUT_FILE = 'regie-margin.json'

FUEL_BY_TABLE = {1: 'regular', 2: 'super', 3: 'diesel'}
RACK_TABLE = 4

USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)

# A data row: a region label followed by four right-aligned cells. The label is
# non-greedy so a parenthetical (e.g. "Nunavik (essence super, après subvention)")
# stays part of the name; columns are at least two spaces apart in -layout mode.
_ROW_RE = re.compile(
    r'^\s*(?P<name>.+?)\s{2,}'
    r'(?P<c1>n/d|[-\u2212]?\d+(?:,\d+)?)\s{2,}'
    r'(?P<c2>n/d|[-\u2212]?\d+(?:,\d+)?)\s{2,}'
    r'(?P<c3>n/d|[-\u2212]?\d+(?:,\d+)?)\s{2,}'
    r'(?P<c4>n/d|[-\u2212]?\d+(?:,\d+)?)\s*$'
)
_TABLE_RE = re.compile(r'^\s*Tableau\s+(\d+)\s*$', re.MULTILINE)
_NUMBERED_REGION_RE = re.compile(r'^\s*\d{1,2}\.\s*(?P<name>.+)$')
_NUNAVIK_RE = re.compile(r'^Nunavik\b')
_JAMESIE_RE = re.compile(r'^Jam[eé]sie\b')
_QUEBEC_RE = re.compile(r'^Qu[eé]bec\s*\(Moyenne\s+pond[eé]r[eé]e\)', re.IGNORECASE)
_RACK_ROW_RE = re.compile(
    r'^\s*(?P<week>\d{4}-\d{2}-\d{2})\s+'
    r'(?P<regular>[\d,]+)\s+(?P<super>[\d,]+)\s+'
    r'(?P<diesel>[\d,]+)\s+(?P<mazout>[\d,]+)\s*$'
)
_ISO_DATE_RE = re.compile(r'\d{4}-\d{2}-\d{2}')
_PUBLISHED_RE = re.compile(
    r'R[eé]gie de l.{1,3}[eé]nergie du Qu[eé]bec\s*,\s*le\s+(\d{4}-\d{2}-\d{2})'
)

FUELS = ('regular', 'super', 'diesel')


# ── Generic helpers ──────────────────────────────────────────────────────────

def parse_number(raw):
    """Return a float, or None for the Régie's ``n/d`` / empty markers."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() == 'n/d':
        return None
    text = text.replace('\u2212', '-').replace(',', '.')
    try:
        return float(text)
    except ValueError:
        return None


def load_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write('\n')


def to_iso(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None


# ── Parsing ──────────────────────────────────────────────────────────────────

def split_tables(text):
    """Split the -layout text into {table_number: chunk} by ``Tableau N``."""
    parts = _TABLE_RE.split(text)
    tables = {}
    for i in range(1, len(parts) - 1, 2):
        tables[int(parts[i])] = parts[i + 1]
    return tables


def parse_margin_table(chunk):
    """Parse one Tableau 1/2/3 chunk into regions + weighted Québec."""
    regions = {}
    quebec = None
    sub_rows = {}

    for line in chunk.splitlines():
        match = _ROW_RE.match(line)
        if not match:
            continue
        name = match.group('name').strip()
        entry = {
            'prev_mean': parse_number(match.group('c1')),
            'mean': parse_number(match.group('c2')),
            'delta': parse_number(match.group('c3')),
            'margin': parse_number(match.group('c4')),
        }

        parent = _NUMBERED_REGION_RE.match(name)
        if parent:
            regions[parent.group('name').strip()] = entry
            continue

        if _QUEBEC_RE.match(name):
            quebec = entry
            continue

        # Nunavik / Jamésie are sub-rows of Nord-du-Québec. They must never
        # overwrite the parent region value; Nunavik (whose data is largely
        # missing) is stored separately.
        if _NUNAVIK_RE.match(name):
            sub_rows.setdefault('nunavik', entry)['_name'] = 'Nunavik'
            continue
        if _JAMESIE_RE.match(name):
            sub_rows.setdefault('jamesie', entry)['_name'] = 'Jamésie'

    nord = regions.get('Nord-du-Québec')
    if nord is not None and sub_rows:
        sub = {}
        jamesie = sub_rows.get('jamesie')
        nunavik = sub_rows.get('nunavik')
        if jamesie is not None:
            sub['Jamésie'] = _strip_name(jamesie)
        if nunavik is not None:
            sub['Nunavik'] = _strip_name(nunavik)
        if sub:
            nord['sub'] = sub

    return regions, quebec


def _strip_name(entry):
    out = dict(entry)
    out.pop('_name', None)
    return out


def parse_rack_table(chunk):
    rows = []
    for line in chunk.splitlines():
        match = _RACK_ROW_RE.match(line)
        if not match:
            continue
        rows.append({
            'week': match.group('week'),
            'regular': parse_number(match.group('regular')),
            'super': parse_number(match.group('super')),
            'diesel': parse_number(match.group('diesel')),
            'mazout': parse_number(match.group('mazout')),
        })
    return rows


def parse_week(text, regular_chunk):
    """Extract {start,end,published_at} from the document header/footer."""
    dates = _ISO_DATE_RE.findall(regular_chunk or '')
    start = dates[0] if len(dates) >= 1 else None
    end = dates[1] if len(dates) >= 2 else None
    published = _PUBLISHED_RE.search(text)
    return {
        'start': start,
        'end': end,
        'published_at': published.group(1) if published else None,
    }


def parse_bulletin(text):
    """Parse a ``pdftotext -layout`` bulletin into the frontend payload.

    Returns ``{'week': ..., 'fuels': ..., 'rack': [...]}``. Missing values
    (``n/d``) become ``None`` so the frontend can degrade instead of showing a
    fabricated number.
    """
    tables = split_tables(text or '')
    fuels = {}
    for number, fuel in FUEL_BY_TABLE.items():
        chunk = tables.get(number)
        if chunk is None:
            continue
        regions, quebec = parse_margin_table(chunk)
        if not regions and quebec is None:
            continue
        fuels[fuel] = {'regions': regions, 'quebec': quebec}

    rack = parse_rack_table(tables.get(RACK_TABLE, ''))

    return {
        'week': parse_week(text or '', tables.get(1)),
        'fuels': fuels,
        'rack': rack,
    }


# ── Document assembly / change detection ─────────────────────────────────────

def content_signature(doc):
    """Stable signature of the payload, ignoring volatile fetch metadata.

    ``generated_at`` and the HTTP validator are deliberately excluded: an
    unchanged bulletin must not rewrite the file (and therefore must not create
    a data commit).
    """
    if not isinstance(doc, dict):
        return ''
    payload = {
        'week': doc.get('week'),
        'fuels': doc.get('fuels'),
        'rack': doc.get('rack'),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def compose_document(parsed, source, now):
    doc = {
        'v': 1,
        'generated_at': to_iso(now),
        'source_url': BULLETIN_URL,
        'week': parsed['week'],
        'fuels': parsed['fuels'],
        'rack': parsed['rack'],
    }
    if source:
        doc['source'] = source
    return doc


def build_conditional_headers(source):
    """Map a previously stored HTTP validator to conditional request headers."""
    headers = {}
    if not isinstance(source, dict):
        return headers
    etag = source.get('etag')
    last_modified = source.get('last_modified')
    if etag:
        headers['If-None-Match'] = etag
    if last_modified:
        headers['If-Modified-Since'] = last_modified
    return headers


def already_downloaded_today(source, now):
    """True when a full PDF download already happened on ``now``'s UTC day.

    Combined with the conditional request this caps the upstream cost: at most
    one full download per day, and (because the Bulletin is weekly) usually
    none at all thanks to ``304 Not Modified``.
    """
    fetched = parse_iso((source or {}).get('fetched_at') if isinstance(source, dict) else None)
    if not fetched:
        return False
    return fetched.astimezone(timezone.utc).date() == now.astimezone(timezone.utc).date()


def apply_parsed(out_path, existing, text, source, now, force=False):
    """Parse ``text`` and write ``out_path`` only when the payload changed.

    Returns True when the file was (re)written. A parse that yields no fuel
    data is treated as a failure so a bad download can never clobber good data.
    """
    parsed = parse_bulletin(text)
    if not parsed.get('fuels'):
        print('bulletin parse produced no region data; keeping previous file')
        return False

    doc = compose_document(parsed, source, now)
    if not force and existing is not None \
            and content_signature(existing) == content_signature(doc):
        print('bulletin unchanged; not rewriting data file')
        return False

    save_json(out_path, doc)
    total = sum(len(f.get('regions') or {}) for f in parsed['fuels'].values())
    print(f'wrote {out_path} ({total} region/fuel entries, week end '
          f'{parsed["week"].get("end")})')
    return True


# ── Network + pdftotext ──────────────────────────────────────────────────────

def pdf_to_text(pdf_bytes):
    """Convert PDF bytes to layout-preserving text via poppler's pdftotext."""
    exe = shutil.which('pdftotext')
    if not exe:
        raise RuntimeError('pdftotext not found (install poppler-utils)')
    result = subprocess.run(
        [exe, '-layout', '-', '-'],
        input=pdf_bytes,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f'pdftotext failed ({result.returncode}): '
            f'{result.stderr.decode("utf-8", "replace").strip()}'
        )
    return result.stdout.decode('utf-8', 'replace')


def fetch_pdf(url, source, timeout):
    """Conditional GET.

    Returns ``(pdf_bytes, new_source)`` on 200, ``(None, source)`` on 304, and
    raises on network errors.
    """
    headers = {
        'User-Agent': USER_AGENT,
        'Accept': 'application/pdf,*/*',
    }
    headers.update(build_conditional_headers(source))

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        new_source = {
            'etag': response.headers.get('ETag'),
            'last_modified': response.headers.get('Last-Modified'),
            'fetched_at': to_iso(datetime.now(timezone.utc)),
        }
        return response.read(), new_source


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default=str(DEFAULT_DATA_DIR))
    parser.add_argument('--url', default=BULLETIN_URL)
    parser.add_argument('--text', help='parse a local pdftotext -layout file (offline)')
    parser.add_argument('--pdf', help='parse a local PDF file (offline)')
    parser.add_argument('--now', help='UTC ISO timestamp override (tests/logs)')
    parser.add_argument('--timeout', type=int, default=60)
    parser.add_argument('--force', action='store_true',
                        help='write even when the payload is unchanged')
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    out_path = data_dir / OUTPUT_FILE
    existing = load_json(out_path)
    now = parse_iso(args.now) or datetime.now(timezone.utc)
    source = existing.get('source') if isinstance(existing, dict) else None

    if args.text:
        text_path = Path(args.text)
        if not text_path.exists():
            print(f'text file not found: {text_path}; keeping previous file')
            return 0
        apply_parsed(out_path, existing, text_path.read_text(encoding='utf-8'),
                     source, now, force=args.force)
        return 0

    if args.pdf:
        pdf_path = Path(args.pdf)
        if not pdf_path.exists():
            print(f'pdf file not found: {pdf_path}; keeping previous file')
            return 0
        try:
            text = pdf_to_text(pdf_path.read_bytes())
        except RuntimeError as exc:
            print(f'{exc}; keeping previous file')
            return 0
        apply_parsed(out_path, existing, text, source, now, force=args.force)
        return 0

    # Network path: conditional request, download only when the PDF changed.
    if not args.force and already_downloaded_today(source, now):
        print('bulletin already downloaded today; skipping upstream request')
        return 0
    try:
        pdf_bytes, new_source = fetch_pdf(args.url, source, args.timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            print('bulletin not modified (HTTP 304); keeping previous file')
            return 0
        print(f'bulletin download failed (HTTP {exc.code}); keeping previous file')
        return 0
    except Exception as exc:  # noqa: BLE001 - the workflow step must not break
        print(f'bulletin download failed ({exc}); keeping previous file')
        return 0

    if pdf_bytes is None:
        print('bulletin not modified; keeping previous file')
        return 0

    try:
        text = pdf_to_text(pdf_bytes)
    except RuntimeError as exc:
        print(f'{exc}; keeping previous file')
        return 0

    apply_parsed(out_path, existing, text, new_source, now, force=args.force)
    return 0


if __name__ == '__main__':
    sys.exit(main())
