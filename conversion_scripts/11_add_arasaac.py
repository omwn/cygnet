#!/usr/bin/env python3
"""
Populate the arasaac table in cygnet.db from the ARASAAC pictogram API.

Downloads the ARASAAC English pictogram data, maps each entry's WordNet 3.1
synset IDs to ILIs via omw-en31, caches the result as araasac-ili.json, then
inserts one row per synset into cygnet.db.

Image URL pattern: https://static.arasaac.org/pictograms/{id}/{id}_300.png

License: Pictograms by Sergio Palao. Origin: ARASAAC (https://arasaac.org).
License: CC BY-NC-SA 4.0. Owner: Government of Aragon (Spain).
"""

import json
import sqlite3
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

import wn

ARASAAC_API_URL = 'https://api.arasaac.org/api/pictograms/all/en'
RAW_PATH = Path('bin/araasac-en.json')       # downloaded, not committed
MAPPING_PATH = Path('data/araasac-ili.json') # committed to repo
WN_DATA_DIR = Path('bin/wordnet_data')
DB_PATH = Path('web/cygnet.db')


def ensure_raw(path: Path) -> None:
    """Download the ARASAAC English pictogram data if not already cached.

    Args:
        path: Local cache path for the raw API response.
    """
    if path.exists():
        print(f'  Using cached {path}')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f'  Downloading ARASAAC data from {ARASAAC_API_URL}...')
    urllib.request.urlretrieve(ARASAAC_API_URL, path)
    print(f'  Cached at {path}')


def build_mapping(raw_path: Path, mapping_path: Path) -> None:
    """Build ILI→{lemma:[arasaac_id]} mapping from ARASAAC raw data.

    Uses omw-en31:1.4 to resolve WN3.1 synset offsets to ILIs.

    Args:
        raw_path: Path to the cached ARASAAC API JSON.
        mapping_path: Output path for the ILI mapping.
    """
    WN_DATA_DIR.mkdir(parents=True, exist_ok=True)
    wn.config.data_directory = str(WN_DATA_DIR)
    print('  Ensuring omw-en31:1.4 is available...')
    wn.download('omw-en31:1.4', progress_handler=None)

    wn31 = wn.Wordnet(lexicon='omw-en31:1.4')

    with open(raw_path, encoding='utf-8') as f:
        data = json.load(f)

    ili2ara: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for entry in data:
        synsets = entry.get('synsets') or []
        pic_id = entry['_id']
        keywords = [kw['keyword'] for kw in entry.get('keywords', []) if kw.get('keyword')]
        for ss_offset in synsets:
            try:
                ili = wn31.synset(f'omw-en31-{ss_offset}').ili.id
            except Exception:
                continue
            for kw in keywords:
                if pic_id not in ili2ara[ili][kw]:
                    ili2ara[ili][kw].append(pic_id)

    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    with open(mapping_path, 'w', encoding='utf-8') as f:
        json.dump(ili2ara, f)
    print(f'  Wrote {len(ili2ara):,} ILI entries to {mapping_path}')


def load_mapping(path: Path) -> dict[str, int]:
    """Load ILI→arasaac_id mapping, returning one ID per ILI.

    Args:
        path: Path to araasac-ili.json.

    Returns:
        Dict mapping ILI string (e.g. 'i58915') to first ARASAAC pictogram ID.
    """
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)

    result = {}
    for ili, lemma_map in raw.items():
        for ids in lemma_map.values():
            if ids:
                result[ili] = ids[0]
                break
    return result


def populate(db_path: Path, mapping: dict[str, int]) -> int:
    """Insert arasaac rows for synsets whose ILI appears in the mapping.

    Args:
        db_path: Path to cygnet.db.
        mapping: Dict from ILI string to ARASAAC pictogram ID.

    Returns:
        Number of rows inserted.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS arasaac (
                synset_rowid INTEGER NOT NULL REFERENCES synsets(rowid),
                arasaac_id   INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_arasaac_synset ON arasaac(synset_rowid);
        """)

        synsets = conn.execute(
            'SELECT rowid, ili FROM synsets WHERE ili IS NOT NULL'
        ).fetchall()

        rows = [
            (rowid, mapping[ili])
            for rowid, ili in synsets
            if ili in mapping
        ]

        conn.execute('DELETE FROM arasaac')
        conn.executemany(
            'INSERT INTO arasaac (synset_rowid, arasaac_id) VALUES (?, ?)', rows
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def main(rebuild: bool = False) -> None:
    """Main entry point.

    Args:
        rebuild: If True, re-download ARASAAC data and regenerate the mapping
                 even if data/araasac-ili.json already exists.
    """
    if not DB_PATH.exists():
        print(f'Database not found: {DB_PATH}')
        print('Run conversion_scripts/6_synthesise.py first.')
        sys.exit(1)

    if rebuild or not MAPPING_PATH.exists():
        print('Fetching ARASAAC pictogram data...')
        ensure_raw(RAW_PATH)
        print('Building ILI mapping...')
        build_mapping(RAW_PATH, MAPPING_PATH)
    else:
        print(f'Using committed mapping at {MAPPING_PATH}')

    print(f'Loading ARASAAC mapping from {MAPPING_PATH}...')
    mapping = load_mapping(MAPPING_PATH)
    print(f'  {len(mapping):,} ILIs with pictograms')

    print(f'Populating arasaac table in {DB_PATH}...')
    n = populate(DB_PATH, mapping)
    print(f'  Inserted {n:,} rows')
    print('Done.')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--rebuild', action='store_true',
        help='Re-download ARASAAC data and regenerate the ILI mapping',
    )
    args = parser.parse_args()
    main(rebuild=args.rebuild)
