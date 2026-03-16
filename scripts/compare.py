#!/usr/bin/env python3
"""
Compare Cygnet and wn library database statistics for the wordnets listed
in wordnets.toml.  Outputs a LaTeX table suitable for inclusion in a paper.

Usage:
    uv run python scripts/compare.py
"""

import re
import sqlite3
from pathlib import Path

import tomllib

import wn

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOML_PATH = PROJECT_ROOT / "wordnets.toml"
CYGNET_DB = PROJECT_ROOT / "web" / "cygnet.db"
WN_DATA_DIR = PROJECT_ROOT / "bin" / "wordnet_data"


def load_toml_urls(toml_path: Path) -> list[str]:
    """Return all wordnet URLs from wordnets.toml in declaration order."""
    with open(toml_path, "rb") as f:
        config = tomllib.load(f)
    return [url for urls in config.values() for url in urls]


def url_to_wn_id(url: str) -> str:
    """Derive a wn package identifier (e.g. 'omw-bg:2.0') from a download URL."""
    parts = url.rstrip("/").split("/")
    name = parts[-1]

    for ext in (".tar.xz", ".tar.gz", ".tar.bz2", ".xz", ".gz"):
        if name.endswith(ext):
            name = name[: -len(ext)]
            break
    if name.endswith(".xml"):
        name = name[:-4]

    # Try version embedded in filename: "omw-bg-2.0" → base="omw-bg", ver="2.0"
    m = re.match(r"^(.+?)[-_](\d[\d.]*)$", name)
    if m:
        base, version = m.group(1), m.group(2)
    else:
        base = name
        # Fall back to version in URL path component: ".../download/v1.0.0/..."
        version = next(
            (re.match(r"^v(\d[\d.]*)$", p).group(1) for p in parts[:-1]
             if re.match(r"^v(\d[\d.]*)$", p)),
            None,
        )

    if base == "english-wordnet":
        base = "oewn"

    return f"{base}:{version}" if version else base


def setup_wn(data_dir: Path, urls: list[str]) -> None:
    """Download all wordnets into wn's local database, skipping those already present."""
    data_dir.mkdir(parents=True, exist_ok=True)
    wn.config.data_directory = str(data_dir)

    # Delete the wn database if it is incompatible with the installed wn version.
    # The downloaded source files (data_dir/downloads/) are kept as a cache.
    wn_db = data_dir / "wn.db"
    if wn_db.exists():
        try:
            wn.lexicons()
        except wn.DatabaseError:
            print("  (wn database schema changed — rebuilding)")
            wn_db.unlink()

    for url in urls:
        pkg_id = url_to_wn_id(url)
        print(f"  {pkg_id} ...", end=" ", flush=True)
        try:
            wn.download(url, progress_handler=None)
            print("ok")
        except Exception as e:
            print(f"failed ({e})")


def _count(conn: sqlite3.Connection, table: str, tables: set[str]) -> int:
    if table not in tables:
        return 0
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def db_stats(db_path: Path) -> dict:
    """Query a wordnet SQLite database for key statistics."""
    with sqlite3.connect(db_path) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        # Count loaded wordnets: 'lexicons' in wn, 'resources' in Cygnet
        if "lexicons" in tables:
            wordnets = conn.execute("SELECT COUNT(*) FROM lexicons").fetchone()[0]
        elif "resources" in tables:
            wordnets = conn.execute("SELECT COUNT(*) FROM resources WHERE code != 'cili'").fetchone()[0]
        else:
            wordnets = 0
        return {
            "wordnets":         wordnets,
            "synsets":          _count(conn, "synsets", tables),
            "entries":          _count(conn, "entries", tables),
            "senses":           _count(conn, "senses", tables),
            "synset_relations": _count(conn, "synset_relations", tables),
            "sense_relations":  _count(conn, "sense_relations", tables),
        }


def db_size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)


def fmt(n: int) -> str:
    """Format an integer with LaTeX-friendly thousands separator."""
    return f"{n:,}".replace(",", r"\,")


def latex_table(
    cygnet: dict, cygnet_mb: float, prov_mb: float, wndb: dict, wn_mb: float
) -> str:
    cygnet_rels = cygnet["synset_relations"] + cygnet["sense_relations"]
    wn_rels = wndb["synset_relations"] + wndb["sense_relations"]

    # rows: (metric, cygnet_value, wn_value, comment)
    rows = [
        ("DB size (MB)",       f"{cygnet_mb:.1f}",                    f"{wn_mb:.1f}",  f"+ {prov_mb:.1f}\\,MB provenance DB"),
        ("Wordnets loaded",    fmt(cygnet["wordnets"]),                fmt(wndb["wordnets"]),             r"CantoneseWN counts as 2 in \texttt{wn} (en + yue)"),
        ("Synsets",            fmt(cygnet["synsets"]),                 fmt(wndb["synsets"]),              ""),
        ("Words",              fmt(cygnet["entries"]),                 fmt(wndb["entries"]),              ""),
        ("Senses",             fmt(cygnet["senses"]),                  fmt(wndb["senses"]),               ""),
        ("Synset relations",   fmt(cygnet["synset_relations"]),        fmt(wndb["synset_relations"]),     ""),
        ("Sense relations",    fmt(cygnet["sense_relations"]),         fmt(wndb["sense_relations"]),      ""),
        ("Total relations",    fmt(cygnet_rels),                       fmt(wn_rels),                      ""),
    ]

    lines = [
        r"\begin{table}[h]",
        r"  \centering",
        r"  \begin{tabular}{lrrl}",
        r"    \toprule",
        r"    \textbf{Metric} & \textbf{Cygnet} & \textbf{\texttt{wn}} & \textbf{Note} \\",
        r"    \midrule",
    ]
    for label, cval, wval, comment in rows:
        lines.append(f"    {label} & {cval} & {wval} & {comment} \\\\")
    lines += [
        r"    \bottomrule",
        r"  \end{tabular}",
        r"  \caption{Comparison of Cygnet and \texttt{wn} library databases"
        r" built from the same wordnet sources.}",
        r"  \label{tab:db-comparison}",
        r"\end{table}",
    ]
    return "\n".join(lines)


def main() -> None:
    print("Reading wordnets.toml...")
    urls = load_toml_urls(TOML_PATH)
    print(f"  {len(urls)} wordnet URLs\n")

    print("Setting up wn database...")
    setup_wn(WN_DATA_DIR, urls)

    wn_db_path = Path(wn.config.data_directory) / "wn.db"
    if not wn_db_path.exists():
        print(f"Error: wn database not found at {wn_db_path}")
        return
    if not CYGNET_DB.exists():
        print(f"Error: Cygnet database not found at {CYGNET_DB}")
        return

    prov_db = PROJECT_ROOT / "web" / "provenance.db"
    prov_mb = db_size_mb(prov_db) if prov_db.exists() else 0.0

    print("\nQuerying databases...")
    cygnet_stats = db_stats(CYGNET_DB)
    wn_db_stats = db_stats(wn_db_path)

    print("\n% --- LaTeX output ---\n")
    print(latex_table(
        cygnet_stats, db_size_mb(CYGNET_DB), prov_mb,
        wn_db_stats, db_size_mb(wn_db_path),
    ))


if __name__ == "__main__":
    main()
