#!/usr/bin/env python3
"""Report summary statistics for the merged Cygnet database.

Answers "how big is this wordnet and what's it made of" — languages,
concepts, senses, relations, definitions, examples, with breakdowns by
part of speech and relation type. Complements scripts/report.py, which
reports data-quality *issues* rather than overall size.

Usage:
    uv run python scripts/statistics.py                # plain text
    uv run python scripts/statistics.py --md > reports/_statistics.md
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "web" / "cygnet.db"


def _fmt(n: int) -> str:
    return f"{n:,}"


def fetch_overview(cur: sqlite3.Cursor) -> list[tuple[str, int]]:
    """Headline counts, in the order they should be displayed."""

    def count(table: str) -> int:
        return cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    return [
        ("Languages", count("languages")),
        ("Source resources", count("resources")),
        ("Concepts (synsets)", count("synsets")),
        ("Entries (lexemes)", count("entries")),
        ("Wordforms", count("forms")),
        ("Senses", count("senses")),
        ("Definitions", count("definitions")),
        ("Examples", count("examples")),
        ("Concept relations", count("synset_relations")),
        ("Sense relations", count("sense_relations")),
    ]


def fetch_column_breakdown(
    cur: sqlite3.Cursor, table: str, column: str
) -> list[tuple[str, int]]:
    """(value, count) for *column* in *table*, ranked most-common first."""
    return cur.execute(
        f"SELECT {column}, COUNT(*) FROM {table} "
        f"GROUP BY {column} ORDER BY COUNT(*) DESC"
    ).fetchall()


def fetch_senses_by_pos(cur: sqlite3.Cursor) -> list[tuple[str, int]]:
    """Senses grouped by their concept's part of speech."""
    return cur.execute("""
        SELECT syn.pos, COUNT(*)
        FROM senses se
        JOIN synsets syn ON syn.rowid = se.synset_rowid
        GROUP BY syn.pos
        ORDER BY COUNT(*) DESC
    """).fetchall()


def fetch_relation_breakdown(cur: sqlite3.Cursor, table: str) -> list[tuple[str, int]]:
    """Relation counts grouped by type, for synset_relations or sense_relations."""
    return cur.execute(f"""
        SELECT rt.type, COUNT(*)
        FROM {table} r
        JOIN relation_types rt ON rt.rowid = r.type_rowid
        GROUP BY rt.type
        ORDER BY COUNT(*) DESC
    """).fetchall()


def fetch_senses_by_language(cur: sqlite3.Cursor) -> list[tuple[str, str, int]]:
    """(code, name, sense_count) per language, ranked most-senses first."""
    return cur.execute("""
        SELECT l.code, l.name, COUNT(*)
        FROM senses se
        JOIN entries e ON e.rowid = se.entry_rowid
        JOIN languages l ON l.rowid = e.language_rowid
        GROUP BY l.rowid
        ORDER BY COUNT(*) DESC
    """).fetchall()


def _section(lines: list[str], title: str, markdown: bool) -> None:
    lines.append(f"\n## {title}" if markdown else f"\n{title}\n{'-' * len(title)}")


def _table(
    lines: list[str],
    headers: list[str],
    rows: list[tuple],
    markdown: bool,
) -> None:
    str_rows = [[_fmt(c) if isinstance(c, int) else str(c) for c in row] for row in rows]
    if markdown:
        lines.append(f"\n| {' | '.join(headers)} |")
        aligns = ["---"] + ["---:"] * (len(headers) - 1)
        lines.append(f"|{'|'.join(aligns)}|")
        lines += [f"| {' | '.join(row)} |" for row in str_rows]
    else:
        widths = [
            max(len(headers[i]), *(len(row[i]) for row in str_rows)) if str_rows
            else len(headers[i])
            for i in range(len(headers))
        ]
        lines.append("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
        lines += [
            "  ".join(c.ljust(w) for c, w in zip(row, widths)) for row in str_rows
        ]


def format_statistics(markdown: bool) -> str:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    try:
        overview = fetch_overview(cur)
        languages = fetch_senses_by_language(cur)
        concepts_by_pos = fetch_column_breakdown(cur, "synsets", "pos")
        entries_by_pos = fetch_column_breakdown(cur, "entries", "pos")
        senses_by_pos = fetch_senses_by_pos(cur)
        concept_rel_by_type = fetch_relation_breakdown(cur, "synset_relations")
        sense_rel_by_type = fetch_relation_breakdown(cur, "sense_relations")
    finally:
        con.close()

    lines: list[str] = []
    if markdown:
        lines.append("# Cygnet Database Statistics")
        lines.append("\nGenerated from `web/cygnet.db`.")
    else:
        lines += ["CYGNET DATABASE STATISTICS", "=" * 26]

    _section(lines, "Overview", markdown)
    _table(lines, ["Metric", "Count"], overview, markdown)

    _section(lines, "Senses by language", markdown)
    _table(
        lines, ["Language", "Code", "Senses"],
        [(name or code, code, n) for code, name, n in languages],
        markdown,
    )

    _section(lines, "Concepts by part of speech", markdown)
    _table(lines, ["POS", "Concepts"], concepts_by_pos, markdown)

    _section(lines, "Entries by part of speech", markdown)
    _table(lines, ["POS", "Entries"], entries_by_pos, markdown)

    _section(lines, "Senses by part of speech (of their concept)", markdown)
    _table(lines, ["POS", "Senses"], senses_by_pos, markdown)

    _section(lines, "Concept relations by type", markdown)
    _table(lines, ["Relation type", "Count"], concept_rel_by_type, markdown)

    _section(lines, "Sense relations by type", markdown)
    _table(lines, ["Relation type", "Count"], sense_rel_by_type, markdown)

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Report summary statistics for the merged Cygnet database.",
    )
    parser.add_argument("--md", action="store_true", help="Output in Markdown format")
    args = parser.parse_args()

    if not DB_PATH.exists():
        raise SystemExit(f"{DB_PATH} not found — run a build first.")

    print(format_statistics(args.md))


if __name__ == "__main__":
    main()
