"""Tests for MergeBuilder post-processing pipeline phases.

Covers: cascade_delete, merge_case_variants, remove_orphans,
        compute_sense_indices, insert_resources, and detect_cycles.
"""

import sqlite3
from pathlib import Path

import pytest

from conftest import MergeBuilder, build_test_db, wn_xml
from cyg.merge import fold_lemma, reading_clusters

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
        assert removed == {'identical': 0, 'variant': 1}
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
        assert removed == {'identical': 0, 'variant': 0}
        n = builder_polysemy.cur.execute(
            'SELECT COUNT(*) FROM senses'
        ).fetchone()[0]
        assert n == 2


    def test_merged_entry_keeps_one_lemma(self, builder_case_variants):
        """The moved variant becomes a non-lemma form, not a second lemma."""
        builder_case_variants.merge_case_variants()
        lemmas = [r[0] for r in builder_case_variants.cur.execute(
            'SELECT f.form FROM forms f JOIN senses s USING (entry_rowid) '
            'WHERE f.rank = 0'
        ).fetchall()]
        assert lemmas == ['dog']


    def test_reading_lookup_uses_pronunciation_index(self, builder_case_variants):
        """Signatures look pronunciations up by form without a table scan."""
        plan = ' '.join(r[3] for r in builder_case_variants.cur.execute(
            'EXPLAIN QUERY PLAN SELECT p.pronunciation FROM forms f '
            'LEFT JOIN pronunciations p ON p.form_rowid = f.rowid '
            'WHERE f.entry_rowid = 1'
        ).fetchall())
        assert 'idx_pronunciations_form' in plan

def _variant_lexeme(lid: str, lemma: str, others: tuple[str, ...] = (),
                    lang: str = 'ja') -> str:
    """Return a Lexeme with a sense for cili.h1."""
    forms = ''.join(f'<Wordform form="{f}"/>' for f in (lemma, *others))
    return (
        f'<Lexeme id="{lid}" language="{lang}" grammatical_category="NOUN">'
        f'{forms}<Provenance resource="wn-a" version="1.0"/></Lexeme>'
        f'<Sense id="s.{lid}" signifier="{lid}" signified="cili.h1">'
        f'<Provenance resource="wn-a" version="1.0"/></Sense>'
    )


_H1 = """\
<Concept id="cili.h1" ontological_category="NOUN" status="1">
  <Provenance resource="wn-a" version="1.0"/>
</Concept>
<Gloss definiendum="cili.h1" language="en">
  <AnnotatedSentence>a thing</AnnotatedSentence>
  <Provenance resource="wn-a" version="1.0"/>
</Gloss>
"""


def _load_lexemes(builder, tmp_path, *lexemes: str):
    (tmp_path / 'wn.xml').write_text(wn_xml('wn-a', _H1 + ''.join(lexemes)))
    builder.process_file(tmp_path / 'wn.xml')
    builder.create_indexes()
    return builder


def _n_senses(builder) -> int:
    return builder.cur.execute('SELECT COUNT(*) FROM senses').fetchone()[0]


class TestMergeVariantsReadings:
    """Homographs with different readings stay apart (issue #22)."""

    def test_homographs_with_different_readings_kept(self, builder, tmp_path):
        b = _load_lexemes(builder, tmp_path,
                          _variant_lexeme('l1', '物', ('ブツ', 'ぶつ')),
                          _variant_lexeme('l2', '物', ('モノ', 'もの')))
        assert b.merge_case_variants() == {'identical': 0, 'variant': 0}
        assert _n_senses(b) == 2

    def test_same_readings_merged(self, builder, tmp_path):
        b = _load_lexemes(builder, tmp_path,
                          _variant_lexeme('l1', '物', ('もの',)),
                          _variant_lexeme('l2', '物', ('モノ', 'もの')))
        assert b.merge_case_variants() == {'identical': 1, 'variant': 0}
        assert _n_senses(b) == 1

    def test_bare_entry_merged_with_single_reading(self, builder, tmp_path):
        b = _load_lexemes(builder, tmp_path,
                          _variant_lexeme('l1', '物'),
                          _variant_lexeme('l2', '物', ('もの',)))
        assert b.merge_case_variants()['identical'] == 1
        assert _n_senses(b) == 1

    def test_ambiguous_bare_entry_kept(self, builder, tmp_path):
        """A bare 物 could go with either reading, so nothing merges."""
        b = _load_lexemes(builder, tmp_path,
                          _variant_lexeme('l1', '物'),
                          _variant_lexeme('l2', '物', ('ぶつ',)),
                          _variant_lexeme('l3', '物', ('もの',)))
        b.merge_case_variants()
        assert _n_senses(b) == 3

    def test_full_width_variant_merged(self, builder, tmp_path):
        b = _load_lexemes(builder, tmp_path,
                          _variant_lexeme('l1', 'VIP'),
                          _variant_lexeme('l2', 'ＶＩＰ'))
        assert b.merge_case_variants() == {'identical': 0, 'variant': 1}

    def test_non_ascii_case_variant_merged(self, builder, tmp_path):
        b = _load_lexemes(builder, tmp_path,
                          _variant_lexeme('l1', 'Москва', lang='ru'),
                          _variant_lexeme('l2', 'москва', lang='ru'))
        assert b.merge_case_variants() == {'identical': 0, 'variant': 1}

    def test_spelling_variants_not_merged(self, builder, tmp_path):
        b = _load_lexemes(builder, tmp_path,
                          _variant_lexeme('l1', 'hiruk pikuk', lang='id'),
                          _variant_lexeme('l2', 'hiruk-pikuk', lang='id'),
                          _variant_lexeme('l3', '嘆息'),
                          _variant_lexeme('l4', '歎息'))
        b.merge_case_variants()
        assert _n_senses(b) == 4


