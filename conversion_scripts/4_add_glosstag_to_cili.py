#!/usr/bin/env python3
"""
Script to update CILI glosses with sense annotations from PWN 3.0 definitions.

This script:
1. Loads the OEWN file and creates a mapping from old sense IDs to new sense IDs
2. Loads the CILI file and creates a mapping from PWN original IDs to glosses
3. Processes the JSON file to add AnnotatedToken elements to glosses
"""

import json
from pathlib import Path
from lxml import etree as ET
from nltk.corpus import wordnet as wn


def oewn_id_to_json_format(oewn_id):
    """
    Convert OEWN original_id to JSON sense ID format.
    Example: "oewn-necessary__3.00.00.." -> "necessary%3:00:00::"
    """
    # Remove "oewn-" prefix
    assert oewn_id.startswith("oewn-")
    oewn_id = oewn_id[5:]

    # Replace __ with %
    assert len(oewn_id.split('__')) == 2
    before, after = oewn_id.split('__')

    # Replace . with :
    after = after.replace(".", ":")

    oewn_id = before + '%' + after

    return oewn_id


def load_oewn_sense_mapping(oewn_file):
    """
    Load OEWN file and create mapping from old sense IDs to new sense IDs.
    Returns: dict of old_id -> new_id
    """
    print(f"Loading OEWN file: {oewn_file}")
    tree = ET.parse(oewn_file)
    root = tree.getroot()

    sense_layer = root.find('SenseLayer')
    if sense_layer is None:
        raise ValueError("No SenseLayer found in OEWN file")

    old_to_new = {}
    duplicate_old_ids = []
    multiple_from_warnings = []

    for sense in sense_layer.findall('Sense'):
        sense_id = sense.get('id')
        from_elements = sense.findall('Provenance')

        if len(from_elements) > 1:
            multiple_from_warnings.append(f"Sense {sense_id} has {len(from_elements)} Provenance elements")

        for from_elem in from_elements:
            original_id = from_elem.get('original_id')
            if original_id:
                # Convert to JSON format
                json_format_id = oewn_id_to_json_format(original_id)

                if json_format_id in old_to_new:
                    duplicate_old_ids.append(
                        f"Duplicate old ID: {json_format_id} (new IDs: {old_to_new[json_format_id]}, {sense_id})")

                old_to_new[json_format_id] = sense_id

    print(f"Loaded {len(old_to_new)} sense mappings from OEWN")

    if duplicate_old_ids:
        print("\n⚠️  WARNING: Duplicate old IDs found:")
        for warning in duplicate_old_ids:
            print(f"  {warning}")

    if multiple_from_warnings:
        print("\n⚠️  WARNING: Senses with multiple Provenance elements:")
        for warning in multiple_from_warnings:
            print(f"  {warning}")

    return old_to_new


def load_cili_concept_mapping(cili_file):
    """
    Load CILI file and create mapping from PWN original IDs to (concept_id, gloss_element).
    Returns: dict of pwn_id -> (concept_id, gloss_element)
    """
    print(f"\nLoading CILI file: {cili_file}")
    tree = ET.parse(cili_file)
    root = tree.getroot()

    concept_layer = root.find('ConceptLayer')
    gloss_layer = root.find('GlossLayer')

    if concept_layer is None:
        raise ValueError("No ConceptLayer found in CILI file")
    if gloss_layer is None:
        raise ValueError("No GlossLayer found in CILI file")

    # First, map PWN original IDs to concept IDs
    pwn_to_concept = {}
    duplicate_pwn_ids = []
    multiple_from_warnings = []

    for concept in concept_layer.findall('Concept'):
        concept_id = concept.get('id')
        from_elements = concept.findall('Provenance')

        if len(from_elements) > 1:
            multiple_from_warnings.append(f"Concept {concept_id} has {len(from_elements)} Provenance elements")

        for from_elem in from_elements:
            resource = from_elem.get('resource')
            if resource == 'pwn':
                original_id = from_elem.get('original_id')
                if original_id:
                    if original_id in pwn_to_concept:
                        duplicate_pwn_ids.append(
                            f"Duplicate PWN ID: {original_id} (concepts: {pwn_to_concept[original_id]}, {concept_id})")
                    pwn_to_concept[original_id] = concept_id

    print(f"Loaded {len(pwn_to_concept)} concept mappings from CILI")

    if duplicate_pwn_ids:
        print("\n⚠️  WARNING: Duplicate PWN original IDs found:")
        for warning in duplicate_pwn_ids:
            print(f"  {warning}")

    if multiple_from_warnings:
        print("\n⚠️  WARNING: Concepts with multiple Provenance elements:")
        for warning in multiple_from_warnings:
            print(f"  {warning}")

    # Invert the mapping for O(1) lookups: concept_id -> pwn_id
    concept_to_pwn = {concept_id: pwn_id for pwn_id, concept_id in pwn_to_concept.items()}

    # Now map PWN IDs to gloss elements
    pwn_to_gloss = {}
    for gloss in gloss_layer.findall('Gloss'):
        definiendum = gloss.get('definiendum')
        if definiendum in concept_to_pwn:
            pwn_id = concept_to_pwn[definiendum]
            pwn_to_gloss[pwn_id] = gloss

    print(f"Mapped {len(pwn_to_gloss)} PWN IDs to glosses")

    return pwn_to_gloss, root


