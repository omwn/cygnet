#!/usr/bin/env python3
"""Aggregate converter log files and print a cross-wordnet summary table.

For every numeric metric stored in the per-wordnet ``*_log.json`` files this
script shows: the total across all wordnets, the number of wordnets that have
at least one occurrence, and the top contributors.

Usage:
    uv run python scripts/log_summary.py               # all logs in default dir
    uv run python scripts/log_summary.py bin/foo/      # custom directory
    uv run python scripts/log_summary.py --top 5       # show top-5 wordnets
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PRESYNTH_DIR = PROJECT_ROOT / "bin" / "cygnets_presynth"

# ---------------------------------------------------------------------------
# Metrics to extract: (display_name, dotted_path_into_log_dict)
# Grouped by section for readability in the output.
# ---------------------------------------------------------------------------

METRICS: list[tuple[str, str, str]] = [
    # section, display label, dot-path
    ("Statistics",     "concepts (newly created)",          "statistics.concepts.newly_created"),
    ("Statistics",     "concepts (from CILI)",              "statistics.concepts.from_cili"),
    ("Statistics",     "concepts (total)",                  "statistics.concepts.total"),
    ("Statistics",     "lexemes created",                   "statistics.lexemes.created"),
    ("Statistics",     "senses created",                    "statistics.senses.created"),
    ("Statistics",     "glosses created",                   "statistics.glosses.created"),
    ("Statistics",     "concept relations created",         "statistics.relations.concept_relations_created"),
    ("Statistics",     "sense relations created",           "statistics.relations.sense_relations_created"),
    ("Statistics",     "examples found",                    "statistics.examples.total_found"),
    ("Statistics",     "examples processed",                "statistics.examples.processed"),
    ("Statistics",     "examples skipped",                  "statistics.examples.skipped"),
    ("Relations",      "missing inverses added (concept)",  "relation_processing.missing_inverses_added.concept_relations.count"),
    ("Relations",      "missing inverses added (sense)",    "relation_processing.missing_inverses_added.sense_relations.count"),
    ("Relations",      "duplicates removed (concept)",      "relation_processing.duplicates_removed.concept_relations.count"),
    ("Relations",      "duplicates removed (sense)",        "relation_processing.duplicates_removed.sense_relations.count"),
    ("Relations",      "unknown types (concept)",           "relation_processing.unknown_relation_types.concept_relations.count"),
    ("Relations",      "unknown types (sense)",             "relation_processing.unknown_relation_types.sense_relations.count"),
    ("Relations",      "skipped existing (concept)",        "relation_processing.skipped_existing_relations.concept_relations.count"),
    ("Relations",      "category mismatches",               "relation_processing.ontological_category_mismatches.count"),
    ("Data quality",   "lexeme merges",                     "lexeme_merging.total_merges"),
    ("Data quality",   "POS mismatches synset↔CILI",        "synset_concept_pos_mismatches.total_count"),
    ("Data quality",   "POS mismatches lexeme↔concept",     "lexeme_concept_pos_mismatches.total_count"),
    ("Data quality",   "missing CILI concepts",             "missing_cili_concepts.count"),
    ("Data quality",   "missing lemmas",                    "missing_lemmas.count"),
    ("Data quality",   "sense missing synset",              "sense_missing_synset.count"),
    ("Data quality",   "synset not found",                  "synset_not_found.count"),
]


def _get(d: dict, path: str) -> int:
    """Retrieve a nested int value by dot-separated path; return 0 if missing."""
    for key in path.split("."):
        if not isinstance(d, dict):
            return 0
        d = d.get(key, {})
    return int(d) if isinstance(d, (int, float)) else 0


def load_logs(log_dir: Path) -> dict[str, dict]:
    """Return {stem: log_dict} for every *_log.json in log_dir."""
    logs: dict[str, dict] = {}
    for p in sorted(log_dir.glob("*_log.json")):
        stem = p.stem[: -len("_log")]   # strip trailing "_log"
        try:
            with open(p, encoding="utf-8") as f:
                logs[stem] = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  Warning: could not read {p.name}: {e}")
    return logs


def fmt(n: int) -> str:
    return f"{n:,}" if n else "–"


def print_summary(logs: dict[str, dict], top_n: int, log_dir: Path) -> None:
    if not logs:
        print("No log files found.")
        return

    n_total = len(logs)
    try:
        display_dir = log_dir.relative_to(PROJECT_ROOT)
    except ValueError:
        display_dir = log_dir
    print(f"Loaded {n_total} log file(s) from {display_dir}\n")

    # Compute totals and per-wordnet values for each metric
    current_section = ""
    col_label = 55
    col_total = 12
    col_wns   = 8

    header = (
        f"{'Metric':<{col_label}}  {'Total':>{col_total}}  {'WNs':>{col_wns}}  Top contributors"
    )
    rule = "─" * len(header)

    print(rule)
    print(header)
    print(rule)

    for section, label, path in METRICS:
        if section != current_section:
            if current_section:
                print()
            print(f"  {section.upper()}")
            current_section = section

        # Collect value per wordnet
        by_wn: list[tuple[int, str]] = []
        for wn_id, log in logs.items():
            v = _get(log, path)
            if v:
                by_wn.append((v, wn_id))

        total = sum(v for v, _ in by_wn)
        n_wns = len(by_wn)

        # Build top-N string
        by_wn.sort(reverse=True)
        top_parts = [f"{wn_id} ({fmt(v)})" for v, wn_id in by_wn[:top_n]]
        top_str = ",  ".join(top_parts)
        if n_wns > top_n:
            top_str += f",  …+{n_wns - top_n} more"

        total_str = fmt(total)
        wns_str   = f"{n_wns}/{n_total}" if n_wns else "–"

        print(
            f"  {label:<{col_label - 2}}  {total_str:>{col_total}}  {wns_str:>{col_wns}}"
            + (f"  {top_str}" if top_str else "")
        )

    print(rule)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarise converter log files across all wordnets.",
    )
    parser.add_argument(
        "log_dir", nargs="?", type=Path, default=PRESYNTH_DIR,
        help=f"Directory containing *_log.json files (default: {PRESYNTH_DIR})",
    )
    parser.add_argument(
        "--top", type=int, default=3, metavar="N",
        help="Number of top-contributing wordnets to list per metric (default: 3)",
    )
    args = parser.parse_args()

    logs = load_logs(args.log_dir)
    print_summary(logs, args.top, args.log_dir)


if __name__ == "__main__":
    main()
