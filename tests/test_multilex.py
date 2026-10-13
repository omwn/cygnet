"""Tests for multi-lexicon releases linked by a 'mul' concepts lexicon.

Covers cyg/multilex.py (language matching, concept map), the converter's
shared-concept mode, and the planning step in 2_batch_convert_lmfs.py
(which member lexicons to keep, and under which language code).
"""

import importlib.util
import sys
from pathlib import Path

import pytest
from lxml import etree

from cyg.converters import WordNetToCygnetConverter
from cyg.multilex import (
    SharedConcepts,
    build_concept_map,
    fix_language,
    language_problem,
    match_language,
    read_lexicon_header,
)

_SCRIPTS = Path(__file__).parent.parent / "conversion_scripts"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


batch = _load_script("2_batch_convert_lmfs")
synth = _load_script("6_synthesise")

_CILI = """\
<?xml version='1.0' encoding='UTF-8'?>
<CygnetResource id="cili" version="1.0">
  <ConceptLayer>
    <Concept id="cili.i100" ontological_category="NOUN" status="1"/>
  </ConceptLayer>
</CygnetResource>
"""

_LMF_HEAD = """\
<?xml version='1.0' encoding='UTF-8'?>
<LexicalResource xmlns:dc="https://globalwordnet.github.io/schemas/dc/">
  <Lexicon id="{id}" label="t" language="{lang}" email="a@b.c"
           license="https://creativecommons.org/licenses/by/4.0/"
           version="0.1" url="https://example.org">
"""
_LMF_TAIL = "  </Lexicon>\n</LexicalResource>\n"

# Concepts lexicon: 'Pa' has an ILI in CILI, 'Fa' and 'Mo' do not yet.
_CONCEPTS = _LMF_HEAD.format(id="kin", lang="mul") + """\
    <LexicalEntry id="kin-e-Pa">
      <Lemma writtenForm="Pa" partOfSpeech="n"/>
      <Sense id="kin-e-Pa-1" synset="kin-Pa" lexicalized="false"/>
    </LexicalEntry>
    <LexicalEntry id="kin-e-Fa">
      <Lemma writtenForm="Fa" partOfSpeech="n"/>
      <Sense id="kin-e-Fa-1" synset="kin-Fa" lexicalized="false"/>
    </LexicalEntry>
    <Synset id="kin-Pa" ili="i100" partOfSpeech="n" lexicalized="false"
            dc:identifier="K:Pa">
      <Definition language="en">a parent</Definition>
    </Synset>
    <Synset id="kin-Fa" ili="in" partOfSpeech="n" lexicalized="false"
            dc:identifier="K:Fa">
      <Definition language="en">a male parent</Definition>
      <SynsetRelation relType="hypernym" target="kin-Pa"/>
    </Synset>
    <Synset id="kin-Mo" ili="in" partOfSpeech="n" lexicalized="false"
            dc:identifier="K:Mo">
      <Definition language="en">a female parent</Definition>
      <SynsetRelation relType="hypernym" target="kin-Pa"/>
    </Synset>
""" + _LMF_TAIL

# A member lexicon: a word for 'Fa', a recorded gap for 'Mo'.
_MEMBER = _LMF_HEAD.format(id="kin-ar", lang="ar") + """\
    <LexicalEntry id="kin-ar-e1">
      <Lemma writtenForm="أب" partOfSpeech="n"/>
      <Sense id="kin-ar-e1-Fa" synset="kin-ar-Fa"/>
    </LexicalEntry>
    <LexicalEntry id="kin-ar-e2">
      <Lemma writtenForm="mother (gap)" partOfSpeech="n"/>
      <Sense id="kin-ar-e2-Mo" synset="kin-ar-Mo" lexicalized="false"/>
    </LexicalEntry>
    <Synset id="kin-ar-Fa" ili="" partOfSpeech="n" dc:identifier="K:Fa">
      <Definition language="en">a male parent</Definition>
    </Synset>
    <Synset id="kin-ar-Mo" ili="" partOfSpeech="n" lexicalized="false"
            dc:identifier="K:Mo">
      <Definition language="en">a female parent</Definition>
    </Synset>
""" + _LMF_TAIL


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _run(tmp_path: Path, lmf: str, shared=None, language=None):
    """Convert an LMF string; return (Cygnet root, converter)."""
    cili = _write(tmp_path / "cili.xml", _CILI)
    src = _write(tmp_path / f"src-{len(list(tmp_path.iterdir()))}.xml", lmf)
    converter = WordNetToCygnetConverter(
        cili_path=str(cili), shared=shared, language=language
    )
    return converter.convert(str(src)).getroot(), converter


