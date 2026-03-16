"""Shared utilities for LaTeX table generation scripts."""

from __future__ import annotations

import re

# Maps log-file stem (and DB resource code) → display name.
# Log stems carry version suffixes; call stem_to_code() to normalise before lookup.
WORDNET_NAMES: dict[str, str] = {
    "abwn":        "Abui Wordnet",
    "cantown-yue": "Cantonese Wordnet",
    "dn":          "DanNet",
    "kurdnet":     "KurdNet",
    "oewn":        "Open English Wordnet",
    "omw-arb":     "Arabic WordNet",
    "omw-bg":      "BulTreeBank Wordnet",
    "omw-ca":      "Multilingual Central Repo.\\ (Catalan)",
    "omw-cmn":     "Chinese Open Wordnet",
    "omw-da":      "DanNet",
    "omw-el":      "Greek Wordnet",
    "omw-es":      "Multilingual Central Repo.\\ (Spanish)",
    "omw-eu":      "Multilingual Central Repo.\\ (Basque)",
    "omw-fi":      "FinnWordNet",
    "omw-fr":      "WOLF",
    "omw-gl":      "Multilingual Central Repo.\\ (Galician)",
    "omw-he":      "Hebrew Wordnet",
    "omw-hr":      "Croatian Wordnet",
    "omw-id":      "Wordnet Bahasa (Indonesian)",
    "omw-is":      "IceWordNet",
    "omw-it":      "MultiWordNet",
    "omw-iwn":     "ItalWordNet",
    "omw-ja":      "Japanese Wordnet",
    "omw-lt":      "Lithuanian WordNet",
    "omw-nb":      "Norwegian Wordnet (Bokm{\\aa}l)",
    "omw-nl":      "Open Dutch WordNet",
    "omw-nn":      "Norwegian Wordnet (Nynorsk)",
    "omw-pl":      "plWordNet",
    "omw-ro":      "Romanian Wordnet",
    "omw-sk":      "Slovak WordNet",
    "omw-sl":      "sloWNet",
    "omw-sq":      "Albanet",
    "omw-sv":      "WordNet-SALDO",
    "omw-th":      "Thai Wordnet",
    "omw-zsm":     "Wordnet Bahasa (Malaysian)",
    "own-pt":      "OpenWordnet-PT",
    "tufs-as":     "TUFS Assamese",
    "tufs-km":     "TUFS Khmer",
    "tufs-ko":     "TUFS Korean",
    "tufs-mn":     "TUFS Mongolian",
    "tufs-my":     "TUFS Burmese",
    "tufs-ru":     "TUFS Russian",
    "tufs-tl":     "TUFS Filipino",
    "tufs-tr":     "TUFS Turkish",
    "tufs-ur":     "TUFS Urdu",
    "tufs-vi":     "TUFS Vietnamese",
    "wordnet_lv":  "Latvian Wordnet",
    "tufs-ar":      "TUFS Arabic",
    "tufs-de":      "TUFS German",
    "tufs-en":      "TUFS English",
    "tufs-es":      "TUFS Spanish",
    "tufs-fr":      "TUFS French",
    "tufs-id":      "TUFS Indonesian",
    "tufs-ja":      "TUFS Japanese",
    "tufs-ms":      "TUFS Malaysian",
    "tufs-pt":      "TUFS Portuguese",
    "tufs-th":      "TUFS Thai",
    "tufs-zh":      "TUFS Chinese",
    "odenet":       "Open German WordNet",
}

# Licence URL substring → short LaTeX label (checked in order; most specific first).
_LICENCE_LABELS: dict[str, str] = {
    r"creativecommons.org/licenses/by/4":    "CC BY 4.0",
    r"creativecommons.org/licenses/by/3":    "CC BY 3.0",
    r"creativecommons.org/licenses/by-sa/4": "CC BY-SA 4.0",
    r"creativecommons.org/licenses/by-sa/3": "CC BY-SA 3.0",
    r"creativecommons.org/licenses/by-sa/":  "CC BY-SA",
    r"creativecommons.org/licenses/by-nc/4": "CC BY-NC 4.0",
    r"creativecommons.org/licenses/by/":     "CC BY",
    r"opensource.org/licenses/Apache-2.0":   "Apache 2.0",
    r"opensource.org/licenses/MIT":          "MIT",
    r"cecill.info":                           "CeCILL-C",
    r"opendefinition.org/licenses/odc-by":   "ODC-BY",
}

# Strips a trailing version / date suffix from a log-file stem to give the
# DB resource code.  Examples:
#   "oewn-2025"      → "oewn"
#   "omw-sq-2.0"     → "omw-sq"
#   "dn-2025-07-03"  → "dn"
#   "wordnet_lv-1.0" → "wordnet_lv"
_VERSION_RE = re.compile(r'([-_]\d[\d.\-]*)$')


def stem_to_code(stem: str) -> str:
    """Return the DB resource code for a log-file stem."""
    while True:
        m = _VERSION_RE.search(stem)
        if not m:
            return stem
        stem = stem[: m.start()]


def fmt_int(n: int | None) -> str:
    """Format an integer with LaTeX thousands separators, or '---' for None."""
    if n is None:
        return "---"
    return f"{n:,}".replace(",", "{,}")


def licence_label(raw: str | None) -> str:
    """Return a short LaTeX licence label for a raw licence URL or keyword."""
    if not raw:
        return "N/A"
    if raw.lower() == "wordnet":
        return "WordNet"
    for pattern, label in _LICENCE_LABELS.items():
        if pattern in raw:
            return label
    return raw
