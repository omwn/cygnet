#!/usr/bin/env python3
"""Split provenance.db into one small database per resource.

The full provenance database is large (most of it Princeton and OEWN), but a
link by original id (``#/search?id=omw-kin-Fa-Pr``) needs only the rows of one
resource.  This writes ``web/provenance/<resource>.db.gz``, each with the same
schema as provenance.db, and ``web/provenance/index.json`` mapping each
resource code to its file and row count, so the web UI can fetch just one.
"""

import gzip
import itertools
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

SCHEMA = """
CREATE TABLE prov_resources (
    rowid INTEGER PRIMARY KEY,
    code  TEXT NOT NULL UNIQUE
);
CREATE TABLE prov_tables (
    rowid INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE
);
CREATE TABLE provenance (
    rowid          INTEGER PRIMARY KEY,
    table_rowid    INTEGER NOT NULL REFERENCES prov_tables(rowid),
    item_rowid     INTEGER NOT NULL,
    resource_rowid INTEGER NOT NULL REFERENCES prov_resources(rowid),
    version        TEXT,
    original_id    TEXT
);
"""


def file_stem(code: str, taken: set[str]) -> str:
    """A file name stem for a resource code, safe in a URL and unique.

    Args:
        code: The resource code, e.g. 'omw-kin-cmn-Hant'
        taken: Stems already used; the new one is added to it

    Returns:
        The code with anything but letters, digits, '.', '_' and '-' replaced
        by '_', with a numeric suffix if that collides with an earlier stem
    """
    base = re.sub(r"[^A-Za-z0-9._-]", "_", code)
    stem, n = base, 1
    while stem.lower() in taken:
        n += 1
        stem = f"{base}_{n}"
    taken.add(stem.lower())
    return stem


def split_provenance(prov_path: Path, out_dir: Path) -> dict[str, dict]:
    """Write one gzipped provenance database per resource, and an index.

    Args:
        prov_path: The full provenance.db
        out_dir: Directory to write into; replaced if it exists

    Returns:
        The index: resource code -> {"file": file name, "rows": row count}
    """
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    src = sqlite3.connect(prov_path)
    tables = src.execute("SELECT rowid, name FROM prov_tables").fetchall()
    codes = dict(src.execute("SELECT rowid, code FROM prov_resources"))
    index: dict[str, dict] = {}
    taken: set[str] = set()
    # One pass over the table, grouped by resource.
    all_rows = src.execute(
        "SELECT rowid, table_rowid, item_rowid, resource_rowid, version, original_id "
        "FROM provenance ORDER BY resource_rowid")
    for res_rowid, group in itertools.groupby(all_rows, key=lambda row: row[3]):
        rows = list(group)
        code = codes[res_rowid]
        stem = file_stem(code, taken)
        db_path = out_dir / f"{stem}.db"
        dst = sqlite3.connect(db_path)
        dst.executescript(SCHEMA)
        # Rowids are kept, so queries written for provenance.db work unchanged.
        dst.execute(
            "INSERT INTO prov_resources (rowid, code) VALUES (?, ?)", (res_rowid, code))
        dst.executemany("INSERT INTO prov_tables (rowid, name) VALUES (?, ?)", tables)
        dst.executemany("INSERT INTO provenance VALUES (?, ?, ?, ?, ?, ?)", rows)
        dst.execute(
            "CREATE INDEX idx_provenance_lookup ON provenance(table_rowid, item_rowid)")
        dst.commit()
        dst.execute("VACUUM")
        dst.close()
        gz_path = f"{db_path}.gz"
        with open(db_path, "rb") as fin, gzip.open(gz_path, "wb", 9) as fout:
            shutil.copyfileobj(fin, fout)
        db_path.unlink()
        index[code] = {"file": f"{stem}.db.gz", "rows": len(rows)}
    src.close()
    (out_dir / "index.json").write_text(json.dumps(
        dict(sorted(index.items())), ensure_ascii=False, separators=(",", ":")))
    return index


if __name__ == "__main__":
    prov_path = Path(sys.argv[1] if len(sys.argv) > 1 else "web/provenance.db")
    out_dir = Path(sys.argv[2] if len(sys.argv) > 2 else "web/provenance")
    if not prov_path.exists():
        print(f"Error: {prov_path} not found", file=sys.stderr)
        sys.exit(1)
    index = split_provenance(prov_path, out_dir)
    print(f"Wrote {len(index)} per-resource provenance databases to {out_dir}")
