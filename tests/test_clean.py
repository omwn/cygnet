"""Tests for cyg/clean.py, the per-source WN-LMF cleaner."""

import json
from pathlib import Path

import pytest
from lxml import etree

from cyg.cili import CiliIndex
from cyg.clean import (
    CHANGED_SUFFIX,
    DOCTYPE,
    XML_DECL,
    CleanOptions,
    LmfCleaner,
    normalise_text,
    validate_lmf,
)
from cyg.multilex import SharedConcepts

_WORDNETS = Path(__file__).parent / 'wordnets'
_DC = 'https://globalwordnet.github.io/schemas/dc/'
_HEADER = f'{XML_DECL}\n{DOCTYPE}\n<LexicalResource xmlns:dc="{_DC}">\n'
_CILI = CiliIndex({f'i{n}': 'NOUN' for n in range(1, 20)})


def _lexicon(body: str, lid: str = 'tt', language: str = 'tt', version: str = '1.0',
             extra: str = '') -> str:
    return (f'{_HEADER}<Lexicon id="{lid}" label="Test" language="{language}" email="e" '
            f'license="https://creativecommons.org/licenses/by/4.0/" version="{version}" '
            f'url="u"{extra}>\n{body}</Lexicon>\n</LexicalResource>\n')


def _entry(eid: str, form: str, senses: str, pos: str = 'n') -> str:
    return (f'  <LexicalEntry id="{eid}">\n    <Lemma writtenForm="{form}" partOfSpeech="{pos}"/>\n'
            f'{senses}  </LexicalEntry>\n')


def _sense(sid: str, synset: str, inner: str = '', attrs: str = '') -> str:
    if inner:
        return f'    <Sense id="{sid}" synset="{synset}"{attrs}>\n{inner}    </Sense>\n'
    return f'    <Sense id="{sid}" synset="{synset}"{attrs}/>\n'


def _synset(sid: str, ili: str, inner: str = '', attrs: str = '', pos: str = 'n') -> str:
    return f'  <Synset id="{sid}" ili="{ili}" partOfSpeech="{pos}"{attrs}>\n{inner}  </Synset>\n'


def _clean(tmp_path: Path, text: str, cili=_CILI, options=None, name='src.xml'):
    src = tmp_path / name
    src.write_text(text, encoding='utf-8')
    result = LmfCleaner(src, cili, options).clean(tmp_path / 'out')
    tree = etree.parse(str(result.output))
    log = json.loads(result.log.read_text(encoding='utf-8'))
    return result, tree, log


class TestNormaliseText:
    def test_nfc_whitespace_and_entities(self):
        assert normalise_text('  a\u0301   b\n c &#39;d ') == "á b c 'd"
        assert normalise_text(None) == ''
        assert normalise_text('   ') == ''


