"""Tests for scripts/paper_facts.py — figures the paper cites, as LaTeX macros."""

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

from cyg.merge import PROV_SCHEMA, SCHEMA

_PATH = Path(__file__).parent.parent / "scripts" / "paper_facts.py"
_spec = importlib.util.spec_from_file_location("paper_facts", _PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["paper_facts"] = _mod
_spec.loader.exec_module(_mod)

baseline_facts = _mod.baseline_facts
build_facts = _mod.build_facts
converted_facts = _mod.converted_facts
render_tex = _mod.render_tex


def _seed_db(path: Path) -> None:
    """Two languages plus 'mul'; CILI, PWN, three wordnets and a kinship release."""
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    for rowid, code in enumerate(["en", "ja", "mul"], 1):
        con.execute("INSERT INTO languages (rowid, code) VALUES (?, ?)", (rowid, code))
    for code in ["cili", "pwn", "oewn", "wnja", "tufs-ja",
                 "omw-kin", "omw-kin-en", "omw-kin-ja"]:
        con.execute("INSERT INTO resources (code, version) VALUES (?, '0.1')",
                    (code,))
    for rowid, lang in enumerate([1, 2, 3], 1):
        con.execute(
            "INSERT INTO entries (rowid, language_rowid, pos) VALUES (?, ?, 'NOUN')",
            (rowid, lang),
        )
    con.commit()
    con.close()


def _seed_build(tmp_path: Path) -> dict:
    """A minimal build tree: DB, presynth logs with a manifest, conflicts, toml."""
    db = tmp_path / "cygnet.db"
    _seed_db(db)
    presynth = tmp_path / "presynth"
    presynth.mkdir()
    (presynth / "manifest.txt").write_text("a-1.0.xml\nb-1.0.xml\nc-1.0.xml\n")
    for name, skipped in [("a-1.0", 5), ("b-1.0", 0), ("c-1.0", 7), ("stale-1.0", 100)]:
        log = {"statistics": {"examples": {"skipped": skipped}}}
        (presynth / f"{name}_log.json").write_text(json.dumps(log))
    conflicts = tmp_path / "conflicts.json"
    conflicts.write_text(json.dumps({
        "reversed_relations": [{"resource_id": "x"}, {"resource_id": "x"},
                               {"resource_id": "y"}],
        "cycles": [{"xml_stem": "z-1.0"}],
    }))
    toml = tmp_path / "wordnets.toml"
    toml.write_text(
        'el = ["https://github.com/igamasniak/omw-converters/G.xml.xz"]\n'
        'ja = ["https://github.com/bond-lab/wnja/wnja-2.1.tar.xz"]\n'
        '# old = ["https://github.com/igamasniak/omw-converters/commented-out.xml"]\n'
        'mul = ["https://github.com/omwn/omw-kinship/omw-kin-0.1.tar.xz"]\n'
    )
    raw = tmp_path / "raw"
    raw.mkdir()
    for name in ["G.xml.xz", "wnja-2.1.xml",
                 "omw-kin-0.1.xml", "omw-kin-en-0.1.xml", "omw-kin-ja-0.1.xml",
                 "omw-kin-zu-0.1.xml", "omw-kin-zu-0.0.xml"]:
        (raw / name).write_text("")
    return {"db": db, "presynth": presynth, "conflicts": conflicts, "toml": toml,
            "raw_wns": raw}


def test_coverage_counts_languages_and_wordnets(tmp_path):
    facts = build_facts(**_seed_build(tmp_path))
    cygnet = facts["cygnet"]
    assert cygnet["languages"] == 2       # 'mul' is not a language
    assert cygnet["wordnets"] == 4        # oewn, wnja, tufs-ja, and omw-kin once
    assert cygnet["tufs"] == 1
    assert cygnet["lexicons"] == 6        # every resource but cili and pwn


def test_examples_use_only_planned_files(tmp_path):
    facts = build_facts(**_seed_build(tmp_path))
    assert facts["examples"] == {"excluded": 12, "wordnets": 2}


def test_relations_count_rows_and_resources(tmp_path):
    facts = build_facts(**_seed_build(tmp_path))
    assert facts["relations"] == {"reversed": 3, "reversed-wordnets": 2,
                                  "loops": 1, "loop-wordnets": 1}


def test_converted_counts_converter_urls_plus_kenet():
    sources = {"a": ["https://github.com/igamasniak/omw-converters/x.xml.xz",
                     "https://github.com/igamasniak/omw-converters/y.xml.xz"],
               "b": ["https://example.org/z.xml"]}
    assert converted_facts(sources) == {"wordnets": 3}


def test_converted_ignores_commented_urls(tmp_path):
    facts = build_facts(**_seed_build(tmp_path))
    assert facts["converted"] == {"wordnets": 2}   # one converter URL + KeNet


def test_tex_has_separators_and_fallback():
    tex = render_tex({"cygnet": {"senses": 3694232}, "x": {"ratio": 0.5}})
    assert r"\expandafter\def\csname fact@cygnet.senses\endcsname{3,694,232}" in tex
    assert r"\csname fact@x.ratio\endcsname{0.5}" in tex
    assert r"\providecommand{\fact}" in tex


def test_multilingual_counts_languages_of_built_version(tmp_path):
    """Three language files of version 0.1 (not the concepts file or 0.0); two kept."""
    facts = build_facts(**_seed_build(tmp_path))
    assert facts["multilingual"] == {"omw-kin": {"languages": 3, "kept": 2}}


def test_baseline_measured_like_current(tmp_path):
    paths = _seed_build(tmp_path)
    assert baseline_facts(paths["db"], ("omw-kin",)) == build_facts(**paths)["cygnet"]


def test_wordnets_include_failed_downloads(tmp_path):
    """A wordnets.toml URL with no download still counts as a wordnet."""
    paths = _seed_build(tmp_path)
    with paths["toml"].open("a") as f:
        f.write('ga = ["http://irishwn.teanga.io/downloads/gfong-0.1.xml.gz"]\n')
    assert build_facts(**paths)["cygnet"]["wordnets"] == 5   # 4 built + gfong


def test_languages_include_failed_downloads(tmp_path):
    """A failed download's language counts, but not twice if already present."""
    paths = _seed_build(tmp_path)
    with paths["toml"].open("a") as f:
        f.write('ga = ["http://irishwn.teanga.io/downloads/gfong-0.1.xml.gz"]\n'
                'en = ["https://example.org/lost-en-1.0.xml"]\n')
    assert build_facts(**paths)["cygnet"]["languages"] == 3   # en, ja + ga


def _seed_dannet(tmp_path: Path) -> tuple[Path, Path]:
    """cygnet.db and provenance.db with omw-da, TUFS Arabic and unreleased dn.

    Entries: 1 Arabic (arb), 2 Arabic (ar), 3 Danish from omw-da and dn,
    4 Danish from dn alone.  Synset 1 is from both Danish wordnets, 2 from dn.
    """
    db, prov = tmp_path / "cygnet.db", tmp_path / "provenance.db"
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    for rowid, code in enumerate(["arb", "ar", "da"], 1):
        con.execute("INSERT INTO languages (rowid, code) VALUES (?, ?)", (rowid, code))
    for code in ["cili", "omw-arb", "tufs-ar", "omw-da", "dn"]:
        con.execute("INSERT INTO resources (code, version) VALUES (?, '1')", (code,))
    for rowid, lang in [(1, 1), (2, 2), (3, 3), (4, 3)]:
        con.execute("INSERT INTO entries (rowid, language_rowid, pos) "
                    "VALUES (?, ?, 'NOUN')", (rowid, lang))
    for rowid in [1, 2]:
        con.execute("INSERT INTO synsets (rowid, pos) VALUES (?, 'NOUN')", (rowid,))
    con.commit()
    con.close()

    con = sqlite3.connect(prov)
    con.executescript(PROV_SCHEMA)
    con.executemany("INSERT INTO prov_resources (rowid, code) VALUES (?, ?)",
                    [(1, "omw-arb"), (2, "tufs-ar"), (3, "omw-da"), (4, "dn")])
    con.executemany("INSERT INTO prov_tables (rowid, name) VALUES (?, ?)",
                    [(1, "synsets"), (2, "entries")])
    rows = [(2, 1, 1), (2, 2, 2), (2, 3, 3), (2, 3, 4), (2, 4, 4),
            (1, 1, 3), (1, 1, 4), (1, 2, 4)]
    con.executemany("INSERT INTO provenance (table_rowid, item_rowid, resource_rowid) "
                    "VALUES (?, ?, ?)", rows)
    con.commit()
    con.close()
    return db, prov


def test_ar_and_arb_count_as_one_language(tmp_path):
    db, prov = _seed_dannet(tmp_path)
    assert baseline_facts(db, (), prov)["languages"] == 2     # Arabic, Danish


def test_unreleased_dannet_and_its_rows_not_counted(tmp_path):
    db, prov = _seed_dannet(tmp_path)
    cygnet = baseline_facts(db, (), prov)
    assert cygnet["wordnets"] == 3        # omw-arb, tufs-ar, omw-da; not dn
    assert cygnet["lexicons"] == 3
    assert cygnet["entries"] == 3         # entry 4 came from dn alone
    assert cygnet["synsets"] == 1         # synset 2 came from dn alone


def test_unreleased_without_provenance_omits_sizes(tmp_path):
    db, _ = _seed_dannet(tmp_path)
    cygnet = baseline_facts(db, ())
    assert cygnet["wordnets"] == 3
    assert "entries" not in cygnet and "synsets" not in cygnet
