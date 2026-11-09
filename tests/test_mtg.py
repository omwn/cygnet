"""Tests for cyg/mtg.py, the machine-translated glosses sidecar."""

import csv
from pathlib import Path

from cyg.mtg import MTG_TSV_COLUMNS, glosses_to_translate, write_mtg_tsv

_WORDNETS = Path(__file__).parent / 'wordnets'


class TestGlossesToTranslate:
    def test_lmf_definitions_of_non_cili_concepts_in_other_languages(self, tmp_path):
        (tmp_path / 'lmf-xx-1.0.xml').write_bytes((_WORDNETS / 'lmf-xx.xml').read_bytes())
        glosses = glosses_to_translate(tmp_path)
        # xx-ghost is the only local concept; its definition is in xx.  The
        # English definition of xx-dog and every CILI concept are left out.
        assert glosses == [{'definition': 'paikallinen käsite', 'definiendum_id': 'lmf-xx.xx-ghost',
                            'language': 'xx'}]

    def test_cygnet_xml_still_read(self, tmp_path):
        (tmp_path / 'wn-zz-1.0.xml').write_text(
            '<CygnetResource id="wn-zz" language="zz" version="1.0">'
            '<ConceptLayer><Concept id="wn-zz.i1" ontological_category="NOUN"/></ConceptLayer>'
            '<GlossLayer>'
            '<Gloss definiendum="wn-zz.i1" language="zz"><AnnotatedSentence>un  chien</AnnotatedSentence></Gloss>'
            '<Gloss definiendum="wn-zz.i1" language="en"><AnnotatedSentence>a dog</AnnotatedSentence></Gloss>'
            '<Gloss definiendum="cili.i3" language="zz"><AnnotatedSentence>chien</AnnotatedSentence></Gloss>'
            '</GlossLayer></CygnetResource>', encoding='utf-8')
        assert glosses_to_translate(tmp_path) == [
            {'definition': 'un  chien', 'definiendum_id': 'wn-zz.i1', 'language': 'zz'}]


class TestWriteMtgTsv:
    def test_rows_and_blank_translations_skipped(self, tmp_path):
        path = tmp_path / 'mtg.tsv'
        n = write_mtg_tsv([
            {'definiendum_id': 'cili.i3', 'source_language': 'fr', 'translated_definition': ' a  dog '},
            {'definiendum_id': 'lmf-xx.xx-ghost', 'source_language': 'xx', 'translated_definition': ''},
        ], path)
        assert n == 1
        with open(path, encoding='utf-8', newline='') as f:
            rows = list(csv.reader(f, delimiter='\t'))
        assert rows == [list(MTG_TSV_COLUMNS), ['cili.i3', 'en', 'fr', 'a dog']]
