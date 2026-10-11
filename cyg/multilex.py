"""Support for multi-lexicon releases linked by a shared 'mul' concept lexicon.

Some releases (e.g. omw-pronoun, kindiv) bundle one concepts lexicon with
language="mul" — unlexicalized synsets carrying the definitions and the
hierarchy — and one lexicon per language.  Each synset's dc:identifier links
it to its concept, since most concepts have no ILI yet.  They are listed in
wordnets.toml under the key 'mul'.

This module also holds the language-code fix-ups shared by all sources.
"""
import gzip
import lzma
from dataclasses import dataclass
from pathlib import Path

import langcodes
from lxml import etree

DC_NAMESPACES = (
    "https://globalwordnet.github.io/schemas/dc/",
    "http://globalwordnet.github.io/schemas/dc/",
    "http://purl.org/dc/elements/1.1/",
)

# Codes that sources use but that are wrong; corrected when a file is read.
# Remove an entry once the source is fixed upstream.
LANGUAGE_FIXES: dict[str, str] = {
    "ua": "uk",  # Ukrajinet: 'ua' is the country code, Ukrainian is 'uk'
}

# Codes that name the same language as a different code, beyond what
# langcodes' macrolanguage normalisation already catches (arb~ar, cmn~zh,
# zsm~ms, tsn~tn, tl~fil, ...).
LANGUAGE_ALIASES: dict[str, str] = {
    "no": "nb",  # Norwegian macrolanguage; Cygnet's Norwegian wordnet is Bokmål
}


@dataclass(frozen=True)
class SharedConcepts:
    """Concept identity shared across the lexicons of one release.

    Attributes:
        concepts: dc:identifier -> (ILI or None, local concept id)
        role: 'concepts' for the mul lexicon, 'member' for a language lexicon
    """

    concepts: dict[str, tuple[str | None, str]]
    role: str


@dataclass(frozen=True)
class LexiconHeader:
    """Identifying attributes of a file's first Lexicon."""

    id: str | None
    version: str | None
    language: str | None


def fix_language(code: str | None) -> str | None:
    """Correct a known-bad language code.

    Args:
        code: Language code as given by the source

    Returns:
        The corrected code (unchanged if it is not a known error)
    """
    return LANGUAGE_FIXES.get(code, code)


def language_problem(code: str | None) -> str | None:
    """Say why a language code cannot be used, or None if it can.

    'mul' (the concepts lexicon of a multi-lexicon release) is accepted.

    Args:
        code: Language code, after fix_language

    Returns:
        'missing', 'invalid' or 'private use', or None for a usable code
    """
    if not code:
        return "missing"
    if code == "mul":
        return None
    if not langcodes.tag_is_valid(code):
        return "invalid"
    primary = code.split("-")[0].lower()
    if primary == "x" or "qaa" <= primary <= "qtz":
        return "private use"
    return None


def _language_key(code: str) -> tuple[str, str | None]:
    """Reduce a code to (macrolanguage-normalised language, script)."""
    code = LANGUAGE_ALIASES.get(code, code)
    tag = langcodes.Language.get(langcodes.standardize_tag(code, macro=True))
    return tag.language, tag.script


def match_language(code: str, known: set[str]) -> str | None:
    """Find the code under which a language is already known.

    A match needs the same language after macrolanguage normalisation and,
    when both codes give a script, the same script (so cmn-Hant does not
    match cmn-Hans).  Varieties stay distinct (apc does not match arb).

    Args:
        code: Language code to look up
        known: Codes already present in the build

    Returns:
        The matching known code (an exact match first, otherwise the
        shortest candidate), or None if the language is not known
    """
    if code in known:
        return code
    lang, script = _language_key(code)
    candidates = []
    for k in known:
        k_lang, k_script = _language_key(k)
        same_script = script is None or k_script is None or script == k_script
        if k_lang == lang and same_script:
            candidates.append(k)
    return min(candidates, key=lambda k: (len(k), k)) if candidates else None


def _open(path: Path):
    """Open a possibly compressed XML file for binary reading."""
    if path.name.endswith(".gz"):
        return gzip.open(path, "rb")
    if path.name.endswith(".xz"):
        return lzma.open(path, "rb")
    return open(path, "rb")


def read_lexicon_header(path: Path) -> LexiconHeader:
    """Read the first Lexicon's id, version and language without a full parse.

    Args:
        path: LMF XML file (.xml, .xml.gz or .xml.xz)

    Returns:
        The header; fields are None if no Lexicon is found
    """
    with _open(path) as f:
        for _event, elem in etree.iterparse(f, events=("start",), load_dtd=False,
                                            no_network=True, resolve_entities=False):
            if etree.QName(elem).localname == "Lexicon":
                return LexiconHeader(elem.get("id"), elem.get("version"),
                                     elem.get("language"))
    return LexiconHeader(None, None, None)


def dc_attr(elem: etree._Element, name: str) -> str | None:
    """Return an element's dc:<name> attribute, whatever the dc namespace.

    Args:
        elem: An LMF element
        name: Dublin Core term, e.g. 'identifier' or 'type'

    Returns:
        The attribute value, or None if absent or empty
    """
    for ns in DC_NAMESPACES:
        value = elem.get(f"{{{ns}}}{name}")
        if value:
            return value
    return None


def dc_identifier(elem: etree._Element) -> str | None:
    """Return an element's dc:identifier attribute, whatever the dc namespace."""
    return dc_attr(elem, "identifier")


def encode_for_xml_id(text: str) -> str:
    """Encode a string as an XML-ID-safe key, one-to-one.

    ASCII letters and digits pass through; every other character becomes
    xHHHH (its 4-digit uppercase hex code point).

    Args:
        text: Any Unicode string

    Returns:
        The encoded string
    """
    return "".join(
        c if c.isascii() and c.isalnum() else f"x{ord(c):04X}" for c in text
    )


def build_concept_map(path: Path) -> dict[str, tuple[str | None, str]]:
    """Map each concept's dc:identifier to its ILI and a stable local id.

    The local id is '<lexicon id>.<synset id without the lexicon prefix>',
    encoded, e.g. synset kindiv-Fa-Br-So -> 'kindiv.Fax002DBrx002DSo'.

    Args:
        path: The concepts (language="mul") lexicon file

    Returns:
        dc:identifier -> (ILI, or None for '' and 'in'; local concept id)
    """
    with _open(path) as f:
        root = etree.parse(f).getroot()
    lexicon = next(e for e in root.iter() if etree.QName(e).localname == "Lexicon")
    lex_id = lexicon.get("id")
    concepts: dict[str, tuple[str | None, str]] = {}
    for synset in lexicon.iter("Synset"):
        ident = dc_identifier(synset)
        if ident is None:
            continue
        ili = synset.get("ili", "")
        synset_id = synset.get("id")
        local = synset_id.removeprefix(f"{lex_id}-")
        concepts[ident] = (
            ili if ili not in ("", "in") else None,
            f"{lex_id}.{encode_for_xml_id(local)}",
        )
    return concepts
