"""Tests for the provenance database schema and query correctness."""

import sqlite3

import pytest

PROV_SCHEMA = """
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
    original_id    TEXT NOT NULL
);
CREATE INDEX idx_provenance_lookup ON provenance(table_rowid, item_rowid);
"""


@pytest.fixture()
def db():
    """In-memory SQLite database with the provenance schema applied."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript(PROV_SCHEMA)

    # Seed reference data
    conn.execute("INSERT INTO prov_resources (code) VALUES ('oewn')")
    conn.execute("INSERT INTO prov_resources (code) VALUES ('pwn')")
    conn.execute("INSERT INTO prov_tables (name) VALUES ('synsets')")
    conn.execute("INSERT INTO prov_tables (name) VALUES ('senses')")

    # Single provenance row
    conn.execute(
        "INSERT INTO provenance (table_rowid, item_rowid, resource_rowid, version, original_id) "
        "VALUES (1, 42, 1, '2025', 'oewn-00001234-n')"
    )
    # Second row same item – different resource (many-to-one merge case)
    conn.execute(
        "INSERT INTO provenance (table_rowid, item_rowid, resource_rowid, version, original_id) "
        "VALUES (1, 42, 2, '3.1', 'pwn-00001234-n')"
    )
    # Row for a different table/item
    conn.execute(
        "INSERT INTO provenance (table_rowid, item_rowid, resource_rowid, version, original_id) "
        "VALUES (2, 7, 1, '2025', 'oewn-sense-xyz')"
    )
    conn.commit()
    yield conn
    conn.close()


def test_all_tables_exist(db):
    """All three schema tables must be present."""
    tables = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {'prov_resources', 'prov_tables', 'provenance'}.issubset(tables)


def test_prov_resources_unique_constraint(db):
    """Inserting a duplicate resource code must raise an IntegrityError."""
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO prov_resources (code) VALUES ('oewn')")


def test_query_by_table_and_item(db):
    """Querying by (table_name, item_rowid) returns the expected rows."""
    rows = db.execute(
        """SELECT pr.code, p.version, p.original_id
           FROM provenance p
           JOIN prov_tables pt ON p.table_rowid = pt.rowid
           JOIN prov_resources pr ON p.resource_rowid = pr.rowid
           WHERE pt.name = 'synsets' AND p.item_rowid = 42
           ORDER BY pr.code"""
    ).fetchall()
    assert len(rows) == 2
    codes = [r['code'] for r in rows]
    assert 'oewn' in codes
    assert 'pwn' in codes


def test_no_null_original_id(db):
    """No provenance row may have a NULL original_id."""
    count = db.execute(
        "SELECT COUNT(*) FROM provenance WHERE original_id IS NULL"
    ).fetchone()[0]
    assert count == 0


def test_table_rowid_fk_resolves(db):
    """Every table_rowid in provenance must exist in prov_tables."""
    orphans = db.execute(
        """SELECT COUNT(*) FROM provenance p
           WHERE NOT EXISTS (
               SELECT 1 FROM prov_tables pt WHERE pt.rowid = p.table_rowid
           )"""
    ).fetchone()[0]
    assert orphans == 0


def test_resource_rowid_fk_resolves(db):
    """Every resource_rowid in provenance must exist in prov_resources."""
    orphans = db.execute(
        """SELECT COUNT(*) FROM provenance p
           WHERE NOT EXISTS (
               SELECT 1 FROM prov_resources pr WHERE pr.rowid = p.resource_rowid
           )"""
    ).fetchone()[0]
    assert orphans == 0


def test_multiple_provenance_rows_per_item(db):
    """One item can have multiple provenance rows (many-to-one merge)."""
    count = db.execute(
        """SELECT COUNT(*) FROM provenance p
           JOIN prov_tables pt ON p.table_rowid = pt.rowid
           WHERE pt.name = 'synsets' AND p.item_rowid = 42"""
    ).fetchone()[0]
    assert count == 2
