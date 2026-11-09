"""Tests for MergeBuilder.process_lmf_file(): merging a WN-LMF file
directly, alongside the Cygnet XML fixtures (tests/wordnets/lmf-xx.xml)."""

import sqlite3
from pathlib import Path

import pytest
from conftest import build_test_db

from cyg.merge import root_tag

_WORDNETS_DIR = Path(__file__).parent / 'wordnets'


@pytest.fixture(scope='module')
def dbs(tmp_path_factory):
    """wn-en.xml (Cygnet XML) then lmf-xx.xml (WN-LMF) merged into one DB;
    returns (cygnet connection, provenance connection)."""
    build_dir = tmp_path_factory.mktemp('lmf_merge')
    db_path, prov_path = build_dir / 'cygnet.db', build_dir / 'provenance.db'
    build_test_db(db_path, prov_path, [_WORDNETS_DIR / 'wn-en.xml', _WORDNETS_DIR / 'lmf-xx.xml'])
    conn = sqlite3.connect(str(db_path))
    conn.execute(f"ATTACH '{prov_path}' AS p")
    return conn


def _synset(conn, ili):
    return conn.execute('SELECT rowid FROM synsets WHERE ili = ?', (ili,)).fetchone()[0]


def _senses_of(conn, lemma, lang='xx'):
    """(synset rowid, sense rowid) of every sense of *lemma* in *lang*."""
    return conn.execute('''
        SELECT s.synset_rowid, s.rowid FROM senses s
        JOIN entries e ON e.rowid = s.entry_rowid
        JOIN forms f ON f.entry_rowid = e.rowid AND f.rank = 0
        JOIN languages l ON l.rowid = e.language_rowid
        WHERE f.form = ? AND l.code = ?''', (lemma, lang)).fetchall()


def _prov(conn, table, rowid):
    return conn.execute('''
        SELECT pr.code, pv.version, pv.original_id FROM p.provenance pv
        JOIN p.prov_tables pt ON pt.rowid = pv.table_rowid
        JOIN p.prov_resources pr ON pr.rowid = pv.resource_rowid
        WHERE pt.name = ? AND pv.item_rowid = ? ORDER BY pv.rowid''', (table, rowid)).fetchall()


def _relations(conn, table, source_rowid):
    return conn.execute(f'''
        SELECT rt.type, r.target_rowid, r.subtype, r.subtype_label FROM {table} r
        JOIN relation_types rt ON rt.rowid = r.type_rowid
        WHERE r.source_rowid = ? ORDER BY rt.type''', (source_rowid,)).fetchall()


class TestDispatch:
    def test_root_tag_tells_the_formats_apart(self):
        assert root_tag(_WORDNETS_DIR / 'lmf-xx.xml') == 'LexicalResource'
        assert root_tag(_WORDNETS_DIR / 'wn-en.xml') == 'CygnetResource'


class TestResource:
    def test_lexicon_header_becomes_the_resource(self, dbs):
        row = dbs.execute('''
            SELECT r.version, r.label, l.code, r.url, r.licence, r.email, r.citation
            FROM resources r JOIN languages l ON l.rowid = r.language_rowid
            WHERE r.code = 'lmf-xx' ''').fetchone()
        assert row == ('1.0', 'Test LMF WordNet', 'xx', 'https://example.org/lmf-xx',
                       'https://creativecommons.org/licenses/by/4.0/', 'x@example.org',
                       'Bond, F. 2026. A test LMF wordnet.')