def get_gloss_text(gloss_element):
    """
    Extract the text content from a gloss element's AnnotatedSentence.
    According to the XSD, all glosses must have an AnnotatedSentence element.
    """
    text_parts = []

    # Get the AnnotatedSentence child (required by XSD)
    annotated_sentence = gloss_element.find('AnnotatedSentence')

    if annotated_sentence is None:
        raise ValueError("Gloss is missing required AnnotatedSentence element")

    # Extract text from AnnotatedSentence
    if annotated_sentence.text:
        text_parts.append(annotated_sentence.text)

    for child in annotated_sentence:
        if child.text:
            text_parts.append(child.text)
        if child.tail:
            text_parts.append(child.tail)

    return ''.join(text_parts)


def check_overlapping_annotations(annotations):
    """
    Check if any annotations overlap. Returns list of overlapping pairs.
    """
    overlaps = []
    sorted_annots = sorted(annotations, key=lambda x: x[0])

    for a1, a2 in zip(sorted_annots, sorted_annots[1:]):
        start1, end1, _ = a1
        start2, end2, _ = a2
        if end1 > start2:
            overlaps.append((a1, a2))

    return overlaps


def validate_glosses(cili_tree):
    """
    Validate that all glosses are well-formed according to the XSD schema.
    Checks:
    1. All glosses have definiendum and language attributes
    2. All glosses have exactly one AnnotatedSentence element
    3. AnnotatedSentence comes before Provenance elements
    4. AnnotatedTokens have valid sense attributes
    """
    print("\n" + "=" * 80)
    print("VALIDATING GLOSSES")
    print("=" * 80)

    # Handle both tree and root
    if hasattr(cili_tree, 'getroot'):
        root = cili_tree.getroot()
    else:
        root = cili_tree

    gloss_layer = root.find('GlossLayer')

    if gloss_layer is None:
        print("ERROR: No GlossLayer found")
        return False

    errors = []
    warnings = []
    total_glosses = 0

    for gloss in gloss_layer.findall('Gloss'):
        total_glosses += 1
        definiendum = gloss.get('definiendum')
        language = gloss.get('language')

        # Check required attributes
        if not definiendum:
            errors.append(f"Gloss missing definiendum attribute")
            continue
        if not language:
            errors.append(f"Gloss {definiendum} missing language attribute")

        # Check element order and count
        children = list(gloss)
        if len(children) == 0:
            errors.append(f"Gloss {definiendum} has no children")
            continue

        # First child must be AnnotatedSentence
        if children[0].tag != 'AnnotatedSentence':
            errors.append(f"Gloss {definiendum}: First child must be AnnotatedSentence, got {children[0].tag}")

        # Count AnnotatedSentence elements
        annotated_sentences = [c for c in children if c.tag == 'AnnotatedSentence']
        if len(annotated_sentences) != 1:
            errors.append(
                f"Gloss {definiendum}: Must have exactly 1 AnnotatedSentence, found {len(annotated_sentences)}")

        # Check that all Provenance elements come after AnnotatedSentence
        found_from = False
        for child in children:
            if child.tag == 'Provenance':
                found_from = True
            elif found_from and child.tag == 'AnnotatedSentence':
                errors.append(f"Gloss {definiendum}: AnnotatedSentence found after Provenance element")

        # Validate AnnotatedTokens within AnnotatedSentence
        annotated_sentence = gloss.find('AnnotatedSentence')
        if annotated_sentence is not None:
            for token in annotated_sentence.findall('AnnotatedToken'):
                sense = token.get('sense')
                if not sense:
                    errors.append(f"Gloss {definiendum}: AnnotatedToken missing sense attribute")
                if not token.text:
                    warnings.append(f"Gloss {definiendum}: AnnotatedToken has empty text")

    print(f"Total glosses validated: {total_glosses}")
    print(
        f"  Glosses with AnnotatedSentence: {sum(1 for g in gloss_layer.findall('Gloss') if g.find('AnnotatedSentence') is not None)}")
    print(
        f"  AnnotatedTokens found: {sum(len(g.find('AnnotatedSentence').findall('AnnotatedToken')) for g in gloss_layer.findall('Gloss') if g.find('AnnotatedSentence') is not None)}")
    print(f"  Provenance elements found: {sum(len(g.findall('Provenance')) for g in gloss_layer.findall('Gloss'))}")

    if errors:
        print(f"\n❌ Found {len(errors)} ERRORS:")
        for error in errors[:30]:  # Show first 30
            print(f"  {error}")
        if len(errors) > 30:
            print(f"  ... and {len(errors) - 30} more errors")
        return False

    if warnings:
        print(f"\n⚠️  Found {len(warnings)} warnings:")
        for warning in warnings[:20]:  # Show first 20
            print(f"  {warning}")
        if len(warnings) > 20:
            print(f"  ... and {len(warnings) - 20} more warnings")

    if not errors:
        print("✅ All glosses are well-formed!")
        return True

    return False


