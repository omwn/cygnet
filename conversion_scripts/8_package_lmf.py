#!/usr/bin/env python3
"""Step 8: package each cleaned WN-LMF file as web/lmf/<id>-<version>.tar.gz
for download from the web UI (see cyg.package)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cyg.package import package_all
from cyg.presynth import SOURCES_NAME


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--presynth-dir', type=Path, default=Path('bin/cygnets_presynth'))
    parser.add_argument('--extras-dir', type=Path, default=Path('bin/raw_wns/extras'))
    parser.add_argument('--out-dir', type=Path, default=Path('web/lmf'))
    args = parser.parse_args()
    if not (args.presynth_dir / SOURCES_NAME).exists():
        print(f'  No {SOURCES_NAME} in {args.presynth_dir}: run Step 2 first')
        return 1
    written, kept, removed = package_all(args.presynth_dir, args.extras_dir, args.out_dir)
    print(f'  {written} archive(s) written, {kept} up to date, {removed} stale removed, in {args.out_dir}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
