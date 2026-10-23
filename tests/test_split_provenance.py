"""Tests for conversion_scripts/13_split_provenance.py."""

import gzip
import json
import sqlite3
from pathlib import Path

from conftest import load_split_provenance

split_provenance = load_split_provenance()
build_id = split_provenance.__globals__["build_id"]

PROV_SCHEMA = """
CREATE TABLE prov_resources (rowid INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE);
CREATE TABLE prov_tables (rowid INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE provenance (
    rowid          INTEGER PRIMARY KEY,
    table_rowid    INTEGER NOT NULL,
    item_rowid     INTEGER NOT NULL,
    resource_rowid INTEGER NOT NULL,
    version        TEXT,
    original_id    TEXT
);
"""


def _make_provenance(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(PROV_SCHEMA)
    conn.executemany(
        "INSERT INTO prov_resources (rowid, code) VALUES (?, ?)",
        [
            (1, "oewn"), (2, "omw-kin"), (3, "omw-kin-cmn-Hant"), (4, "a/b"),
            (5, "unused"),
        ],
    )
    conn.executemany(
        "INSERT INTO prov_tables (rowid, name) VALUES (?, ?)",
        [(1, "synsets"), (2, "senses")],
    )
    conn.executemany(
        "INSERT INTO provenance VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, 1, 10, 1, "2025", "oewn-00001740-n"),
            (2, 2, 11, 1, "2025", "oewn-entity-n-00001740-01"),
            (3, 1, 20, 2, "0.1", "omw-kin-Fa-Pr"),
            (4, 1, 21, 3, "0.1", "omw-kin-cmn-Hant-Fa-Pr"),
            (5, 1, 22, 4, "1", "ab-1"),
        ],
    )
    conn.commit()
    conn.close()


def _make_main(path: Path, synsets: int = 3) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE synsets (rowid INTEGER PRIMARY KEY)")
    conn.executemany("INSERT INTO synsets VALUES (?)", [(i,) for i in range(synsets)])
    conn.commit()
    conn.close()


def _split(tmp_path: Path) -> dict:
    _make_main(tmp_path / "cygnet.db")
    _make_provenance(tmp_path / "provenance.db")
    return split_provenance(
        tmp_path / "cygnet.db", tmp_path / "provenance.db", tmp_path / "out")


def _user_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _open(path: Path) -> sqlite3.Connection:
    raw = path.with_suffix("")
    raw.write_bytes(gzip.decompress(path.read_bytes()))
    return sqlite3.connect(raw)


def test_one_file_per_resource_with_rows(tmp_path):
    index = _split(tmp_path)
    assert set(index["resources"]) == {"oewn", "omw-kin", "omw-kin-cmn-Hant", "a/b"}
    assert index["resources"]["oewn"]["rows"] == 2
    on_disk = json.loads((tmp_path / "out" / "index.json").read_text())
    assert on_disk == index


def test_resource_db_keeps_rowids_and_original_ids(tmp_path):
    index = _split(tmp_path)
    conn = _open(tmp_path / "out" / index["resources"]["omw-kin"]["file"])
    row = conn.execute(
        "SELECT p.item_rowid FROM provenance p "
        "JOIN prov_tables pt ON p.table_rowid = pt.rowid "
        "WHERE pt.name = 'synsets' AND p.original_id = ?",
        ("omw-kin-Fa-Pr",),
    ).fetchone()
    assert row == (20,)
    assert conn.execute("SELECT rowid, code FROM prov_resources").fetchall() == [
        (2, "omw-kin")
    ]


def test_unsafe_codes_get_safe_file_names(tmp_path):
    index = _split(tmp_path)
    assert index["resources"]["a/b"]["file"] == "a_b.db.gz"
    assert (tmp_path / "out" / "a_b.db.gz").exists()


def test_rerun_replaces_old_files(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "stale.db.gz").write_bytes(b"")
    _split(tmp_path)
    assert not (tmp_path / "out" / "stale.db.gz").exists()


def test_build_id_stamped_in_main_and_split_dbs(tmp_path):
    index = _split(tmp_path)
    build = index["build"]
    assert build > 0
    assert _user_version(sqlite3.connect(tmp_path / "cygnet.db")) == build
    for entry in index["resources"].values():
        assert _user_version(_open(tmp_path / "out" / entry["file"])) == build


def test_build_id_differs_between_builds(tmp_path):
    _make_main(tmp_path / "a.db", synsets=3)
    _make_main(tmp_path / "b.db", synsets=4)
    assert build_id(tmp_path / "a.db") != build_id(tmp_path / "b.db")