def update_gloss_with_annotations(gloss_element, gloss_text, annotations, sense_mapping):
    """
    Update a gloss element with annotated tokens.
    Modifies the existing AnnotatedSentence in place, leaves everything else untouched.
    """
    # Sort annotations by start position
    sorted_annotations = sorted(annotations, key=lambda x: x[0])

    # Get the existing AnnotatedSentence element
    annotated_sentence = gloss_element.find('AnnotatedSentence')
    if annotated_sentence is None:
        raise ValueError("Gloss is missing AnnotatedSentence element")

    # Remove all children from AnnotatedSentence, but preserve its tail
    for child in list(annotated_sentence):
        annotated_sentence.remove(child)

    # Clear the text content
    annotated_sentence.text = None

    # Build the new content
    last_end = 0
    for i, (start, end, old_sense_id) in enumerate(sorted_annotations):
        # Text before this annotation
        text_before = gloss_text[last_end:start]

        if i == 0:
            # First piece of text
            annotated_sentence.text = text_before
        else:
            # Text between tokens goes in previous token's tail
            annotated_sentence[-1].tail = text_before

        # Get the new sense ID
        new_sense_id = sense_mapping[old_sense_id]

        # Create AnnotatedToken
        token = ET.SubElement(annotated_sentence, 'AnnotatedToken')
        token.set('sense', new_sense_id)
        token.text = gloss_text[start:end]

        last_end = end

    # Add remaining text after last annotation
    if last_end < len(gloss_text):
        remaining_text = gloss_text[last_end:]
        if len(annotated_sentence) > 0:
            annotated_sentence[-1].tail = remaining_text
        else:
            annotated_sentence.text = remaining_text


def synset_id_to_pwn_format(synset_id):
    """
    Convert a synset ID like 'able.a.01' to PWN format like '00128262-s'.
    Uses NLTK to get the offset and POS.
    """
    try:
        synset = wn.synset(synset_id)
        offset = str(synset.offset()).zfill(8)
        pos = synset.pos()
        return f"{offset}-{pos}"
    except Exception as e:
        raise ValueError(f"Could not convert synset ID {synset_id}: {e}")


