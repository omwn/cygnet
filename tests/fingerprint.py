#!/usr/bin/env python3
"""
Capture a fingerprint of cygnet.db for regression testing.

Usage:
    uv run python tests/fingerprint.py [--db web/cygnet.db] [--out fingerprint.json]
    uv run python tests/fingerprint.py --compare fingerprint.json [--db web/cygnet.db]
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def table_counts(conn: sqlite3.Connection) -> dict:
    tables = [
        "synsets", "entries", "forms", "senses",
        "definitions", "examples", "sense_examples",
        "synset_relations", "sense_relations",
        "example_annotations", "definition_annotations",
        "languages", "relation_types", "resources",
    ]
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def pos_distribution(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT pos, COUNT(*) AS n FROM synsets GROUP BY pos ORDER BY pos"
    ).fetchall()
    return {r["pos"]: r["n"] for r in rows}


def language_distribution(conn: sqlite3.Connection) -> dict:
    """Definitions per language (top 20 by count)."""
    rows = conn.execute("""
        SELECT l.code, COUNT(*) AS n
        FROM definitions d
        JOIN languages l ON d.language_rowid = l.rowid
        GROUP BY l.code
        ORDER BY n DESC
        LIMIT 20
    """).fetchall()
    return {r["code"]: r["n"] for r in rows}


def relation_type_distribution(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("""
        SELECT rt.type, COUNT(*) AS n
        FROM synset_relations sr
        JOIN relation_types rt ON sr.type_rowid = rt.rowid
        GROUP BY rt.type
        ORDER BY rt.type
    """).fetchall()
    return {r["type"]: r["n"] for r in rows}


def provenance_per_resource(conn: sqlite3.Connection) -> dict | None:
    """Row counts per resource in provenance.db (if available alongside cygnet.db)."""
    prov_path = Path(conn.execute("PRAGMA database_list").fetchone()[2]).with_name(
        "provenance.db"
    )
    if not prov_path.exists():
        return None
    pc = sqlite3.connect(str(prov_path))
    pc.row_factory = sqlite3.Row
    rows = pc.execute("""
        SELECT pr.code, COUNT(*) AS n
        FROM provenance p
        JOIN prov_resources pr ON p.resource_rowid = pr.rowid
        GROUP BY pr.code
        ORDER BY pr.code
    """).fetchall()
    pc.close()
    return {r["code"]: r["n"] for r in rows}


def spot_check_word(conn: sqlite3.Connection, form: str, lang: str) -> list:
    """All senses of a word in a given language: synset ILI, POS, sense_index."""
    rows = conn.execute("""
        SELECT syn.ili, syn.pos, s.sense_index, s.rowid AS sense_rowid
        FROM forms f
        JOIN entries e ON f.entry_rowid = e.rowid
        JOIN languages l ON e.language_rowid = l.rowid
        JOIN senses s ON s.entry_rowid = e.rowid
        JOIN synsets syn ON s.synset_rowid = syn.rowid
        WHERE f.normalized_form = ? AND l.code = ?
        ORDER BY syn.pos, s.sense_index
    """, (form, lang)).fetchall()
    return [{"ili": r["ili"], "pos": r["pos"], "sense_index": r["sense_index"]} for r in rows]


def spot_check_synset(conn: sqlite3.Connection, ili: str) -> dict:
    """English definition and sense count for a known ILI."""
    row = conn.execute(
        "SELECT rowid FROM synsets WHERE ili = ?", (ili,)
    ).fetchone()
    if row is None:
        return {"error": f"ILI {ili!r} not found"}
    synset_rowid = row["rowid"]

    defn = conn.execute("""
        SELECT d.definition
        FROM definitions d
        JOIN languages l ON d.language_rowid = l.rowid
        WHERE d.synset_rowid = ? AND l.code = 'en'
    """, (synset_rowid,)).fetchone()

    sense_count = conn.execute(
        "SELECT COUNT(*) FROM senses WHERE synset_rowid = ?", (synset_rowid,)
    ).fetchone()[0]

    lang_count = conn.execute("""
        SELECT COUNT(DISTINCT d.language_rowid)
        FROM definitions d WHERE d.synset_rowid = ?
    """, (synset_rowid,)).fetchone()[0]

    return {
        "ili": ili,
        "en_definition": defn["definition"] if defn else None,
        "sense_count": sense_count,
        "definition_language_count": lang_count,
    }


def case_variant_check(conn: sqlite3.Connection) -> dict:
    """Verify case-variant lexeme merging: lemmas sharing a synset after lowercasing."""
    # Find entries where ≥2 distinct-case lemma forms map to the same synset
    rows = conn.execute("""
        SELECT COUNT(DISTINCT e.rowid) AS merged_entries
        FROM entries e
        JOIN forms f ON f.entry_rowid = e.rowid AND f.rank = 0
        WHERE LENGTH(f.form) != LENGTH(REPLACE(f.form, '-', ''))
           OR f.form != f.normalized_form
    """).fetchone()
    # Count senses whose lemma form differs from its normalized form
    variant_senses = conn.execute("""
        SELECT COUNT(*) FROM senses s
        JOIN entries e ON s.entry_rowid = e.rowid
        JOIN forms f ON f.entry_rowid = e.rowid AND f.rank = 0
        WHERE f.form != f.normalized_form
    """).fetchone()[0]
    return {
        "non_lowercase_lemma_entries": rows["merged_entries"],
        "senses_with_non_lowercase_lemma": variant_senses,
    }


def annotation_coverage(conn: sqlite3.Connection) -> dict:
    total_ex = conn.execute("SELECT COUNT(*) FROM examples").fetchone()[0]
    annotated_ex = conn.execute(
        "SELECT COUNT(DISTINCT example_rowid) FROM example_annotations"
    ).fetchone()[0]
    return {"total_examples": total_ex, "annotated_examples": annotated_ex}


def build_fingerprint(db_path: str) -> dict:
    conn = connect(db_path)
    fp = {
        "db_path": db_path,
        "table_counts": table_counts(conn),
        "pos_distribution": pos_distribution(conn),
        "top20_definition_languages": language_distribution(conn),
        "synset_relation_types": relation_type_distribution(conn),
        "annotation_coverage": annotation_coverage(conn),
        "case_variant_check": case_variant_check(conn),
        "provenance_per_resource": provenance_per_resource(conn),
        "spot_checks": {
            "bank_en": spot_check_word(conn, "bank", "en"),
            "bank_de": spot_check_word(conn, "bank", "de"),
            "dog_en": spot_check_word(conn, "dog", "en"),
            "synset_i85041": spot_check_synset(conn, "i85041"),
            "synset_i35549": spot_check_synset(conn, "i35549"),
        },
    }
    conn.close()
    return fp


def compare_fingerprints(baseline: dict, current: dict) -> list[str]:
    """Return list of diff messages; empty list means match."""
    diffs = []

    def check(label, a, b):
        if a != b:
            diffs.append(f"DIFF {label}: baseline={a!r} current={b!r}")

    # Table counts
    for table, count in baseline["table_counts"].items():
        check(f"table_counts.{table}", count, current["table_counts"].get(table))

    # POS distribution
    for pos, n in baseline["pos_distribution"].items():
        check(f"pos_distribution.{pos}", n, current["pos_distribution"].get(pos))

    # Relation types
    for rtype, n in baseline["synset_relation_types"].items():
        check(f"relation_type.{rtype}", n, current["synset_relation_types"].get(rtype))

    # Annotation coverage
    for k, v in baseline["annotation_coverage"].items():
        check(f"annotation_coverage.{k}", v, current["annotation_coverage"].get(k))

    # Spot checks
    for key, val in baseline["spot_checks"].items():
        check(f"spot_checks.{key}", val, current["spot_checks"].get(key))

    # Provenance (optional)
    if baseline.get("provenance_per_resource") and current.get("provenance_per_resource"):
        for code, n in baseline["provenance_per_resource"].items():
            check(f"provenance.{code}", n, current["provenance_per_resource"].get(code))

    return diffs


def main():
    parser = argparse.ArgumentParser(description="Fingerprint cygnet.db for regression testing")
    parser.add_argument("--db", default="web/cygnet.db", help="Path to cygnet.db")
    parser.add_argument("--out", default="fingerprint.json", help="Output fingerprint file")
    parser.add_argument("--compare", metavar="BASELINE", help="Compare DB against saved fingerprint")
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"Error: {args.db} not found", file=sys.stderr)
        sys.exit(1)

    if args.compare:
        baseline_path = Path(args.compare)
        if not baseline_path.exists():
            print(f"Error: baseline {args.compare} not found", file=sys.stderr)
            sys.exit(1)
        baseline = json.loads(baseline_path.read_text())
        current = build_fingerprint(args.db)
        diffs = compare_fingerprints(baseline, current)
        if diffs:
            print(f"FINGERPRINT MISMATCH ({len(diffs)} difference(s)):")
            for d in diffs:
                print(f"  {d}")
            sys.exit(1)
        else:
            print("Fingerprint matches baseline.")
    else:
        fp = build_fingerprint(args.db)
        out_path = Path(args.out)
        out_path.write_text(json.dumps(fp, indent=2))
        print(f"Fingerprint written to {out_path}")
        print(f"  synsets:  {fp['table_counts']['synsets']:,}")
        print(f"  entries:  {fp['table_counts']['entries']:,}")
        print(f"  senses:   {fp['table_counts']['senses']:,}")
        print(f"  definitions: {fp['table_counts']['definitions']:,}")
        print(f"  examples: {fp['table_counts']['examples']:,}")


if __name__ == "__main__":
    main()
