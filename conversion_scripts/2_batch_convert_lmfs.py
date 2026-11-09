#!/usr/bin/env python3
"""
Step 2: turn every source wordnet listed in wordnets.toml into a pre-synth
file -- a cleaned WN-LMF file (--format lmf, cyg.clean) or, for the old
path still being compared against it, a Cygnet XML file (--format cygnet,
cyg.converters).  See TODO.md, "Replace the Cygnet XML schema with GWA
WN-LMF".
"""

import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import cyg.cili
import cyg.clean
import cyg.converters
import cyg.linker
import cyg.lmf
import cyg.multilex
from cyg.cili import CiliIndex
from cyg.clean import CHANGED_SUFFIX, CleanOptions, LmfCleaner, validate_lmf
from cyg.converters import WordNetToCygnetConverter
from cyg.lmf import DIRECTION_SWAP_CONFIG_FILENAME, load_direction_swap_config
from cyg.multilex import (
    SharedConcepts,
    build_concept_map,
    fix_language,
    language_problem,
    match_language,
    read_lexicon_header,
    url_stem,
)
from cyg.presynth import (
    read_manifest,
    read_manifest_converter,
    source_hash,
    write_manifest,
)

# wordnets.toml key for multi-lexicon releases linked by a 'mul' concepts lexicon.
MUL_KEY = "mul"

# Written to the output directory: the outputs this build planned (converted
# now or reused).  6_synthesise.py merges only these, so outputs left over
# from earlier builds (a dropped or replaced wordnet) are not merged.

# The code that determines the outputs. An existing output is reused only if
# the last manifest lists it with the same hash of these files; otherwise it
# is converted again, so a converter change reaches every wordnet.
CONVERTER_SOURCES = (cyg.converters.__file__, cyg.multilex.__file__, cyg.lmf.__file__,
                     cyg.linker.__file__, __file__)
CLEANER_SOURCES = (cyg.clean.__file__, cyg.cili.__file__, cyg.lmf.__file__,
                   cyg.linker.__file__, cyg.multilex.__file__, __file__)
DEFAULT_OUTPUT_DIRS = {"cygnet": "bin/cygnets_presynth", "lmf": "bin/lmf_presynth"}

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
    """Per-file options for lexicons of multi-lexicon releases.

    Attributes:
        shared: The release's concepts and this file's role in it.
        language: The code the build uses for a member's language.
        requires: (id, version) of the concepts lexicon a member depends on.
    """

    shared: SharedConcepts
    language: str | None = None
    requires: tuple[str, str] | None = None


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
        concepts_header = headers[concept_files[0]]
        requires = (concepts_header.id, concepts_header.version)
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
            options[p] = FileOptions(SharedConcepts(concepts, "member"), target or lang, requires)
            kept.append(lang if target in (None, lang) else f"{lang}->{target}")
        print(f"  {concept_files[0].name}: {len(concepts)} concepts, "
              f"{len(kept)} member lexicons kept, {len(dropped)} skipped "
              "(no other wordnet for the language)")
        if kept and not all_languages:
            print(f"    kept: {' '.join(kept)}")
    return options, skip