class TestFoldLemma:
    """fold_lemma() folds case and width only."""

    @pytest.mark.parametrize('form, folded', [
        ('Bank', 'bank'),
        ('ＶＩＰ', 'vip'),
        ('４月', '4月'),
        ('ｶﾞｽ', 'ガス'),
        ('UNIXシステム', 'unixシステム'),
        ('ΟΔΟΣ', 'οδος'),
    ])
    def test_folded(self, form, folded):
        assert fold_lemma(form) == folded

    @pytest.mark.parametrize('form', ['garcía', 'm²', 'ϱ', 'hiruk-pikuk', 'ジャム・セッション'])
    def test_unchanged(self, form):
        assert fold_lemma(form) == form


class TestReadingClusters:
    """reading_clusters() groups entries with compatible readings."""

    def test_subset_joins_superset(self):
        a, b = frozenset({'x'}), frozenset({'x', 'y'})
        assert reading_clusters({1: a, 2: b}) == [[1, 2]]

    def test_ambiguous_entries_group_by_signature(self):
        e, x, y = frozenset(), frozenset({'x'}), frozenset({'y'})
        assert reading_clusters({1: e, 2: x, 3: y, 4: e}) == [[1, 4], [2], [3]]


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

    def test_synset_count_populated(self, built_db):
        with sqlite3.connect(built_db) as conn:
            row = conn.execute(
                "SELECT synset_count FROM resources WHERE code = 'wn-en'"
            ).fetchone()
        assert row is not None
        assert row[0] > 0

    def test_sense_count_populated(self, built_db):
        with sqlite3.connect(built_db) as conn:
            row = conn.execute(
                "SELECT sense_count FROM resources WHERE code = 'wn-en'"
            ).fetchone()
        assert row is not None
        assert row[0] > 0

    def test_fr_sense_count_equals_one(self, built_db):
        """wn-fr contributes exactly 1 sense (fr:chien)."""
        with sqlite3.connect(built_db) as conn:
            row = conn.execute(
                "SELECT sense_count FROM resources WHERE code = 'wn-fr'"
            ).fetchone()
        assert row is not None
        assert row[0] == 1


# ---------------------------------------------------------------------------
# detect_cycles
# ---------------------------------------------------------------------------

# XML body: three concepts A→B→C (no cycle)
_CHAIN_BODY = """\
<Concept id="cili.a1" ontological_category="NOUN" status="1">
  <Provenance resource="wn-a" version="1.0"/>
</Concept>
<Concept id="cili.a2" ontological_category="NOUN" status="1">
  <Provenance resource="wn-a" version="1.0"/>
</Concept>
<Concept id="cili.a3" ontological_category="NOUN" status="1">
  <Provenance resource="wn-a" version="1.0"/>
</Concept>
<Gloss definiendum="cili.a1" language="en">
  <AnnotatedSentence>alpha</AnnotatedSentence>
  <Provenance resource="wn-a" version="1.0"/>
</Gloss>
<Gloss definiendum="cili.a2" language="en">
  <AnnotatedSentence>beta</AnnotatedSentence>
  <Provenance resource="wn-a" version="1.0"/>
</Gloss>
<Gloss definiendum="cili.a3" language="en">
  <AnnotatedSentence>gamma</AnnotatedSentence>
  <Provenance resource="wn-a" version="1.0"/>
</Gloss>
<ConceptRelation relation_type="hypernym" source="cili.a1" target="cili.a2">
  <Provenance resource="wn-a" version="1.0"/>
</ConceptRelation>
<ConceptRelation relation_type="hypernym" source="cili.a2" target="cili.a3">
  <Provenance resource="wn-a" version="1.0"/>
</ConceptRelation>
"""

