#!/usr/bin/env python3
"""
Step 2: clean every source wordnet listed in wordnets.toml into a WN-LMF
pre-synth file (cyg.clean), one per source, in bin/cygnets_presynth.
"""

import dataclasses
import json
import sys
import tomllib
from pathlib import Path

from cyg import cili, clean, linker, lmf, multilex
from cyg.cili import CiliIndex
from cyg.clean import CHANGED_SUFFIX, CleanOptions, LmfCleaner, validate_lmf
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
    write_sources,
)

# wordnets.toml key for multi-lexicon releases linked by a 'mul' concepts lexicon.
MUL_KEY = "mul"

# The code that determines the outputs. An existing output is reused only if
# the last manifest lists it with the same hash of these files; otherwise it
# is cleaned again, so a cleaner change reaches every wordnet.
CLEANER_SOURCES = (
    clean.__file__,
    cili.__file__,
    lmf.__file__,
    linker.__file__,
    multilex.__file__,
    __file__,
)

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


def source_urls(toml_path: Path, raw_wns_dir: Path) -> dict[Path, str]:
    """The wordnets.toml URL each raw XML file came from.

    Args:
        toml_path: Path to wordnets.toml.
        raw_wns_dir: Directory containing downloaded wordnet XML files.

    Returns:
        {raw XML path: URL}; a file matched by two URLs keeps the first.
    """
    with open(toml_path, "rb") as f:
        config = tomllib.load(f)
    urls: dict[Path, str] = {}
    for lang, lang_urls in config.items():
        for url in lang_urls:
            for m in _match_url(url, lang, raw_wns_dir):
                urls.setdefault(m, url)
    return urls


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


def plan_multilexicon(
    toml_path: Path, raw_wns_dir: Path, xml_files: list[Path], all_languages: bool
) -> tuple[dict[Path, CleanOptions], set[Path]]:
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
        for p in xml_files
        if p not in mul_files
    } - {None}

    options: dict[Path, CleanOptions] = {}
    skip: set[Path] = set()
    for url, group in zip(mul_urls, groups):
        headers = {p: read_lexicon_header(p) for p in group}
        concept_files = [p for p, h in headers.items() if h.language == MUL_KEY]
        if len(concept_files) != 1:
            print(
                f"  Warning: {url} has {len(concept_files)} 'mul' concepts "
                "lexicons (need exactly 1); skipping the release"
            )
            skip.update(group)
            continue
        concepts = build_concept_map(concept_files[0])
        options[concept_files[0]] = CleanOptions(
            shared=SharedConcepts(concepts, "concepts")
        )
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
            options[p] = CleanOptions(
                language=target or lang,
                shared=SharedConcepts(concepts, "member"),
                requires=requires,
            )
            kept.append(lang if target in (None, lang) else f"{lang}->{target}")
        print(
            f"  {concept_files[0].name}: {len(concepts)} concepts, "
            f"{len(kept)} member lexicons kept, {len(dropped)} skipped "
            "(no other wordnet for the language)"
        )
        if kept and not all_languages:
            print(f"    kept: {' '.join(kept)}")
    return options, skip


def _is_concepts_file(options: CleanOptions | None) -> bool:
    return options is not None and options.shared is not None and options.shared.role == "concepts"


def _note_concept_version(output: Path, concept_versions: dict[str, str]) -> None:
    """Record the version a cleaned concepts lexicon carries, for its members."""
    header = read_lexicon_header(output)
    if header.language == MUL_KEY and header.id and header.version:
        concept_versions[header.id] = header.version