def _convert(tmp_path: Path, lmf: str, shared=None, language=None) -> etree._Element:
    return _run(tmp_path, lmf, shared, language)[0]


# --- Language codes ---------------------------------------------------------

@pytest.mark.parametrize("code, known, expected", [
    ("fr", {"fr", "en"}, "fr"),            # exact
    ("ar", {"arb", "en"}, "arb"),          # macrolanguage
    ("cmn", {"cmn-Hans"}, "cmn-Hans"),     # script only on one side
    ("cmn-Hant", {"cmn-Hans"}, None),      # different scripts
    ("ms", {"zsm"}, "zsm"),
    ("tn", {"tsn"}, "tsn"),
    ("tl", {"fil"}, "fil"),
    ("no", {"nb", "nn"}, "nb"),            # explicit alias
    ("apc", {"arb"}, None),                # varieties stay distinct
    ("sh", {"hr"}, None),                  # Serbo-Croatian is not Croatian
    ("pt", {"pt", "pt-BR"}, "pt"),
    ("xx", {"en"}, None),
])
def test_match_language(code, known, expected):
    assert match_language(code, known) == expected


def test_fix_language():
    assert fix_language("ua") == "uk"
    assert fix_language("en") == "en"
    assert fix_language(None) is None


def test_ukrainian_code_fixed_on_conversion(tmp_path):
    lmf = _LMF_HEAD.format(id="ukr", lang="ua") + """\
    <LexicalEntry id="ukr-e1">
      <Lemma writtenForm="батько" partOfSpeech="n"/>
      <Sense id="ukr-e1-1" synset="ukr-s1"/>
    </LexicalEntry>
    <Synset id="ukr-s1" ili="i100" partOfSpeech="n"/>
""" + _LMF_TAIL
    root = _convert(tmp_path, lmf)
    assert root.get("language") == "uk"
    assert {lx.get("language") for lx in root.iter("Lexeme")} == {"uk"}


# --- Concept map --------------------------------------------------------------

def test_build_concept_map(tmp_path):
    concepts = build_concept_map(_write(tmp_path / "kin.xml", _CONCEPTS))
    assert concepts["K:Pa"] == ("i100", "kin.Pa")
    assert concepts["K:Fa"] == (None, "kin.Fa")


def test_read_lexicon_header(tmp_path):
    h = read_lexicon_header(_write(tmp_path / "m.xml", _MEMBER))
    assert (h.id, h.version, h.language) == ("kin-ar", "0.1", "ar")


# --- Converter in shared-concept mode ----------------------------------------

def test_concepts_lexicon_keeps_hierarchy_and_lemmas(tmp_path):
    concepts = build_concept_map(_write(tmp_path / "kin.xml", _CONCEPTS))
    root = _convert(tmp_path, _CONCEPTS, SharedConcepts(concepts, "concepts"))
    # Concepts without an ILI get the stable shared id; ILI ones map to CILI
    assert {c.get("id") for c in root.iter("Concept")} == {"kin.Fa", "kin.Mo"}
    senses = {s.get("signified") for s in root.iter("Sense")}
    assert senses == {"cili.i100", "kin.Fa"}
    # The unlexicalized 'mul' lemmas are kept, under language 'mul'
    assert {lx.get("language") for lx in root.iter("Lexeme")} == {"mul"}
    rels = {(r.get("source"), r.get("target"))
            for r in root.iter("ConceptRelation")
            if r.get("relation_type") in ("class_hypernym", "hypernym")}
    assert ("kin.Fa", "cili.i100") in rels


