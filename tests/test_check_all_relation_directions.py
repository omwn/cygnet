"""Tests for scripts/check_all_relation_directions.py.

build_swap_config(), the cache helpers and compute_candidate_stats() are
tested here on fixtures; main()'s report is exercised manually against the
actual corpus instead (see the full-run findings in
scripts/check_all_relation_directions.py's docstring and the
relation-direction-checker memory note).
"""

import importlib.util
import json
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).parent.parent / 'scripts'


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS_DIR / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


crd = _load('check_relation_directions')
carb = _load('check_all_relation_directions')

DirectionStats = crd.DirectionStats
build_swap_config = carb.build_swap_config
_file_stat_key = carb._file_stat_key
load_cache = carb.load_cache
load_cached_reference_pairs = carb.load_cached_reference_pairs
reference_pairs_cache_entry = carb.reference_pairs_cache_entry
compute_candidate_stats = carb.compute_candidate_stats
candidate_files = carb.candidate_files
unlisted_files = carb.unlisted_files


def _stats(rel_type, agree=0, reversed_=0, no_overlap=0):
    return DirectionStats(rel_type, agree=agree, reversed=reversed_, no_overlap=no_overlap)


class TestBuildSwapConfig:
    def test_flagged_type_and_inverse_both_marked_swapped(self):
        stats = _stats('mero_part', agree=10, reversed_=925)
        config = build_swap_config([('plwn:5.0', 'file.xml', stats)])
        assert config['plwn']['mero_part']['swapped'] is True
        assert config['plwn']['holo_part']['swapped'] is True

    def test_measured_side_keeps_its_stats(self):
        stats = _stats('mero_part', agree=10, reversed_=925)
        config = build_swap_config([('plwn:5.0', 'file.xml', stats)])
        entry = config['plwn']['mero_part']
        assert entry['reversed'] == 925
        assert entry['comparable'] == 935

    def test_paired_inverse_has_null_stats_when_not_independently_flagged(self):
        stats = _stats('mero_part', agree=10, reversed_=925)
        config = build_swap_config([('plwn:5.0', 'file.xml', stats)])
        entry = config['plwn']['holo_part']
        assert entry['reversed'] is None
        assert entry['comparable'] is None
        assert entry['reversed_share'] is None

    def test_both_sides_independently_flagged_keep_their_own_stats(self):
        """If a relation type AND its inverse both independently clear
        the threshold (not yet seen in practice, but possible), neither
        should be overwritten with the other's null-stats placeholder."""
        fwd = _stats('mero_part', agree=1, reversed_=99)
        inv = _stats('holo_part', agree=2, reversed_=88)
        config = build_swap_config([
            ('plwn:5.0', 'file.xml', fwd),
            ('plwn:5.0', 'file.xml', inv),
        ])
        assert config['plwn']['mero_part']['reversed'] == 99
        assert config['plwn']['holo_part']['reversed'] == 88

    def test_symmetric_relation_type_not_given_a_separate_inverse_entry(self):
        """A symmetric type (fwd == inv in INVERSE_CONCEPT_RELATIONS, e.g.
        'similar') must not spawn a duplicate/overwriting entry for
        itself."""
        stats = _stats('similar', agree=0, reversed_=50)
        config = build_swap_config([('x:1.0', 'file.xml', stats)])
        assert config['x'] == {'similar': {
            'reversed': 50, 'comparable': 50, 'reversed_share': 1.0, 'swapped': True,
        }}

    def test_lexicon_id_strips_version_from_spec(self):
        stats = _stats('hypernym', agree=0, reversed_=30)
        config = build_swap_config([('own-pt:1.0.0', 'own-pt-lmf.xml', stats)])
        assert 'own-pt' in config
        assert 'own-pt:1.0.0' not in config

    def test_no_flagged_pairs_yields_empty_config(self):
        assert build_swap_config([]) == {}

    def test_different_lexicons_kept_separate(self):
        stats_a = _stats('mero_part', agree=0, reversed_=30)
        stats_b = _stats('hypernym', agree=0, reversed_=30)
        config = build_swap_config([
            ('plwn:5.0', 'a.xml', stats_a),
            ('own-pt:1.0.0', 'b.xml', stats_b),
        ])
        assert set(config) == {'plwn', 'own-pt'}
        assert 'hyponym' in config['own-pt']
        assert 'hyponym' not in config['plwn']


