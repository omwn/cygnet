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


def validate(xml_path: Path, dtd_path: Path = DTD_PATH) -> bool:
    """Validate an exported file against the WN-LMF DTD.

    With xmlstarlet installed, a copy whose DOCTYPE points at the local DTD
    is validated with it (no network, and it copes with a file lxml would
    need many gigabytes to hold); otherwise lxml validates in memory.

    Args:
        xml_path: The file to check.
        dtd_path: The DTD.

    Returns:
        Whether the file is valid; problems are printed.
    """
    if shutil.which('xmlstarlet'):
        local = xml_path.with_suffix('.localdtd.xml')
        with open(xml_path, 'rb') as src, open(local, 'wb') as dst:
            dst.write(src.readline())                          # XML declaration
            src.readline()                                      # the remote DOCTYPE
            dst.write(f'<!DOCTYPE LexicalResource SYSTEM "{dtd_path.resolve()}">\n'.encode())
            shutil.copyfileobj(src, dst)
        try:
            result = subprocess.run(
                ['xmlstarlet', 'val', '--embed', '--err', str(local)],
                capture_output=True, text=True, check=False,
            )
        finally:
            local.unlink(missing_ok=True)
        ok = result.returncode == 0
        problems = (result.stderr or result.stdout).splitlines()[:20] if not ok else []
    else:
        problems = validate_dtd(xml_path, dtd_path)
        ok = not problems
    print(f'  {"valid" if ok else "INVALID"}: {xml_path.name}')
    for line in problems:
        print(f'    {line}')
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