class TestFixtureEndToEnd:
    """tests/wordnets/lmf-xx.xml through the cleaner (the merge fixture,
    which carries one of everything the cleaner handles)."""

    @pytest.fixture(scope='class')
    def cleaned(self, tmp_path_factory):
        out = tmp_path_factory.mktemp('clean_fixture')
        cili = CiliIndex.from_file(_WORDNETS.parent.parent / 'bin' / 'cygnets_presynth' / 'cili-1.0.xml') \
            if (_WORDNETS.parent.parent / 'bin' / 'cygnets_presynth' / 'cili-1.0.xml').exists() else _CILI
        result = LmfCleaner(_WORDNETS / 'lmf-xx.xml', cili).clean(out)
        return result, etree.parse(str(result.output)), json.loads(result.log.read_text())

    def test_header_version_and_note(self, cleaned):
        result, tree, _log = cleaned
        head = result.output.read_text(encoding='utf-8').splitlines()[:2]
        assert head == [XML_DECL, DOCTYPE]
        lexicon = tree.getroot()[0]
        assert lexicon.get('version') == '1.0' + CHANGED_SUFFIX
        assert 'cleaned by Cygnet' in lexicon.get('note')
        assert result.output.name == 'lmf-xx-1.0.xml'
        assert result.changed

    def test_duplicate_entry_merged_with_its_id_noted(self, cleaned):
        _r, tree, log = cleaned
        ids = [e.get('id') for e in tree.iter('LexicalEntry')]
        assert 'xx-koira-n' in ids and 'xx-koira-n-dup' not in ids
        koira = tree.find('.//LexicalEntry[@id="xx-koira-n"]')
        assert koira.get('note') == 'merged=xx-koira-n-dup'
        assert [s.get('id') for s in koira.iter('Sense')] == ['xx-koira-n-1']
        assert koira.find('Lemma/Pronunciation').text == 'ˈkoi̯rɑ'
        assert log['lexeme_merging']['total_merges'] == 1
        assert log['duplicate_senses']['count'] == 1

    def test_entry_whose_only_sense_dangles_is_dropped_gap_kept(self, cleaned):
        _r, tree, log = cleaned
        ids = [e.get('id') for e in tree.iter('LexicalEntry')]
        assert 'xx-orpo-n' not in ids
        assert tree.find('.//Sense[@id="xx-gap-n-1"]').get('lexicalized') == 'false'
        assert log['synset_not_found']['count'] == 1

    def test_every_definition_has_a_language(self, cleaned):
        _r, tree, _log = cleaned
        langs = {d.get('language') for d in tree.iter('Definition')}
        assert None not in langs and langs == {'xx', 'en'}

    def test_relations_forward_only_symmetric_once(self, cleaned):
        _r, tree, log = cleaned
        def rels(sid):
            return [(r.get('relType'), r.get('target')) for r in
                    tree.find(f'.//Synset[@id="{sid}"]').iter('SynsetRelation')]
        assert ('hypernym', 'xx-animal') in rels('xx-dog')
        assert all(t != 'hyponym' for t, _ in rels('xx-animal'))
        assert rels('xx-animal') == [('mero_part', 'xx-cat')]       # was cat holo_part animal
        assert ('hypernym', 'xx-nowhere') not in rels('xx-dog')       # dangling
        kissa = tree.find('.//Sense[@id="xx-kissa-n-1"]')
        koira = tree.find('.//Sense[@id="xx-koira-n-1"]')
        assert sorted(r.get('relType') for r in kissa.iter('SenseRelation')) == ['antonym', 'derivation']
        assert list(koira.iter('SenseRelation')) == []                # lower id keeps symmetric ones
        rp = log['relation_processing']
        assert rp['inverse_direction_turned']['concept_relations']['count'] == 2
        assert rp['dangling_removed']['concept_relations']['count'] == 1
        assert rp['duplicates_removed']['concept_relations']['count'] == 1

    def test_dc_type_attributes_survive(self, cleaned):
        _r, tree, _log = cleaned
        cat = tree.find('.//Synset[@id="xx-cat"]')
        dim = next(r for r in cat.iter('SynsetRelation') if r.get('relType') == 'diminutive')
        assert dim.get(f'{{{_DC}}}type') == 'xx:56'
        assert dim.get(f'{{{_DC}}}description') == 'pikku'
        dog = tree.find('.//Synset[@id="xx-dog"]')
        other = next(r for r in dog.iter('SynsetRelation') if r.get('relType') == 'other')
        assert other.get(f'{{{_DC}}}type') == 'https://example.org/schema/pet'

    def test_sense_examples_get_offsets_source_notes_kept(self, cleaned):
        _r, tree, _log = cleaned
        koira = tree.find('.//Sense[@id="xx-koira-n-1"]')
        notes = [(e.text, e.get('note')) for e in koira.iter('Example')]
        assert notes == [('Koira haukkuu.', 'offsets=0,5'), ('Se on eläin.', None),
                         ('Iso koira juoksee.', 'R=1;offsets=4,9')]

    def test_unlinkable_synset_example_stays(self, cleaned):
        _r, tree, log = cleaned
        dog = tree.find('.//Synset[@id="xx-dog"]')
        assert [e.text for e in dog.iter('Example')] == ['Tämä esimerkki jää synsetille.']
        assert log['statistics']['examples']['left_on_synset'] == 1

    def test_output_validates_with_wn(self, cleaned):
        result, _tree, _log = cleaned
        errors, _warnings = validate_lmf(result.output)
        assert errors == []

    def test_cleaning_the_output_again_changes_nothing(self, cleaned, tmp_path):
        result, _tree, _log = cleaned
        again = LmfCleaner(result.output, _CILI).clean(tmp_path)
        assert not again.changed
        assert again.output.read_bytes() == result.output.read_bytes()
        assert again.version == '1.0' + CHANGED_SUFFIX           # not appended twice


