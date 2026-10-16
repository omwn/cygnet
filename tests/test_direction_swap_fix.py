"""Regression tests for the direction-swap fix in cyg/converters.py.

Several resources' exports have a relation type with source and target
systematically swapped relative to the GWA convention used everywhere
else in Cygnet. scripts/check_all_relation_directions.py finds these by
comparing each candidate wordnet's relations against OEWN on shared ILI
concepts, and writes a swap config (lexicon_id -> relation types to swap)
that WordNetToCygnetConverter reads. Found so far:

- plwn: mero_part (925/935, 98.9%) and mero_member (398/398, 100%)
  comparable pairs reversed. mero_substance was NOT one-sided enough
  (85/123, 69.1%) to swap without breaking the other 38 agreeing pairs --
  not covered here since it's a "don't swap" case, see
  test_plwn_mero_substance_is_not_swapped.
- latin-revised-la: mero_part (594/594), mero_member (170/170) AND
  mero_substance (59/59) ALL reversed -- cleaner than plwn's, no
  mixed-agreement group to protect.
- hypernym/hyponym (generic, not resource-specific here -- own-pt and
  UzWordnet-uz have ~100% reversed hypernym in real data, see
  scripts/check_all_relation_directions.py's findings) -- proves the
  mechanism isn't meronym-specific.

A source wordnet that declares a reversed mero_part typically declares
the equally reversed holo_part too (plwn and latin-revised-la both do).
Swapping only one side lets per-file inverse-generation (Stage 3 in
pass3_relations) regenerate the original bugged direction from the
untouched other side -- see
test_explicit_mero_and_holo_both_declared_yields_one_correct_relation.
"""

from pathlib import Path

import pytest
from lxml import etree

from cyg.converters import WordNetToCygnetConverter

_CILI_XML = """\
<?xml version='1.0' encoding='UTF-8'?>
<CygnetResource id="cili" version="1.0">
  <ConceptLayer>
    <Concept id="cili.i9001" ontological_category="NOUN" status="1"/>
    <Concept id="cili.i9002" ontological_category="NOUN" status="1"/>
  </ConceptLayer>
</CygnetResource>
"""


def _lmf_single(lexicon_id: str, rel_type: str) -> str:
    """Two synsets ('whole', the correct whole, and 'part', the correct
    part) with a single SynsetRelation from 'part' to 'whole' using
    *rel_type*."""
    return f"""\
<?xml version='1.0' encoding='UTF-8'?>
<LexicalResource xmlns:dc="https://globalwordnet.github.io/schemas/dc/">
  <Lexicon id="{lexicon_id}"
           label="t"
           language="pl"
           email="a@b.c"
           license="https://creativecommons.org/licenses/by/4.0/"
           version="0.1"
           url="https://example.org">
    <Synset id="s-whole" ili="i9001" partOfSpeech="n">
      <Definition language="en">the whole</Definition>
    </Synset>
    <Synset id="s-part" ili="i9002" partOfSpeech="n">
      <Definition language="en">the part</Definition>
      <SynsetRelation relType="{rel_type}" target="s-whole"/>
    </Synset>
  </Lexicon>
</LexicalResource>
"""


def _lmf_both_directions(fwd_type: str, inv_type: str, lexicon_id: str = 'plwn') -> str:
    """plwn's (and latin-revised-la's) actual export pattern: BOTH a
    relation type and its explicit GWA inverse are declared for the same
    pair, in the buggy (swapped) convention -- 'part' --fwd_type--> 'whole'
    AND 'whole' --inv_type--> 'part'. This is the scenario that originally
    caused the fix to regenerate the bugged direction via per-file
    inverse-generation when only one side was swapped: swapping fwd_type
    alone left the untouched inv_type declaration to auto-generate a
    fresh (still bugged) fwd_type edge in Stage 3.
    """
    return f"""\
<?xml version='1.0' encoding='UTF-8'?>
<LexicalResource xmlns:dc="https://globalwordnet.github.io/schemas/dc/">
  <Lexicon id="{lexicon_id}"
           label="t"
           language="pl"
           email="a@b.c"
           license="https://creativecommons.org/licenses/by/4.0/"
           version="0.1"
           url="https://example.org">
    <Synset id="s-whole" ili="i9001" partOfSpeech="n">
      <Definition language="en">the whole</Definition>
      <SynsetRelation relType="{inv_type}" target="s-part"/>
    </Synset>
    <Synset id="s-part" ili="i9002" partOfSpeech="n">
      <Definition language="en">the part</Definition>
      <SynsetRelation relType="{fwd_type}" target="s-whole"/>
    </Synset>
  </Lexicon>
</LexicalResource>
"""


def _convert(
    lmf_xml: str, tmp_path: Path, direction_swap_config: dict[str, frozenset[str]] = {},
) -> etree.Element:
    cili_path = tmp_path / 'cili.xml'
    lmf_path = tmp_path / 'lmf.xml'
    cili_path.write_text(_CILI_XML)
    lmf_path.write_text(lmf_xml)
    converter = WordNetToCygnetConverter(
        cili_path=str(cili_path), skip_cili_defns=False,
        direction_swap_config=direction_swap_config,
    )
    tree = converter.convert(str(lmf_path))
    return tree.getroot()


