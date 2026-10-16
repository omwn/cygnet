"""Tests for MergeBuilder.write_wordnet_summaries().

Covers the precomputed per-resource JSON summaries that power the
Wordnets-tab detail view (pos/ILI coverage, example lemmas, relation-type
distributions) -- these used to be computed live in the browser.
"""

import json
from pathlib import Path

import pytest

from conftest import wn_xml

# Two concepts: "dog" (hyponym) under "animal" (hypernym), one resource.
# A SenseRelation and an annotated Example exercise the remaining fields.
_SUMMARY_BODY = """\
<Concept id="cili.animal" ontological_category="NOUN" status="1">
  <Provenance resource="wn-a" version="1.0"/>
</Concept>
<Concept id="cili.dog" ontological_category="NOUN" status="1">
  <Provenance resource="wn-a" version="1.0"/>
</Concept>
<Gloss definiendum="cili.animal" language="en">
  <AnnotatedSentence>a living organism</AnnotatedSentence>
  <Provenance resource="wn-a" version="1.0"/>
</Gloss>
<Gloss definiendum="cili.dog" language="en">
  <AnnotatedSentence>a domesticated canine</AnnotatedSentence>
  <Provenance resource="wn-a" version="1.0"/>
</Gloss>
<Lexeme id="lex.en.animal" language="en" grammatical_category="NOUN">
  <Wordform form="animal"/>
  <Provenance resource="wn-a" version="1.0"/>
</Lexeme>
<Lexeme id="lex.en.dog" language="en" grammatical_category="NOUN">
  <Wordform form="dog"/>
  <Provenance resource="wn-a" version="1.0"/>
</Lexeme>
<Sense id="sense.animal" signifier="lex.en.animal" signified="cili.animal">
  <Provenance resource="wn-a" version="1.0"/>
</Sense>
<Sense id="sense.dog" signifier="lex.en.dog" signified="cili.dog">
  <Provenance resource="wn-a" version="1.0"/>
</Sense>
<ConceptRelation relation_type="hypernym" source="cili.dog" target="cili.animal">
  <Provenance resource="wn-a" version="1.0"/>
</ConceptRelation>
<SenseRelation relation_type="similar" source="sense.dog" target="sense.animal">
  <Provenance resource="wn-a" version="1.0"/>
</SenseRelation>
<Example>
  <AnnotatedSentence>The <AnnotatedToken sense="sense.dog">dog</AnnotatedToken> barked.</AnnotatedSentence>
  <Provenance resource="wn-a" version="1.0"/>
</Example>
"""


@pytest.fixture()
def builder_summaries(builder, tmp_path):
    (tmp_path / 'wn.xml').write_text(wn_xml('wn-a', _SUMMARY_BODY))
    builder.process_file(tmp_path / 'wn.xml')
    builder.create_indexes()
    builder.merge_case_variants()
    builder.cascade_delete()
    builder.remove_orphans()
    builder.compute_sense_indices()
    builder.insert_resources()
    builder.load_core_synsets()
    return builder


def _write_and_load(builder, out_dir: Path) -> dict:
    n = builder.write_wordnet_summaries(out_dir)
    assert n == 1
    return json.loads((out_dir / 'wn-a.json').read_text())


class TestWriteWordnetSummaries:
    def test_writes_one_file_per_resource(self, builder_summaries, tmp_path):
        out_dir = tmp_path / 'summaries'
        n = builder_summaries.write_wordnet_summaries(out_dir)
        assert n == 1
        assert (out_dir / 'wn-a.json').exists()

    def test_pos_stats_and_ili_coverage(self, builder_summaries, tmp_path):
        summary = _write_and_load(builder_summaries, tmp_path / 'summaries')
        assert summary['posStats'] == [{'pos': 'NOUN', 'cnt': 2, 'withIli': 2}]
        assert summary['iliPct'] == 100

    def test_definition_and_example_counts(self, builder_summaries, tmp_path):
        summary = _write_and_load(builder_summaries, tmp_path / 'summaries')
        assert summary['defCount'] == 2
        assert summary['exCount'] == 1

    def test_example_lemmas_include_both_forms(self, builder_summaries, tmp_path):
        summary = _write_and_load(builder_summaries, tmp_path / 'summaries')
        forms = {e['form'] for e in summary['examples']}
        assert forms == {'animal', 'dog'}

    def test_concept_relation_distribution(self, builder_summaries, tmp_path):
        summary = _write_and_load(builder_summaries, tmp_path / 'summaries')
        assert len(summary['relDist']) == 1
        rel = summary['relDist'][0]
        assert rel['type'] == 'hypernym'
        assert rel['cnt'] == 1

    def test_inverse_concept_relation_excluded(self, builder_summaries, tmp_path):
        """hyponym is hypernym's inverse and must not appear separately."""
        summary = _write_and_load(builder_summaries, tmp_path / 'summaries')
        types = {r['type'] for r in summary['relDist']}
        assert 'hyponym' not in types

    def test_sense_relation_distribution_with_forms(self, builder_summaries, tmp_path):
        summary = _write_and_load(builder_summaries, tmp_path / 'summaries')
        assert len(summary['senseRelDist']) == 1
        rel = summary['senseRelDist'][0]
        assert rel['type'] == 'similar'
        # 'similar' is symmetric -- _do_sense_relation() stores both
        # directions, so the one declared relation yields a count of 2.
        assert rel['cnt'] == 2
        assert rel['src_form'] == 'dog'
        assert rel['tgt_form'] == 'animal'

    def test_stale_summary_files_are_cleared(self, builder_summaries, tmp_path):
        out_dir = tmp_path / 'summaries'
        out_dir.mkdir()
        stale = out_dir / 'stale-resource.json'
        stale.write_text('{}')
        builder_summaries.write_wordnet_summaries(out_dir)
        assert not stale.exists()

    def test_no_senses_resource_writes_nothing(self, builder, tmp_path):
        """A resource with zero senses (e.g. CILI-only) gets no file."""
        n = builder.write_wordnet_summaries(tmp_path / 'summaries')
        assert n == 0

    def test_direction_swaps_empty_without_config_path(self, builder_summaries, tmp_path):
        summary = _write_and_load(builder_summaries, tmp_path / 'summaries')
        assert summary['directionSwaps'] == []

    def test_direction_swaps_empty_when_config_missing(self, builder_summaries, tmp_path):
        out_dir = tmp_path / 'summaries'
        builder_summaries.write_wordnet_summaries(out_dir, tmp_path / 'nope.json')
        summary = json.loads((out_dir / 'wn-a.json').read_text())
        assert summary['directionSwaps'] == []

    def test_direction_swaps_populated_from_config(self, builder_summaries, tmp_path):
        swaps_path = tmp_path / 'relation_direction_swaps.json'
        swaps_path.write_text(json.dumps({
            'wn-a': {
                'hypernym': {'reversed': 30496, 'comparable': 30498,
                             'reversed_share': 0.9999, 'swapped': True},
                'hyponym': {'reversed': None, 'comparable': None,
                            'reversed_share': None, 'swapped': True},
            },
            'other-resource': {
                'mero_part': {'reversed': 10, 'comparable': 10,
                              'reversed_share': 1.0, 'swapped': True},
            },
        }))
        out_dir = tmp_path / 'summaries'
        builder_summaries.write_wordnet_summaries(out_dir, swaps_path)
        summary = json.loads((out_dir / 'wn-a.json').read_text())
        assert summary['directionSwaps'] == [
            {'relationType': 'hypernym', 'reversed': 30496, 'comparable': 30498,
             'reversedShare': 0.9999},
        ]