class TestPassThrough:
    def test_clean_source_is_copied_byte_for_byte(self, tmp_path):
        text = _lexicon(_entry('e1', 'word', _sense('s1', 'y1'))
                        + _synset('y1', 'i1', '    <Definition language="tt">a word</Definition>\n'))
        result, _tree, log = _clean(tmp_path, text)
        assert not result.changed
        assert log['unchanged'] is True and log['changes'] == {}
        assert result.output.read_text(encoding='utf-8') == text
        assert result.version == '1.0'

    def test_header_alone_forces_a_rewrite(self, tmp_path):
        text = _lexicon(_entry('e1', 'word', _sense('s1', 'y1'))
                        + _synset('y1', 'i1', '    <Definition language="tt">a word</Definition>\n'))
        text = text.replace(XML_DECL, "<?xml version='1.0' encoding='UTF-8'?>")
        result, tree, log = _clean(tmp_path, text)
        assert result.changed
        assert 'header' in log['metadata_changes']
        assert result.output.read_text(encoding='utf-8').startswith(XML_DECL)
        assert tree.getroot()[0].get('version') == '1.0' + CHANGED_SUFFIX


class TestMetadata:
    def test_language_fix_and_known_licence_error(self, tmp_path):
        text = _lexicon(_entry('e1', 'w', _sense('s1', 'y1'))
                        + _synset('y1', 'i1', '    <Definition language="uk">d</Definition>\n'),
                        lid='wordnet_lv', language='ua')
        _r, tree, log = _clean(tmp_path, text)
        lexicon = tree.getroot()[0]
        assert lexicon.get('language') == 'uk'
        assert lexicon.get('license') == 'https://creativecommons.org/licenses/by-sa/4.0/'
        assert log['metadata_changes']['language'] == {'from': 'ua', 'to': 'uk'}
        assert 'license' in log['metadata_changes']

    def test_language_override(self, tmp_path):
        text = _lexicon(_entry('e1', 'w', _sense('s1', 'y1'))
                        + _synset('y1', 'i1', '    <Definition language="ar">d</Definition>\n'),
                        language='ar')
        _r, tree, _log = _clean(tmp_path, text, options=CleanOptions(language='arb'))
        assert tree.getroot()[0].get('language') == 'arb'


