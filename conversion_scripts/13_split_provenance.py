#!/usr/bin/env python3
"""Split provenance.db into one small database per resource.

The full provenance database is large (most of it Princeton and OEWN), but a
link by original id (``#/search?id=omw-kin-Fa-Pr``) needs only the rows of one
resource.  This writes ``web/provenance/<resource>.db.gz``, each with the same
schema as provenance.db, and ``web/provenance/index.json`` mapping each
resource code to its file and row count, so the web UI can fetch just one.

Provenance rows point into cygnet.db by rowid, which changes with every build,
so a split file must only be used with the cygnet.db it was made from.  The
build id (a checksum of cygnet.db) is stamped into the ``user_version`` of
cygnet.db and of every split file, and recorded in index.json; the web UI
ignores split files whose build id differs from the main DB's.  Run this
before cygnet.db is compressed.
"""

import gzip
import itertools
import json
import re
import shutil
import sqlite3
import sys
import zlib
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


def build_id(db_path: Path) -> int:
    """A positive 31-bit checksum of a database file, to identify its build.

    Args:
        db_path: The database, e.g. cygnet.db

    Returns:
        The CRC-32 of the file, masked to 31 bits (SQLite's user_version is
        signed), and never 0, which is the user_version of an unstamped DB
    """
    crc = 0
    with open(db_path, "rb") as f:
        while chunk := f.read(1 << 20):
            crc = zlib.crc32(chunk, crc)
    return (crc & 0x7FFFFFFF) or 1


def stamp(db_path: Path, build: int) -> None:
    """Set a database's user_version to the build id."""
    conn = sqlite3.connect(db_path)
    conn.execute(f"PRAGMA user_version = {int(build)}")
    conn.close()


def split_provenance(main_path: Path, prov_path: Path, out_dir: Path) -> dict:
    """Write one gzipped provenance database per resource, and an index.

    Args:
        main_path: The main cygnet.db; stamped with the build id
        prov_path: The full provenance.db
        out_dir: Directory to write into; replaced if it exists

    Returns:
        The index: {"build": build id, "resources": {resource code ->
        {"file": file name, "rows": row count}}}
    """
    build = build_id(main_path)
    stamp(main_path, build)
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
        dst.execute(f"PRAGMA user_version = {build}")
        dst.commit()
        dst.execute("VACUUM")
        dst.close()
        gz_path = f"{db_path}.gz"
        with open(db_path, "rb") as fin, gzip.open(gz_path, "wb", 9) as fout:
            shutil.copyfileobj(fin, fout)
        db_path.unlink()
        index[code] = {"file": f"{stem}.db.gz", "rows": len(rows)}
    src.close()
    result = {"build": build, "resources": dict(sorted(index.items()))}
    (out_dir / "index.json").write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return result


if __name__ == "__main__":
    main_path = Path(sys.argv[1] if len(sys.argv) > 1 else "web/cygnet.db")
    prov_path = Path(sys.argv[2] if len(sys.argv) > 2 else "web/provenance.db")
    out_dir = Path(sys.argv[3] if len(sys.argv) > 3 else "web/provenance")
    for path in (main_path, prov_path):
        if not path.exists():
            print(f"Error: {path} not found", file=sys.stderr)
            sys.exit(1)
    index = split_provenance(main_path, prov_path, out_dir)
    print(f"Wrote {len(index['resources'])} per-resource provenance databases "
          f"to {out_dir} (build {index['build']})")