class TestFileStatKey:
    def test_returns_mtime_and_size(self, tmp_path):
        f = tmp_path / 'x.txt'
        f.write_text('hello')
        key = _file_stat_key(f)
        assert key['size'] == 5
        assert key['mtime'] == f.stat().st_mtime


class TestLoadCache:
    def _write_reference(self, tmp_path):
        ref = tmp_path / 'ref.xml'
        ref.write_text('reference')
        return ref

    def test_missing_cache_path_returns_empty(self, tmp_path):
        ref = self._write_reference(tmp_path)
        assert load_cache(None, ref) == {}
        assert load_cache(tmp_path / 'nope.json', ref) == {}

    def test_matching_reference_returns_files(self, tmp_path):
        ref = self._write_reference(tmp_path)
        cache_path = tmp_path / 'cache.json'
        cache_path.write_text(json.dumps({
            'reference': {'path': str(ref), **_file_stat_key(ref)},
            'files': {'/some/file.xml': {'lexicons': {}}},
        }))
        assert load_cache(cache_path, ref) == {'/some/file.xml': {'lexicons': {}}}

    def test_changed_reference_invalidates_whole_cache(self, tmp_path):
        ref = self._write_reference(tmp_path)
        cache_path = tmp_path / 'cache.json'
        cache_path.write_text(json.dumps({
            'reference': {'path': str(ref), 'mtime': 0.0, 'size': 999},
            'files': {'/some/file.xml': {'lexicons': {}}},
        }))
        assert load_cache(cache_path, ref) == {}

    def test_corrupt_cache_file_returns_empty(self, tmp_path):
        ref = self._write_reference(tmp_path)
        cache_path = tmp_path / 'cache.json'
        cache_path.write_text('not json')
        assert load_cache(cache_path, ref) == {}


class TestReferencePairsCache:
    def _write_reference(self, tmp_path):
        ref = tmp_path / 'ref.xml'
        ref.write_text('reference')
        return ref

    def test_round_trip_through_cache_entry(self, tmp_path):
        ref = self._write_reference(tmp_path)
        pairs = {'hypernym': {('i1', 'i2'), ('i3', 'i4')}, 'mero_part': {('i5', 'i6')}}
        cache_path = tmp_path / 'cache.json'
        cache_path.write_text(json.dumps({
            'reference': {'path': str(ref), **_file_stat_key(ref)},
            'reference_pairs': reference_pairs_cache_entry('oewn:2025+', pairs),
            'files': {},
        }))
        assert load_cached_reference_pairs(cache_path, ref) == ('oewn:2025+', pairs)

    def test_cache_entry_is_json_serialisable_and_sorted(self):
        entry = reference_pairs_cache_entry('x:1', {'hypernym': {('i2', 'i1'), ('i1', 'i2')}})
        assert json.loads(json.dumps(entry)) == {
            'spec': 'x:1', 'pairs': {'hypernym': [['i1', 'i2'], ['i2', 'i1']]}}

    def test_missing_or_old_style_cache_returns_none(self, tmp_path):
        ref = self._write_reference(tmp_path)
        assert load_cached_reference_pairs(None, ref) is None
        cache_path = tmp_path / 'cache.json'
        cache_path.write_text(json.dumps({
            'reference': {'path': str(ref), **_file_stat_key(ref)},
            'files': {},
        }))
        assert load_cached_reference_pairs(cache_path, ref) is None

    def test_changed_reference_discards_pairs(self, tmp_path):
        ref = self._write_reference(tmp_path)
        cache_path = tmp_path / 'cache.json'
        cache_path.write_text(json.dumps({
            'reference': {'path': str(ref), 'mtime': 0.0, 'size': 999},
            'reference_pairs': reference_pairs_cache_entry('x:1', {'hypernym': {('i1', 'i2')}}),
            'files': {},
        }))
        assert load_cached_reference_pairs(cache_path, ref) is None


