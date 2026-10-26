#!/usr/bin/env python3
"""
Batch convert GlobalWordNet XML files to Cygnet XML format.
"""

import sys
from dataclasses import dataclass
from pathlib import Path

import tomllib

from cyg.converters import WordNetToCygnetConverter
from cyg.presynth import write_manifest
from cyg.multilex import (
    SharedConcepts,
    build_concept_map,
    fix_language,
    language_problem,
    match_language,
    read_lexicon_header,
    url_stem,
)

# wordnets.toml key for multi-lexicon releases linked by a 'mul' concepts lexicon.
MUL_KEY = "mul"

# Written to the output directory: the outputs this build planned (converted
# now or reused).  6_synthesise.py merges only these, so outputs left over
# from earlier builds (a dropped or replaced wordnet) are not merged.

# Lexicon IDs that are valid as the English base (first wordnet processed).
# Both OEWN and the OMW English wordnet provide the synset backbone.
_EN_BASE_IDS: frozenset[str] = frozenset({"oewn", "omw-en"})
# Filename substrings used to detect the English base before reading the file.
_EN_BASE_FILENAME_PATTERNS: tuple[str, ...] = ("english-wordnet", "omw-en")


# Some hosts (e.g. SADiLaR's DSpace bitstream URLs) put no filename in the
# URL at all — it's just ".../download" — so the extracted content's actual
# name bears no relation to anything url_stem() can derive. Map those
# language codes directly to their real resource stem.
_KNOWN_STEM_OVERRIDES = {
    "xh": "wnxho",
    "zu": "wnzul",
    "nso": "wnnso",
    "tn": "wntsn",
    "ve": "wnven",
}


def collect_xml_files(toml_path: Path, raw_wns_dir: Path) -> list[Path]:
    """Collect XML files from raw_wns_dir matching the URLs in wordnets.toml.

    Handles both flat files (from per-language downloads) and the nested
    omw-2.0/omw-{lang}/omw-{lang}.xml layout from a full OMW bundle.

    Args:
        toml_path: Path to wordnets.toml.
        raw_wns_dir: Directory containing downloaded wordnet XML files.

    Returns:
        Deduplicated list of matching XML paths, sorted.
    """
    with open(toml_path, "rb") as f:
        config = tomllib.load(f)

    found: list[Path] = []
    seen: set[Path] = set()

    for lang, urls in config.items():
        for url in urls:
            for m in _match_url(url, lang, raw_wns_dir):
                if m not in seen:
                    seen.add(m)
                    found.append(m)

    return sorted(found)


def _match_url(url: str, lang: str, raw_wns_dir: Path) -> list[Path]:
    """Find the raw XML files that came from one wordnets.toml URL.

    Args:
        url: Source URL from wordnets.toml
        lang: The wordnets.toml key the URL is listed under
        raw_wns_dir: Directory containing downloaded wordnet XML files

    Returns:
        Matching XML paths (empty, with a warning, if there are none)
    """
    stem = url_stem(url)
    matches = (
        sorted(raw_wns_dir.glob(f"{stem}*.xml"))
        + sorted(raw_wns_dir.glob(f"{stem}*.xml.gz"))
        + sorted(raw_wns_dir.glob(f"{stem}*.xml.xz"))
        + sorted(raw_wns_dir.glob(f"*/{stem}*/*.xml"))
    )
    if not matches and lang in _KNOWN_STEM_OVERRIDES:
        stem = _KNOWN_STEM_OVERRIDES[lang]
        matches = sorted(raw_wns_dir.glob(f"{stem}*.xml"))
    if not matches:
        print(f"  Warning: no XML found for {url} (stem: {stem})")
    return matches


def drop_unusable_languages(xml_files: list[Path]) -> list[Path]:
    """Remove lexicons whose language code is missing, invalid or private-use.

    Each removed file is reported in the build log.

    Args:
        xml_files: Collected XML files

    Returns:
        The files whose (fixed) language code is usable
    """
    kept = []
    for p in xml_files:
        lang = fix_language(read_lexicon_header(p).language)
        problem = language_problem(lang)
        if problem:
            print(f"  Skipping {p.name}: {problem} language code {lang!r}")
        else:
            kept.append(p)
    return kept