class TestTrimming:
    def test_low_confidence_elements_dropped(self, tmp_path):
        text = _lexicon(
            _entry('e1', 'w', _sense('s1', 'y1')
                   + _sense('s2', 'y2', attrs=' confidenceScore="0.3"'))
            + _entry('e2', 'v', _sense('s3', 'y2', '      <SenseRelation relType="antonym" target="s1" confidenceScore="0.1"/>\n'))
            + _synset('y1', 'i1', '    <Definition language="tt">keep</Definition>\n'
                                  '    <Definition language="tt" confidenceScore="0.2">drop</Definition>\n'
                                  '    <SynsetRelation relType="hypernym" target="y3"/>\n')
            + _synset('y2', 'i2', '    <Definition language="tt">d</Definition>\n')
            + _synset('y3', 'i3', '    <Definition language="tt">gone</Definition>\n', attrs=' confidenceScore="0.5"'))
        _r, tree, log = _clean(tmp_path, text)
        assert [s.get('id') for s in tree.iter('Synset')] == ['y1', 'y2']
        assert [s.get('id') for s in tree.iter('Sense')] == ['s1', 's3']
        assert [d.text for d in tree.find('.//Synset[@id="y1"]').iter('Definition')] == ['keep']
        assert list(tree.iter('SynsetRelation')) == [] and list(tree.iter('SenseRelation')) == []
        assert log['low_confidence_skipped'] == {'synsets': 1, 'senses': 1, 'definitions': 1,
                                                 'relations': 1}

    def test_repeated_and_unknown_ilis_are_cleared(self, tmp_path):
        text = _lexicon(
            _entry('e1', 'a', _sense('s1', 'y1')) + _entry('e2', 'b', _sense('s2', 'y2'))
            + _entry('e3', 'c', _sense('s3', 'y3'))
            + _synset('y1', 'i1', '    <Definition language="tt">d</Definition>\n')
            + _synset('y2', 'i1', '    <Definition language="tt">d</Definition>\n')
            + _synset('y3', 'i999', '    <Definition language="tt">d</Definition>\n'))
        _r, tree, log = _clean(tmp_path, text)
        assert [s.get('ili') for s in tree.iter('Synset')] == ['', '', '']
        assert log['repeated_ili']['synsets'] == {'i1': ['y1', 'y2']}
        assert log['missing_cili_concepts']['count'] == 1

    def test_ili_definition_on_a_real_ili_is_dropped(self, tmp_path):
        text = _lexicon(
            _entry('e1', 'a', _sense('s1', 'y1')) + _entry('e2', 'b', _sense('s2', 'y2'))
            + _synset('y1', 'i1', '    <Definition language="tt">d</Definition>\n'
                                  '    <ILIDefinition>spurious</ILIDefinition>\n')
            + _synset('y2', 'in', '    <Definition language="tt">d</Definition>\n'
                                  '    <ILIDefinition>a proposed  concept</ILIDefinition>\n'))
        _r, tree, log = _clean(tmp_path, text)
        assert [d.text for d in tree.iter('ILIDefinition')] == ['a proposed concept']
        assert log['changes']['synsets.ili_definition_spurious'] == 1

    def test_invalid_pos_becomes_u_and_blank_text_goes(self, tmp_path):
        text = _lexicon(
            _entry('e1', 'w', _sense('s1', 'y1', '      <Example>   </Example>\n'), pos='')
            + _synset('y1', 'i1', '    <Definition language="tt">  spaced   out </Definition>\n'
                                  '    <Definition language="tt"></Definition>\n', pos='q'))
        _r, tree, log = _clean(tmp_path, text)
        assert tree.find('.//Lemma').get('partOfSpeech') == 'u'
        assert tree.find('.//Synset').get('partOfSpeech') == 'u'
        assert [d.text for d in tree.iter('Definition')] == ['spaced out']
        assert list(tree.iter('Example')) == []
        assert log['invalid_pos_values'] == {'': 1, 'q': 1}
        assert log['blank_removed'] == {'definitions': 1, 'examples': 1}


class TestRelations:
    def test_direction_swap_moves_the_relation(self, tmp_path):
        text = _lexicon(
            _entry('e1', 'a', _sense('s1', 'y1')) + _entry('e2', 'b', _sense('s2', 'y2'))
            + _synset('y1', 'i1', '    <Definition language="tt">d</Definition>\n'
                                  '    <SynsetRelation relType="mero_part" target="y2"/>\n')
            + _synset('y2', 'i2', '    <Definition language="tt">d</Definition>\n'))
        options = CleanOptions(direction_swaps=frozenset({'mero_part', 'holo_part'}),
                               swap_stats={'mero_part': {'reversed': 9, 'comparable': 9}})
        _r, tree, log = _clean(tmp_path, text, options=options)
        assert list(tree.find('.//Synset[@id="y1"]').iter('SynsetRelation')) == []
        y2 = [(r.get('relType'), r.get('target')) for r in tree.find('.//Synset[@id="y2"]').iter('SynsetRelation')]
        assert y2 == [('mero_part', 'y1')]
        assert log['statistics']['direction_swaps'] == {'holo_part': None,
                                                        'mero_part': {'reversed': 9, 'comparable': 9}}

    def test_self_loop_removed_unknown_type_kept(self, tmp_path):
        text = _lexicon(
            _entry('e1', 'a', _sense('s1', 'y1')) + _entry('e2', 'b', _sense('s2', 'y2'))
            + _synset('y1', 'i1', '    <Definition language="tt">d</Definition>\n'
                                  '    <SynsetRelation relType="hypernym" target="y1"/>\n'
                                  '    <SynsetRelation relType="other" dc:type="plwn:19" target="y2"/>\n')
            + _synset('y2', 'i2', '    <Definition language="tt">d</Definition>\n'))
        _r, tree, log = _clean(tmp_path, text)
        rels = [(r.get('relType'), r.get(f'{{{_DC}}}type')) for r in tree.iter('SynsetRelation')]
        assert rels == [('other', 'plwn:19')]
        assert log['relation_processing']['self_loops_removed']['concept_relations']['count'] == 1