def test_member_lexicon_points_at_shared_concepts(tmp_path):
    concepts = build_concept_map(_write(tmp_path / "kin.xml", _CONCEPTS))
    root, converter = _run(tmp_path, _MEMBER, SharedConcepts(concepts, "member"), "arb")
    # No concepts or glosses of its own: they come from the concepts lexicon
    assert list(root.iter("Concept")) == []
    assert list(root.iter("Gloss")) == []
    # The word links to the shared concept; the recorded gap is dropped
    senses = [(s.get("signifier"), s.get("signified")) for s in root.iter("Sense")]
    assert len(senses) == 1 and senses[0][1] == "kin.Fa"
    assert converter.log["unlexicalized_skipped"] == {"synsets": 1, "senses": 1}
    # The language override puts the lexeme under the existing wordnet's code
    assert {lx.get("language") for lx in root.iter("Lexeme")} == {"arb"}
    assert root.get("language") == "arb"


# --- Planning which lexicons to convert -----------------------------------------

def _raw_release(tmp_path: Path) -> tuple[Path, Path]:
    raw = tmp_path / "raw_wns"
    raw.mkdir()
    _write(raw / "kin-0.1.xml", _CONCEPTS)
    _write(raw / "kin-ar-0.1.xml", _MEMBER)
    _write(raw / "kin-xx-0.1.xml", _MEMBER.replace('"kin-ar"', '"kin-xx"')
           .replace('language="ar"', 'language="xx"'))
    _write(raw / "omw-arb.xml", _LMF_HEAD.format(id="omw-arb", lang="arb") + _LMF_TAIL)
    toml = _write(tmp_path / "wordnets.toml",
                  'arb = ["https://example.org/omw-arb-2.0.tar.xz"]\n'
                  'mul = ["file:///somewhere/kin-0.1.tar.xz"]\n')
    return raw, toml


def test_plan_keeps_only_languages_with_a_wordnet(tmp_path):
    raw, toml = _raw_release(tmp_path)
    files = batch.collect_xml_files(toml, raw)
    options, skip = batch.plan_multilexicon(toml, raw, files, all_languages=False)
    assert options[raw / "kin-0.1.xml"].shared.role == "concepts"
    assert options[raw / "kin-ar-0.1.xml"].shared.role == "member"
    assert options[raw / "kin-ar-0.1.xml"].language == "arb"
    assert skip == {raw / "kin-xx-0.1.xml"}


def test_plan_all_languages(tmp_path):
    raw, toml = _raw_release(tmp_path)
    files = batch.collect_xml_files(toml, raw)
    options, skip = batch.plan_multilexicon(toml, raw, files, all_languages=True)
    assert skip == set()
    assert options[raw / "kin-xx-0.1.xml"].language == "xx"


def test_plan_without_mul_key(tmp_path):
    raw, _ = _raw_release(tmp_path)
    toml = _write(tmp_path / "plain.toml",
                  'arb = ["https://example.org/omw-arb-2.0.tar.xz"]\n')
    files = batch.collect_xml_files(toml, raw)
    assert batch.plan_multilexicon(toml, raw, files, all_languages=False) == ({}, set())


def test_synthesise_detects_concepts_lexicon(tmp_path):
    mul = _write(tmp_path / "kin-0.1.xml",
                 '<CygnetResource id="kin" language="mul"/>')
    ar = _write(tmp_path / "kin-ar-0.1.xml",
                '<CygnetResource id="kin-ar" language="arb"/>')
    assert synth.is_concepts_lexicon(mul)
    assert not synth.is_concepts_lexicon(ar)


