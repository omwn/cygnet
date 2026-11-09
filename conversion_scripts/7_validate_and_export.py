#!/usr/bin/env python3
"""
Step 7: export cygnet.db + provenance.db as WN-LMF 1.4 and validate the
result against the DTD.

Writes cygnet.xml (sources in dc:source) and cygnet_small.xml (without),
each a multi-lexicon release: a concepts lexicon and one lexicon per
language (see cyg.export).
"""

import argparse
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from cyg.export import DTD_PATH, LmfExporter, validate_dtd


def _write_lexicon_parts(xml_path: Path, header: str):
    """Yield (lexicon id, part file) for each Lexicon of an exported file,
    each part a document of its own, streamed line by line.  cyg.export
    writes Lexicon start and end tags at two spaces' indentation, one
    element per line group."""
    part = lid = None
    with open(xml_path, encoding='utf-8') as f:
        for line in f:
            if line.startswith('  <Lexicon '):
                lid = line.split('id="', 1)[1].split('"', 1)[0]
                part = open(xml_path.with_name(f'{xml_path.stem}.{lid}.part.xml'), 'w', encoding='utf-8')
                part.write(header)
            if part is not None:
                part.write(line)
            if line.startswith('  </Lexicon>') and part is not None:
                part.write('</LexicalResource>\n')
                part.close()
                yield lid, Path(part.name)
                part = None


def validate(xml_path: Path, dtd_path: Path = DTD_PATH) -> bool:
    """Validate an exported file against the WN-LMF DTD, one Lexicon at a time.

    libxml2 (xmlstarlet and lxml alike) reports spurious duplicate IDs, or
    crashes, on a document with millions of IDs, so each Lexicon is
    validated as a document of its own: its IDREFs all point inside it
    (a Sense at its lexicon's own Synsets, a relation at its lexicon's
    elements), and Requires@ref is plain text.  The DOCTYPE is pointed at
    the local DTD so nothing is fetched.

    Args:
        xml_path: The file to check.
        dtd_path: The DTD.

    Returns:
        Whether every lexicon is valid; problems are printed.
    """
    header = (f'<?xml version="1.0" encoding="UTF-8"?>\n'
              f'<!DOCTYPE LexicalResource SYSTEM "{dtd_path.resolve()}">\n'
              f'<LexicalResource xmlns:dc="https://globalwordnet.github.io/schemas/dc/">\n')
    ok = True
    n = 0
    for lid, part in _write_lexicon_parts(xml_path, header):
        n += 1
        try:
            if shutil.which('xmlstarlet'):
                result = subprocess.run(
                    ['xmlstarlet', 'val', '--embed', '--err', str(part)],
                    capture_output=True, text=True, check=False)
                problems = [] if result.returncode == 0 else \
                    (result.stderr or result.stdout).splitlines()[:10]
            else:
                problems = validate_dtd(part, dtd_path)
        finally:
            part.unlink(missing_ok=True)
        if problems:
            ok = False
            print(f'  INVALID lexicon {lid}:')
            for line in problems:
                print(f'    {line}')
    if n == 0:
        ok = False
        print(f'  INVALID: {xml_path.name} has no Lexicon the validator could cut out')
    print(f'  {"valid" if ok else "INVALID"}: {xml_path.name} ({n} lexicons checked)')
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--web-dir', type=Path, default=Path('web'),
                        help='where cygnet.db and provenance.db are')
    parser.add_argument('--out-dir', type=Path, default=Path('.'),
                        help='where cygnet.xml and cygnet_small.xml go')
    args = parser.parse_args()

    db_path = args.web_dir / 'cygnet.db'
    prov_db_path = args.web_dir / 'provenance.db'
    if not db_path.exists():
        print(f'Error: {db_path} not found', file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    full, small = args.out_dir / 'cygnet.xml', args.out_dir / 'cygnet_small.xml'
    print(f'Writing {full} and {small}...')
    counts = LmfExporter(conn, prov_db_path if prov_db_path.exists() else None).write(full, small)
    conn.close()
    print('  ' + ', '.join(f'{v:,} {k}' for k, v in counts.items())
          + f' ({full.stat().st_size / 1e6:.1f} MB and {small.stat().st_size / 1e6:.1f} MB)')
    outputs = [full, small]

    print('\nValidating against the WN-LMF 1.4 DTD...')
    results = [validate(path) for path in outputs]   # validate both, then judge
    sys.exit(0 if all(results) else 1)


if __name__ == '__main__':
    main()