# XML body: adds the back-edge a3→a1 to close a cycle
_CYCLE_BODY = _CHAIN_BODY + """\
<ConceptRelation relation_type="hypernym" source="cili.a3" target="cili.a1">
  <Provenance resource="wn-a" version="1.0"/>
</ConceptRelation>
"""


class TestDetectCycles:
    """detect_cycles() finds cycles in the IS-A hypernym graph."""

    def test_no_cycle_in_chain(self, builder, tmp_path):
        (tmp_path / 'wn.xml').write_text(wn_xml('wn-a', _CHAIN_BODY))
        builder.process_file(tmp_path / 'wn.xml')
        builder.create_indexes()
        assert builder.detect_cycles() == 0

    def test_simple_cycle_detected(self, builder, tmp_path):
        (tmp_path / 'wn.xml').write_text(wn_xml('wn-a', _CYCLE_BODY))
        builder.process_file(tmp_path / 'wn.xml')
        builder.create_indexes()
        assert builder.detect_cycles() >= 1

    def test_cycle_logged_as_warning(self, builder, tmp_path, caplog):
        import logging
        (tmp_path / 'wn.xml').write_text(wn_xml('wn-a', _CYCLE_BODY))
        builder.process_file(tmp_path / 'wn.xml')
        builder.create_indexes()
        with caplog.at_level(logging.WARNING, logger='synthesise'):
            builder.detect_cycles()
        assert 'Cyclic SCC remaining' in caplog.text

    def test_wn_en_has_no_cycles(self, builder):
        """The curated test wordnet must be cycle-free."""
        builder.process_file(_WORDNETS_DIR / 'wn-en.xml')
        builder.create_indexes()
        assert builder.detect_cycles() == 0


# ---------------------------------------------------------------------------
# check_and_remove_new_cycles
# ---------------------------------------------------------------------------

class TestCheckAndRemoveNewCycles:
    """check_and_remove_new_cycles() removes cycle-closing edges per-file."""

    def test_cycle_edge_removed_from_db(self, builder, tmp_path):
        """The back-edge that closes a cycle is deleted from synset_relations."""
        chain_file = tmp_path / 'wn-chain.xml'
        chain_file.write_text(wn_xml('wn-a', _CHAIN_BODY))
        first = builder._next_synset_rel_id
        builder.process_file(chain_file)
        builder.check_and_remove_new_cycles('wn-a', first)
        # Chain has no cycles — nothing removed
        assert builder.detect_cycles() == 0

    def test_cycle_back_edge_is_rejected(self, builder, tmp_path):
        """A second file that closes a cycle has its offending edge removed."""
        chain_file = tmp_path / 'wn-chain.xml'
        chain_file.write_text(wn_xml('wn-a', _CHAIN_BODY))
        builder.process_file(chain_file)

        back_file = tmp_path / 'wn-back.xml'
        back_file.write_text(wn_xml('wn-b', """\
<ConceptRelation relation_type="hypernym" source="cili.a3" target="cili.a1">
  <Provenance resource="wn-b" version="1.0"/>
</ConceptRelation>
"""))
        first = builder._next_synset_rel_id
        builder.process_file(back_file)
        removed = builder.check_and_remove_new_cycles('wn-b', first)

        assert removed >= 1
        builder.create_indexes()
        assert builder.detect_cycles() == 0

    def test_cycle_removal_logged_with_chain(self, builder, tmp_path, caplog):
        """Log message names the resource, the edge, and the existing chain."""
        import logging
        chain_file = tmp_path / 'wn-chain.xml'
        chain_file.write_text(wn_xml('wn-a', _CHAIN_BODY))
        builder.process_file(chain_file)

        back_file = tmp_path / 'wn-back.xml'
        back_file.write_text(wn_xml('wn-b', """\
<ConceptRelation relation_type="hypernym" source="cili.a3" target="cili.a1">
  <Provenance resource="wn-b" version="1.0"/>
</ConceptRelation>
"""))
        first = builder._next_synset_rel_id
        builder.process_file(back_file)
        with caplog.at_level(logging.WARNING, logger='synthesise'):
            builder.check_and_remove_new_cycles('wn-b', first)

        assert 'wn-b' in caplog.text
        assert 'hypernym' in caplog.text
        assert 'existing chain' in caplog.text


