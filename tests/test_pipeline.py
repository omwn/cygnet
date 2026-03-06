"""Tests for MergeBuilder post-processing pipeline phases.

Covers: cascade_delete, merge_case_variants, remove_orphans,
        compute_sense_indices, and insert_resources.
"""

import sqlite3
from pathlib import Path

import pytest

from conftest import MergeBuilder, build_test_db, wn_xml

_WORDNETS_DIR = Path(__file__).parent / 'wordnets'


# ---------------------------------------------------------------------------
# Shared XML bodies
# ---------------------------------------------------------------------------

# Two concepts: c1 has a definition, c2 does not.
# Senses: foo→c1 (survives), bar→c2 (cascade-deleted).
_CASCADE_BODY = """\
<Concept id="cili.c1" ontological_category="NOUN" status="1">
  <Provenance resource="wn-a" version="1.0"/>
</Concept>
<Concept id="cili.c2" ontological_category="NOUN" status="1">
  <Provenance resource="wn-a" version="1.0"/>
</Concept>
<Gloss definiendum="cili.c1" language="en">
  <AnnotatedSentence>a defined concept</AnnotatedSentence>
  <Provenance resource="wn-a" version="1.0"/>
</Gloss>
<Lexeme id="lex.en.foo" language="en" grammatical_category="NOUN">
  <Wordform form="foo"/>
  <Provenance resource="wn-a" version="1.0"/>
</Lexeme>
<Sense id="sense.foo" signifier="lex.en.foo" signified="cili.c1">
  <Provenance resource="wn-a" version="1.0"/>
</Sense>
<Lexeme id="lex.en.bar" language="en" grammatical_category="NOUN">
  <Wordform form="bar"/>
  <Provenance resource="wn-a" version="1.0"/>
</Lexeme>
<Sense id="sense.bar" signifier="lex.en.bar" signified="cili.c2">
  <Provenance resource="wn-a" version="1.0"/>
</Sense>
"""

# One concept: entries "dog" and "Dog" both sense the same concept — case variants.
_CASE_VARIANT_BODY = """\
<Concept id="cili.v1" ontological_category="NOUN" status="1">
  <Provenance resource="wn-a" version="1.0"/>
</Concept>
<Gloss definiendum="cili.v1" language="en">
  <AnnotatedSentence>a dog</AnnotatedSentence>
  <Provenance resource="wn-a" version="1.0"/>
</Gloss>
<Lexeme id="lex.en.dog.lower" language="en" grammatical_category="NOUN">
  <Wordform form="dog"/>
  <Provenance resource="wn-a" version="1.0"/>
</Lexeme>
<Lexeme id="lex.en.Dog.upper" language="en" grammatical_category="NOUN">
  <Wordform form="Dog"/>
  <Provenance resource="wn-a" version="1.0"/>
</Lexeme>
<Sense id="sense.dog.lower" signifier="lex.en.dog.lower" signified="cili.v1">
  <Provenance resource="wn-a" version="1.0"/>
</Sense>
<Sense id="sense.dog.upper" signifier="lex.en.Dog.upper" signified="cili.v1">
  <Provenance resource="wn-a" version="1.0"/>
</Sense>
"""

