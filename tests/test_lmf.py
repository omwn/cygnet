"""Tests for cyg/lmf.py: the relation tables and helpers shared by the
cleaning, merge and export sides."""

import json

import pytest
from lxml import etree

from cyg import converters, lmf, merge
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
    def test_converters_and_merge_share_the_tables(self):
        assert converters.CONCEPT_REVERSE_RELATIONS is lmf.CONCEPT_REVERSE_RELATIONS
        assert converters.SENSE_REVERSE_RELATIONS is lmf.SENSE_REVERSE_RELATIONS
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
