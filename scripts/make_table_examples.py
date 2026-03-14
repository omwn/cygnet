"""Generate LaTeX table of example statistics from presynth log files.

Reads bin/cygnets_presynth/*_log.json, filters to wordnets that have at least
one example, and prints a LaTeX tabular with included/excluded counts.

Usage:
    python3 scripts/make_table_examples.py
"""

from __future__ import annotations

import json
from pathlib import Path

from latex_utils import WORDNET_NAMES, fmt_int, stem_to_code

_LOG_DIR = Path(__file__).resolve().parent.parent / "bin" / "cygnets_presynth"


def main() -> None:
    """Read logs, filter, and print LaTeX tabular."""
    rows: list[tuple[str, int, int]] = []
    for log_path in sorted(_LOG_DIR.glob("*_log.json")):
        stem = log_path.stem.removesuffix("_log")
        data = json.loads(log_path.read_text())
        ex = data.get("statistics", {}).get("examples", {})
        total = ex.get("total_found", 0)
        if total == 0:
            continue
        skipped = ex.get("skipped", 0)
        code = stem_to_code(stem)
        name = WORDNET_NAMES.get(code, stem)
        rows.append((name, total - skipped, skipped))

    rows.sort(key=lambda r: r[0].lower())

    lines = [
        r"\begin{table}",
        r"    \centering",
        r"    \small",
        r"    \begin{tabular}{lrr} \toprule",
        r"         Wordnet & \# Included & \# Excluded \\ \midrule",
    ]
    for name, included, excluded in rows:
        lines.append(f"         {name} & {fmt_int(included)} & {fmt_int(excluded)} \\\\")
    lines += [
        r"    \bottomrule",
        r"    \end{tabular}",
        r"    \caption{Statistics of Examples}",
        r"    \label{tab:examples}",
        r"\end{table}",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