# ---------------------------------------------------------------------------
# Same concept pair given as both hypernym and instance_hypernym
# ---------------------------------------------------------------------------

def _count_hyper_rows(builder, src_ili: str, tgt_ili: str) -> int:
    """Rows of hypernym or instance_hypernym from src to tgt."""
    return builder.cur.execute(
        """
        SELECT COUNT(*) FROM synset_relations sr
        JOIN synsets s1 ON s1.rowid = sr.source_rowid
        JOIN synsets s2 ON s2.rowid = sr.target_rowid
        JOIN relation_types rt ON rt.rowid = sr.type_rowid
        WHERE s1.ili = ? AND s2.ili = ?
          AND rt.type IN ('hypernym', 'instance_hypernym')
        """,
        (src_ili.replace('cili.', ''), tgt_ili.replace('cili.', '')),
    ).fetchone()[0]


class TestDoubledPairCycle:
    """A cycle-closing pair stated as both relation types must be removed
    in full: deleting only one of the two rows leaves the cycle in place."""

    def test_both_rows_removed_and_no_cycle_left(self, builder, tmp_path):
        (tmp_path / 'chain.xml').write_text(wn_xml('wn-a', _CHAIN_BODY))
        builder.process_file(tmp_path / 'chain.xml')

        (tmp_path / 'back.xml').write_text(wn_xml('wn-b', """\
<ConceptRelation relation_type="hypernym" source="cili.a3" target="cili.a1">
  <Provenance resource="wn-b" version="1.0"/>
</ConceptRelation>
<ConceptRelation relation_type="instance_hypernym" source="cili.a3" target="cili.a1">
  <Provenance resource="wn-b" version="1.0"/>
</ConceptRelation>
"""))
        first = builder._next_synset_rel_id
        builder.process_file(tmp_path / 'back.xml')
        removed = builder.check_and_remove_new_cycles('wn-b', first)

        assert removed == 2
        assert _count_hyper_rows(builder, 'cili.a3', 'cili.a1') == 0
        assert builder.detect_cycles() == 0
        # One log record per removed row, not one row logged twice
        rels = sorted(c['rel'] for c in builder._conflicts['cycles'])
        assert rels == ['hypernym', 'instance_hypernym']


# ---------------------------------------------------------------------------
# Merge order: LOW_TRUST_RESOURCES merged last, so they lose conflicts
# ---------------------------------------------------------------------------

_SYNTH_PATH = Path(__file__).parent.parent / 'conversion_scripts' / '6_synthesise.py'


