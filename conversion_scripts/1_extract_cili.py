#!/usr/bin/env python3
"""
Convert CILI TSV data to Cygnet XML format.
"""

import csv
import re
import sys
from lxml import etree as ET

from cyg.converters import NEW_POS_LABELS

_WHITESPACE_RE = re.compile(r'\s+')

VALID_POS_CHARS = set(NEW_POS_LABELS.keys())


def normalize_whitespace(text: str) -> str:
    """Collapse runs of whitespace and strip leading/trailing space."""
    if not text:
        return text
    return _WHITESPACE_RE.sub(' ', text).strip()


def get_ontological_category(origin: str) -> str:
    """Extract POS category from the last character of the origin field.

    Args:
        origin: Origin string ending in a POS character (e.g. 'pwn-3.0:...-n').

    Returns:
        Cygnet POS label (e.g. 'NOUN').

    Raises:
        ValueError: If origin is empty or the trailing character is unrecognised.
    """
    if not origin or not origin.strip():
        raise ValueError(f"Empty origin field: {origin!r}")
    char = origin.strip()[-1].lower()
    if char not in VALID_POS_CHARS:
        raise ValueError(f"Unrecognised POS character {char!r} in origin {origin!r}")
    return NEW_POS_LABELS[char]


def get_from(origin: str) -> tuple[str, str, str]:
    """Parse 'wn_name-version:original_id' from origin field.

    Args:
        origin: Origin string of the form 'pwn-3.0:synset_id'.

    Returns:
        Tuple of (wn_name, version, original_id).

    Raises:
        ValueError: If the origin format is unexpected.
    """
    wn_code, original_id = origin.split(':')
    wn_name, version = wn_code.split('-')
    if wn_name != 'pwn':
        raise ValueError(f"Expected 'pwn' wordnet, got {wn_name!r} in {origin!r}")
    if version != "3.0":
        raise ValueError(f"Expected version '3.0', got {version!r} in {origin!r}")
    return wn_name, version, original_id


def load_tsv_data(tsv_file: str) -> list[dict]:
    """Load CILI TSV data.

    Args:
        tsv_file: Path to the CILI TSV file.

    Returns:
        List of row dicts.
    """
    print(f"Loading TSV data from {tsv_file}...")
    with open(tsv_file, 'r', encoding='utf-8') as f:
        rows = list(csv.DictReader(f, delimiter='\t'))
    print(f"Loaded {len(rows)} rows from TSV")
    return rows


def validate_data(tsv_rows: list[dict]) -> None:
    """Validate TSV data, exiting on errors.

    Args:
        tsv_rows: Rows loaded from the CILI TSV file.
    """
    print("Validating data...")
    errors = [
        f"Row {i}: ILI {row['ili_id']} has empty definition"
        for i, row in enumerate(tsv_rows, 1)
        if not row.get('definition', '').strip()
    ]
    if errors:
        print(f"ERROR: Found {len(errors)} validation errors:")
        for error in errors[:20]:
            print(f"  {error}")
        if len(errors) > 20:
            print(f"  ... and {len(errors) - 20} more")
        sys.exit(1)
    print("Validation passed!")


def create_cygnet_xml(tsv_rows: list[dict], output_file: str) -> None:
    """Create Cygnet XML from TSV data and write to file.

    Args:
        tsv_rows: Validated rows from the CILI TSV file.
        output_file: Destination XML file path.
    """
    print("Creating Cygnet XML...")

    root = ET.Element('CygnetResource')
    root.set('id', 'cili')
    root.set('label', 'Collaborative Interlingua Index')
    root.set('version', '1.0')

    concept_layer = ET.SubElement(root, 'ConceptLayer')
    for row in tsv_rows:
        ontological_category = get_ontological_category(row['origin'])
        concept = ET.SubElement(concept_layer, 'Concept')
        concept.set('id', f"cili.{row['ili_id']}")
        concept.set('ontological_category', ontological_category)
        concept.set('status', row['status'])
        if row['superseded_by'].strip():
            concept.set('superseded_by', row['superseded_by'])
        wn_name, version, original_id = get_from(row['origin'])
        prov = ET.SubElement(concept, 'Provenance')
        prov.set('resource', wn_name)
        prov.set('version', version)
        prov.set('original_id', original_id)

    gloss_layer = ET.SubElement(root, 'GlossLayer')
    for row in tsv_rows:
        gloss = ET.SubElement(gloss_layer, 'Gloss')
        gloss.set('definiendum', f"cili.{row['ili_id']}")
        gloss.set('language', 'en')
        wn_name, version, original_id = get_from(row['origin'])
        annotated = ET.SubElement(gloss, 'AnnotatedSentence')
        annotated.text = normalize_whitespace(row['definition'])
        prov = ET.SubElement(gloss, 'Provenance')
        prov.set('resource', wn_name)
        prov.set('version', version)
        prov.set('original_id', original_id)

    ET.ElementTree(root).write(
        output_file, encoding='UTF-8', xml_declaration=True, pretty_print=True
    )
    print(f"Created {output_file}")
    print(f"  Concepts: {len(tsv_rows)}")
    print(f"  Glosses: {len(tsv_rows)}")


def main() -> None:
    """Main conversion process."""
    tsv_rows = load_tsv_data('bin/cili.tsv')
    validate_data(tsv_rows)
    create_cygnet_xml(tsv_rows, 'bin/cygnets_presynth/cili-1.0.xml')
    print("\nConversion complete!")


if __name__ == '__main__':
    main()
