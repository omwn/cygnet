"""Tests for cyg/lmf.py: the relation tables and helpers shared by the
cleaning, merge and export sides."""

import json

import pytest
from lxml import etree

from cyg import lmf, merge
from cyg.lmf import (
    CONCEPT_INVERSE_ONLY_TYPES,
    CONCEPT_REVERSE_RELATIONS,
    CONCEPT_SYMMETRIC_TYPES,
    SENSE_INVERSE_ONLY_TYPES,
    SENSE_REVERSE_RELATIONS,
    below_confidence_threshold,
    inverse_only_types,
    is_forward_relation,
    load_direction_swap_config,
    symmetric_types,
)


class TestRelationTables:
    def test_merge_shares_the_tables(self):
        assert merge.INVERSE_CONCEPT_RELATIONS is lmf.INVERSE_CONCEPT_RELATIONS
        assert merge.INVERSE_SENSE_RELATIONS is lmf.INVERSE_SENSE_RELATIONS

    def test_inverse_dicts_drop_only_the_none_entries(self):
        assert lmf.INVERSE_CONCEPT_RELATIONS['hypernym'] == 'hyponym'
        assert lmf.INVERSE_CONCEPT_RELATIONS['antonym'] == 'antonym'
        assert 'other' not in lmf.INVERSE_CONCEPT_RELATIONS
        assert 'pertainym' not in lmf.INVERSE_SENSE_RELATIONS

    def test_inverse_only_types_are_values_that_are_not_keys(self):
        reverse = {'hypernym': 'hyponym', 'antonym': 'antonym', 'other': None,
                   'both': 'ways', 'ways': 'both'}
        assert inverse_only_types(reverse) == {'hyponym'}
        assert symmetric_types(reverse) == {'antonym'}

    def test_known_inverse_only_and_symmetric_types(self):
        assert {'hyponym', 'holo_part', 'is_caused_by', 'has_domain_topic'} <= CONCEPT_INVERSE_ONLY_TYPES
        assert not {'hypernym', 'mero_part', 'antonym', 'other'} & CONCEPT_INVERSE_ONLY_TYPES
        assert {'antonym', 'similar', 'eq_synonym', 'derivation'} <= CONCEPT_SYMMETRIC_TYPES
        assert {'has_feminine', 'is_exemplified_by'} <= SENSE_INVERSE_ONLY_TYPES


class TestIsForwardRelation:
    def test_forward_type_is_written_whatever_the_ids(self):
        assert is_forward_relation('hypernym', 5, 2, CONCEPT_REVERSE_RELATIONS)
        assert is_forward_relation('mero_part', 2, 5, CONCEPT_REVERSE_RELATIONS)

    def test_inverse_only_type_is_never_written(self):
        assert not is_forward_relation('hyponym', 2, 5, CONCEPT_REVERSE_RELATIONS)
        assert not is_forward_relation('has_feminine', 2, 5, SENSE_REVERSE_RELATIONS)

    def test_symmetric_type_is_written_once_from_the_lower_id(self):
        assert is_forward_relation('antonym', 2, 5, CONCEPT_REVERSE_RELATIONS)
        assert not is_forward_relation('antonym', 5, 2, CONCEPT_REVERSE_RELATIONS)
        assert not is_forward_relation('similar', 3, 3, CONCEPT_REVERSE_RELATIONS)

    def test_type_without_inverse_and_unknown_type_are_written(self):
        assert is_forward_relation('other', 5, 2, CONCEPT_REVERSE_RELATIONS)
        assert is_forward_relation('pertainym', 5, 2, SENSE_REVERSE_RELATIONS)
        assert is_forward_relation('plwn:19', 5, 2, CONCEPT_REVERSE_RELATIONS)


class TestBelowConfidenceThreshold:
    @pytest.mark.parametrize('xml, expected', [
        ('<Synset id="s"/>', False),
        ('<Synset id="s" confidenceScore="1.0"/>', False),
        ('<Synset id="s" confidenceScore="0.6"/>', False),
        ('<Synset id="s" confidenceScore="0.5"/>', True),
        ('<Synset id="s" confidenceScore="high"/>', False),
        ('<Synset id="s" xmlns:dc="https://globalwordnet.github.io/schemas/dc/" dc:confidence="0.2"/>', True),
    ])
    def test_threshold(self, xml, expected):
        assert below_confidence_threshold(etree.fromstring(xml)) is expected


