"""Tests for conversion_scripts/7_validate_and_export.py: the XML export
of the merged database."""

import importlib.util
import sqlite3
from collections import Counter
from pathlib import Path

import pytest
from conftest import build_test_db
from lxml import etree

_ROOT = Path(__file__).parent.parent
_WORDNETS_DIR = Path(__file__).parent / 'wordnets'
_XSD = _ROOT / 'cyg' / 'cygnet.xsd'


def _load_exporter():
    spec = importlib.util.spec_from_file_location(
        'validate_and_export', _ROOT / 'conversion_scripts' / '7_validate_and_export.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def exported(tmp_path_factory):
    """Export the wn-en + wn-fr test DB both with and without provenance;
    returns (exporter module, full xml path, small xml path)."""
    exp = _load_exporter()
    build_dir = tmp_path_factory.mktemp('export_build')
    db_path, prov_path = build_dir / 'cygnet.db', build_dir / 'provenance.db'
    build_test_db(db_path, prov_path, [_WORDNETS_DIR / 'wn-en.xml', _WORDNETS_DIR / 'wn-fr.xml'])
    prov_conn = sqlite3.connect(str(prov_path))
    prov = exp.load_provenance(prov_conn)
    prov_conn.close()
    conn = sqlite3.connect(str(db_path))
    full, small = build_dir / 'cygnet.xml', build_dir / 'cygnet_small.xml'
    exp.generate_xml(conn, prov, full, include_provenance=True)
    exp.generate_xml(conn, prov, small, include_provenance=False)
    conn.close()
    return exp, full, small


def _relation_types(xml_path: Path, tag: str) -> Counter:
    tree = etree.parse(str(xml_path))
    return Counter(el.get('relation_type') for el in tree.iter(tag))


class TestRelationDirection:
    def test_forward_concept_relations_only(self, exported):
        """The DB holds hypernym and hyponym; the XML holds hypernym only,
        with the current GWA names (the filter once listed class_hypernym
        and member_meronym and so dropped nearly every relation)."""
        _exp, full, _small = exported
        types = _relation_types(full, 'ConceptRelation')
        assert types['hypernym'] == 2
        assert 'hyponym' not in types

    def test_symmetric_relations_written_once(self, exported):
        """The DB holds a symmetric relation in both directions; the XML
        holds it once (the fixture's antonym is a sense relation)."""
        _exp, full, _small = exported
        concept_types = _relation_types(full, 'ConceptRelation')
        assert concept_types['similar'] == 1
        assert concept_types['eq_synonym'] == 1
        assert _relation_types(full, 'SenseRelation')['antonym'] == 1


class TestValidation:
    def test_both_variants_validate_against_the_xsd(self, exported):
        exp, full, small = exported
        assert exp.validate(full, _XSD)
        assert exp.validate(small, _XSD)

    def test_small_variant_has_no_provenance(self, exported):
        _exp, full, small = exported
        assert etree.parse(str(full)).find('.//Provenance') is not None
        assert etree.parse(str(small)).find('.//Provenance') is None
