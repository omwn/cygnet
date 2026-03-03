"""Tests for the resources table in the Cygnet SQLite database."""

import json
import sqlite3

import pytest


RESOURCES_DDL = """
CREATE TABLE languages (
    rowid INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE
);

CREATE TABLE resources (
    rowid INTEGER PRIMARY KEY,
    code TEXT NOT NULL,
    version TEXT,
    label TEXT,
    language_rowid INTEGER REFERENCES languages(rowid),
    url TEXT,
    citation TEXT,
    licence TEXT,
    email TEXT,
    status TEXT,
    confidence_score REAL,
    extra TEXT
);
"""

SAMPLE_DATA = [
    # (lang_code, code, version, label, url, citation, licence, extra)
    ('en', 'ewn', '2.0', 'English WordNet', 'https://ewn.example.org', 'Vossen 2022', 'CC BY 4.0', None),
    ('ja', 'jawn', '1.3', 'Japanese WordNet', 'https://jawn.example.org', 'Bond 2020', 'CC BY 3.0', '{"dc:publisher": "NTU"}'),
    ('fr', 'wolf', None, 'WOLF', None, None, None, None),
]


@pytest.fixture()
def db():
    """In-memory SQLite DB with resources schema and sample rows."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript(RESOURCES_DDL)

    lang_rowids = {}
    for row in SAMPLE_DATA:
        lang_code = row[0]
        if lang_code not in lang_rowids:
            cur = conn.execute('INSERT INTO languages (code) VALUES (?)', (lang_code,))
            lang_rowids[lang_code] = cur.lastrowid

    for lang_code, code, version, label, url, citation, licence, extra in SAMPLE_DATA:
        conn.execute(
            'INSERT INTO resources '
            '(code, version, label, language_rowid, url, citation, licence, extra) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (code, version, label, lang_rowids[lang_code], url, citation, licence, extra),
        )

    conn.commit()
    yield conn
    conn.close()


def test_resources_table_exists(db):
    """resources table exists with the expected columns."""
    cur = db.execute("PRAGMA table_info(resources)")
    columns = {row['name'] for row in cur.fetchall()}
    expected = {
        'rowid', 'code', 'version', 'label', 'language_rowid',
        'url', 'citation', 'licence', 'email', 'status',
        'confidence_score', 'extra',
    }
    assert expected.issubset(columns)


def test_no_null_code_or_label(db):
    """No resource row has a NULL code or label."""
    rows = db.execute('SELECT code, label FROM resources').fetchall()
    assert len(rows) > 0
    for row in rows:
        assert row['code'] is not None, "NULL code found"
        assert row['label'] is not None, "NULL label found"


def test_at_least_one_row_with_url_and_licence(db):
    """At least one resource row has a non-NULL url and licence."""
    cur = db.execute('SELECT COUNT(*) FROM resources WHERE url IS NOT NULL AND licence IS NOT NULL')
    count = cur.fetchone()[0]
    assert count >= 1


def test_language_rowid_fk_resolves(db):
    """Every non-NULL language_rowid in resources resolves to a languages row."""
    cur = db.execute(
        'SELECT r.rowid FROM resources r '
        'LEFT JOIN languages l ON r.language_rowid = l.rowid '
        'WHERE r.language_rowid IS NOT NULL AND l.rowid IS NULL'
    )
    unresolved = cur.fetchall()
    assert unresolved == [], f"Unresolved language_rowid FKs: {unresolved}"


def test_extra_is_null_or_valid_json(db):
    """The extra column is either NULL or valid JSON."""
    cur = db.execute('SELECT extra FROM resources WHERE extra IS NOT NULL')
    for row in cur.fetchall():
        try:
            json.loads(row['extra'])
        except json.JSONDecodeError:
            pytest.fail(f"Invalid JSON in extra column: {row['extra']!r}")