def clean_all(cili_file, toml_path, raw_wns_dir="bin/raw_wns", output_dir="bin/lmf_presynth",
              all_languages=False, validate=False) -> int:
    """Clean every source listed in wordnets.toml into WN-LMF (cyg.clean).

    An existing output is reused when the manifest says this cleaner made
    it; otherwise the source is cleaned again.  The direction-swap config
    is read from beside the CILI file, where Step 1.5 writes it.

    Args:
        cili_file: The CILI file (cili-1.0.xml or cili.tsv).
        toml_path: Path to wordnets.toml.
        raw_wns_dir: Directory of downloaded source files.
        output_dir: Where the cleaned files, logs and manifest go.
        all_languages: Keep 'mul' members in every language.
        validate: Check each cleaned file with wn (slow for big files).

    Returns:
        The number of sources that failed.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    print(f"Scanning {raw_wns_dir} for XML files (from {toml_path})...")
    xml_files = collect_xml_files(Path(toml_path), Path(raw_wns_dir))
    if not xml_files:
        print(f"No XML files found matching wordnets.toml entries in {raw_wns_dir}")
        return 0
    xml_files = drop_unusable_languages(xml_files)
    file_options, skip = plan_multilexicon(
        Path(toml_path), Path(raw_wns_dir), xml_files, all_languages)
    xml_files = [p for p in xml_files if p not in skip]
    print(f"Found {len(xml_files)} files to clean")

    cleaner_hash = source_hash(CLEANER_SOURCES)
    if read_manifest_converter(output_path) == cleaner_hash:
        reusable = set(read_manifest(output_path) or [])
    else:
        reusable = set()
        print(f"Cleaner {cleaner_hash} has not made these outputs: cleaning all")
    cili = CiliIndex.from_file(Path(cili_file))
    swaps, swap_stats = load_direction_swap_config(
        Path(cili_file).parent / DIRECTION_SWAP_CONFIG_FILENAME)
    print()

    counts = {"cleaned": 0, "unchanged": 0, "reused": 0, "errors": 0}
    planned: list[str] = []
    for i, xml_file in enumerate(xml_files, 1):
        print(f"[{i}/{len(xml_files)}] {xml_file.name}...")
        header = read_lexicon_header(xml_file)
        if header.id is None or header.version is None:
            print(f"  Skipping {xml_file.name} (could not read the Lexicon header)")
            counts["errors"] += 1
            continue
        version = header.version.removesuffix(CHANGED_SUFFIX)
        output_name = f"{header.id}-{version}.xml"
        if output_name in reusable and (output_path / output_name).exists():
            print(f"  Reusing {output_name}")
            counts["reused"] += 1
            planned.append(output_name)
            continue
        opts = file_options.get(xml_file)
        options = CleanOptions(
            language=opts.language if opts else None,
            shared=opts.shared if opts else None,
            requires=opts.requires if opts else None,
            direction_swaps=swaps.get(header.id, frozenset()),
            swap_stats=swap_stats.get(header.id, {}),
        )
        try:
            result = LmfCleaner(xml_file, cili, options).clean(output_path)
        except Exception as e:  # noqa: BLE001  (one bad source must not stop the batch)
            print(f"  Error cleaning {xml_file.name}: {e}")
            counts["errors"] += 1
            continue
        planned.append(result.output.name)
        if result.changed:
            changes = json.loads(result.log.read_text(encoding="utf-8"))["changes"]
            summary = ", ".join(f"{k} {v}" for k, v in sorted(changes.items())[:8])
            print(f"  Cleaned to {result.output.name} ({summary})")
            counts["cleaned"] += 1
        else:
            print(f"  Unchanged: {result.output.name}")
            counts["unchanged"] += 1
        if validate:
            errors, warnings = validate_lmf(result.output)
            for line in warnings:
                print(f"  wn warning {line}")
            for line in errors:
                print(f"  wn ERROR {line}")
            if errors:
                counts["errors"] += 1
        print()

    write_manifest(output_path, planned, cleaner_hash)
    print("=" * 70)
    print("Cleaning complete!")
    for key, n in counts.items():
        print(f"  {key.capitalize():10} {n}")
    print(f"  Total:     {len(xml_files)}")
    print(f"  Output:    {output_dir}")
    print("=" * 70)
    return counts["errors"]


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
    converter_hash = source_hash(CONVERTER_SOURCES)
    if read_manifest_converter(output_path) == converter_hash:
        reusable = set(read_manifest(output_path) or [])
    else:
        reusable = set()
        print(f"Converter {converter_hash} has not made these outputs: converting all")
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

            if output_file.exists() and output_filename in reusable:
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

    write_manifest(output_path, planned_outputs, converter_hash)

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

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-wns-dir", default="bin/raw_wns")
    parser.add_argument("--output-dir", default=None,
                        help="default: bin/cygnets_presynth (cygnet) or bin/lmf_presynth (lmf)")
    parser.add_argument("--cili-file", default="bin/cygnets_presynth/cili-1.0.xml")
    parser.add_argument("--wordnets", default="wordnets.toml")
    parser.add_argument("--format", choices=sorted(DEFAULT_OUTPUT_DIRS), default="cygnet",
                        help="what Step 2 writes: cleaned WN-LMF, or Cygnet XML (default)")
    parser.add_argument("--validate", action="store_true",
                        help="with --format lmf: check each cleaned file with wn")
    parser.add_argument(
        "--all-languages", action="store_true",
        help="keep 'mul' member lexicons in every language (for local builds)",
    )

    args = parser.parse_args()
    output_dir = args.output_dir or DEFAULT_OUTPUT_DIRS[args.format]

    if args.format == "lmf":
        errors = clean_all(
            cili_file=args.cili_file,
            toml_path=args.wordnets,
            raw_wns_dir=args.raw_wns_dir,
            output_dir=output_dir,
            all_languages=args.all_languages,
            validate=args.validate,
        )
        sys.exit(1 if errors else 0)

    batch_convert(
        raw_wns_dir=args.raw_wns_dir,
        output_dir=output_dir,
        cili_file=args.cili_file,
        toml_path=args.wordnets,
        all_languages=args.all_languages,
    )


if __name__ == "__main__":
    main()
