"""Tests for cyg/export.py and conversion_scripts/7_validate_and_export.py:
the WN-LMF export of the merged database."""

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from conftest import build_test_db
from lxml import etree

from cyg.export import DC_NS, LmfExporter, load_provenance, source_tokens, validate_dtd

_ROOT = Path(__file__).parent.parent
_WORDNETS_DIR = Path(__file__).parent / 'wordnets'
_DTD = _ROOT / 'cyg' / 'WN-LMF-1.4.dtd'
DC = f'{{{DC_NS}}}'


def _load_script():
    spec = importlib.util.spec_from_file_location(
        'validate_and_export', _ROOT / 'conversion_scripts' / '7_validate_and_export.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def exported(tmp_path_factory):
    """wn-en + wn-fr + lmf-xx merged, then exported with and without
    provenance; returns (full path, small path, cygnet.db connection)."""
    build_dir = tmp_path_factory.mktemp('export_build')
    db_path, prov_path = build_dir / 'cygnet.db', build_dir / 'provenance.db'
    build_test_db(db_path, prov_path, [_WORDNETS_DIR / 'wn-en.xml', _WORDNETS_DIR / 'wn-fr.xml',
                                       _WORDNETS_DIR / 'lmf-xx.xml'])
    prov_conn = sqlite3.connect(str(prov_path))
    prov = load_provenance(prov_conn)
    prov_conn.close()
    conn = sqlite3.connect(str(db_path))
    full, small = build_dir / 'cygnet.xml', build_dir / 'cygnet_small.xml'
    LmfExporter(conn, prov).write(full)
    LmfExporter(conn, None).write(small)
    return full, small, conn


def _tree(path: Path):
    return etree.parse(str(path), etree.XMLParser(load_dtd=False, no_network=True,
                                                  resolve_entities=False))


class TestStructure:
    def test_concepts_lexicon_and_one_lexicon_per_language(self, exported):
        full, _small, _conn = exported
        lexicons = _tree(full).getroot().findall('Lexicon')
        ids = [lx.get('id') for lx in lexicons]
        assert ids[0] == 'cygnet' and lexicons[0].get('language') == 'mul'
        assert set(ids[1:]) == {'cygnet-en', 'cygnet-fr', 'cygnet-xx'}
        for lx in lexicons[1:]:
            assert lx.find('Requires').get('ref') == 'cygnet'

    def test_concepts_carry_definitions_and_forward_relations(self, exported):
        full, _small, conn = exported
        root = _tree(full).getroot()
        concepts = root.find('Lexicon[@id="cygnet"]')
        dog_rowid = conn.execute("SELECT rowid FROM synsets WHERE ili = 'i3'").fetchone()[0]
        dog = concepts.find(f'Synset[@id="cygnet-{dog_rowid}"]')
        assert dog.get('ili') == 'i3' and dog.get(f'{DC}identifier') == f'cyg:{dog_rowid}'
        langs = {d.get('language') for d in dog.findall('Definition')}
        assert {'en', 'xx'} <= langs   # wn-fr's gloss copies the English one and is dropped
        rels = [(r.get('relType'), r.get('target')) for r in dog.findall('SynsetRelation')]
        animal_rowid = conn.execute("SELECT rowid FROM synsets WHERE ili = 'i2'").fetchone()[0]
        assert ('hypernym', f'cygnet-{animal_rowid}') in rels
        animal = concepts.find(f'Synset[@id="cygnet-{animal_rowid}"]')
        assert all(r.get('relType') != 'hyponym' for r in animal.findall('SynsetRelation'))

    def test_unknown_relation_type_becomes_other_with_dc_type(self, exported):
        full, _small, _conn = exported
        root = _tree(full).getroot()
        others = [r for r in root.iter('SynsetRelation') if r.get('relType') == 'other']
        assert any(r.get(f'{DC}type') == 'pet' for r in others)   # lmf-xx's dc:type relation
        dims = [r for r in root.iter('SynsetRelation') if r.get('relType') == 'diminutive']
        assert dims and dims[0].get(f'{DC}type') == 'xx:56' and dims[0].get(f'{DC}description') == 'pikku'

    def test_entries_senses_and_examples_with_offsets(self, exported):
        full, _small, _conn = exported
        root = _tree(full).getroot()
        xx = root.find('Lexicon[@id="cygnet-xx"]')
        koira = next(e for e in xx.iter('LexicalEntry') if e.find('Lemma').get('writtenForm') == 'koira')
        assert koira.find('Lemma').get('partOfSpeech') == 'n'
        assert [f.get('writtenForm') for f in koira.findall('Form')] == ['koirat']
        assert koira.find('Lemma/Pronunciation').text == 'ˈkoi̯rɑ'
        sense = koira.find('Sense')
        assert sense.get('synset').startswith('cygnet-xx-')
        examples = {e.text: e.get('note') for e in sense.findall('Example')}
        assert examples['Koira haukkuu.'] == 'offsets=0,5'
        assert examples['Se on eläin.'] is None
        # the language lexicon has a Synset for each concept it lexicalises, with its own definition
        synset = xx.find(f'Synset[@id="{sense.get("synset")}"]')
        assert synset.get('ili') == 'i3'
        assert [d.text for d in synset.findall('Definition')] == ['kotieläin, joka haukkuu']

    def test_symmetric_sense_relation_written_once(self, exported):
        full, _small, _conn = exported
        root = _tree(full).getroot()
        antonyms = [r for r in root.iter('SenseRelation') if r.get('relType') == 'antonym']
        assert len(antonyms) == 2   # gleaming/glowing (en) and koira/kissa (xx)


class TestProvenance:
    def test_source_tokens(self):
        assert source_tokens([('oewn', '2025+', 'oewn-dog-n'), ('plwn', '5.0', None)]) == \
            'oewn:2025+:oewn-dog-n plwn:5.0'

    def test_full_variant_has_sources_small_has_none(self, exported):
        full, small, _conn = exported
        full_root, small_root = _tree(full).getroot(), _tree(small).getroot()
        dog = next(s for s in full_root.find('Lexicon[@id="cygnet"]').iter('Synset') if s.get('ili') == 'i3')
        assert 'wn-en:1.0:oewn-dog-n' in dog.get(f'{DC}source')
        assert not any(e.get(f'{DC}source') for e in small_root.iter())


class TestValidity:
    def test_both_variants_validate_against_the_dtd(self, exported):
        full, small, _conn = exported
        assert validate_dtd(full, _DTD) == []
        assert validate_dtd(small, _DTD) == []

    def test_script_validate_agrees(self, exported):
        full, _small, _conn = exported
        assert _load_script().validate(full)

    def test_wn_loads_and_adds_the_export(self, exported, tmp_path):
        """The export is a usable wn resource: the concepts lexicon and the
        language lexicons that Require it all install."""
        import wn
        import wn.lmf
        _full, small, _conn = exported
        resource = wn.lmf.load(str(small), progress_handler=None)
        ids = [lx['id'] for lx in resource['lexicons']]
        assert ids[0] == 'cygnet' and 'cygnet-xx' in ids
        wn.config.data_directory = str(tmp_path / 'wn')
        wn.add(str(small), progress_handler=None)
        installed = {lx.id for lx in wn.lexicons()}
        assert {'cygnet', 'cygnet-en', 'cygnet-fr', 'cygnet-xx'} <= installed
        assert wn.Wordnet(lexicon='cygnet-xx').synsets('koira')