class TestMulRelease:
    def test_member_gets_requires_and_ili_from_the_concepts(self, tmp_path):
        shared = SharedConcepts({'k:Fa': ('i7', 'kin.Fa'), 'k:Mo': (None, 'kin.Mo')}, 'member')
        text = _lexicon(
            _entry('e1', 'isä', _sense('s1', 'y1')) + _entry('e2', 'äiti', _sense('s2', 'y2'))
            + _synset('y1', '', '', attrs=' dc:identifier="k:Fa"')
            + _synset('y2', '', '', attrs=' dc:identifier="k:Mo"'),
            lid='kin-fi', language='fi')
        options = CleanOptions(shared=shared, requires=('kin', '0.2'))
        _r, tree, log = _clean(tmp_path, text, options=options)
        lexicon = tree.getroot()[0]
        assert lexicon[0].tag == 'Requires' and lexicon[0].get('ref') == 'kin'
        assert [s.get('ili') for s in tree.iter('Synset')] == ['i7', '']
        assert log['changes']['synsets.ili_filled'] == 1
        assert log['changes']['metadata.requires'] == 1

    def test_member_that_needs_only_requires_is_still_rewritten(self, tmp_path):
        """A member whose only edit is the Requires element must not pass
        through unchanged, or the merge cannot tell whose concepts it uses."""
        shared = SharedConcepts({'k:Mo': (None, 'kin.Mo')}, 'member')
        text = _lexicon(_entry('e1', 'äiti', _sense('s1', 'y1'))
                        + _synset('y1', '', '    <Definition language="fi">d</Definition>\n',
                                  attrs=' dc:identifier="k:Mo"'),
                        lid='kin-fi', language='fi')
        result, tree, log = _clean(tmp_path, text, options=CleanOptions(shared=shared, requires=('kin', '0.2')))
        assert result.changed
        assert tree.getroot()[0][0].tag == 'Requires'
        assert log['changes'] == {'metadata.requires': 1}


class TestCiliIndex:
    def test_from_tsv_and_lookup(self, tmp_path):
        tsv = tmp_path / 'cili.tsv'
        tsv.write_text('ili_id\tstatus\tsuperseded_by\torigin\tdefinition\n'
                       'i1\t1\t\tpwn-3.0:00001740-a\tgloss one\n'
                       'i2\t1\t\tpwn-3.0:00002098-n\tgloss  two\n', encoding='utf-8')
        index = CiliIndex.from_file(tsv)
        assert 'i1' in index and 'i2' in index and 'i3' not in index
        assert index.pos('i1') == 'ADJ' and index.pos('i2') == 'NOUN'
        assert len(index) == 2

    def test_from_cygnet_xml(self, tmp_path):
        xml = tmp_path / 'cili-1.0.xml'
        xml.write_text('<CygnetResource id="cili"><ConceptLayer>'
                       '<Concept id="cili.i5" ontological_category="VERB"/>'
                       '</ConceptLayer></CygnetResource>', encoding='utf-8')
        index = CiliIndex.from_file(xml)
        assert index.pos('i5') == 'VERB' and len(index) == 1