@dataclass(frozen=True)
class FileOptions:
    """Per-file converter options for lexicons of multi-lexicon releases."""

    shared: SharedConcepts
    language: str | None = None


def plan_multilexicon(
    toml_path: Path, raw_wns_dir: Path, xml_files: list[Path], all_languages: bool
) -> tuple[dict[Path, FileOptions], set[Path]]:
    """Decide how to convert each lexicon of the 'mul' releases.

    Each release listed under the 'mul' key must contain exactly one
    concepts lexicon (language="mul").  Its member lexicons share its
    concepts by dc:identifier.  A member is kept only if its language
    already has another wordnet in the build, unless all_languages is set;
    a kept member takes the code that wordnet uses (e.g. ar -> arb).

    Args:
        toml_path: Path to wordnets.toml
        raw_wns_dir: Directory containing downloaded wordnet XML files
        xml_files: All XML files collected for the build
        all_languages: Keep members in every language

    Returns:
        (options for each concepts or member file, files to skip)
    """
    with open(toml_path, "rb") as f:
        mul_urls = tomllib.load(f).get(MUL_KEY, [])
    if not mul_urls:
        return {}, set()

    groups = [_match_url(url, MUL_KEY, raw_wns_dir) for url in mul_urls]
    mul_files = {p for g in groups for p in g}
    known = {
        fix_language(read_lexicon_header(p).language)
        for p in xml_files if p not in mul_files
    } - {None}

    options: dict[Path, FileOptions] = {}
    skip: set[Path] = set()
    for url, group in zip(mul_urls, groups):
        headers = {p: read_lexicon_header(p) for p in group}
        concept_files = [p for p, h in headers.items() if h.language == MUL_KEY]
        if len(concept_files) != 1:
            print(f"  Warning: {url} has {len(concept_files)} 'mul' concepts "
                  "lexicons (need exactly 1); skipping the release")
            skip.update(group)
            continue
        concepts = build_concept_map(concept_files[0])
        options[concept_files[0]] = FileOptions(SharedConcepts(concepts, "concepts"))
        kept, dropped = [], []
        for p, h in sorted(headers.items()):
            if p == concept_files[0]:
                continue
            lang = fix_language(h.language)
            target = match_language(lang, known)
            if target is None and not all_languages:
                skip.add(p)
                dropped.append(lang)
                continue
            options[p] = FileOptions(SharedConcepts(concepts, "member"), target or lang)
            kept.append(lang if target in (None, lang) else f"{lang}->{target}")
        print(f"  {concept_files[0].name}: {len(concepts)} concepts, "
              f"{len(kept)} member lexicons kept, {len(dropped)} skipped "
              "(no other wordnet for the language)")
        if kept and not all_languages:
            print(f"    kept: {' '.join(kept)}")
    return options, skip