def test_other_relation_uses_dc_type(tmp_path):
    """relType="other" + dc:type keeps the real type, so inverse pairs survive."""
    lmf = _LMF_HEAD.format(id="pr", lang="mul") + """\
    <Synset id="pr-1" ili="in" partOfSpeech="n" dc:identifier="P:1">
      <Definition language="en">first person</Definition>
      <SynsetRelation relType="other" dc:type="possessive" target="pr-1-Poss"/>
    </Synset>
    <Synset id="pr-1-Poss" ili="in" partOfSpeech="a" dc:identifier="P:1;Poss">
      <Definition language="en">first person possessive</Definition>
      <SynsetRelation relType="other" dc:type="possessor" target="pr-1"/>
    </Synset>
""" + _LMF_TAIL
    concepts = build_concept_map(_write(tmp_path / "pr.xml", lmf))
    root = _convert(tmp_path, lmf, SharedConcepts(concepts, "concepts"))
    rels = {(r.get("source"), r.get("relation_type"), r.get("target"))
            for r in root.iter("ConceptRelation")}
    assert ("pr.1", "possessive", "pr.1x002DPoss") in rels
    assert all(t != "other" for _, t, _ in rels)


@pytest.mark.parametrize("code, problem", [
    ("en", None), ("cmn-Hans", None), ("mul", None), ("uk", None),
    ("obj", "invalid"), ("ua", "invalid"), ("", "missing"), (None, "missing"),
    ("qgu", "private use"), ("qaa", "private use"), ("x-foo", "private use"),
])
def test_language_problem(code, problem):
    assert language_problem(code) == problem


def test_unusable_languages_are_skipped(tmp_path, capsys):
    good = _write(tmp_path / "a.xml", _LMF_HEAD.format(id="a", lang="fr") + _LMF_TAIL)
    bad = _write(tmp_path / "b.xml", _LMF_HEAD.format(id="b", lang="qgu") + _LMF_TAIL)
    fixed = _write(tmp_path / "c.xml", _LMF_HEAD.format(id="c", lang="ua") + _LMF_TAIL)
    assert batch.drop_unusable_languages([good, bad, fixed]) == [good, fixed]
    assert "private use language code 'qgu'" in capsys.readouterr().out


def test_synthesise_merges_only_planned_files(tmp_path, capsys):
    for name in ("cili-1.0.xml", "oewn-2025+.xml", "oewn-2025.xml",
                 "kin-0.1.xml", "mtg-1.0.xml"):
        _write(tmp_path / name, "<CygnetResource/>")
    _write(tmp_path / "manifest.txt", "oewn-2025+.xml\nkin-0.1.xml\n")
    names = [f.name for f in synth.select_files(tmp_path)]
    # CILI and mtg are always merged; the stale base edition is not
    assert names == ["cili-1.0.xml", "kin-0.1.xml", "mtg-1.0.xml", "oewn-2025+.xml"]
    assert "oewn-2025.xml" in capsys.readouterr().out


def test_synthesise_without_manifest_merges_everything(tmp_path):
    for name in ("cili-1.0.xml", "a-1.xml", "b-1.xml"):
        _write(tmp_path / name, "<CygnetResource/>")
    assert len(synth.select_files(tmp_path)) == 3


def test_batch_writes_manifest(tmp_path, monkeypatch):
    """Step 2 lists both converted and reused outputs in the manifest."""
    raw, toml = _raw_release(tmp_path)
    out = tmp_path / "presynth"
    out.mkdir()
    _write(out / "kin-ar-0.1.xml", "<CygnetResource/>")    # reused
    _write(out / "stale-9.xml", "<CygnetResource/>")       # not planned

    class FakeConverter:
        def __init__(self, **kwargs):
            pass

        def read_metadata(self, path):
            h = read_lexicon_header(Path(path))
            self.lexicon_id, self.lexicon_version = h.id, h.version
            return None, None

        def convert_from_tree(self, root):
            pass

        def save(self, path):
            Path(path).write_text("<CygnetResource/>")

    monkeypatch.setattr(batch, "WordNetToCygnetConverter", FakeConverter)
    monkeypatch.setattr(batch, "_EN_BASE_IDS", frozenset({"omw-arb"}))
    monkeypatch.setattr(batch, "_EN_BASE_FILENAME_PATTERNS", ("omw-arb",))
    batch.batch_convert(cili_file="cili.xml", toml_path=toml, raw_wns_dir=raw,
                        output_dir=out)
    planned = (out / "manifest.txt").read_text().split()
    assert sorted(planned) == ["kin-0.1.xml", "kin-ar-0.1.xml", "omw-arb-0.1.xml"]
