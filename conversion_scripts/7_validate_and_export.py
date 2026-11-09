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

from cyg.export import LmfExporter, load_provenance, validate_dtd

DTD_PATH = Path(__file__).resolve().parent.parent / 'cyg' / 'WN-LMF-1.4.dtd'


def _lexicon_chunks(xml_path: Path):
    """Yield (lexicon id, lines) for each Lexicon of an exported file, which
    cyg.export writes one top-level element per line group, Lexicon start
    and end tags at two spaces' indentation."""
    chunk: list[str] = []
    lid = None
    with open(xml_path, encoding='utf-8') as f:
        for line in f:
            if line.startswith('  <Lexicon '):
                chunk = [line]
                lid = line.split('id="', 1)[1].split('"', 1)[0]
            elif line.startswith('  </Lexicon>'):
                chunk.append(line)
                yield lid, chunk
                chunk = []
            elif chunk:
                chunk.append(line)


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
    for lid, lines in _lexicon_chunks(xml_path):
        n += 1
        part = xml_path.with_name(f'{xml_path.stem}.{lid}.part.xml')
        with open(part, 'w', encoding='utf-8') as out:
            out.write(header)
            out.writelines(lines)
            out.write('</LexicalResource>\n')
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

    print('Loading provenance...')
    prov_conn = sqlite3.connect(str(prov_db_path))
    prov = load_provenance(prov_conn)
    prov_conn.close()
    print(f'  {sum(len(v) for v in prov.values()):,} provenance rows')

    conn = sqlite3.connect(str(db_path))
    outputs = []
    for name, with_prov in (('cygnet.xml', True), ('cygnet_small.xml', False)):
        path = args.out_dir / name
        print(f'\nWriting {path}...')
        counts = LmfExporter(conn, prov if with_prov else None).write(path)
        print('  ' + ', '.join(f'{v:,} {k}' for k, v in counts.items())
              + f' ({path.stat().st_size / 1e6:.1f} MB)')
        outputs.append(path)
    conn.close()

    print('\nValidating against the WN-LMF 1.4 DTD...')
    results = [validate(path) for path in outputs]   # validate both, then judge
    sys.exit(0 if all(results) else 1)


if __name__ == '__main__':
    main()