def _concept_relations(root: etree.Element, rel_type: str) -> list[tuple[str, str]]:
    return [
        (el.get('source'), el.get('target'))
        for el in root.iter('ConceptRelation')
        if el.get('relation_type') == rel_type
    ]


def _concept_relation(root: etree.Element, rel_type: str):
    rels = _concept_relations(root, rel_type)
    return rels[0] if rels else None


class TestPlwnDirectionSwap:
    PLWN_SWAPS = {'plwn': frozenset({'mero_part', 'holo_part', 'mero_member', 'holo_member'})}

    @pytest.mark.parametrize('rel_type', ['mero_part', 'mero_member'])
    def test_plwn_reversed_export_is_swapped(self, tmp_path, rel_type):
        """A lone mero_* declaration from plwn gets source and target
        swapped. (holo_part/holo_member are covered by the combined test
        below, since a lone holo_* declaration gets canonicalised to its
        mero_* form regardless of the swap -- the swap's effect on that
        canonicalised output is only visible when both sides are
        declared, as plwn actually does.)
        """
        root = _convert(_lmf_single('plwn', rel_type), tmp_path, self.PLWN_SWAPS)
        source, target = _concept_relation(root, rel_type)
        assert (source, target) == ('cili.i9001', 'cili.i9002'), (
            'expected source/target swapped relative to the raw declaration '
            '(i9002 part -> i9001 whole, in the file)'
        )

    @pytest.mark.parametrize('rel_type', ['mero_substance', 'holo_substance'])
    def test_plwn_mero_substance_is_not_swapped(self, tmp_path, rel_type):
        """mero_substance/holo_substance are deliberately excluded from
        PLWN_SWAPS -- plwn's export is not one-sidedly reversed for this
        type (only 69.1% of comparable pairs), so swapping would break
        the rest. plwn's output must be identical to any other resource's
        (i.e. the swap has no effect), whatever the canonicalised
        (source, target) for this relation type turns out to be.
        """
        plwn_dir, other_dir = tmp_path / 'plwn', tmp_path / 'other'
        plwn_dir.mkdir()
        other_dir.mkdir()
        plwn_root = _convert(_lmf_single('plwn', rel_type), plwn_dir, self.PLWN_SWAPS)
        other_root = _convert(_lmf_single('other-wn', rel_type), other_dir, self.PLWN_SWAPS)
        plwn_rels = {r for t in ('mero_substance', 'holo_substance')
                     for r in _concept_relations(plwn_root, t)}
        other_rels = {r for t in ('mero_substance', 'holo_substance')
                      for r in _concept_relations(other_root, t)}
        assert plwn_rels == other_rels
        assert plwn_rels, 'expected at least one relation to compare'

    @pytest.mark.parametrize('rel_type', ['mero_part', 'mero_member'])
    def test_other_resources_are_not_swapped(self, tmp_path, rel_type):
        """The fix is scoped per-resource by the config -- a resource not
        present in it must have its declared direction passed through
        unchanged, even if another resource's config has that type."""
        root = _convert(_lmf_single('other-wn', rel_type), tmp_path, self.PLWN_SWAPS)
        source, target = _concept_relation(root, rel_type)
        assert (source, target) == ('cili.i9002', 'cili.i9001')

    def test_plwn_hypernym_is_not_swapped(self, tmp_path):
        """The fix is scoped to the relation types present in the config
        -- plwn's hypernym relations (not similarly reversed: 99.15%
        agreement with OEWN) must be passed through unchanged."""
        root = _convert(_lmf_single('plwn', 'hypernym'), tmp_path, self.PLWN_SWAPS)
        source, target = _concept_relation(root, 'hypernym')
        assert (source, target) == ('cili.i9002', 'cili.i9001')

    @pytest.mark.parametrize('mero_type,holo_type', [
        ('mero_part', 'holo_part'),
        ('mero_member', 'holo_member'),
    ])
    def test_explicit_mero_and_holo_both_declared_yields_one_correct_relation(
        self, tmp_path, mero_type, holo_type,
    ):
        """Regression test for the real bug this fix originally missed:
        when plwn declares BOTH the mero_* and the explicit holo_* inverse
        for the same pair (its actual export pattern), swapping only
        mero_* left the untouched, equally-backwards holo_* declaration
        to regenerate the original bugged mero_* edge via per-file
        inverse-generation -- doubling the relation count and leaving the
        wrong direction present alongside the corrected one. After
        swapping both sides, exactly one mero_*-direction relation must
        exist, and it must have the correct (whole, part) direction.
        """
        root = _convert(_lmf_both_directions(mero_type, holo_type), tmp_path, self.PLWN_SWAPS)
        mero_rels = _concept_relations(root, mero_type)
        assert len(mero_rels) == 1, (
            f'expected exactly one {mero_type} relation, got {mero_rels} -- '
            'the untouched holo_* side likely regenerated a duplicate'
        )
        assert mero_rels[0] == ('cili.i9001', 'cili.i9002'), (
            'expected the whole (i9001) as source, the part (i9002) as target'
        )


