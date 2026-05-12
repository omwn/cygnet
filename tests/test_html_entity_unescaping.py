"""Regression tests for HTML entity unescaping in WordNetToCygnetConverter.

Some source wordnets (e.g. UzWordnet, omw-fr, tezaurs) contain
double-encoded entities such as ``&amp;#39;`` in Definition and Example
text.  lxml resolves one layer (``&amp;`` → ``&``), leaving ``&#39;`` as
literal text.  The converter must call ``html.unescape()`` to produce
clean Unicode.
"""

from pathlib import Path

import pytest
from lxml import etree

from cyg.converters import WordNetToCygnetConverter

_CILI_XML = """\
<?xml version='1.0' encoding='UTF-8'?>
<CILI>
  <Concept id="i1" ontological_category="NOUN" status="1"/>
</CILI>
"""

_LMF_ENTITY_DEFINITION = """\
<?xml version='1.0' encoding='UTF-8'?>
<LexicalResource xmlns:dc="https://globalwordnet.github.io/schemas/dc/">
  <Lexicon id="test-wn"
           label="Test Wordnet"
           language="uz"
           email="test@example.org"
           license="https://creativecommons.org/licenses/by/4.0/"
           version="1.0"
           url="https://example.org">
    <LexicalEntry id="test-wn-entry-1">
      <Lemma writtenForm="so&apos;z" partOfSpeech="n"/>
      <Sense id="test-wn-sense-1" synset="test-wn-n-1"/>
    </LexicalEntry>
    <Synset id="test-wn-n-1" ili="i1" partOfSpeech="n">
      <Definition>o&amp;#39;z-o&amp;#39;zidan mavjud bo&amp;#39;lgan narsa</Definition>
    </Synset>
  </Lexicon>
</LexicalResource>
"""

_LMF_ENTITY_EXAMPLE = """\
<?xml version='1.0' encoding='UTF-8'?>
<LexicalResource xmlns:dc="https://globalwordnet.github.io/schemas/dc/">
  <Lexicon id="test-wn"
           label="Test Wordnet"
           language="uz"
           email="test@example.org"
           license="https://creativecommons.org/licenses/by/4.0/"
           version="1.0"
           url="https://example.org">
    <LexicalEntry id="test-wn-entry-1">
      <Lemma writtenForm="so&apos;z" partOfSpeech="n"/>
      <Sense id="test-wn-sense-1" synset="test-wn-n-1"/>
    </LexicalEntry>
    <Synset id="test-wn-n-1" ili="i1" partOfSpeech="n">
      <Definition>a thing</Definition>
      <Example>u so&amp;#39;z aytdi</Example>
    </Synset>
  </Lexicon>
</LexicalResource>
"""


def _convert(lmf_xml: str, tmp_path: Path) -> etree.Element:
    cili_path = tmp_path / "cili.xml"
    lmf_path = tmp_path / "lmf.xml"
    cili_path.write_text(_CILI_XML)
    lmf_path.write_text(lmf_xml)
    converter = WordNetToCygnetConverter(
        cili_path=str(cili_path),
        skip_cili_defns=False,
    )
    return converter.convert(str(lmf_path)).getroot()


class TestHtmlEntityUnescaping:
    def test_definition_entities_decoded(self, tmp_path):
        """Double-encoded &#39; in a Definition is decoded to an apostrophe."""
        root = _convert(_LMF_ENTITY_DEFINITION, tmp_path)
        glosses = [
            g.findtext("AnnotatedSentence", "").strip()
            for g in root.iter("Gloss")
        ]
        assert any("o'z-o'zidan" in g for g in glosses), (
            f"Expected decoded apostrophes in definition, got: {glosses}"
        )
        assert not any("&#39;" in g for g in glosses), (
            f"Raw entity still present in definition: {glosses}"
        )

    def test_example_entities_decoded(self, tmp_path):
        """Double-encoded &#39; in an Example is decoded to an apostrophe."""
        root = _convert(_LMF_ENTITY_EXAMPLE, tmp_path)

        def full_text(elem: etree.Element) -> str:
            return "".join(elem.itertext())

        sentences = [
            full_text(s)
            for ex in root.iter("Example")
            for s in ex.iter("AnnotatedSentence")
        ]
        assert any("so'z" in s for s in sentences), (
            f"Expected decoded apostrophe in example, got: {sentences}"
        )
        assert not any("&#39;" in s for s in sentences), (
            f"Raw entity still present in example: {sentences}"
        )
