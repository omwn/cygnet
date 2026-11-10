"""Tests for multi-lexicon releases linked by a 'mul' concepts lexicon.

Covers cyg/multilex.py (language matching, concept map) and the planning
and manifest steps in 2_batch_convert_lmfs.py (which member lexicons to
keep, under which language code, and which outputs to reuse).  The
cleaner's handling of a release is tested in test_clean.py, the merge's in
test_lmf_merge.py.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from cyg.clean import CHANGED_SUFFIX, CleanOptions, CleanResult
from cyg.lmf import DIRECTION_SWAP_CONFIG_FILENAME
from cyg.multilex import (
    build_concept_map,
    fix_language,
    language_problem,
    match_language,
    read_lexicon_header,
)
from cyg.presynth import (
    read_manifest,
    read_manifest_converter,
    source_hash,
    write_manifest,
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


def test_build_concept_map(tmp_path):
    concepts = build_concept_map(_write(tmp_path / "kin.xml", _CONCEPTS))
    assert concepts["K:Pa"] == "i100"
    assert concepts["K:Fa"] is None


def test_read_lexicon_header(tmp_path):
    h = read_lexicon_header(_write(tmp_path / "m.xml", _MEMBER))
    assert (h.id, h.version, h.language) == ("kin-ar", "0.1", "ar")


# --- Converter in shared-concept mode ----------------------------------------

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
                 '<LexicalResource><Lexicon id="kin" language="mul"/></LexicalResource>')
    ar = _write(tmp_path / "kin-ar-0.1.xml",
                '<LexicalResource><Lexicon id="kin-ar" language="arb"/></LexicalResource>')
    assert synth.is_concepts_lexicon(mul)
    assert not synth.is_concepts_lexicon(ar)


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
        _write(tmp_path / name, "<LexicalResource/>")
    _write(tmp_path / "manifest.txt", "oewn-2025+.xml\nkin-0.1.xml\n")
    names = [f.name for f in synth.select_files(tmp_path)]
    # CILI and mtg are always merged; the stale base edition is not
    assert names == ["cili-1.0.xml", "kin-0.1.xml", "mtg-1.0.xml", "oewn-2025+.xml"]
    assert "oewn-2025.xml" in capsys.readouterr().out


def test_synthesise_without_manifest_merges_everything(tmp_path):
    for name in ("cili-1.0.xml", "a-1.xml", "b-1.xml"):
        _write(tmp_path / name, "<LexicalResource/>")
    assert len(synth.select_files(tmp_path)) == 3


def test_merge_order_puts_low_trust_last_worst_last(tmp_path, monkeypatch):
    """Low-trust wordnets go after all others (worst last) but before mtg."""
    monkeypatch.setattr(synth, "LOW_TRUST_RESOURCES", ("Uz-uz", "bad-xx"))
    names = ("Uz-uz-1.0.xml", "bad-xx-2.xml", "cili-1.0.xml", "fr-1.xml",
             "mtg-1.0.xml", "oewn-2025.xml", "zz-1.xml")
    for name in names:
        _write(tmp_path / name, "<LexicalResource/>")
    order = [f.name for f in synth.merge_order(sorted(tmp_path.glob("*.xml")))]
    assert order == ["cili-1.0.xml", "oewn-2025.xml", "fr-1.xml", "zz-1.xml",
                     "bad-xx-2.xml", "Uz-uz-1.0.xml", "mtg-1.0.xml"]


class _FakeCleaner:
    """Stands in for LmfCleaner: reads the header, writes a stub."""

    def __init__(self, source, cili, options=None):
        self.source = Path(source)

    def clean(self, out_dir):
        h = read_lexicon_header(self.source)
        out = Path(out_dir) / f"{h.id}-{h.version}.xml"
        out.write_text("<LexicalResource/>")
        log = out.with_name(out.stem + "_log.json")
        log.write_text('{"changes": {}}')
        return CleanResult(out, log, False)


def _run_batch(tmp_path, monkeypatch, presynth_files: dict[str, str]) -> Path:
    """Run Step 2 on the test release with presynth_files already in place."""
    raw, toml = _raw_release(tmp_path)
    out = tmp_path / "presynth"
    out.mkdir(exist_ok=True)
    for name, text in presynth_files.items():
        _write(out / name, text)
    cili = _write(tmp_path / "cili.tsv",
                  "ili_id\tstatus\tsuperseded_by\torigin\tdefinition\n"
                  "i100\t1\t\tpwn-3.0:00000001-n\td\n")
    monkeypatch.setattr(batch, "LmfCleaner", _FakeCleaner)
    batch.clean_all(cili_file=cili, toml_path=toml, raw_wns_dir=raw, output_dir=out)
    return out


def _current_converter() -> str:
    return source_hash(batch.CLEANER_SOURCES)


def test_batch_applies_direction_swaps_from_output_dir(tmp_path, monkeypatch):
    """The swap config Step 1.5 writes into the output directory reaches the
    cleaner of the lexicon it names, and no other."""
    seen: dict[str, CleanOptions] = {}

    class Recording(_FakeCleaner):
        def __init__(self, source, cili, options=None):
            super().__init__(source, cili, options)
            seen[self.source.name] = options

    raw, toml = _raw_release(tmp_path)
    out = tmp_path / "presynth"
    out.mkdir()
    measured = {"reversed": 5, "comparable": 5, "reversed_share": 1.0, "swapped": True}
    paired = {"reversed": None, "comparable": None, "reversed_share": None, "swapped": True}
    _write(out / DIRECTION_SWAP_CONFIG_FILENAME,
           json.dumps({"kin-ar": {"hypernym": measured, "hyponym": paired}}))
    cili = _write(tmp_path / "cili.tsv",
                  "ili_id\tstatus\tsuperseded_by\torigin\tdefinition\n"
                  "i100\t1\t\tpwn-3.0:00000001-n\td\n")
    monkeypatch.setattr(batch, "LmfCleaner", Recording)

    batch.clean_all(cili_file=cili, toml_path=toml, raw_wns_dir=raw, output_dir=out)

    assert seen["kin-ar-0.1.xml"].direction_swaps == frozenset({"hypernym", "hyponym"})
    assert set(seen["kin-ar-0.1.xml"].swap_stats) == {"hypernym"}
    assert seen["kin-0.1.xml"].direction_swaps == frozenset()


def test_batch_members_require_the_cleaned_concepts_version(tmp_path, monkeypatch):
    """The concepts file is cleaned first; a member then Requires the
    version its cleaned output carries, suffix included."""
    seen: dict[str, CleanOptions] = {}

    class Suffixing(_FakeCleaner):
        def __init__(self, source, cili, options=None):
            super().__init__(source, cili, options)
            seen[self.source.name] = options

        def clean(self, out_dir):
            h = read_lexicon_header(self.source)
            version = h.version + CHANGED_SUFFIX if h.language == "mul" else h.version
            out = Path(out_dir) / f"{h.id}-{h.version}.xml"
            out.write_text(f'<LexicalResource><Lexicon id="{h.id}" version="{version}" '
                           f'language="{h.language}"/></LexicalResource>')
            log = out.with_name(out.stem + "_log.json")
            log.write_text('{"changes": {}}')
            return CleanResult(out, log, True)

    raw, toml = _raw_release(tmp_path)
    out = tmp_path / "presynth"
    cili = _write(tmp_path / "cili.tsv",
                  "ili_id\tstatus\tsuperseded_by\torigin\tdefinition\n"
                  "i100\t1\t\tpwn-3.0:00000001-n\td\n")
    monkeypatch.setattr(batch, "LmfCleaner", Suffixing)

    batch.clean_all(cili_file=cili, toml_path=toml, raw_wns_dir=raw, output_dir=out)

    assert list(seen)[0] == "kin-0.1.xml"
    assert seen["kin-ar-0.1.xml"].requires == ("kin", "0.1" + CHANGED_SUFFIX)


def test_batch_writes_manifest(tmp_path, monkeypatch):
    """Step 2 lists both cleaned and reused outputs in the manifest."""
    manifest = f"# converter {_current_converter()}\nkin-ar-0.1.xml\n"
    out = _run_batch(tmp_path, monkeypatch, {
        "manifest.txt": manifest,
        "kin-ar-0.1.xml": "<old/>",           # reused
        "stale-9.xml": "<LexicalResource/>",  # not planned
    })
    assert sorted(read_manifest(out)) == [
        "kin-0.1.xml", "kin-ar-0.1.xml", "omw-arb-0.1.xml"]
    assert read_manifest_converter(out) == _current_converter()


def test_batch_reuses_output_of_same_converter(tmp_path, monkeypatch):
    """An output the same cleaner made is not cleaned again."""
    manifest = f"# converter {_current_converter()}\nkin-ar-0.1.xml\n"
    out = _run_batch(tmp_path, monkeypatch, {
        "manifest.txt": manifest, "kin-ar-0.1.xml": "<old/>"})
    assert (out / "kin-ar-0.1.xml").read_text() == "<old/>"


@pytest.mark.parametrize("manifest", [
    "# converter 0123456789abcdef\nkin-ar-0.1.xml\n",  # another cleaner
    "kin-ar-0.1.xml\n",                                 # made before hashes
    None,                                               # no manifest
])
def test_batch_reconverts_output_of_other_converter(tmp_path, monkeypatch, manifest):
    """An output from another or unknown cleaner is cleaned again."""
    files = {"kin-ar-0.1.xml": "<old/>"}
    if manifest is not None:
        files["manifest.txt"] = manifest
    out = _run_batch(tmp_path, monkeypatch, files)
    assert (out / "kin-ar-0.1.xml").read_text() == "<LexicalResource/>"


def test_batch_reconverts_output_not_in_manifest(tmp_path, monkeypatch):
    """An output the manifest does not list is cleaned again."""
    manifest = f"# converter {_current_converter()}\nomw-arb-0.1.xml\n"
    out = _run_batch(tmp_path, monkeypatch, {
        "manifest.txt": manifest, "kin-ar-0.1.xml": "<old/>"})
    assert (out / "kin-ar-0.1.xml").read_text() == "<LexicalResource/>"


def test_source_hash_changes_with_content(tmp_path):
    a = _write(tmp_path / "a.py", "x = 1\n")
    first = source_hash([a])
    assert source_hash([a]) == first and len(first) == 16
    a.write_text("x = 2\n")
    assert source_hash([a]) != first


def test_manifest_round_trip(tmp_path):
    write_manifest(tmp_path, ["a-1.xml", "b-2.xml"], "0123456789abcdef")
    assert read_manifest(tmp_path) == ["a-1.xml", "b-2.xml"]
    assert read_manifest_converter(tmp_path) == "0123456789abcdef"
    write_manifest(tmp_path, ["a-1.xml"])
    assert read_manifest(tmp_path) == ["a-1.xml"]
    assert read_manifest_converter(tmp_path) is None
