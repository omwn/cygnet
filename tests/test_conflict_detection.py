"""Tests for duplicate/reversed relation conflict detection in MergeBuilder."""

import logging
from pathlib import Path

import pytest

from conftest import (
    BASE_CONCEPTS,
    BASE_CONCEPTS_SENSE_REL,
    BASE_LEXEMES_AND_SENSES,
    wn_xml,
)

_WORDNETS_DIR = Path(__file__).parent / 'wordnets'


# ---------------------------------------------------------------------------
# Synset-relation conflict tests
# ---------------------------------------------------------------------------

class TestSynsetRelationConflict:
    def test_reversed_hypernym_is_skipped(self, builder, tmp_path, caplog):
        """
        wn-a adds hypernym(dog→animal), which auto-generates hyponym(animal→dog).
        wn-b then explicitly adds hypernym(animal→dog) — reversed — which must be
        skipped with a warning.
        """
        wn_a = tmp_path / 'wn-a.xml'
        wn_a.write_text(wn_xml('wn-a', BASE_CONCEPTS + BASE_LEXEMES_AND_SENSES + """\
<ConceptRelation relation_type="class_hypernym" source="cili.i1" target="cili.i2">
  <Provenance resource="wn-a" version="1.0"/>
</ConceptRelation>
"""))

        wn_b = tmp_path / 'wn-b.xml'
        wn_b.write_text(wn_xml('wn-b', """\
<ConceptRelation relation_type="class_hypernym" source="cili.i2" target="cili.i1">
  <Provenance resource="wn-b" version="1.0"/>
</ConceptRelation>
"""))

        with caplog.at_level(logging.WARNING, logger='synthesise'):
            builder.process_file(wn_a)
            builder.process_file(wn_b)

        assert builder.n_rel_conflicts == 1
        assert 'wn-b' in caplog.text
        assert 'class_hypernym' in caplog.text
        assert 'cili.i1' in caplog.text
        assert 'cili.i2' in caplog.text

    def test_reversed_hypernym_relation_not_stored(self, builder, tmp_path):
        """The conflicting reversed relation must not be persisted to the DB."""
        wn_a = tmp_path / 'wn-a.xml'
        wn_a.write_text(wn_xml('wn-a', BASE_CONCEPTS + BASE_LEXEMES_AND_SENSES + """\
<ConceptRelation relation_type="class_hypernym" source="cili.i1" target="cili.i2">
  <Provenance resource="wn-a" version="1.0"/>
</ConceptRelation>
"""))
        wn_b = tmp_path / 'wn-b.xml'
        wn_b.write_text(wn_xml('wn-b', """\
<ConceptRelation relation_type="class_hypernym" source="cili.i2" target="cili.i1">
  <Provenance resource="wn-b" version="1.0"/>
</ConceptRelation>
"""))

        builder.process_file(wn_a)
        builder.process_file(wn_b)

        rows = builder.cur.execute(
            'SELECT source_rowid, target_rowid FROM synset_relations'
        ).fetchall()
        # Expect exactly two rows: the forward hypernym and the auto-inverse hyponym
        assert len(rows) == 2

    def test_symmetric_relation_not_flagged(self, builder, tmp_path, caplog):
        """
        Symmetric relations (opposite, antonym at concept level) with both
        directions explicitly asserted should NOT trigger a conflict warning.
        """
        wn_a = tmp_path / 'wn-a.xml'
        wn_a.write_text(wn_xml('wn-a', BASE_CONCEPTS + BASE_LEXEMES_AND_SENSES + """\
<ConceptRelation relation_type="opposite" source="cili.i1" target="cili.i2">
  <Provenance resource="wn-a" version="1.0"/>
</ConceptRelation>
"""))
        wn_b = tmp_path / 'wn-b.xml'
        wn_b.write_text(wn_xml('wn-b', """\
<ConceptRelation relation_type="opposite" source="cili.i2" target="cili.i1">
  <Provenance resource="wn-b" version="1.0"/>
</ConceptRelation>
"""))

        with caplog.at_level(logging.WARNING, logger='synthesise'):
            builder.process_file(wn_a)
            builder.process_file(wn_b)

        assert builder.n_rel_conflicts == 0

    def test_no_conflict_on_normal_relations(self, builder, tmp_path, caplog):
        """Single-source wordnet with correct hypernym should produce zero conflicts."""
        wn_a = tmp_path / 'wn-a.xml'
        wn_a.write_text(wn_xml('wn-a', BASE_CONCEPTS + BASE_LEXEMES_AND_SENSES + """\
<ConceptRelation relation_type="class_hypernym" source="cili.i1" target="cili.i2">
  <Provenance resource="wn-a" version="1.0"/>
</ConceptRelation>
"""))

        with caplog.at_level(logging.WARNING, logger='synthesise'):
            builder.process_file(wn_a)

        assert builder.n_rel_conflicts == 0


# ---------------------------------------------------------------------------
# Sense-relation conflict tests
# ---------------------------------------------------------------------------

