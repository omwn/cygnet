"""Tests for the memory-saving parts of WordNetToCygnetConverter.

Pass 4 frees the input tree before loading spaCy, the spaCy document cache
is bounded, and CILI is kept as a map from concept ID to category.
"""

from pathlib import Path

from lxml import etree

import cyg.converters as converters
from cyg.converters import WordNetToCygnetConverter

_CILI_XML = """\
<?xml version='1.0' encoding='UTF-8'?>
<CygnetResource>
  <ConceptLayer>
    <Concept id="cili.i1" ontological_category="NOUN" status="1"/>
    <Concept id="cili.i2" status="1"/>
  </ConceptLayer>
</CygnetResource>
"""

_LMF_XML = """\
<?xml version='1.0' encoding='UTF-8'?>
<LexicalResource xmlns:dc="https://globalwordnet.github.io/schemas/dc/">
  <Lexicon id="test-wn" label="Test Wordnet" language="en"
           email="test@example.org"
           license="https://creativecommons.org/licenses/by/4.0/"
           version="1.0" url="https://example.org">
    <LexicalEntry id="test-wn-dog">
      <Lemma writtenForm="dog" partOfSpeech="n"/>
      <Sense id="test-wn-dog-1" synset="test-wn-n-1">
        <Example>the dog slept</Example>
      </Sense>
    </LexicalEntry>
    <Synset id="test-wn-n-1" ili="i1" partOfSpeech="n">
      <Definition>a domestic animal</Definition>
      <Example>a dog barked</Example>
      <Example>dog days</Example>
    </Synset>
  </Lexicon>
</LexicalResource>
"""


def _converter(tmp_path: Path) -> tuple[WordNetToCygnetConverter, etree.Element]:
    """A converter for the test lexicon, and its parsed input root."""
    cili_path = tmp_path / "cili.xml"
    lmf_path = tmp_path / "lmf.xml"
    cili_path.write_text(_CILI_XML)
    lmf_path.write_text(_LMF_XML)
    converter = WordNetToCygnetConverter(cili_path=str(cili_path))
    root, _tree = converter.read_metadata(str(lmf_path))
    return converter, root


def test_pass4_clears_input_tree(tmp_path):
    """After conversion the input tree is empty: its memory is freed."""
    converter, root = _converter(tmp_path)

    converter.convert_from_tree(root)

    assert len(root) == 0


def test_examples_in_source_order(tmp_path):
    """Synset examples come first, then sense examples, in source order."""
    converter, root = _converter(tmp_path)

    converter.convert_from_tree(root)

    texts = ["".join(ex.itertext()) for ex in converter.examples]
    assert texts == ["a dog barked", "dog days", "the dog slept"]
    assert converter.log["statistics"]["examples"]["total_found"] == 3


def test_cili_kept_as_categories(tmp_path):
    """CILI concepts map to their category, or None if they have none."""
    converter, _root = _converter(tmp_path)

    converter.load_cili()

    assert converter.cili_concepts == {"cili.i1": "NOUN", "cili.i2": None}


def test_doc_cache_bounded(monkeypatch):
    """The spaCy document cache drops the least recently used document."""
    monkeypatch.setattr(converters, "DOC_CACHE_SIZE", 2)
    conv = object.__new__(WordNetToCygnetConverter)
    conv.doc_cache = converters.OrderedDict()
    conv.nlp = lambda text: object()
    first = conv._get_doc("a")
    conv._get_doc("b")
    assert conv._get_doc("a") is first  # cache hit; "b" is now the oldest

    conv._get_doc("c")

    assert list(conv.doc_cache) == ["a", "c"]