class TestSynsets:
    def test_shared_ili_joins_the_existing_concept(self, dbs):
        assert dbs.execute("SELECT COUNT(*) FROM synsets WHERE ili = 'i3'").fetchone()[0] == 1
        # the concept came from wn-en; the LMF file adds no synset provenance for it
        assert all(code == 'wn-en' for code, _v, _o in _prov(dbs, 'synsets', _synset(dbs, 'i3')))

    def test_new_ili_creates_a_concept_with_provenance(self, dbs):
        rowid = _synset(dbs, 'i10')
        assert _prov(dbs, 'synsets', rowid) == [('lmf-xx', '1.0', 'xx-cat')]
        assert dbs.execute('SELECT pos FROM synsets WHERE rowid = ?', (rowid,)).fetchone()[0] == 'NOUN'

    def test_synset_without_ili_is_a_local_concept(self, dbs):
        (synset_rowid, _sense), = _senses_of(dbs, 'aave')
        assert dbs.execute('SELECT ili FROM synsets WHERE rowid = ?', (synset_rowid,)).fetchone()[0] is None
        assert _prov(dbs, 'synsets', synset_rowid) == [('lmf-xx', '1.0', 'xx-ghost')]


class TestEntriesAndSenses:
    def test_lemma_forms_and_pronunciation(self, dbs):
        (synset_rowid, sense_rowid), = _senses_of(dbs, 'koira')
        assert synset_rowid == _synset(dbs, 'i3')
        entry_rowid = dbs.execute('SELECT entry_rowid FROM senses WHERE rowid = ?',
                                  (sense_rowid,)).fetchone()[0]
        forms = dbs.execute('SELECT form, rank FROM forms WHERE entry_rowid = ? ORDER BY rank',
                            (entry_rowid,)).fetchall()
        assert forms == [('koira', 0), ('koirat', 1)]
        prons = dbs.execute('''
            SELECT p.variety, p.pronunciation FROM pronunciations p
            JOIN forms f ON f.rowid = p.form_rowid WHERE f.entry_rowid = ?''',
            (entry_rowid,)).fetchall()
        assert prons == [('xx', 'ˈkoi̯rɑ')]
        assert dbs.execute('SELECT pos FROM entries WHERE rowid = ?', (entry_rowid,)).fetchone()[0] == 'NOUN'

    def test_second_entry_with_the_same_forms_is_absorbed_with_its_ids(self, dbs):
        (_synset, sense_rowid), = _senses_of(dbs, 'koira')
        entry_rowid = dbs.execute('SELECT entry_rowid FROM senses WHERE rowid = ?',
                                  (sense_rowid,)).fetchone()[0]
        assert _prov(dbs, 'entries', entry_rowid) == [
            ('lmf-xx', '1.0', 'xx-koira-n'),
            ('lmf-xx', '1.0', 'xx-koira-n-dup'),
            ('lmf-xx', '1.0', 'xx-koira-n-old'),   # from note="merged=..."
        ]
        # the duplicate's sense collapsed into the first; its id is not recorded
        assert _prov(dbs, 'senses', sense_rowid) == [('lmf-xx', '1.0', 'xx-koira-n-1')]

    def test_unlexicalized_and_dangling_senses_are_skipped(self, dbs):
        assert _senses_of(dbs, 'gap') == []
        assert _senses_of(dbs, 'orpo') == []


class TestDefinitions:
    def test_definition_language_defaults_to_the_lexicon(self, dbs):
        rows = dbs.execute('''
            SELECT l.code, d.definition FROM definitions d
            JOIN languages l ON l.rowid = d.language_rowid
            WHERE d.synset_rowid = ? ORDER BY l.code''', (_synset(dbs, 'i3'),)).fetchall()
        assert ('xx', 'kotieläin, joka haukkuu') in rows

    def test_english_definition_joins_the_existing_one(self, dbs):
        """The first definition per (concept, language) wins; a later one
        only adds its provenance to it."""
        rows = dbs.execute('''
            SELECT d.rowid, pr.code FROM definitions d
            JOIN languages l ON l.rowid = d.language_rowid
            JOIN p.provenance pv ON pv.item_rowid = d.rowid
            JOIN p.prov_tables pt ON pt.rowid = pv.table_rowid AND pt.name = 'definitions'
            JOIN p.prov_resources pr ON pr.rowid = pv.resource_rowid
            WHERE d.synset_rowid = ? AND l.code = 'en' ORDER BY pv.rowid''',
            (_synset(dbs, 'i3'),)).fetchall()
        assert len({rowid for rowid, _code in rows}) == 1
        assert [code for _rowid, code in rows] == ['wn-en', 'lmf-xx']