class TestSenseRelationConflict:
    def test_reversed_derivation_is_skipped(self, builder, tmp_path, caplog):
        """
        wn-a adds derivation(run→running), auto-generating derivation_of(running→run).
        wn-b explicitly adds derivation(running→run), which conflicts and must be skipped.
        """
        wn_a = tmp_path / 'wn-a.xml'
        wn_a.write_text(wn_xml('wn-a', BASE_CONCEPTS_SENSE_REL + """\
<SenseRelation relation_type="derivation" source="sense.run" target="sense.running">
  <Provenance resource="wn-a" version="1.0"/>
</SenseRelation>
"""))
        wn_b = tmp_path / 'wn-b.xml'
        wn_b.write_text(wn_xml('wn-b', """\
<SenseRelation relation_type="derivation" source="sense.running" target="sense.run">
  <Provenance resource="wn-b" version="1.0"/>
</SenseRelation>
"""))

        with caplog.at_level(logging.WARNING, logger='synthesise'):
            builder.process_file(wn_a)
            builder.process_file(wn_b)

        assert builder.n_rel_conflicts == 1
        assert 'wn-b' in caplog.text
        assert 'derivation' in caplog.text

    def test_reversed_derivation_not_stored(self, builder, tmp_path):
        """The conflicting reversed sense relation must not be persisted."""
        wn_a = tmp_path / 'wn-a.xml'
        wn_a.write_text(wn_xml('wn-a', BASE_CONCEPTS_SENSE_REL + """\
<SenseRelation relation_type="derivation" source="sense.run" target="sense.running">
  <Provenance resource="wn-a" version="1.0"/>
</SenseRelation>
"""))
        wn_b = tmp_path / 'wn-b.xml'
        wn_b.write_text(wn_xml('wn-b', """\
<SenseRelation relation_type="derivation" source="sense.running" target="sense.run">
  <Provenance resource="wn-b" version="1.0"/>
</SenseRelation>
"""))

        builder.process_file(wn_a)
        builder.process_file(wn_b)

        rows = builder.cur.execute(
            'SELECT source_rowid, target_rowid FROM sense_relations'
        ).fetchall()
        # derivation(run→running) + auto derivation_of(running→run) = 2 rows
        assert len(rows) == 2

    def test_symmetric_antonym_not_flagged(self, builder, tmp_path, caplog):
        """
        antonym is symmetric (inv == rel_type); both directions being explicitly
        asserted should not trigger a conflict warning.
        """
        wn_a = tmp_path / 'wn-a.xml'
        wn_a.write_text(wn_xml('wn-a', BASE_CONCEPTS_SENSE_REL + """\
<SenseRelation relation_type="antonym" source="sense.run" target="sense.running">
  <Provenance resource="wn-a" version="1.0"/>
</SenseRelation>
"""))
        wn_b = tmp_path / 'wn-b.xml'
        wn_b.write_text(wn_xml('wn-b', """\
<SenseRelation relation_type="antonym" source="sense.running" target="sense.run">
  <Provenance resource="wn-b" version="1.0"/>
</SenseRelation>
"""))

        with caplog.at_level(logging.WARNING, logger='synthesise'):
            builder.process_file(wn_a)
            builder.process_file(wn_b)

        assert builder.n_rel_conflicts == 0


# ---------------------------------------------------------------------------
# Integration tests using the shared test wordnet files
# ---------------------------------------------------------------------------

class TestWordnetFiles:
    def test_wn_en_loads_cleanly(self, builder):
        """wn-en.xml alone produces no conflicts."""
        builder.process_file(_WORDNETS_DIR / 'wn-en.xml')
        assert builder.n_rel_conflicts == 0

    def test_wn_en_plus_fr_loads_cleanly(self, builder):
        """wn-en + wn-fr: French sense references an ILI from wn-en — no conflicts."""
        builder.process_file(_WORDNETS_DIR / 'wn-en.xml')
        builder.process_file(_WORDNETS_DIR / 'wn-fr.xml')
        assert builder.n_rel_conflicts == 0

    def test_wn_en_plus_fr_merges_dog_concept(self, builder):
        """After merging, the dog concept (cili.i3) has senses in both en and fr."""
        builder.process_file(_WORDNETS_DIR / 'wn-en.xml')
        builder.process_file(_WORDNETS_DIR / 'wn-fr.xml')

        dog_rowid = builder.cur.execute(
            "SELECT rowid FROM synsets WHERE ili = 'i3'"
        ).fetchone()[0]
        lang_codes = {
            row[0] for row in builder.cur.execute(
                """SELECT l.code FROM senses s
                   JOIN entries e ON s.entry_rowid = e.rowid
                   JOIN languages l ON e.language_rowid = l.rowid
                   WHERE s.synset_rowid = ?""",
                (dog_rowid,),
            )
        }
        assert lang_codes == {'en', 'fr'}

    def test_wn_bad_triggers_conflict(self, builder, caplog):
        """wn-bad.xml asserts a reversed hypernym that conflicts with wn-en.xml."""
        builder.process_file(_WORDNETS_DIR / 'wn-en.xml')

        with caplog.at_level(logging.WARNING, logger='synthesise'):
            builder.process_file(_WORDNETS_DIR / 'wn-bad.xml')

        assert builder.n_rel_conflicts == 1
        assert 'wn-bad' in caplog.text
        assert 'class_hypernym' in caplog.text

    def test_wn_bad_conflict_not_stored(self, builder):
        """The reversed relation from wn-bad.xml must not be persisted."""
        builder.process_file(_WORDNETS_DIR / 'wn-en.xml')
        builder.process_file(_WORDNETS_DIR / 'wn-bad.xml')

        # wn-en has 2 explicit hypernyms → 4 rows total (2 + 2 auto-hyponyms)
        count = builder.cur.execute(
            'SELECT COUNT(*) FROM synset_relations'
        ).fetchone()[0]
        assert count == 4