def clean_all(
    cili_file,
    toml_path,
    raw_wns_dir="bin/raw_wns",
    output_dir="bin/cygnets_presynth",
    all_languages=False,
    validate=False,
) -> int:
    """Clean every source listed in wordnets.toml into WN-LMF (cyg.clean).

    An existing output is reused when the manifest says this cleaner made
    it; otherwise the source is cleaned again.  The direction-swap config
    is read from the output directory, where Step 1.5 writes it.

    Args:
        cili_file: The CILI release file (bin/cili.tsv).
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
        Path(toml_path), Path(raw_wns_dir), xml_files, all_languages
    )
    xml_files = [p for p in xml_files if p not in skip]
    # A release's members Require its concepts lexicon at the version the
    # cleaned concepts file carries (CHANGED_SUFFIX if it changed), so the
    # concepts files go first and their members take the version they got.
    xml_files.sort(key=lambda p: not _is_concepts_file(file_options.get(p)))
    concept_versions: dict[str, str] = {}
    print(f"Found {len(xml_files)} files to clean")

    urls = source_urls(Path(toml_path), Path(raw_wns_dir))
    sources: dict[str, dict] = {}
    cleaner_hash = source_hash(CLEANER_SOURCES)
    if read_manifest_converter(output_path) == cleaner_hash:
        reusable = set(read_manifest(output_path) or [])
    else:
        reusable = set()
        print(f"Cleaner {cleaner_hash} has not made these outputs: cleaning all")
    cili = CiliIndex.from_tsv(Path(cili_file))
    swaps, swap_stats = load_direction_swap_config(output_path / DIRECTION_SWAP_CONFIG_FILENAME)
    if swaps:
        print(f"Direction swaps for {', '.join(sorted(swaps))}")
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
        url = urls.get(xml_file)
        requires = file_options.get(xml_file, CleanOptions()).requires
        sources[header.id] = {"file": output_name, "url": url,
                              "extras": url_stem(url) if url else None,
                              "requires": requires[0] if requires else None}
        if output_name in reusable and (output_path / output_name).exists():
            print(f"  Reusing {output_name}")
            counts["reused"] += 1
            planned.append(output_name)
            _note_concept_version(output_path / output_name, concept_versions)
            continue
        options = dataclasses.replace(
            file_options.get(xml_file, CleanOptions()),
            direction_swaps=swaps.get(header.id, frozenset()),
            swap_stats=swap_stats.get(header.id, {}),
        )
        if options.requires and options.requires[0] in concept_versions:
            options = dataclasses.replace(
                options, requires=(options.requires[0], concept_versions[options.requires[0]]))
        try:
            result = LmfCleaner(xml_file, cili, options).clean(output_path)
        except Exception as e:  # noqa: BLE001  (one bad source must not stop the batch)
            print(f"  Error cleaning {xml_file.name}: {e}")
            counts["errors"] += 1
            continue
        planned.append(result.output.name)
        _note_concept_version(result.output, concept_versions)
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

    write_sources(output_path, {k: v for k, v in sources.items() if v['file'] in planned})
    print("=" * 70)
    print("Cleaning complete!")
    for key, n in counts.items():
        print(f"  {key.capitalize():10} {n}")
    print(f"  Total:     {len(xml_files)}")
    print(f"  Output:    {output_dir}")
    print("=" * 70)
    return counts["errors"]


def main():
    import argparse

    sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-wns-dir", default="bin/raw_wns")
    parser.add_argument("--output-dir", default="bin/cygnets_presynth")
    parser.add_argument("--cili-file", default="bin/cili.tsv")
    parser.add_argument("--wordnets", default="wordnets.toml")
    parser.add_argument(
        "--validate",
        action="store_true",
        help="check each cleaned file with wn (slow for big files)",
    )
    parser.add_argument(
        "--all-languages",
        action="store_true",
        help="keep 'mul' member lexicons in every language (for local builds)",
    )
    args = parser.parse_args()
    errors = clean_all(
        cili_file=args.cili_file,
        toml_path=args.wordnets,
        raw_wns_dir=args.raw_wns_dir,
        output_dir=args.output_dir,
        all_languages=args.all_languages,
        validate=args.validate,
    )
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