class TestLoadDirectionSwapConfig:
    def test_missing_file_means_no_swaps(self, tmp_path):
        assert load_direction_swap_config(tmp_path / 'none.json') == ({}, {})

    def test_swapped_types_and_measured_stats(self, tmp_path):
        path = tmp_path / 'swaps.json'
        path.write_text(json.dumps({
            'plwn': {
                'mero_part': {'reversed': 925, 'comparable': 935, 'reversed_share': 0.989, 'swapped': True},
                'holo_part': {'reversed': None, 'comparable': None, 'reversed_share': None, 'swapped': True},
                'hypernym': {'reversed': 1, 'comparable': 900, 'reversed_share': 0.001, 'swapped': False},
            },
        }))
        swaps, stats = load_direction_swap_config(path)
        assert swaps == {'plwn': frozenset({'mero_part', 'holo_part'})}
        assert stats == {'plwn': {
            'mero_part': {'reversed': 925, 'comparable': 935, 'reversed_share': 0.989},
            'hypernym': {'reversed': 1, 'comparable': 900, 'reversed_share': 0.001},
        }}


class TestLmfReadingHelpers:
    def test_pos_label(self):
        assert [lmf.pos_label(p) for p in ('n', 's', 'u', '', None, 'zz')] == [
            'NOUN', 'ADJ', 'UNK', 'UNK', 'UNK', 'UNK']

    def test_entry_written_forms_lemma_first_then_sorted_distinct(self):
        entry = etree.fromstring(
            '<LexicalEntry id="e"><Lemma writtenForm="zebra" partOfSpeech="n"/>'
            '<Form writtenForm="zebras"/><Form writtenForm="Zebra"/><Form writtenForm="zebra"/>'
            '<Form/></LexicalEntry>')
        assert lmf.entry_written_forms(entry) == ['zebra', 'Zebra', 'zebras']
        assert lmf.entry_written_forms(etree.fromstring('<LexicalEntry id="e"/>')) == []

    def test_lexeme_id_scheme(self):
        assert lmf.lexeme_id('en', 'NOUN', ['dog', 'dogs']) == 'en.NOUN.dog-dogs'

    def test_relation_type_and_subtype(self):
        dc = 'xmlns:dc="https://globalwordnet.github.io/schemas/dc/"'
        plain = etree.fromstring(f'<SynsetRelation {dc} relType="hypernym" target="t"/>')
        other = etree.fromstring(
            f'<SynsetRelation {dc} relType="other" dc:type="https://w3id.org/own/schema/agent" target="t"/>')
        finer = etree.fromstring(
            f'<SynsetRelation {dc} relType="diminutive" dc:type="plwn:56" dc:description="x" target="t"/>')
        assert lmf.relation_type(plain) == 'hypernym'
        assert lmf.relation_type(other) == 'agent'
        assert lmf.relation_subtype(other, 'agent') == (None, None)
        assert lmf.relation_type(finer) == 'diminutive'
        assert lmf.relation_subtype(finer, 'diminutive') == ('plwn:56', 'x')

    def test_parse_note_keeps_source_keys_and_ignores_bare_notes(self):
        assert lmf.parse_note('R=1;F=2;offsets=0,3') == {'R': '1', 'F': '2', 'offsets': '0,3'}
        assert lmf.parse_note('@is_primary') == {}
        assert lmf.parse_note('offsets=0,3;offsets=5,8') == {'offsets': '0,3'}
        assert lmf.parse_note(None) == {}

    @pytest.mark.parametrize('note, expected', [
        ('offsets=0,5', [(0, 5)]),
        ('offsets=0,5 6,8', [(0, 5), (6, 8)]),
        ('R=1;offsets=6,8', [(6, 8)]),
        ('offsets=5,0', []),          # reversed
        ('offsets=0,99', []),         # past the end
        ('offsets=3,3', []),          # empty span
        ('offsets=a,b', []),          # malformed
        ('offsets=', []),
        (None, []),
    ])
    def test_note_offsets(self, note, expected):
        assert lmf.note_offsets(note, 'Koira haukkuu.') == expected


class TestCanonicalRelation:
    def test_inverse_only_type_is_turned_round(self):
        assert lmf.canonical_relation('hyponym', 'a', 'b', lmf.CONCEPT_FORWARD_OF_INVERSE) == ('hypernym', 'b', 'a')
        assert lmf.canonical_relation('has_feminine', 1, 2, lmf.SENSE_FORWARD_OF_INVERSE) == ('feminine', 2, 1)

    def test_forward_symmetric_and_unknown_types_are_kept(self):
        for rel in ('hypernym', 'antonym', 'other', 'plwn:19'):
            assert lmf.canonical_relation(rel, 'a', 'b', lmf.CONCEPT_FORWARD_OF_INVERSE) == (rel, 'a', 'b')

    def test_forward_of_inverse_covers_exactly_the_inverse_only_types(self):
        assert set(lmf.CONCEPT_FORWARD_OF_INVERSE) == lmf.CONCEPT_INVERSE_ONLY_TYPES
        assert lmf.CONCEPT_FORWARD_OF_INVERSE['is_caused_by'] == 'causes'
