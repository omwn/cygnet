"""Generate a LaTeX table of wordnet resources for the appendix.

Queries web/cygnet.db (and web/provenance.db for word counts) and outputs a
longtable with columns: Language, Wordnet, Concepts, Senses, Words, Licence.

Usage:
    python3 scripts/make_table_wordnets.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from latex_utils import WORDNET_NAMES, fmt_int, licence_label

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DB = _REPO_ROOT / "web" / "cygnet.db"
_PROV_DB = _REPO_ROOT / "web" / "provenance.db"

# Language code → display name overrides (supplements the DB languages table).
# Keys must match l.code exactly as stored in the DB.
_LANG_OVERRIDES: dict[str, str] = {
    "arb":      "Arabic, Standard",
    "cmn-Hans": "Chinese, Mandarin",
    "nb":       "Norwegian Bokmål",
    "nn":       "Norwegian Nynorsk",
    "zsm":      "Malay, Standard",
}


def _lang_name(code: str, db_name: str | None) -> str:
    return _LANG_OVERRIDES.get(code, db_name or code)


def main() -> None:
    main_con = sqlite3.connect(_DB)
    prov_con = sqlite3.connect(_PROV_DB)

    entries_tid = prov_con.execute(
        "SELECT rowid FROM prov_tables WHERE name='entries'"
    ).fetchone()[0]
    prov_word_counts: dict[str, int] = {
        code: cnt
        for code, cnt in prov_con.execute(
            """SELECT pr.code, COUNT(DISTINCT p.item_rowid)
               FROM provenance p
               JOIN prov_resources pr ON p.resource_rowid = pr.rowid
               WHERE p.table_rowid = ?
               GROUP BY pr.code""",
            (entries_tid,),
        )
    }

    raw_rows = main_con.execute(
        """SELECT r.code, r.label, l.code, l.name,
                  r.synset_count, r.sense_count, r.licence
           FROM resources r
           LEFT JOIN languages l ON r.language_rowid = l.rowid
           WHERE r.code NOT IN ('cili', 'pwn')""",
    ).fetchall()

    display_rows = [
        (_lang_name(lang_code or "", lang_name_db), code,
         WORDNET_NAMES.get(code, label), concepts, senses, lic)
        for code, label, lang_code, lang_name_db, concepts, senses, lic in raw_rows
    ]
    display_rows.sort(key=lambda r: (r[0].lower(), r[2].lower()))

    lines = [
        r"\begin{longtable}{llrrrl} \toprule",
        r"    Language & Wordnet & Concepts & Senses & Words & Licence \\ \midrule",
        r"    \endfirsthead",
        r"    \toprule",
        r"    Language & Wordnet & Concepts & Senses & Words & Licence \\ \midrule",
        r"    \endhead",
        r"    \midrule \multicolumn{6}{r}{\textit{continued \ldots}} \\",
        r"    \endfoot",
        r"    \bottomrule",
        r"    \endlastfoot",
    ]
    for lang, code, label, concepts, senses, lic in display_rows:
        words = prov_word_counts.get(code)
        lines.append(
            f"    {lang} & {label} & {fmt_int(concepts)} & {fmt_int(senses)}"
            f" & {fmt_int(words)} & {licence_label(lic)} \\\\"
        )
    lines += [
        r"\caption{Wordnet resources in Cygnet}",
        r"\label{tab:wordnets}",
        r"\end{longtable}",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
