"""Tests for scripts/check_relation_directions.py.

compute_direction_stats()/DirectionStats are the pure comparison logic;
lmf_ili_relation_pairs() is the XML reader that feeds it. The report in
run()/main() is exercised manually against real candidate files (see the
plWordnet meronym-direction investigation this script grew out of).
"""

from pathlib import Path
import importlib.util
import sys

_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "check_relation_directions.py"
_spec = importlib.util.spec_from_file_location("check_relation_directions", _SCRIPT_PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["check_relation_directions"] = _mod
_spec.loader.exec_module(_mod)

compute_direction_stats = _mod.compute_direction_stats
DirectionStats = _mod.DirectionStats
lmf_ili_relation_pairs = _mod.lmf_ili_relation_pairs
single_lexicon_pairs = _mod.single_lexicon_pairs


class TestComputeDirectionStats:
    def test_all_agree(self):
        candidate = {('i1', 'i2'), ('i3', 'i4')}
        reference = {('i1', 'i2'), ('i3', 'i4'), ('i5', 'i6')}
        stats = compute_direction_stats('hypernym', candidate, reference)
        assert stats.agree == 2
        assert stats.reversed == 0
        assert stats.no_overlap == 0

    def test_all_reversed(self):
        """The plwn meronym case: every comparable pair has swapped
        source/target relative to the reference."""
        candidate = {('i1', 'i2'), ('i3', 'i4')}
        reference = {('i2', 'i1'), ('i4', 'i3')}
        stats = compute_direction_stats('mero_part', candidate, reference)
        assert stats.agree == 0
        assert stats.reversed == 2
        assert stats.no_overlap == 0

    def test_no_overlap_when_reference_lacks_the_pair(self):
        candidate = {('i1', 'i2')}
        reference = {('i9', 'i10')}
        stats = compute_direction_stats('hypernym', candidate, reference)
        assert stats.agree == 0
        assert stats.reversed == 0
        assert stats.no_overlap == 1

    def test_mixed_classification(self):
        candidate = {('i1', 'i2'), ('i3', 'i4'), ('i5', 'i6')}
        reference = {('i1', 'i2'), ('i4', 'i3')}  # agree, reversed, no_overlap
        stats = compute_direction_stats('hypernym', candidate, reference)
        assert stats.agree == 1
        assert stats.reversed == 1
        assert stats.no_overlap == 1


class TestDirectionStats:
    def test_comparable_sums_agree_and_reversed(self):
        stats = DirectionStats('hypernym', agree=3, reversed=2, no_overlap=100)
        assert stats.comparable == 5

    def test_reversed_share_computed_correctly(self):
        stats = DirectionStats('mero_part', agree=1, reversed=3, no_overlap=11563)
        assert stats.reversed_share == 0.75

    def test_reversed_share_none_when_no_comparable_pairs(self):
        stats = DirectionStats('rare_rel', agree=0, reversed=0, no_overlap=5)
        assert stats.reversed_share is None

    def test_is_flagged_requires_both_min_pairs_and_threshold(self):
        # Meets the reversal threshold but too few comparable pairs to trust.
        small = DirectionStats('x', agree=0, reversed=5, no_overlap=0)
        assert not small.is_flagged(min_pairs=20, threshold=0.9)

        # Meets both: this is the plwn mero_part shape.
        big = DirectionStats('mero_part', agree=0, reversed=925, no_overlap=11563)
        assert big.is_flagged(min_pairs=20, threshold=0.9)

    def test_is_flagged_false_when_mostly_agreeing(self):
        # This is the plwn hypernym shape: comparable pairs exist, but the
        # mix (when not purely reversed) should not be flagged.
        stats = DirectionStats('hypernym', agree=60, reversed=8, no_overlap=211998)
        assert not stats.is_flagged(min_pairs=20, threshold=0.9)

    def test_is_flagged_false_with_no_comparable_pairs(self):
        stats = DirectionStats('x', agree=0, reversed=0, no_overlap=1000)
        assert not stats.is_flagged(min_pairs=20, threshold=0.9)


_LMF_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE LexicalResource SYSTEM "https://globalwordnet.github.io/schemas/WN-LMF-1.4.dtd">
<LexicalResource xmlns:dc="https://globalwordnet.github.io/schemas/dc/">
"""


def _lexicon(lid, version, body):
    return (f'<Lexicon id="{lid}" label="{lid}" language="xx" email="e" license="l" '
            f'version="{version}" url="u">\n'
            f'<LexicalEntry id="{lid}-e"><Lemma writtenForm="w" partOfSpeech="n"/>'
            f'<Sense id="{lid}-s" synset="{lid}-1"/></LexicalEntry>\n{body}</Lexicon>\n')


def _synset(sid, ili, relations=()):
    rels = ''.join(f'<SynsetRelation relType="{r}" target="{t}"/>' for r, t in relations)
    ili_attr = f' ili="{ili}"' if ili is not None else ''
    return f'<Synset id="{sid}"{ili_attr} partOfSpeech="n">{rels}</Synset>\n'


class TestLmfIliRelationPairs:
    def _write(self, tmp_path, lexicons, name='wn.xml'):
        path = tmp_path / name
        text = _LMF_HEADER + ''.join(lexicons) + '</LexicalResource>\n'
        if name.endswith('.gz'):
            import gzip
            path.write_bytes(gzip.compress(text.encode()))
        else:
            path.write_text(text)
        return path

    def test_pairs_keyed_by_lexicon_spec_and_relation_type(self, tmp_path):
        path = self._write(tmp_path, [_lexicon('a', '1.0',
            _synset('a-1', 'i1', [('hypernym', 'a-2'), ('mero_part', 'a-2')])
            + _synset('a-2', 'i2', [('mero_member', 'a-1')]))])
        assert lmf_ili_relation_pairs(path) == {
            'a:1.0': {
                'hypernym': {('i1', 'i2')},
                'mero_part': {('i1', 'i2')},
                'mero_member': {('i2', 'i1')},
            }
        }

    def test_only_requested_relation_types_are_collected(self, tmp_path):
        path = self._write(tmp_path, [_lexicon('a', '1.0',
            _synset('a-1', 'i1', [('hypernym', 'a-2'), ('antonym', 'a-2'), ('similar', 'a-2')])
            + _synset('a-2', 'i2'))])
        # symmetric types are not directed (and inverse-side types like
        # hyponym are not in the checked list), so not collected by default
        assert lmf_ili_relation_pairs(path) == {'a:1.0': {'hypernym': {('i1', 'i2')}}}
        assert lmf_ili_relation_pairs(path, ('similar',)) == {'a:1.0': {'similar': {('i1', 'i2')}}}

    def test_synsets_without_an_ili_or_with_a_proposed_one_are_skipped(self, tmp_path):
        path = self._write(tmp_path, [_lexicon('a', '1.0',
            _synset('a-1', 'i1', [('hypernym', 'a-2'), ('hypernym', 'a-3'), ('hypernym', 'a-4')])
            + _synset('a-2', None, [('hypernym', 'a-1')])
            + _synset('a-3', 'in', [('hypernym', 'a-1')])
            + _synset('a-4', '', [('hypernym', 'a-1')])
            + _synset('a-5', 'i5', [('hypernym', 'a-1')]))])
        assert lmf_ili_relation_pairs(path) == {'a:1.0': {'hypernym': {('i5', 'i1')}}}

    def test_target_missing_from_the_file_is_skipped(self, tmp_path):
        path = self._write(tmp_path, [_lexicon('a', '1.0',
            _synset('a-1', 'i1', [('hypernym', 'nowhere'), ('hypernym', 'a-2')])
            + _synset('a-2', 'i2'))])
        assert lmf_ili_relation_pairs(path) == {'a:1.0': {'hypernym': {('i1', 'i2')}}}

    def test_target_declared_later_in_the_file_is_resolved(self, tmp_path):
        path = self._write(tmp_path, [_lexicon('a', '1.0',
            _synset('a-1', 'i1', [('hypernym', 'a-9')])
            + _synset('a-9', 'i9'))])
        assert lmf_ili_relation_pairs(path)['a:1.0'] == {'hypernym': {('i1', 'i9')}}

    def test_several_lexicons_are_kept_apart_but_targets_resolve_across_them(self, tmp_path):
        path = self._write(tmp_path, [
            _lexicon('a', '1.0', _synset('a-1', 'i1', [('hypernym', 'b-1')])),
            _lexicon('b', '2.0', _synset('b-1', 'i2', [('mero_part', 'a-1')])),
        ])
        assert lmf_ili_relation_pairs(path) == {
            'a:1.0': {'hypernym': {('i1', 'i2')}},
            'b:2.0': {'mero_part': {('i2', 'i1')}},
        }

    def test_lexicon_without_relations_still_appears(self, tmp_path):
        path = self._write(tmp_path, [_lexicon('a', '1.0', _synset('a-1', 'i1'))])
        assert lmf_ili_relation_pairs(path) == {'a:1.0': {}}

    def test_duplicate_declarations_collapse_to_one_pair(self, tmp_path):
        path = self._write(tmp_path, [_lexicon('a', '1.0',
            _synset('a-1', 'i1', [('hypernym', 'a-2'), ('hypernym', 'a-2')])
            + _synset('a-2', 'i2'))])
        assert lmf_ili_relation_pairs(path)['a:1.0'] == {'hypernym': {('i1', 'i2')}}

    def test_gzipped_input(self, tmp_path):
        path = self._write(tmp_path, [_lexicon('a', '1.0',
            _synset('a-1', 'i1', [('hypernym', 'a-2')]) + _synset('a-2', 'i2'))], name='wn.xml.gz')
        assert lmf_ili_relation_pairs(path) == {'a:1.0': {'hypernym': {('i1', 'i2')}}}

    def test_single_lexicon_pairs_returns_spec_and_pairs(self, tmp_path):
        path = self._write(tmp_path, [_lexicon('a', '1.0',
            _synset('a-1', 'i1', [('hypernym', 'a-2')]) + _synset('a-2', 'i2'))])
        assert single_lexicon_pairs(path) == ('a:1.0', {'hypernym': {('i1', 'i2')}})

    def test_single_lexicon_pairs_rejects_multi_lexicon_files(self, tmp_path):
        path = self._write(tmp_path, [
            _lexicon('a', '1.0', _synset('a-1', 'i1')),
            _lexicon('b', '1.0', _synset('b-1', 'i2')),
        ])
        import pytest
        with pytest.raises(RuntimeError, match='2 lexicons'):
            single_lexicon_pairs(path)
