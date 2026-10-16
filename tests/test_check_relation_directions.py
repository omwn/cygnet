"""Tests for scripts/check_relation_directions.py's pure comparison logic.

Only compute_direction_stats()/DirectionStats are tested here -- the
wn-backed loading and reporting in run()/main() need a live wn database
and real wordnet files, and are exercised manually against the actual
candidate file instead (see the plWordnet meronym-direction investigation
this script grew out of).
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
