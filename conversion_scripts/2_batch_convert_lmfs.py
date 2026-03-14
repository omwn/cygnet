#!/usr/bin/env python3
"""
Batch convert GlobalWordNet XML files to Cygnet XML format.
"""

import re
from pathlib import Path

import tomllib

from cyg.converters import WordNetToCygnetConverter


def _url_stem(url: str) -> str:
    """Derive a file-matching prefix from a download URL."""
    name = url.rstrip("/").split("/")[-1]
    for ext in (".tar.xz", ".tar.gz", ".tar.bz2", ".xz", ".gz"):
        if name.endswith(ext):
            name = name[: -len(ext)]
            break
    if name.endswith(".xml"):
        name = name[:-4]
    return re.sub(r"-\d[\d.]*$", "", name)


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

    for _lang, urls in config.items():
        for url in urls:
            stem = _url_stem(url)
            matches = (
                sorted(raw_wns_dir.glob(f"{stem}*.xml"))
                + sorted(raw_wns_dir.glob(f"{stem}*.xml.gz"))
                + sorted(raw_wns_dir.glob(f"{stem}*.xml.xz"))
                + sorted(raw_wns_dir.glob(f"*/{stem}*/*.xml"))
            )
            if not matches:
                print(f"  Warning: no XML found for {url} (stem: {stem})")
                continue
            for m in matches:
                if m not in seen:
                    seen.add(m)
                    found.append(m)

    return sorted(found)


def batch_convert(cili_file, toml_path, raw_wns_dir="raw_wns", output_dir="cygnets_presynth"):
    """Batch convert all GWN XML files listed in wordnets.toml to Cygnet format.

    Args:
        cili_file: Path to the CILI XML file.
        toml_path: Path to wordnets.toml.
        raw_wns_dir: Directory containing raw WordNet XML files.
        output_dir: Directory to save converted Cygnet XML files.
    """
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)

    print(f"Scanning {raw_wns_dir} for XML files (from {toml_path})...")
    xml_files = collect_xml_files(Path(toml_path), Path(raw_wns_dir))

    if not xml_files:
        print(f"No XML files found matching wordnets.toml entries in {raw_wns_dir}")
        return

    print(f"Found {len(xml_files)} files to process")
    print()

    success_count = 0
    error_count = 0
    skipped_count = 0
    oewn_relations_path = None

    # OEWN must be processed first so other wordnets can reference its relations
    oewn = next((t for t in xml_files if "english-wordnet" in str(t)), None)
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
                    oewn_matches = sorted(output_path.glob("oewn-*.xml"))
                    if not oewn_matches:
                        print(f"  Skipping {xml_file.name} (OEWN output not found)")
                        error_count += 1
                        continue
                    oewn_relations_path = str(oewn_matches[0])
                converter = WordNetToCygnetConverter(
                    cili_path=cili_file,
                    relations_path=oewn_relations_path,
                    skip_cili_defns=False,
                )

            root, tree = converter.read_metadata(str(xml_file))
            lexicon_id = converter.lexicon_id
            lexicon_version = converter.lexicon_version

            if i == 1 and lexicon_id != "oewn":
                raise ValueError(
                    f"Expected OEWN first, got '{lexicon_id}' from {xml_file.name}"
                )
            if i != 1 and lexicon_id == "oewn":
                raise ValueError(
                    f"OEWN appeared at position {i}, expected position 1"
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
                continue

            print(f"  Converting {xml_file.name}...")
            converter.convert_from_tree(root)
            converter.save(str(output_file))
            print(f"  Converted to {output_filename}")
            success_count += 1
            if i == 1:
                oewn_relations_path = str(output_file)

        except Exception as e:
            print(f"  Error converting {xml_file.name}: {e}")
            error_count += 1

        print()

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

    parser = argparse.ArgumentParser(
        description="Batch convert GlobalWordNet XML files to Cygnet XML format."
    )
    parser.add_argument("--raw-wns-dir", default="bin/raw_wns")
    parser.add_argument("--output-dir", default="bin/cygnets_presynth")
    parser.add_argument("--cili-file", default="bin/cygnets_presynth/cili-1.0.xml")
    parser.add_argument("--wordnets", default="wordnets.toml")

    args = parser.parse_args()

    batch_convert(
        raw_wns_dir=args.raw_wns_dir,
        output_dir=args.output_dir,
        cili_file=args.cili_file,
        toml_path=args.wordnets,
    )


if __name__ == "__main__":
    main()