class TestLatinRevisedLaDirectionSwap:
    """latin-revised-la has the same bug as plwn, but cleaner: ALL THREE
    meronym subtypes (mero_part, mero_member, mero_substance) are
    reversed (594/594, 170/170, 59/59 comparable pairs against OEWN --
    100% each), with no mixed-agreement group to protect like plwn's
    mero_substance. So unlike plwn, mero_substance IS swapped here."""

    LATIN_SWAPS = {'latin-revised-la': frozenset({
        'mero_part', 'holo_part',
        'mero_member', 'holo_member',
        'mero_substance', 'holo_substance',
    })}

    @pytest.mark.parametrize('rel_type', ['mero_part', 'mero_member', 'mero_substance'])
    def test_latin_revised_la_reversed_export_is_swapped(self, tmp_path, rel_type):
        root = _convert(_lmf_single('latin-revised-la', rel_type), tmp_path, self.LATIN_SWAPS)
        source, target = _concept_relation(root, rel_type)
        assert (source, target) == ('cili.i9001', 'cili.i9002'), (
            'expected source/target swapped relative to the raw declaration '
            '(i9002 part/substance -> i9001 whole, in the file)'
        )

    def test_latin_revised_la_hypernym_is_not_swapped(self, tmp_path):
        """hypernym is NOT affected (12,063/12,064 comparable pairs agree
        with OEWN) -- must be passed through unchanged."""
        root = _convert(_lmf_single('latin-revised-la', 'hypernym'), tmp_path, self.LATIN_SWAPS)
        source, target = _concept_relation(root, 'hypernym')
        assert (source, target) == ('cili.i9002', 'cili.i9001')

    @pytest.mark.parametrize('mero_type,holo_type', [
        ('mero_part', 'holo_part'),
        ('mero_member', 'holo_member'),
        ('mero_substance', 'holo_substance'),
    ])
    def test_explicit_mero_and_holo_both_declared_yields_one_correct_relation(
        self, tmp_path, mero_type, holo_type,
    ):
        """Same Stage-3-regeneration regression as plwn's, for all three
        subtypes (latin-revised-la swaps mero_substance/holo_substance
        too, unlike plwn)."""
        root = _convert(
            _lmf_both_directions(mero_type, holo_type, lexicon_id='latin-revised-la'),
            tmp_path, self.LATIN_SWAPS,
        )
        mero_rels = _concept_relations(root, mero_type)
        assert len(mero_rels) == 1, (
            f'expected exactly one {mero_type} relation, got {mero_rels} -- '
            'the untouched holo_* side likely regenerated a duplicate'
        )
        assert mero_rels[0] == ('cili.i9001', 'cili.i9002'), (
            'expected the whole (i9001) as source, the part (i9002) as target'
        )


class TestHypernymDirectionSwapIsGeneric:
    """The swap mechanism isn't meronym-specific -- own-pt and
    UzWordnet-uz both have ~100% reversed hypernym vs OEWN in real data
    (see scripts/check_all_relation_directions.py's findings), so the
    config can mark hypernym/hyponym for a resource exactly like it marks
    mero_part/holo_part."""

    HYPERNYM_SWAPS = {'reversed-wn': frozenset({'hypernym', 'hyponym'})}

    def test_hypernym_is_swapped_when_configured(self, tmp_path):
        root = _convert(_lmf_single('reversed-wn', 'hypernym'), tmp_path, self.HYPERNYM_SWAPS)
        source, target = _concept_relation(root, 'hypernym')
        assert (source, target) == ('cili.i9001', 'cili.i9002')

    def test_explicit_hypernym_and_hyponym_both_declared_yields_one_correct_relation(
        self, tmp_path,
    ):
        """Mirrors plwn's mero_part/holo_part regression: a resource that
        explicitly declares both hypernym and its GWA inverse hyponym for
        the same pair must not end up with the bugged direction
        regenerated from the untouched side."""
        root = _convert(
            _lmf_both_directions('hypernym', 'hyponym', lexicon_id='reversed-wn'),
            tmp_path, self.HYPERNYM_SWAPS,
        )
        hyp_rels = _concept_relations(root, 'hypernym')
        assert len(hyp_rels) == 1, (
            f'expected exactly one hypernym relation, got {hyp_rels} -- '
            'the untouched hyponym side likely regenerated a duplicate'
        )
        assert hyp_rels[0] == ('cili.i9001', 'cili.i9002')

    def test_other_resources_hypernym_is_not_swapped(self, tmp_path):
        root = _convert(_lmf_single('other-wn', 'hypernym'), tmp_path, self.HYPERNYM_SWAPS)
        source, target = _concept_relation(root, 'hypernym')
        assert (source, target) == ('cili.i9002', 'cili.i9001')
