"""Tests for multi-language Definition handling in WordNetToCygnetConverter.

Regression test for the bug where synsets carrying multiple <Definition>
elements (e.g. language="en" + language="sl" in the Open Slovene Wordnet)
had only the first element imported, and its text was stored under the
lexicon language rather than the element's own language attribute.
"""

from pathlib import Path

import pytest
from lxml import etree

from cyg.converters import WordNetToCygnetConverter

# Minimal CILI XML — the converter needs a concept for the ILI we reference.
_CILI_XML = """\
<?xml version='1.0' encoding='UTF-8'?>
<CILI>
  <Concept id="i9999" ontological_category="ADJ" status="1"/>
</CILI>
"""

# GWN LMF XML: one synset with an English + Slovene definition.
_LMF_BILINGUAL = """\
<?xml version='1.0' encoding='UTF-8'?>
<LexicalResource xmlns:dc="https://globalwordnet.github.io/schemas/dc/">
  <Lexicon id="osw"
           label="Open Slovene Wordnet"
           language="sl"
           email="test@test.org"
           license="https://creativecommons.org/licenses/by/4.0/"
           version="1.0"
           url="https://example.org">
    <Synset id="osw-adj-9999" ili="i9999" partOfSpeech="a">
      <Definition language="en">not marked with a brand</Definition>
      <Definition language="sl">brez oznake znamke</Definition>
    </Synset>
  </Lexicon>
</LexicalResource>
"""

# GWN LMF XML: one synset with a single definition that has no language attr.
_LMF_NO_LANG_ATTR = """\
<?xml version='1.0' encoding='UTF-8'?>
<LexicalResource xmlns:dc="https://globalwordnet.github.io/schemas/dc/">
  <Lexicon id="osw"
           label="Open Slovene Wordnet"
           language="sl"
           email="test@test.org"
           license="https://creativecommons.org/licenses/by/4.0/"
           version="1.0"
           url="https://example.org">
    <Synset id="osw-adj-9999" ili="i9999" partOfSpeech="a">
      <Definition>brez oznake znamke</Definition>
    </Synset>
  </Lexicon>
</LexicalResource>
"""


def _convert(lmf_xml: str, cili_xml: str, tmp_path: Path) -> etree.Element:
    """Run the converter in-memory and return the Cygnet root element."""
    cili_path = tmp_path / 'cili.xml'
    lmf_path = tmp_path / 'lmf.xml'
    cili_path.write_text(cili_xml)
    lmf_path.write_text(lmf_xml)
    converter = WordNetToCygnetConverter(
        cili_path=str(cili_path),
        skip_cili_defns=False,
    )
    tree = converter.convert(str(lmf_path))
    return tree.getroot()


def _glosses(root: etree.Element) -> dict[str, str]:
    """Return {language: definition_text} from all <Gloss> elements."""
    return {
        g.get('language'): g.findtext('AnnotatedSentence', '').strip()
        for g in root.iter('Gloss')
    }


class TestMultiLanguageDefinitions:
    def test_both_languages_imported(self, tmp_path):
        """A synset with en + sl definitions produces two Gloss elements."""
        root = _convert(_LMF_BILINGUAL, _CILI_XML, tmp_path)
        glosses = _glosses(root)
        assert 'en' in glosses, "English Gloss missing"
        assert 'sl' in glosses, "Slovene Gloss missing"

    def test_slovene_gloss_has_slovene_text(self, tmp_path):
        """The sl Gloss must contain the Slovene text, not the English text."""
        root = _convert(_LMF_BILINGUAL, _CILI_XML, tmp_path)
        glosses = _glosses(root)
        assert glosses['sl'] == 'brez oznake znamke', (
            f"Expected Slovene text, got: {glosses.get('sl')!r}"
        )

    def test_english_gloss_has_english_text(self, tmp_path):
        """The en Gloss must contain the English text."""
        root = _convert(_LMF_BILINGUAL, _CILI_XML, tmp_path)
        glosses = _glosses(root)
        assert glosses['en'] == 'not marked with a brand'

    def test_no_language_attr_falls_back_to_lexicon_language(self, tmp_path):
        """A <Definition> without a language attribute uses the lexicon language."""
        root = _convert(_LMF_NO_LANG_ATTR, _CILI_XML, tmp_path)
        glosses = _glosses(root)
        assert 'sl' in glosses
        assert glosses['sl'] == 'brez oznake znamke'
