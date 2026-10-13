"""Tests for the static search-index / concept-detail precompute methods
(MergeBuilder.write_search_index, write_concept_details, write_labels_index,
write_sense_index, write_ili_index, write_manifest).

These replace the browser's synchronous queryConcept()/querySense()/
searchWordform() SQL (web/index.html) with build-time precomputed JSON, so
the dominant search-and-browse path never needs the full database loaded.
Field names and behavioral quirks (raw relation types, no inverse
suppression, definition-overwrite-on-collision, etc.) are asserted exactly
against what those live functions do -- see the architecture plan at
~/.claude/plans/optimized-wondering-lighthouse.md for the full rationale.
"""

import json
from pathlib import Path

import pytest

from conftest import wn_xml

# Two concepts: "dog" (hyponym) under "animal" (hypernym), one resource,
# two languages (en + fr) on "dog" so labels.json's byLang/fallback shape
# is actually exercised. A SenseRelation and an annotated Example exercise
# the remaining fields; "animal" is marked core after build.
_BODY = """\
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
<Lexeme id="lex.fr.chien" language="fr" grammatical_category="NOUN">
  <Wordform form="chien"/>
  <Provenance resource="wn-a" version="1.0"/>
</Lexeme>
<Sense id="sense.animal" signifier="lex.en.animal" signified="cili.animal">
  <Provenance resource="wn-a" version="1.0"/>
</Sense>
<Sense id="sense.dog" signifier="lex.en.dog" signified="cili.dog">
  <Provenance resource="wn-a" version="1.0"/>
</Sense>
<Sense id="sense.chien" signifier="lex.fr.chien" signified="cili.dog">
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
def builder_precompute(builder, tmp_path):
    (tmp_path / 'wn.xml').write_text(wn_xml('wn-a', _BODY))
    builder.process_file(tmp_path / 'wn.xml')
    builder.create_indexes()
    builder.merge_case_variants()
    builder.cascade_delete()
    builder.remove_orphans()
    builder.compute_sense_indices()
    builder.insert_resources()
    builder.load_core_synsets()

    animal_rowid = builder.cur.execute(
        "SELECT rowid FROM synsets WHERE ili = 'animal'"
    ).fetchone()[0]
    builder.cur.execute(
        'INSERT INTO core_synsets (synset_rowid) VALUES (?)', (animal_rowid,)
    )
    builder.animal_rowid = animal_rowid
    builder.dog_rowid = builder.cur.execute(
        "SELECT rowid FROM synsets WHERE ili = 'dog'"
    ).fetchone()[0]
    return builder


def _concept_files(out_dir: Path) -> dict[str, dict]:
    return {
        p.stem: json.loads(p.read_text()) for p in out_dir.glob('*.json')
    }


class TestWriteSearchIndex:
    def test_shard_and_manifest_written(self, builder_precompute, tmp_path):
        out_dir = tmp_path / 'search'
        stats = builder_precompute.write_search_index(out_dir, n_shards=2)
        assert (out_dir / 'manifest.json').exists()
        assert stats['forms'] >= 2  # at least "animal", "dog" (and "chien")
        assert stats['rows'] == stats['forms']  # one sense each here

    def test_shard_boundary_never_splits_a_form(self, builder_precompute, tmp_path):
        out_dir = tmp_path / 'search'
        builder_precompute.write_search_index(out_dir, n_shards=100)
        manifest = json.loads((out_dir / 'manifest.json').read_text())
        all_keys_per_shard = []
        for shard in manifest['shards']:
            data = json.loads((out_dir / shard['file']).read_text())
            all_keys_per_shard.append(set(data.keys()))
        # No form key appears in more than one shard.
        seen = set()
        for keys in all_keys_per_shard:
            assert not (keys & seen)
            seen |= keys

    def test_row_shape_matches_compact_contract(self, builder_precompute, tmp_path):
        out_dir = tmp_path / 'search'
        builder_precompute.write_search_index(out_dir, n_shards=1)
        manifest = json.loads((out_dir / 'manifest.json').read_text())
        data = json.loads((out_dir / manifest['shards'][0]['file']).read_text())
        row = data['dog'][0]
        assert set(row.keys()) == {'sid', 'syn', 'lg', 'pos', 'ind', 'l', 'rk'}
        assert row['l'] == 'dog'
        assert row['lg'] == 'en'
        assert row['pos'] == 'NOUN'
        assert row['rk'] == 0

    def test_stale_shards_cleared(self, builder_precompute, tmp_path):
        out_dir = tmp_path / 'search'
        out_dir.mkdir()
        stale = out_dir / '9999.json'
        stale.write_text('{}')
        builder_precompute.write_search_index(out_dir, n_shards=1)
        assert not stale.exists()


class TestWriteConceptDetails:
    def test_writes_one_file_per_concept(self, builder_precompute, tmp_path):
        out_dir = tmp_path / 'concepts'
        n = builder_precompute.write_concept_details(out_dir)
        assert n == 2
        assert (out_dir / f'{builder_precompute.animal_rowid}.json').exists()
        assert (out_dir / f'{builder_precompute.dog_rowid}.json').exists()

    def test_basic_fields(self, builder_precompute, tmp_path):
        out_dir = tmp_path / 'concepts'
        builder_precompute.write_concept_details(out_dir)
        dog = json.loads((out_dir / f'{builder_precompute.dog_rowid}.json').read_text())
        assert dog['oc'] == 'NOUN'
        assert dog['ili'] == 'dog'
        assert dog['d'] == {'en': 'a domesticated canine'}
        assert 'en' in dog['dr']
        assert dog['core'] is False

    def test_core_flag(self, builder_precompute, tmp_path):
        out_dir = tmp_path / 'concepts'
        builder_precompute.write_concept_details(out_dir)
        animal = json.loads((out_dir / f'{builder_precompute.animal_rowid}.json').read_text())
        assert animal['core'] is True

    def test_concept_relation_raw_type_and_rid(self, builder_precompute, tmp_path):
        """cr entries store the raw relation_types.type, not a short code,
        and must carry rid (needed for provenance display)."""
        out_dir = tmp_path / 'concepts'
        builder_precompute.write_concept_details(out_dir)
        dog = json.loads((out_dir / f'{builder_precompute.dog_rowid}.json').read_text())
        assert len(dog['cr']) == 1
        rel = dog['cr'][0]
        assert rel['type'] == 'hypernym'
        assert rel['to'] == builder_precompute.animal_rowid
        assert isinstance(rel['rid'], int)

    def test_inverse_relations_not_suppressed(self, builder_precompute, tmp_path):
        """Unlike write_wordnet_summaries(), queryConcept() returns every
        stored relation live -- the precompute must match that, not filter
        out the inverse direction."""
        out_dir = tmp_path / 'concepts'
        builder_precompute.write_concept_details(out_dir)
        animal = json.loads((out_dir / f'{builder_precompute.animal_rowid}.json').read_text())
        # hyponym is hypernym's auto-generated inverse, stored on "animal".
        assert animal['cr'] is not None
        assert any(r['type'] == 'hyponym' for r in animal['cr'])

    def test_sense_relation_embeds_target_form_and_concept(self, builder_precompute, tmp_path):
        out_dir = tmp_path / 'concepts'
        builder_precompute.write_concept_details(out_dir)
        dog = json.loads((out_dir / f'{builder_precompute.dog_rowid}.json').read_text())
        dog_sense = next(s for s in dog['senses'].values() if s['l'] == 'dog')
        assert len(dog_sense['sr']) == 1
        rel = dog_sense['sr'][0]
        assert rel['type'] == 'similar'
        assert rel['toForm'] == 'animal'
        assert rel['toConcept'] == builder_precompute.animal_rowid
        assert isinstance(rel['rid'], int)

    def test_senses_grouped_by_language(self, builder_precompute, tmp_path):
        out_dir = tmp_path / 'concepts'
        builder_precompute.write_concept_details(out_dir)
        dog = json.loads((out_dir / f'{builder_precompute.dog_rowid}.json').read_text())
        assert set(dog['s'].keys()) == {'en', 'fr'}
        assert len(dog['senses']) == 2

    def test_example_segments_bold_only_the_owning_sense(self, builder_precompute, tmp_path):
        out_dir = tmp_path / 'concepts'
        builder_precompute.write_concept_details(out_dir)
        dog = json.loads((out_dir / f'{builder_precompute.dog_rowid}.json').read_text())
        dog_sense = next(s for s in dog['senses'].values() if s['l'] == 'dog')
        assert dog_sense['ex'] is not None
        segments = dog_sense['ex'][0]
        bold_texts = [seg['text'] for seg in segments if seg['bold']]
        assert bold_texts == ['dog']

    def test_sense_id_is_string_synset_is_int(self, builder_precompute, tmp_path):
        """querySense() returns id as a string (r.id.toString()) and c as
        a number -- the precomputed shape must match exactly."""
        out_dir = tmp_path / 'concepts'
        builder_precompute.write_concept_details(out_dir)
        dog = json.loads((out_dir / f'{builder_precompute.dog_rowid}.json').read_text())
        dog_sense = next(s for s in dog['senses'].values() if s['l'] == 'dog')
        assert isinstance(dog_sense['id'], str)
        assert isinstance(dog_sense['c'], int)

    def test_atomic_swap_replaces_prior_output(self, builder_precompute, tmp_path):
        out_dir = tmp_path / 'concepts'
        out_dir.mkdir()
        stale = out_dir / 'stale.json'
        stale.write_text('{}')
        builder_precompute.write_concept_details(out_dir)
        assert not stale.exists()
        assert (out_dir / f'{builder_precompute.dog_rowid}.json').exists()


class TestWriteLabelsIndex:
    def test_multilingual_by_lang_and_fallback(self, builder_precompute, tmp_path):
        out_dir = tmp_path / 'labels'
        n = builder_precompute.write_labels_index(out_dir, bucket_size=1000)
        assert n == 2
        bucket = builder_precompute.dog_rowid // 1000
        data = json.loads((out_dir / f'{bucket}.json').read_text())
        dog = data[str(builder_precompute.dog_rowid)]
        assert dog['byLang'] == {'en': 'dog', 'fr': 'chien'}
        assert dog['fallback']['lang'] in ('en', 'fr')
        assert dog['fallback']['label'] in ('dog', 'chien')

    def test_bucket_matches_rowid_div_bucket_size(self, builder_precompute, tmp_path):
        out_dir = tmp_path / 'labels'
        builder_precompute.write_labels_index(out_dir, bucket_size=1)
        # With bucket_size=1, every synset is its own bucket file.
        assert (out_dir / f'{builder_precompute.dog_rowid}.json').exists()
        assert (out_dir / f'{builder_precompute.animal_rowid}.json').exists()


class TestWriteSenseIndex:
    def test_sense_resolves_to_owning_synset(self, builder_precompute, tmp_path):
        out_dir = tmp_path / 'sense-index'
        n = builder_precompute.write_sense_index(out_dir, bucket_size=1000)
        assert n == 3  # animal, dog, chien senses
        dog_sense_id = builder_precompute.cur.execute(
            "SELECT s.rowid FROM senses s JOIN entries e ON s.entry_rowid = e.rowid "
            "JOIN forms f ON f.entry_rowid = e.rowid WHERE f.form = 'dog'"
        ).fetchone()[0]
        bucket = dog_sense_id // 1000
        data = json.loads((out_dir / f'{bucket}.json').read_text())
        assert data[str(dog_sense_id)] == builder_precompute.dog_rowid


class TestWriteIliIndex:
    def test_ili_maps_to_synset_rowid(self, builder_precompute, tmp_path):
        out_dir = tmp_path / 'ili'
        n = builder_precompute.write_ili_index(out_dir)
        assert n == 2
        data = json.loads((out_dir / 'ili-index.json').read_text())
        assert data['dog'] == builder_precompute.dog_rowid
        assert data['animal'] == builder_precompute.animal_rowid


class TestWriteManifest:
    def test_manifest_shape(self, builder_precompute, tmp_path):
        out_dir = tmp_path / 'manifest_dir'
        builder_precompute.write_manifest(out_dir, '20260101000000')
        manifest = json.loads((out_dir / 'manifest.json').read_text())
        assert manifest['version'] == '20260101000000'
        assert manifest['hasCoreTable'] is True
        assert manifest['hasTranslatedFrom'] is True
        assert manifest['stats']['synsets'] == 2
        codes = {lang['code'] for lang in manifest['languages']}
        assert {'en', 'fr'} <= codes
        resource_codes = {r['code'] for r in manifest['resources']}
        assert 'wn-a' in resource_codes


class TestFinalizeWritesBuildInfo:
    def test_build_info_written_to_both_databases(self, builder_precompute, tmp_path):
        # finalize() writes to the paths MergeBuilder was constructed with
        # (the `builder` fixture uses tmp_path/'cygnet.db' and
        # tmp_path/'provenance.db') -- its db_path/prov_db_path args are
        # only used for the final size-print line, not to redirect output.
        db_path = tmp_path / 'cygnet.db'
        prov_path = tmp_path / 'provenance.db'
        builder_precompute.write_manifest(tmp_path / 'data', '20260101000000')
        builder_precompute.finalize(db_path, prov_path, '20260101000000')

        import sqlite3
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        assert conn.execute('SELECT version FROM build_info').fetchone() == (
            '20260101000000',
        )
        prov_conn = sqlite3.connect(f'file:{prov_path}?mode=ro', uri=True)
        assert prov_conn.execute('SELECT version FROM build_info').fetchone() == (
            '20260101000000',
        )