class TestExamples:
    def test_sense_examples_with_and_without_offsets(self, dbs):
        (_synset, sense_rowid), = _senses_of(dbs, 'koira')
        examples = dbs.execute('''
            SELECT e.rowid, e.example FROM examples e
            JOIN sense_examples se ON se.example_rowid = e.rowid
            WHERE se.sense_rowid = ? ORDER BY e.rowid''', (sense_rowid,)).fetchall()
        assert [text for _r, text in examples] == [
            'Koira haukkuu.', 'Se on eläin.', 'Iso koira juoksee.']
        spans = {
            text: dbs.execute('''SELECT start_offset, end_offset, sense_rowid
                                 FROM example_annotations WHERE example_rowid = ?''',
                              (rowid,)).fetchall()
            for rowid, text in examples
        }
        assert spans['Koira haukkuu.'] == [(0, 5, sense_rowid)]
        assert spans['Se on eläin.'] == []
        assert spans['Iso koira juoksee.'] == [(4, 9, sense_rowid)]

    def test_synset_level_example_is_not_merged(self, dbs):
        assert dbs.execute("SELECT COUNT(*) FROM examples WHERE example LIKE '%synsetille%'"
                           ).fetchone()[0] == 0


class TestRelations:
    def test_forward_relation_gets_its_inverse_once(self, dbs):
        dog, animal = _synset(dbs, 'i3'), _synset(dbs, 'i2')
        assert ('hypernym', animal, None, None) in _relations(dbs, 'synset_relations', dog)
        hyponyms = [r for r in _relations(dbs, 'synset_relations', animal) if r[0] == 'hyponym']
        assert hyponyms.count(('hyponym', dog, None, None)) == 1

    def test_other_with_uri_dc_type_uses_the_local_name(self, dbs):
        (ghost, _s), = _senses_of(dbs, 'aave')
        rels = _relations(dbs, 'synset_relations', _synset(dbs, 'i3'))
        assert ('pet', ghost, None, None) in rels

    def test_finer_dc_type_on_a_known_type_is_the_subtype(self, dbs):
        (ghost, _s), = _senses_of(dbs, 'aave')
        rels = _relations(dbs, 'synset_relations', _synset(dbs, 'i10'))
        assert ('diminutive', ghost, 'xx:56', 'pikku') in rels

    def test_inverse_only_type_is_stored_the_forward_way_round(self, dbs):
        """'cat holo_part animal' in the file is 'animal mero_part cat' in the
        DB, with holo_part as its generated inverse, not a symmetric pair."""
        cat, animal = _synset(dbs, 'i10'), _synset(dbs, 'i2')
        assert ('mero_part', cat, None, None) in _relations(dbs, 'synset_relations', animal)
        assert ('holo_part', animal, None, None) in _relations(dbs, 'synset_relations', cat)
        assert ('holo_part', cat, None, None) not in _relations(dbs, 'synset_relations', animal)
        assert ('mero_part', animal, None, None) not in _relations(dbs, 'synset_relations', cat)

    def test_relation_to_unknown_synset_is_skipped(self, dbs):
        rels = _relations(dbs, 'synset_relations', _synset(dbs, 'i3'))
        assert len([r for r in rels if r[0] == 'hypernym']) == 1

    def test_sense_relations_resolve_after_the_whole_file(self, dbs):
        (_d, koira), = _senses_of(dbs, 'koira')
        (_c, kissa), = _senses_of(dbs, 'kissa')
        assert ('antonym', kissa, None, None) in _relations(dbs, 'sense_relations', koira)
        assert ('antonym', koira, None, None) in _relations(dbs, 'sense_relations', kissa)
        assert ('derivation', koira, None, None) in _relations(dbs, 'sense_relations', kissa)
        assert ('derivation', kissa, None, None) in _relations(dbs, 'sense_relations', koira)