def batch_convert(cili_file, toml_path, raw_wns_dir="raw_wns", output_dir="cygnets_presynth",
                  all_languages=False):
    """Batch convert all GWN XML files listed in wordnets.toml to Cygnet format.

    Args:
        cili_file: Path to the CILI XML file.
        toml_path: Path to wordnets.toml.
        raw_wns_dir: Directory containing raw WordNet XML files.
        output_dir: Directory to save converted Cygnet XML files.
        all_languages: Keep member lexicons of 'mul' releases in every
            language, not only languages that already have a wordnet.
    """
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)

    print(f"Scanning {raw_wns_dir} for XML files (from {toml_path})...")
    xml_files = collect_xml_files(Path(toml_path), Path(raw_wns_dir))

    if not xml_files:
        print(f"No XML files found matching wordnets.toml entries in {raw_wns_dir}")
        return

    xml_files = drop_unusable_languages(xml_files)
    file_options, skip = plan_multilexicon(
        Path(toml_path), Path(raw_wns_dir), xml_files, all_languages
    )
    xml_files = [p for p in xml_files if p not in skip]

    print(f"Found {len(xml_files)} files to process")
    print()

    success_count = 0
    error_count = 0
    skipped_count = 0
    oewn_relations_path = None
    planned_outputs: list[str] = []

    # The English base must be processed first so other wordnets can reference
    # its relations.  Detect it by filename before reading.
    def _is_english_base(p: Path) -> bool:
        return any(pat in p.name for pat in _EN_BASE_FILENAME_PATTERNS)

    oewn = next((t for t in xml_files if _is_english_base(t)), None)
    if oewn:
        xml_files.remove(oewn)
        xml_files.insert(0, oewn)

    for i, xml_file in enumerate(xml_files, 1):
        print(f"[{i}/{len(xml_files)}] Processing {xml_file.name}...")

        try:
            if i == 1:
                converter = WordNetToCygnetConverter(cili_path=cili_file, skip_cili_defns=True)
            else:
                if oewn_relations_path is None:
                    en_base_matches = [
                        m for en_id in _EN_BASE_IDS
                        for m in sorted(output_path.glob(f"{en_id}-*.xml"))
                    ]
                    if not en_base_matches:
                        print(
                            f"  Skipping {xml_file.name} "
                            "(English base output not found)"
                        )
                        error_count += 1
                        continue
                    oewn_relations_path = str(en_base_matches[0])
                opts = file_options.get(xml_file)
                converter = WordNetToCygnetConverter(
                    cili_path=cili_file,
                    relations_path=oewn_relations_path,
                    skip_cili_defns=False,
                    shared=opts.shared if opts else None,
                    language=opts.language if opts else None,
                )

            root, tree = converter.read_metadata(str(xml_file))
            lexicon_id = converter.lexicon_id
            lexicon_version = converter.lexicon_version

            if i == 1 and lexicon_id not in _EN_BASE_IDS:
                raise ValueError(
                    f"Expected English base first (one of: "
                    f"{', '.join(sorted(_EN_BASE_IDS))}), "
                    f"got '{lexicon_id}' from {xml_file.name}"
                )
            if i != 1 and lexicon_id in _EN_BASE_IDS:
                raise ValueError(
                    f"English base '{lexicon_id}' appeared at position {i},"
                    f" expected position 1"
                )

            if lexicon_id is None or lexicon_version is None:
                print(f"  Skipping {xml_file.name} (could not read metadata)")
                error_count += 1
                continue

            output_filename = f"{lexicon_id}-{lexicon_version}.xml"
            output_file = output_path / output_filename

            if output_file.exists():
                print(f"  Skipping {xml_file.name} (output {output_filename} already exists)")
                skipped_count += 1
                planned_outputs.append(output_filename)
                continue

            print(f"  Converting {xml_file.name}...")
            converter.convert_from_tree(root)
            converter.save(str(output_file))
            print(f"  Converted to {output_filename}")
            success_count += 1
            planned_outputs.append(output_filename)
            if i == 1:
                oewn_relations_path = str(output_file)

        except Exception as e:
            print(f"  Error converting {xml_file.name}: {e}")
            error_count += 1

        print()

    write_manifest(output_path, planned_outputs)

    print("=" * 70)
    print("Batch conversion complete!")
    print(f"  Successful: {success_count}")
    print(f"  Skipped:    {skipped_count}")
    print(f"  Errors:     {error_count}")
    print(f"  Total:      {len(xml_files)}")
    print(f"  Output:     {output_dir}")
    print("=" * 70)


def main():
    import argparse

    sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(
        description="Batch convert GlobalWordNet XML files to Cygnet XML format."
    )
    parser.add_argument("--raw-wns-dir", default="bin/raw_wns")
    parser.add_argument("--output-dir", default="bin/cygnets_presynth")
    parser.add_argument("--cili-file", default="bin/cygnets_presynth/cili-1.0.xml")
    parser.add_argument("--wordnets", default="wordnets.toml")
    parser.add_argument(
        "--all-languages", action="store_true",
        help="keep 'mul' member lexicons in every language (for local builds)",
    )

    args = parser.parse_args()

    batch_convert(
        raw_wns_dir=args.raw_wns_dir,
        output_dir=args.output_dir,
        cili_file=args.cili_file,
        toml_path=args.wordnets,
        all_languages=args.all_languages,
    )


if __name__ == "__main__":
    main()