# Two concepts: "bank" (institution) and "bank" (geography) — same lemma, different synsets.
_POLYSEMY_BODY = """\
<Concept id="cili.p1" ontological_category="NOUN" status="1">
  <Provenance resource="wn-a" version="1.0"/>
</Concept>
<Concept id="cili.p2" ontological_category="NOUN" status="1">
  <Provenance resource="wn-a" version="1.0"/>
</Concept>
<Gloss definiendum="cili.p1" language="en">
  <AnnotatedSentence>a financial institution</AnnotatedSentence>
  <Provenance resource="wn-a" version="1.0"/>
</Gloss>
<Gloss definiendum="cili.p2" language="en">
  <AnnotatedSentence>a sloping land beside a body of water</AnnotatedSentence>
  <Provenance resource="wn-a" version="1.0"/>
</Gloss>
<Lexeme id="lex.en.bank1" language="en" grammatical_category="NOUN">
  <Wordform form="bank"/>
  <Provenance resource="wn-a" version="1.0"/>
</Lexeme>
<Lexeme id="lex.en.bank2" language="en" grammatical_category="NOUN">
  <Wordform form="bank"/>
  <Provenance resource="wn-a" version="1.0"/>
</Lexeme>
<Sense id="sense.bank1" signifier="lex.en.bank1" signified="cili.p1">
  <Provenance resource="wn-a" version="1.0"/>
</Sense>
<Sense id="sense.bank2" signifier="lex.en.bank2" signified="cili.p2">
  <Provenance resource="wn-a" version="1.0"/>
</Sense>
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def builder_cascade(builder, tmp_path):
    (tmp_path / 'wn.xml').write_text(wn_xml('wn-a', _CASCADE_BODY))
    builder.process_file(tmp_path / 'wn.xml')
    return builder


@pytest.fixture()
def builder_case_variants(builder, tmp_path):
    (tmp_path / 'wn.xml').write_text(wn_xml('wn-a', _CASE_VARIANT_BODY))
    builder.process_file(tmp_path / 'wn.xml')
    builder.create_indexes()
    return builder


@pytest.fixture()
def builder_polysemy(builder, tmp_path):
    (tmp_path / 'wn.xml').write_text(wn_xml('wn-a', _POLYSEMY_BODY))
    builder.process_file(tmp_path / 'wn.xml')
    builder.create_indexes()
    return builder


# ---------------------------------------------------------------------------
# cascade_delete
# ---------------------------------------------------------------------------

class TestCascadeDelete:
    """cascade_delete() removes synsets with no definitions and cascades."""

    def test_synset_without_definition_removed(self, builder_cascade):
        counts = builder_cascade.cascade_delete()
        assert counts['synsets'] == 1
        ilis = {r[0] for r in builder_cascade.cur.execute(
            'SELECT ili FROM synsets'
        ).fetchall()}
        assert ilis == {'c1'}

    def test_sense_of_deleted_synset_removed(self, builder_cascade):
        builder_cascade.cascade_delete()
        n = builder_cascade.cur.execute(
            'SELECT COUNT(*) FROM senses'
        ).fetchone()[0]
        assert n == 1

    def test_defined_synset_sense_survives(self, builder_cascade):
        builder_cascade.cascade_delete()
        forms = {r[0] for r in builder_cascade.cur.execute(
            'SELECT f.form FROM forms f '
            'JOIN entries e ON f.entry_rowid = e.rowid '
            'JOIN senses s ON s.entry_rowid = e.rowid'
        ).fetchall()}
        assert 'foo' in forms
        assert 'bar' not in forms

    def test_returns_counts_dict(self, builder_cascade):
        counts = builder_cascade.cascade_delete()
        assert {'synsets', 'senses', 'synset_relations'} <= counts.keys()


# ---------------------------------------------------------------------------
# merge_case_variants
# ---------------------------------------------------------------------------

class TestMergeCaseVariants:
    """merge_case_variants() collapses same-concept case-variant entries."""

    def test_two_senses_collapse_to_one(self, builder_case_variants):
        removed = builder_case_variants.merge_case_variants()
        assert removed == 1
        n = builder_case_variants.cur.execute(
            'SELECT COUNT(*) FROM senses'
        ).fetchone()[0]
        assert n == 1

    def test_both_wordforms_kept_in_merged_entry(self, builder_case_variants):
        builder_case_variants.merge_case_variants()
        forms = {r[0] for r in builder_case_variants.cur.execute(
            'SELECT form FROM forms'
        ).fetchall()}
        assert {'dog', 'Dog'} <= forms

    def test_polysemous_same_lemma_not_merged(self, builder_polysemy):
        """Same lemma, different synsets — must NOT be merged."""
        removed = builder_polysemy.merge_case_variants()
        assert removed == 0
        n = builder_polysemy.cur.execute(
            'SELECT COUNT(*) FROM senses'
        ).fetchone()[0]
        assert n == 2


# ---------------------------------------------------------------------------
# remove_orphans
# ---------------------------------------------------------------------------

class TestRemoveOrphans:
    """remove_orphans() deletes entries whose senses were all cascade-deleted."""

    def test_orphan_entry_removed(self, builder_cascade):
        builder_cascade.cascade_delete()
        removed = builder_cascade.remove_orphans()
        assert removed == 1
        entries = {r[0] for r in builder_cascade.cur.execute(
            'SELECT f.form FROM forms f '
            'JOIN entries e ON f.entry_rowid = e.rowid'
        ).fetchall()}
        assert 'bar' not in entries

    def test_surviving_entry_kept(self, builder_cascade):
        builder_cascade.cascade_delete()
        builder_cascade.remove_orphans()
        entries = {r[0] for r in builder_cascade.cur.execute(
            'SELECT f.form FROM forms f '
            'JOIN entries e ON f.entry_rowid = e.rowid'
        ).fetchall()}
        assert 'foo' in entries

    def test_forms_of_orphan_also_removed(self, builder_cascade):
        builder_cascade.cascade_delete()
        builder_cascade.remove_orphans()
        n_forms = builder_cascade.cur.execute(
            'SELECT COUNT(*) FROM forms'
        ).fetchone()[0]
        # Only "foo" entry survives, with its single form
        assert n_forms == 1


# ---------------------------------------------------------------------------
# compute_sense_indices
# ---------------------------------------------------------------------------

class TestComputeSenseIndices:
    """compute_sense_indices() assigns 1-based positions per (language, lemma, pos)."""

    def test_polysemous_lemma_gets_distinct_indices(self, builder_polysemy):
        builder_polysemy.merge_case_variants()
        builder_polysemy.cascade_delete()
        builder_polysemy.remove_orphans()
        builder_polysemy.compute_sense_indices()
        indices = {r[0] for r in builder_polysemy.cur.execute(
            'SELECT sense_index FROM senses'
        ).fetchall()}
        assert indices == {1, 2}

    def test_monosemous_lemma_gets_positive_index(self, builder_case_variants):
        builder_case_variants.merge_case_variants()
        builder_case_variants.cascade_delete()
        builder_case_variants.remove_orphans()
        builder_case_variants.compute_sense_indices()
        indices = [r[0] for r in builder_case_variants.cur.execute(
            'SELECT sense_index FROM senses'
        ).fetchall()]
        assert len(indices) == 1
        assert indices[0] >= 1

    def test_all_sense_indices_positive(self, builder_polysemy):
        builder_polysemy.merge_case_variants()
        builder_polysemy.cascade_delete()
        builder_polysemy.remove_orphans()
        builder_polysemy.compute_sense_indices()
        min_idx = builder_polysemy.cur.execute(
            'SELECT MIN(sense_index) FROM senses'
        ).fetchone()[0]
        assert min_idx >= 1


# ---------------------------------------------------------------------------
# insert_resources (integration)
# ---------------------------------------------------------------------------

class TestResources:
    """insert_resources() populates the resources table from XML metadata."""

    @pytest.fixture(scope='class')
    def built_db(self, tmp_path_factory):
        d = tmp_path_factory.mktemp('res_test')
        db = d / 'cygnet.db'
        prov = d / 'provenance.db'
        build_test_db(db, prov, [
            _WORDNETS_DIR / 'wn-en.xml',
            _WORDNETS_DIR / 'wn-fr.xml',
        ])
        return db

    def test_both_wordnet_codes_in_resources(self, built_db):
        with sqlite3.connect(built_db) as conn:
            codes = {r[0] for r in conn.execute('SELECT code FROM resources')}
        assert {'wn-en', 'wn-fr'} <= codes

    def test_resource_language_matches_xml(self, built_db):
        with sqlite3.connect(built_db) as conn:
            row = conn.execute(
                "SELECT l.code FROM resources r "
                "JOIN languages l ON r.language_rowid = l.rowid "
                "WHERE r.code = 'wn-en'"
            ).fetchone()
        assert row is not None
        assert row[0] == 'en'

    def test_fr_resource_linked_to_french(self, built_db):
        with sqlite3.connect(built_db) as conn:
            row = conn.execute(
                "SELECT l.code FROM resources r "
                "JOIN languages l ON r.language_rowid = l.rowid "
                "WHERE r.code = 'wn-fr'"
            ).fetchone()
        assert row is not None
        assert row[0] == 'fr'
