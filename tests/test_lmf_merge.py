"""Tests for MergeBuilder.process_lmf_file(): merging a WN-LMF file
directly, alongside the Cygnet XML fixtures (tests/wordnets/lmf-xx.xml)."""

import sqlite3
from pathlib import Path

import pytest
from conftest import build_test_db

from cyg.merge import lmf_concept_index, root_tag

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

    def test_dangling_sense_skipped_unlexicalized_kept_outside_a_mul_release(self, dbs):
        assert _senses_of(dbs, 'orpo') == []
        (synset_rowid, _s), = _senses_of(dbs, 'gap')
        assert synset_rowid == _synset(dbs, 'i2')


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


_MUL_HEADER = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               '<!DOCTYPE LexicalResource SYSTEM "https://globalwordnet.github.io/schemas/WN-LMF-1.4.dtd">\n'
               '<LexicalResource xmlns:dc="https://globalwordnet.github.io/schemas/dc/">\n')

_MUL_CONCEPTS = _MUL_HEADER + """<Lexicon id="kin" label="Kin concepts" language="mul" email="e" license="l" version="0.2" url="u">
  <LexicalEntry id="kin-dummy"><Lemma writtenForm="x" partOfSpeech="n"/><Sense id="kin-dummy-s" synset="kin-Fa" lexicalized="false"/></LexicalEntry>
  <Synset id="kin-Fa" ili="in" partOfSpeech="n" lexicalized="false" dc:identifier="k:Fa">
    <Definition language="en">father</Definition>
  </Synset>
  <Synset id="kin-Mo" ili="in" partOfSpeech="n" lexicalized="false" dc:identifier="k:Mo">
    <Definition language="en">mother</Definition>
    <SynsetRelation relType="antonym" target="kin-Fa"/>
  </Synset>
</Lexicon>
</LexicalResource>
"""

_MUL_MEMBER = _MUL_HEADER + """<Lexicon id="kin-fi" label="Kin Finnish" language="fi" email="e" license="l" version="0.2" url="u">
  <Requires ref="kin" version="0.2"/>
  <LexicalEntry id="kin-fi-isa"><Lemma writtenForm="isä" partOfSpeech="n"/><Sense id="kin-fi-isa-1" synset="kin-fi-Fa"/></LexicalEntry>
  <LexicalEntry id="kin-fi-aiti"><Lemma writtenForm="äiti" partOfSpeech="n"/><Sense id="kin-fi-aiti-1" synset="kin-fi-Mo"/></LexicalEntry>
  <Synset id="kin-fi-Fa" ili="" partOfSpeech="n" dc:identifier="k:Fa"/>
  <Synset id="kin-fi-Mo" ili="" partOfSpeech="n" dc:identifier="k:Mo"/>
</Lexicon>
</LexicalResource>
"""


class TestMulRelease:
    """A concepts lexicon (language mul) and a member that Requires it share
    concepts by dc:identifier, so the member's senses land on the concepts'
    synsets, which carry the definitions."""

    @pytest.fixture(scope='class')
    def mul_db(self, tmp_path_factory):
        d = tmp_path_factory.mktemp('mul')
        (d / 'kin-0.2.xml').write_text(_MUL_CONCEPTS, encoding='utf-8')
        (d / 'kin-fi-0.2.xml').write_text(_MUL_MEMBER, encoding='utf-8')
        build_test_db(d / 'c.db', d / 'p.db', [d / 'kin-0.2.xml', d / 'kin-fi-0.2.xml'])
        conn = sqlite3.connect(str(d / 'c.db'))
        conn.execute(f"ATTACH '{d / 'p.db'}' AS p")
        return conn

    def test_member_senses_share_the_concepts_synsets(self, mul_db):
        rows = mul_db.execute('''
            SELECT f.form, d.definition FROM senses s
            JOIN entries e ON e.rowid = s.entry_rowid
            JOIN forms f ON f.entry_rowid = e.rowid AND f.rank = 0
            JOIN definitions d ON d.synset_rowid = s.synset_rowid
            ORDER BY f.form''').fetchall()
        assert rows == [('isä', 'father'), ('äiti', 'mother')]
        assert mul_db.execute("SELECT COUNT(*) FROM synsets").fetchone()[0] == 2

    def test_unlexicalized_concept_sense_is_skipped(self, mul_db):
        assert mul_db.execute(
            "SELECT COUNT(*) FROM forms WHERE form = 'x'").fetchone()[0] == 0

    def test_concept_provenance_names_the_concepts_lexicon(self, mul_db):
        rows = _prov(mul_db, 'synsets', mul_db.execute(
            "SELECT synset_rowid FROM definitions WHERE definition = 'father'").fetchone()[0])
        assert rows == [('kin', '0.2', 'kin-Fa')]


_CILI_TSV = ('ili_id\tstatus\tsuperseded_by\torigin\tdefinition\n'
             'i1\t1\t\tpwn-3.0:00001740-n\tthat which is perceived\n'
             'i2\t1\t\tpwn-3.0:00015388-n\ta living  organism\n'
             'i3\t1\t\tpwn-3.0:02084071-n\ta member of the genus Canis\n'
             'i50\t0\ti3\tpwn-3.0:02084072-n\t\n')
