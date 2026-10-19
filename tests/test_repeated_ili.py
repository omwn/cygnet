"""Tests for synsets that repeat an ILI within one lexicon.

An ILI identifies a single synset, so when a lexicon gives the same ILI to
two synsets (e.g. the two конденсор synsets in Ukrajinet) we cannot tell
which is right: neither keeps it, and each gets its own concept rather than
being merged.
"""

from pathlib import Path

from cyg.converters import WordNetToCygnetConverter

_CILI_XML = """\
<?xml version='1.0' encoding='UTF-8'?>
<CILI>
  <ConceptLayer>
    <Concept id="cili.i52256" ontological_category="n" status="1"/>
  </ConceptLayer>
</CILI>
"""

_LMF_REPEATED_ILI = """\
<?xml version='1.0' encoding='UTF-8'?>
<LexicalResource xmlns:dc="https://globalwordnet.github.io/schemas/dc/">
  <Lexicon id="wnuk"
           label="Ukrainian WordNet"
           language="uk"
           email="test@test.org"
           license="https://creativecommons.org/licenses/by/4.0/"
           version="1.0"
           url="https://example.org">
    <Synset id="wnuk-1482-n" ili="i52256" partOfSpeech="n">
      <SynsetRelation relType="hyponym" target="wnuk-28629-n"/>
    </Synset>
    <Synset id="wnuk-28629-n" ili="i52256" partOfSpeech="n">
      <SynsetRelation relType="hypernym" target="wnuk-1482-n"/>
    </Synset>
  </Lexicon>
</LexicalResource>
"""


def _convert(tmp_path: Path) -> WordNetToCygnetConverter:
    """Run the converter on the repeated-ILI lexicon and return it."""
    cili_path = tmp_path / 'cili.xml'
    lmf_path = tmp_path / 'lmf.xml'
    cili_path.write_text(_CILI_XML)
    lmf_path.write_text(_LMF_REPEATED_ILI)
    converter = WordNetToCygnetConverter(cili_path=str(cili_path))
    converter.convert(str(lmf_path))
    return converter


def test_no_synset_keeps_repeated_ili(tmp_path):
    converter = _convert(tmp_path)
    concepts = {converter.synset_to_concept[s] for s in ('wnuk-1482-n', 'wnuk-28629-n')}
    assert 'cili.i52256' not in concepts
    assert len(concepts) == 2
    assert all(c.startswith('wnuk.i') for c in concepts)


def test_repeated_ili_logged(tmp_path):
    converter = _convert(tmp_path)
    assert converter.log['repeated_ili'] == {
        'count': 1,
        'synsets': {'i52256': ['wnuk-1482-n', 'wnuk-28629-n']},
    }