def process_definitions(json_file, pwn_to_gloss, sense_mapping):
    """
    Process the JSON file and update glosses with annotations.
    """
    print(f"\nLoading definitions from: {json_file}")
    with open(json_file, 'r') as f:
        definitions = json.load(f)

    print(f"Found {len(definitions)} definitions in JSON")

    processed = 0
    skipped = 0
    errors = []

    stats = {
        'no_gloss_in_cili': 0,
        'no_new_sense_id': 0,
        'gloss_mismatch': 0
    }

    for synset_id, defn_data in definitions.items():
        try:
            # Convert synset ID to PWN format
            pwn_id = synset_id_to_pwn_format(synset_id)

            # Get the gloss element
            if pwn_id not in pwn_to_gloss:
                errors.append(f"PWN ID {pwn_id} (from {synset_id}) not found in CILI")
                stats['no_gloss_in_cili'] += 1
                skipped += 1
                continue

            gloss_element = pwn_to_gloss[pwn_id]

            # Get current gloss text
            current_gloss_text = get_gloss_text(gloss_element)
            json_gloss_text = defn_data['string']

            # Check if glosses match
            if current_gloss_text.strip() != json_gloss_text.strip():
                errors.append(f"⚠️  Gloss mismatch for {synset_id} (PWN: {pwn_id}):")
                errors.append(f"  CILI:  '{current_gloss_text}'")
                errors.append(f"  JSON:  '{json_gloss_text}'")
                stats['gloss_mismatch'] += 1

            # Check for overlapping annotations
            annotations = defn_data['annotations']
            overlaps = check_overlapping_annotations(annotations)
            if overlaps:
                errors.append(f"⚠️  Overlapping annotations found for {synset_id}:")
                for annot1, annot2 in overlaps:
                    errors.append(f"  {annot1} overlaps with {annot2}")
                skipped += 1
                continue

            # Filter out annotations with missing sense IDs
            valid_annotations = []
            missing_senses = []

            for start, end, old_sense_id in annotations:
                if old_sense_id not in sense_mapping:
                    missing_senses.append(old_sense_id)
                    stats['no_new_sense_id'] += 1
                else:
                    valid_annotations.append((start, end, old_sense_id))

            if missing_senses:
                errors.append(
                    f"⚠️  Missing sense IDs for {synset_id}: {missing_senses} (skipping these annotations)")

            # Update the gloss with valid annotations only
            update_gloss_with_annotations(gloss_element, json_gloss_text, valid_annotations, sense_mapping)
            processed += 1

        except Exception as e:
            errors.append(f"Error processing {synset_id}: {e}")
            skipped += 1

    print(f"\nProcessed {processed} definitions")
    print(f"Skipped {skipped} definitions")

    if errors:
        print("\n⚠️  ERRORS AND WARNINGS:")
        for error in errors:
            print(error)

    return processed, skipped, stats


def main():
    # File paths
    oewn_matches = sorted(Path('bin/cygnets_presynth').glob('oewn-*.xml'))
    if not oewn_matches:
        raise FileNotFoundError("OEWN output not found in bin/cygnets_presynth/")
    oewn_file = str(oewn_matches[0])
    cili_file = 'bin/cygnets_presynth/cili-1.0.xml'
    json_file = 'bin/concepts_to_definitions.json'
    output_file = 'bin/cygnets_presynth/cili-1.0.xml'

    print("=" * 80)
    print("CILI Gloss Annotation Updater")
    print("=" * 80)

    # Step 1: Load OEWN sense mapping
    sense_mapping = load_oewn_sense_mapping(oewn_file)

    # Step 2: Load CILI concept/gloss mapping
    pwn_to_gloss, cili_root = load_cili_concept_mapping(cili_file)

    # Step 3: Process definitions and update glosses
    processed, skipped, stats = process_definitions(json_file, pwn_to_gloss, sense_mapping)

    # VALIDATE before saving
    if not validate_glosses(cili_root):
        print("\n❌ Validation failed! Check errors above.")
        print("File will still be saved, but may have issues.")

    # Step 4: Save the updated CILI file
    print(f"\nSaving updated CILI file to: {output_file}")
    output_tree = ET.ElementTree(cili_root)
    output_tree.write(
        output_file,
        pretty_print=True,
        xml_declaration=True,
        encoding='UTF-8'
    )

    print("\n" + "=" * 80)
    print("SUMMARY STATISTICS")
    print("=" * 80)
    print(f"Synsets with no gloss in CILI: {stats['no_gloss_in_cili']}")
    print(f"Sense annotations with no new ID in OEWN: {stats['no_new_sense_id']}")
    print(f"Glosses that didn't match: {stats['gloss_mismatch']}")
    print(f"Successfully processed: {processed}")
    print(f"Skipped: {skipped}")
    print("=" * 80)


if __name__ == '__main__':
    main()