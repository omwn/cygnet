"""Tests for the ARASAAC pictogram integration."""

import importlib.util
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# Load 11_add_arasaac module (numerically prefixed filename)
_script = Path(__file__).parent.parent / 'conversion_scripts' / '11_add_arasaac.py'
_spec = importlib.util.spec_from_file_location('add_arasaac', _script)
_mod = importlib.util.module_from_spec(_spec)
sys.modules['add_arasaac'] = _mod
_spec.loader.exec_module(_mod)

load_mapping = _mod.load_mapping
populate = _mod.populate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_MAPPING_DATA = {
    'i100': {'dog': [101, 102], 'hound': [103]},
    'i200': {'cat': [201]},
    'i999': {'nonexistent': [999]},  # no matching synset
}


@pytest.fixture()
def mapping_file(tmp_path):
    """Write sample araasac-ili.json to a temp file."""
    p = tmp_path / 'araasac-ili.json'
    p.write_text(json.dumps(SAMPLE_MAPPING_DATA))
    return p


@pytest.fixture()
def sample_db(tmp_path):
    """SQLite DB with synsets and arasaac tables matching the sample mapping.

    ILIs are stored without the 'cili.' prefix (e.g. 'i100', not 'cili.i100').
    """
    db = tmp_path / 'cygnet.db'
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE synsets (
            rowid INTEGER PRIMARY KEY,
            ili   TEXT,
            pos   TEXT NOT NULL
        );
        CREATE TABLE arasaac (
            synset_rowid INTEGER NOT NULL REFERENCES synsets(rowid),
            arasaac_id   INTEGER NOT NULL
        );
        CREATE INDEX idx_arasaac_synset ON arasaac(synset_rowid);
        INSERT INTO synsets (rowid, ili, pos) VALUES (1, 'i100', 'NOUN');
        INSERT INTO synsets (rowid, ili, pos) VALUES (2, 'i200', 'NOUN');
        INSERT INTO synsets (rowid, ili, pos) VALUES (3, NULL,   'VERB');
    """)
    conn.commit()
    conn.close()
    return db


# ---------------------------------------------------------------------------
# load_mapping tests
# ---------------------------------------------------------------------------

class TestLoadMapping:
    def test_returns_one_id_per_ili(self, mapping_file):
        m = load_mapping(mapping_file)
        assert set(m.keys()) == {'i100', 'i200', 'i999'}

    def test_picks_first_id_of_first_lemma(self, mapping_file):
        m = load_mapping(mapping_file)
        # i100 first lemma 'dog' has ids [101, 102]; first is 101
        assert m['i100'] == 101

    def test_single_id(self, mapping_file):
        m = load_mapping(mapping_file)
        assert m['i200'] == 201

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_mapping(tmp_path / 'nonexistent.json')


# ---------------------------------------------------------------------------
# populate tests
# ---------------------------------------------------------------------------

class TestPopulate:
    def test_inserts_matched_rows(self, sample_db):
        mapping = {'i100': 101, 'i200': 201}
        n = populate(sample_db, mapping)
        assert n == 2

    def test_correct_ids_stored(self, sample_db):
        mapping = {'i100': 101, 'i200': 201}
        populate(sample_db, mapping)
        conn = sqlite3.connect(str(sample_db))
        rows = conn.execute('SELECT synset_rowid, arasaac_id FROM arasaac ORDER BY synset_rowid').fetchall()
        conn.close()
        assert rows == [(1, 101), (2, 201)]

    def test_synset_with_null_ili_skipped(self, sample_db):
        mapping = {'i100': 101, 'i200': 201}
        populate(sample_db, mapping)
        conn = sqlite3.connect(str(sample_db))
        count = conn.execute('SELECT COUNT(*) FROM arasaac').fetchone()[0]
        conn.close()
        assert count == 2  # synset 3 (NULL ili) not inserted

    def test_unmatched_ili_skipped(self, sample_db):
        mapping = {'i999': 999}  # i999 not in synsets
        n = populate(sample_db, mapping)
        assert n == 0

    def test_idempotent(self, sample_db):
        mapping = {'i100': 101, 'i200': 201}
        populate(sample_db, mapping)
        n2 = populate(sample_db, mapping)
        conn = sqlite3.connect(str(sample_db))
        count = conn.execute('SELECT COUNT(*) FROM arasaac').fetchone()[0]
        conn.close()
        assert n2 == 2
        assert count == 2  # no duplicates from second run

    def test_creates_table_if_missing(self, tmp_path):
        """populate() creates arasaac table if the DB doesn't have it yet."""
        db = tmp_path / 'bare.db'
        conn = sqlite3.connect(str(db))
        conn.executescript("""
            CREATE TABLE synsets (rowid INTEGER PRIMARY KEY, ili TEXT, pos TEXT NOT NULL);
            INSERT INTO synsets VALUES (1, 'i100', 'NOUN');
        """)
        conn.commit()
        conn.close()
        n = populate(db, {'i100': 42})
        assert n == 1