_LMF = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE LexicalResource SYSTEM "https://globalwordnet.github.io/schemas/WN-LMF-1.4.dtd">
<LexicalResource xmlns:dc="https://globalwordnet.github.io/schemas/dc/">
<Lexicon id="cand" label="c" language="xx" email="e" license="l" version="1" url="u">
<LexicalEntry id="e"><Lemma writtenForm="w" partOfSpeech="n"/><Sense id="s" synset="c-1"/></LexicalEntry>
<Synset id="c-1" ili="i1" partOfSpeech="n">
  <SynsetRelation relType="hypernym" target="c-2"/>
  <SynsetRelation relType="mero_part" target="c-2"/>
  <SynsetRelation relType="mero_part" target="c-3"/>
</Synset>
<Synset id="c-2" ili="i2" partOfSpeech="n"/>
<Synset id="c-3" ili="i3" partOfSpeech="n"/>
</Lexicon>
</LexicalResource>
"""


class TestComputeCandidateStats:
    def test_stats_per_lexicon_and_relation_type(self, tmp_path):
        path = tmp_path / 'cand.xml'
        path.write_text(_LMF)
        reference = {'hypernym': {('i1', 'i2')}, 'mero_part': {('i2', 'i1')}}
        per_lexicon, failures = compute_candidate_stats(path, reference)
        assert failures == []
        assert set(per_lexicon) == {'cand:1'}
        stats = per_lexicon['cand:1']
        assert (stats['hypernym'].agree, stats['hypernym'].reversed, stats['hypernym'].no_overlap) == (1, 0, 0)
        assert (stats['mero_part'].agree, stats['mero_part'].reversed, stats['mero_part'].no_overlap) == (0, 1, 1)

    def test_unparseable_file_is_reported_not_raised(self, tmp_path):
        path = tmp_path / 'broken.xml'
        path.write_text('<LexicalResource><Lexicon id="x" version="1"><Synset')
        per_lexicon, failures = compute_candidate_stats(path, {})
        assert per_lexicon == {}
        assert len(failures) == 1 and failures[0][0] == 'broken.xml'


class TestCandidateFiles:
    def _populate(self, tmp_path):
        raw = tmp_path / 'raw_wns'
        raw.mkdir()
        for name in ('mywn-1.0.xml', 'other-2.3.xml.gz', 'third.xml.xz', 'ref.xml.gz',
                     'sources.tsv', 'notes.txt'):
            (raw / name).write_text('x')
        return raw

    def test_without_toml_every_lmf_file_but_the_reference(self, tmp_path):
        raw = self._populate(tmp_path)
        got = candidate_files(raw, raw / 'ref.xml.gz')
        assert [p.name for p in got] == ['mywn-1.0.xml', 'other-2.3.xml.gz', 'third.xml.xz']

    def test_with_toml_only_the_files_step_2_would_convert(self, tmp_path):
        raw = self._populate(tmp_path)
        toml = tmp_path / 'wordnets.toml'
        toml.write_text('xx = ["https://example.org/mywn-1.0.xml"]\n'
                        'yy = ["https://example.org/other-2.3.xml.gz"]\n')
        got = candidate_files(raw, raw / 'ref.xml.gz', toml)
        assert [p.name for p in got] == ['mywn-1.0.xml', 'other-2.3.xml.gz']

    def test_reference_is_never_a_candidate_even_when_listed(self, tmp_path):
        raw = self._populate(tmp_path)
        toml = tmp_path / 'wordnets.toml'
        toml.write_text('en = ["https://example.org/ref.xml.gz"]\n'
                        'xx = ["https://example.org/mywn-1.0.xml"]\n')
        got = candidate_files(raw, raw / 'ref.xml.gz', toml)
        assert [p.name for p in got] == ['mywn-1.0.xml']

    def test_unlisted_files_are_the_lmf_files_left_over(self, tmp_path):
        raw = self._populate(tmp_path)
        toml = tmp_path / 'wordnets.toml'
        toml.write_text('xx = ["https://example.org/mywn-1.0.xml"]\n')
        ref = raw / 'ref.xml.gz'
        got = candidate_files(raw, ref, toml)
        assert [p.name for p in unlisted_files(raw, ref, got)] == ['other-2.3.xml.gz', 'third.xml.xz']