def _load_synth():
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location('6_synthesise', _SYNTH_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules['6_synthesise'] = mod
    spec.loader.exec_module(mod)
    return mod


# Three concepts plus a trusted edge a2->a3. Two low-trust files add
# a1->a2 and a3->a1; together they close a cycle.
_TRUST_BASE_BODY = """\
<Concept id="cili.a1" ontological_category="NOUN" status="1">
  <Provenance resource="wn-a" version="1.0"/>
</Concept>
<Concept id="cili.a2" ontological_category="NOUN" status="1">
  <Provenance resource="wn-a" version="1.0"/>
</Concept>
<Concept id="cili.a3" ontological_category="NOUN" status="1">
  <Provenance resource="wn-a" version="1.0"/>
</Concept>
<ConceptRelation relation_type="hypernym" source="cili.a2" target="cili.a3">
  <Provenance resource="wn-a" version="1.0"/>
</ConceptRelation>
"""


class TestLowTrustMergedLast:
    """Merging in merge_order() means the worst low-trust wordnet is the one
    whose edge is removed, even though its filename sorts first."""

    def _merge(self, builder, tmp_path, monkeypatch):
        synth = _load_synth()
        monkeypatch.setattr(synth, 'LOW_TRUST_RESOURCES', ('Wn-worst', 'wn-mild'))
        files = {
            # 'Wn-worst' sorts before everything else by filename (uppercase)
            'Wn-worst-1.0.xml': wn_xml('Wn-worst', """\
<ConceptRelation relation_type="hypernym" source="cili.a3" target="cili.a1">
  <Provenance resource="Wn-worst" version="1.0"/>
</ConceptRelation>
"""),
            'wn-a-1.0.xml': wn_xml('wn-a', _TRUST_BASE_BODY),
            'wn-mild-1.0.xml': wn_xml('wn-mild', """\
<ConceptRelation relation_type="hypernym" source="cili.a1" target="cili.a2">
  <Provenance resource="wn-mild" version="1.0"/>
</ConceptRelation>
"""),
        }
        for name, xml in files.items():
            (tmp_path / name).write_text(xml)
        order = synth.merge_order(sorted(tmp_path.glob('*.xml')))
        for f in order:
            first = builder._next_synset_rel_id
            builder.process_file(f)
            builder.check_and_remove_new_cycles(f.stem, first)
        return [f.name for f in order]

    def test_order_puts_worst_last(self, builder, tmp_path, monkeypatch):
        names = self._merge(builder, tmp_path, monkeypatch)
        assert names == ['wn-a-1.0.xml', 'wn-mild-1.0.xml', 'Wn-worst-1.0.xml']

    def test_worst_edge_removed_others_kept(self, builder, tmp_path, monkeypatch):
        self._merge(builder, tmp_path, monkeypatch)
        assert builder.detect_cycles() == 0
        assert _count_hyper_rows(builder, 'cili.a3', 'cili.a1') == 0
        assert _count_hyper_rows(builder, 'cili.a1', 'cili.a2') == 1
        assert _count_hyper_rows(builder, 'cili.a2', 'cili.a3') == 1


# ---------------------------------------------------------------------------
# translated_from on machine-translated glosses
# ---------------------------------------------------------------------------

def _translated_from(builder, concept_id: str) -> str | None:
    """The language code a concept's English definition was translated from,
    or None (including when there is no English definition at all)."""
    row = builder.cur.execute(
        """
        SELECT l.code FROM definitions d
        JOIN synsets s ON s.rowid = d.synset_rowid
        JOIN languages el ON el.rowid = d.language_rowid AND el.code = 'en'
        LEFT JOIN languages l ON l.rowid = d.translated_from_rowid
        WHERE s.ili = ?
        """,
        (concept_id.replace('cili.', ''),),
    ).fetchone()
    return row[0] if row else None


class TestTranslatedFromGloss:
    """A machine-translated gloss records which language it came from."""

    def test_translated_from_attribute_stored(self, builder, tmp_path):
        body = """\
<Concept id="cili.i1" ontological_category="NOUN" status="1">
  <Provenance resource="wn-a" version="1.0"/>
</Concept>
<Gloss definiendum="cili.i1" language="en" translated_from="hu">
  <AnnotatedSentence>a dog</AnnotatedSentence>
  <Provenance resource="mtg" version="1.0"/>
</Gloss>
"""
        (tmp_path / 'wn.xml').write_text(wn_xml('wn-a', body))
        builder.process_file(tmp_path / 'wn.xml')

        assert _translated_from(builder, 'cili.i1') == 'hu'

    def test_native_gloss_has_no_translated_from(self, builder, tmp_path):
        body = """\
<Concept id="cili.i1" ontological_category="NOUN" status="1">
  <Provenance resource="wn-a" version="1.0"/>
</Concept>
<Gloss definiendum="cili.i1" language="en">
  <AnnotatedSentence>a dog</AnnotatedSentence>
  <Provenance resource="wn-a" version="1.0"/>
</Gloss>
"""
        (tmp_path / 'wn.xml').write_text(wn_xml('wn-a', body))
        builder.process_file(tmp_path / 'wn.xml')

        assert _translated_from(builder, 'cili.i1') is None


# ---------------------------------------------------------------------------
# _lang_rowid
# ---------------------------------------------------------------------------

class TestLangRowid:
    """langcodes doesn't raise for a code it can't resolve — it returns a
    descriptive placeholder string ("Unknown language [xx]") instead, which
    must not be stored as if it were a real name (see cyg.merge._lang_rowid).
    """

    def test_valid_code_gets_real_name(self, builder):
        rowid = builder._lang_rowid('uk')
        name = builder.cur.execute(
            'SELECT name FROM languages WHERE rowid = ?', (rowid,)
        ).fetchone()[0]
        assert name == 'Ukrainian'

    def test_invalid_code_stores_null_not_placeholder(self, builder):
        """'ua' is not a real ISO 639 code (it's Ukraine's country code) —
        the exact case that motivated this hardening.
        """
        rowid = builder._lang_rowid('ua')
        name = builder.cur.execute(
            'SELECT name FROM languages WHERE rowid = ?', (rowid,)
        ).fetchone()[0]
        assert name is None

    def test_invalid_code_is_cached_as_null(self, builder):
        """A second call for the same bad code must reuse the cached rowid,
        not re-insert — and still resolve to the same (null-named) row.
        """
        first = builder._lang_rowid('ua')
        second = builder._lang_rowid('ua')
        assert first == second
