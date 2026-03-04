#!/usr/bin/env python3
"""Populate the languages.name column in cygnet.db using langcodes."""

import sqlite3
import sys
from pathlib import Path

import langcodes


def populate_lang_names(db_path: str) -> None:
    """Add name column if absent, then fill it for every language code.

    Args:
        db_path: Path to cygnet.db.
    """
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    cols = {row[1] for row in cur.execute("PRAGMA table_info(languages)")}
    if "name" not in cols:
        cur.execute("ALTER TABLE languages ADD COLUMN name TEXT")

    rows = cur.execute("SELECT rowid, code FROM languages").fetchall()
    updated = 0
    for rowid, code in rows:
        try:
            name = langcodes.Language.get(code).display_name()
        except Exception:
            name = None
        cur.execute("UPDATE languages SET name = ? WHERE rowid = ?", (name, rowid))
        updated += 1

    con.commit()
    con.close()
    print(f"Updated {updated} language names in {db_path}")


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else "web/cygnet.db"
    if not Path(db_path).exists():
        print(f"Error: {db_path} not found", file=sys.stderr)
        sys.exit(1)
    populate_lang_names(db_path)
