"""Tests for scripts/check_all_relation_directions.py's pure logic.

Only build_swap_config(), _file_stat_key() and load_cache() are tested
here -- the wn-backed loading in compute_candidate_stats()/main() needs a
live wn database and real wordnet files, and is exercised manually
against the actual corpus instead (see the full-run findings in
scripts/check_all_relation_directions.py's docstring and the
plwordrelation_direction_checker project memory).
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
