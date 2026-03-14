#!/usr/bin/env python3
"""
Populate the arasaac table in cygnet.db from the ARASAAC–ILI mapping.

Reads araasac-ili.json (ILI ID → {lemma: [arasaac_id, ...]}) and inserts
one row per synset: the first pictogram ID found for any lemma of that concept.

Image URL pattern: https://static.arasaac.org/pictograms/{id}/{id}_300.png

License: Pictograms by Sergio Palao. Origin: ARASAAC (https://arasaac.org).
License: CC BY-NC-SA 4.0. Owner: Government of Aragon (Spain).
"""

import json
import sqlite3
import sys
from pathlib import Path

MAPPING_PATH = Path('bin/araasac-ili.json')
DB_PATH = Path('web/cygnet.db')


def load_mapping(path: Path) -> dict[str, list[int]]:
    """Load ILI→arasaac_id mapping, returning one ID per ILI.

    Args:
        path: Path to araasac-ili.json.

    Returns:
        Dict mapping ILI string (e.g. 'i58915') to first Arasaac pictogram ID.
    """
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)

    # raw: {"i58915": {"pavement": [2247], "sidewalk": [2247]}, ...}
    # Flatten to one ID per ILI: take first ID from any lemma's list.
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
        mapping: Dict from ILI string to Arasaac pictogram ID.

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

        synsets = conn.execute('SELECT rowid, ili FROM synsets WHERE ili IS NOT NULL').fetchall()

        rows = []
        for rowid, ili in synsets:
            if ili in mapping:
                rows.append((rowid, mapping[ili]))

        conn.execute('DELETE FROM arasaac')
        conn.executemany('INSERT INTO arasaac (synset_rowid, arasaac_id) VALUES (?, ?)', rows)
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def main() -> None:
    """Main entry point."""
    if not MAPPING_PATH.exists():
        print(f'Mapping file not found: {MAPPING_PATH}')
        print('Copy araasac-ili.json from chainnet-viz to bin/araasac-ili.json')
        sys.exit(1)

    if not DB_PATH.exists():
        print(f'Database not found: {DB_PATH}')
        print('Run conversion_scripts/6_synthesise.py first.')
        sys.exit(1)

    print(f'Loading ARASAAC mapping from {MAPPING_PATH}...')
    mapping = load_mapping(MAPPING_PATH)
    print(f'  {len(mapping):,} ILIs with pictograms')

    print(f'Populating arasaac table in {DB_PATH}...')
    n = populate(DB_PATH, mapping)
    print(f'  Inserted {n:,} rows')
    print('Done.')


if __name__ == '__main__':
    main()