_GLOSSTAG = {'i3': [[12, 20], ['sense-of-genus']]}
_MTG_TSV = ('concept\tlanguage\ttranslated_from\tdefinition\n'
            'cili.i3\ten\txx\ta barking pet\n'
            'lmf-xx.xx-ghost\ten\txx\ta local concept\n'
            'cili.i9999\ten\txx\tunknown concept\n')


class TestSidecars:
    """cili.tsv first and mtg.tsv last, around the wordnets."""

    @pytest.fixture(scope='class')
    def db(self, tmp_path_factory):
        d = tmp_path_factory.mktemp('sidecars')
        (d / 'cili.tsv').write_text(_CILI_TSV, encoding='utf-8')
        import json
        (d / 'cili_glosstag.json').write_text(
            json.dumps({'i3': [[12, 20, 'xx-koira-n-1']]}), encoding='utf-8')
        (d / 'mtg.tsv').write_text(_MTG_TSV, encoding='utf-8')
        # the fixture's koira sense stands in for an English-base sense
        import cyg.merge as merge_module
        saved = merge_module.ENGLISH_BASE_IDS
        merge_module.ENGLISH_BASE_IDS = frozenset({'lmf-xx'})
        try:
            build_test_db(d / 'c.db', d / 'p.db',
                          [d / 'cili.tsv', _WORDNETS_DIR / 'lmf-xx.xml', d / 'mtg.tsv'])
        finally:
            merge_module.ENGLISH_BASE_IDS = saved
        conn = sqlite3.connect(str(d / 'c.db'))
        conn.execute(f"ATTACH '{d / 'p.db'}' AS p")
        return conn

    def test_cili_concepts_and_definitions_attributed_to_pwn(self, db):
        rowid = _synset(db, 'i2')
        assert _prov(db, 'synsets', rowid) == [('pwn', '3.0', '00015388-n')]
        defs = db.execute('''SELECT d.rowid, d.definition FROM definitions d
            JOIN languages l ON l.rowid = d.language_rowid
            WHERE d.synset_rowid = ? AND l.code = 'en' ''', (rowid,)).fetchall()
        assert [text for _r, text in defs] == ['a living organism']
        assert _prov(db, 'definitions', defs[0][0]) == [('pwn', '3.0', '00015388-n')]
        # i50 has no definition in the TSV, so cascade_delete() removed it
        assert db.execute("SELECT COUNT(*) FROM synsets WHERE ili = 'i50'").fetchone()[0] == 0

    def test_wordnet_definition_joins_cilis_as_provenance(self, db):
        rows = db.execute('''SELECT pr.code FROM definitions d
            JOIN languages l ON l.rowid = d.language_rowid
            JOIN p.provenance pv ON pv.item_rowid = d.rowid
            JOIN p.prov_tables pt ON pt.rowid = pv.table_rowid AND pt.name = 'definitions'
            JOIN p.prov_resources pr ON pr.rowid = pv.resource_rowid
            WHERE d.synset_rowid = ? AND l.code = 'en' ORDER BY pv.rowid''',
            (_synset(db, 'i3'),)).fetchall()
        assert [c for (c,) in rows] == ['pwn', 'lmf-xx', 'mtg']

    def test_glosstag_annotation_resolves_after_the_base_is_merged(self, db):
        (_s, koira), = _senses_of(db, 'koira')
        rows = db.execute('''SELECT a.start_offset, a.end_offset, a.sense_rowid
            FROM definition_annotations a JOIN definitions d ON d.rowid = a.definition_rowid
            WHERE d.synset_rowid = ?''', (_synset(db, 'i3'),)).fetchall()
        assert rows == [(12, 20, koira)]

    def test_mtg_glosses_land_with_translated_from(self, db):
        rows = db.execute('''SELECT syn.ili, d.definition, l2.code FROM definitions d
            JOIN synsets syn ON syn.rowid = d.synset_rowid
            JOIN languages l ON l.rowid = d.language_rowid
            LEFT JOIN languages l2 ON l2.rowid = d.translated_from_rowid
            JOIN p.provenance pv ON pv.item_rowid = d.rowid
            JOIN p.prov_tables pt ON pt.rowid = pv.table_rowid AND pt.name = 'definitions'
            JOIN p.prov_resources pr ON pr.rowid = pv.resource_rowid AND pr.code = 'mtg'
            ORDER BY syn.ili''').fetchall()
        # cili.i3 already has an English definition (first wins; mtg only adds
        # provenance); the local concept gets the translation
        assert [(ili, text, src) for ili, text, src in rows if ili is None] == [
            (None, 'a local concept', 'xx')]
        assert any(ili == 'i3' for ili, _t, _s in rows)


class TestConceptIndex:
    def test_shared_helper_agrees_with_the_merge(self):
        lexicons, index = lmf_concept_index(_WORDNETS_DIR / 'lmf-xx.xml')
        assert [lx['id'] for lx in lexicons] == ['lmf-xx']
        assert index['xx-dog'] == ('cili.i3', 'NOUN')
        assert index['xx-ghost'] == ('lmf-xx.xx-ghost', 'NOUN')
