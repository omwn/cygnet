"""Tests for scripts/statistics.py — database size/coverage statistics."""

import importlib.util
import sqlite3
import sys
from pathlib import Path

from cyg.merge import SCHEMA

_STATISTICS_PATH = Path(__file__).parent.parent / "scripts" / "statistics.py"
_spec = importlib.util.spec_from_file_location("statistics", _STATISTICS_PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["statistics_report"] = _mod
_spec.loader.exec_module(_mod)

fetch_overview = _mod.fetch_overview
fetch_column_breakdown = _mod.fetch_column_breakdown
fetch_senses_by_pos = _mod.fetch_senses_by_pos
fetch_relation_breakdown = _mod.fetch_relation_breakdown
fetch_senses_by_language = _mod.fetch_senses_by_language
format_statistics = _mod.format_statistics


def _build_db(tmp_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(tmp_path / "cygnet.db")
    con.executescript(SCHEMA)
    return con


def _seed_minimal(con: sqlite3.Connection) -> None:
    """One English noun concept with a definition, example, and self-similar relation."""
    con.execute("INSERT INTO languages (rowid, code, name) VALUES (1, 'en', 'English')")
    con.execute("INSERT INTO resources (rowid, code) VALUES (1, 'wn-test')")
    con.execute("INSERT INTO synsets (rowid, ili, pos) VALUES (1, 'i1', 'NOUN')")
    con.execute("INSERT INTO synsets (rowid, ili, pos) VALUES (2, 'i2', 'NOUN')")
    con.execute("INSERT INTO entries (rowid, language_rowid, pos) VALUES (1, 1, 'NOUN')")
    con.execute("INSERT INTO forms (rowid, entry_rowid, form) VALUES (1, 1, 'dog')")
    con.execute(
        "INSERT INTO senses (rowid, entry_rowid, synset_rowid) VALUES (1, 1, 1)"
    )
    con.execute("INSERT INTO definitions (rowid, synset_rowid, definition) VALUES (1, 1, 'a dog')")
    con.execute("INSERT INTO examples (rowid, example) VALUES (1, 'the dog barked')")
    con.execute("INSERT INTO sense_examples (rowid, sense_rowid, example_rowid) VALUES (1, 1, 1)")
    con.execute("INSERT INTO relation_types (rowid, type) VALUES (1, 'hypernym')")
    con.execute("INSERT INTO relation_types (rowid, type) VALUES (2, 'similar')")
    con.execute(
        "INSERT INTO synset_relations (rowid, source_rowid, target_rowid, type_rowid) "
        "VALUES (1, 1, 2, 1)"
    )
    con.execute(
        "INSERT INTO sense_relations (rowid, source_rowid, target_rowid, type_rowid) "
        "VALUES (1, 1, 1, 2)"
    )
    con.commit()


# ---------------------------------------------------------------------------
# fetch_overview
# ---------------------------------------------------------------------------

class TestFetchOverview:
    def test_counts_match_seeded_rows(self, tmp_path):
        con = _build_db(tmp_path)
        _seed_minimal(con)
        overview = dict(fetch_overview(con.cursor()))
        assert overview["Languages"] == 1
        assert overview["Source resources"] == 1
        assert overview["Concepts (synsets)"] == 2
        assert overview["Entries (lexemes)"] == 1
        assert overview["Wordforms"] == 1
        assert overview["Senses"] == 1
        assert overview["Definitions"] == 1
        assert overview["Examples"] == 1
        assert overview["Concept relations"] == 1
        assert overview["Sense relations"] == 1

    def test_empty_db_reports_zeros(self, tmp_path):
        con = _build_db(tmp_path)
        overview = dict(fetch_overview(con.cursor()))
        assert all(n == 0 for n in overview.values())


# ---------------------------------------------------------------------------
# fetch_column_breakdown / fetch_senses_by_pos
# ---------------------------------------------------------------------------

class TestBreakdowns:
    def test_concepts_by_pos(self, tmp_path):
        con = _build_db(tmp_path)
        _seed_minimal(con)
        rows = fetch_column_breakdown(con.cursor(), "synsets", "pos")
        assert rows == [("NOUN", 2)]

    def test_ranked_most_common_first(self, tmp_path):
        con = _build_db(tmp_path)
        con.execute("INSERT INTO synsets (rowid, ili, pos) VALUES (1, 'i1', 'VERB')")
        con.execute("INSERT INTO synsets (rowid, ili, pos) VALUES (2, 'i2', 'NOUN')")
        con.execute("INSERT INTO synsets (rowid, ili, pos) VALUES (3, 'i3', 'NOUN')")
        con.commit()
        rows = fetch_column_breakdown(con.cursor(), "synsets", "pos")
        assert rows[0] == ("NOUN", 2)
        assert rows[1] == ("VERB", 1)

    def test_senses_by_pos_uses_concepts_pos(self, tmp_path):
        con = _build_db(tmp_path)
        _seed_minimal(con)
        rows = fetch_senses_by_pos(con.cursor())
        assert rows == [("NOUN", 1)]

    def test_relation_breakdown_joins_type_name(self, tmp_path):
        con = _build_db(tmp_path)
        _seed_minimal(con)
        rows = fetch_relation_breakdown(con.cursor(), "synset_relations")
        assert rows == [("hypernym", 1)]

    def test_sense_relation_breakdown(self, tmp_path):
        con = _build_db(tmp_path)
        _seed_minimal(con)
        rows = fetch_relation_breakdown(con.cursor(), "sense_relations")
        assert rows == [("similar", 1)]


# ---------------------------------------------------------------------------
# fetch_senses_by_language
# ---------------------------------------------------------------------------

class TestFetchSensesByLanguage:
    def test_counts_per_language(self, tmp_path):
        con = _build_db(tmp_path)
        _seed_minimal(con)
        rows = fetch_senses_by_language(con.cursor())
        assert rows == [("en", "English", 1)]

    def test_ranked_by_sense_count(self, tmp_path):
        con = _build_db(tmp_path)
        con.execute("INSERT INTO languages (rowid, code, name) VALUES (1, 'en', 'English')")
        con.execute("INSERT INTO languages (rowid, code, name) VALUES (2, 'fr', 'French')")
        con.execute("INSERT INTO entries (rowid, language_rowid, pos) VALUES (1, 1, 'NOUN')")
        con.execute("INSERT INTO entries (rowid, language_rowid, pos) VALUES (2, 2, 'NOUN')")
        con.execute("INSERT INTO synsets (rowid, ili, pos) VALUES (1, 'i1', 'NOUN')")
        con.execute("INSERT INTO senses (rowid, entry_rowid, synset_rowid) VALUES (1, 1, 1)")
        con.execute("INSERT INTO senses (rowid, entry_rowid, synset_rowid) VALUES (2, 2, 1)")
        con.execute("INSERT INTO senses (rowid, entry_rowid, synset_rowid) VALUES (3, 2, 1)")
        con.commit()
        rows = fetch_senses_by_language(con.cursor())
        assert rows[0] == ("fr", "French", 2)
        assert rows[1] == ("en", "English", 1)


# ---------------------------------------------------------------------------
# format_statistics
# ---------------------------------------------------------------------------

class TestFormatStatistics:
    def test_plain_text_has_headline_sections(self, tmp_path, monkeypatch):
        con = _build_db(tmp_path)
        _seed_minimal(con)
        con.close()
        monkeypatch.setattr(_mod, "DB_PATH", tmp_path / "cygnet.db")
        output = format_statistics(markdown=False)
        assert "Overview" in output
        assert "Senses by language" in output
        assert "Concepts by part of speech" in output
        assert "Concept relations by type" in output
        assert "Sense relations by type" in output

    def test_markdown_has_tables(self, tmp_path, monkeypatch):
        con = _build_db(tmp_path)
        _seed_minimal(con)
        con.close()
        monkeypatch.setattr(_mod, "DB_PATH", tmp_path / "cygnet.db")
        output = format_statistics(markdown=True)
        assert "# Cygnet Database Statistics" in output
        assert "| Metric | Count |" in output
        assert "| Concepts (synsets) | 2 |" in output

    def test_zero_rows_produce_no_crash(self, tmp_path, monkeypatch):
        con = _build_db(tmp_path)
        con.close()
        monkeypatch.setattr(_mod, "DB_PATH", tmp_path / "cygnet.db")
        output = format_statistics(markdown=False)
        assert "Languages" in output
