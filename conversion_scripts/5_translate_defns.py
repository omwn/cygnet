import xml.etree.ElementTree as ET
from pathlib import Path
import argostranslate.package
import argostranslate.translate
import json
from collections import defaultdict
import time

N_ITER = 100

def extract_glosses():
    """Extract non-English glosses from XML files and cache them."""
    cache_file = Path('bin/extra_glosses.json')

    if cache_file.exists():
        print('Loading glosses from cache')
        with open(cache_file, 'r', encoding='utf-8') as f:
            glosses = json.load(f)
        print(f"Loaded {len(glosses)} glosses from cache")
        return glosses

    print('Extracting non-English glosses from XML files')
    glosses = []
    xml_files = list(Path("bin/cygnets_presynth").glob("*.xml"))

    for i, xml_file in enumerate(xml_files):
        print(f'Processing file {i + 1}/{len(xml_files)}')

        tree = ET.parse(xml_file)
        root = tree.getroot()

        for gloss in root.findall('.//Gloss'):
            language = gloss.get('language')

            # Exclude English
            if language != 'en':
                definiendum_id = gloss.get('definiendum')
                if not definiendum_id.startswith('cili'):
                    # Check for AnnotatedSentence (new format)
                    annotated_sentence = gloss.find('AnnotatedSentence')
                    if annotated_sentence is not None:
                        definition = ''.join(annotated_sentence.itertext()).strip()
                    else:
                        # Old format: extract text directly from Gloss (excluding From elements)
                        text_parts = []
                        if gloss.text:
                            text_parts.append(gloss.text)
                        for child in gloss:
                            if child.tag != 'From':
                                if child.text:
                                    text_parts.append(child.text)
                                if child.tail:
                                    text_parts.append(child.tail)
                        definition = ''.join(text_parts).strip()

                    glosses.append({
                        'definition': definition,
                        'definiendum_id': definiendum_id,
                        'language': language
                    })

    # Cache the extracted glosses
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump(glosses, f, ensure_ascii=False, indent=2)

    print(f"Extracted and cached {len(glosses)} glosses")
    return glosses


def get_already_translated():
    """Load already translated glosses from JSONL file."""
    output_file = Path('bin/translated_glosses.jsonl')
    translated_ids = set()

    if output_file.exists():
        with open(output_file, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    record = json.loads(line)
                    # A concept can have more than one Definition in the same
                    # language (e.g. Latvian tezaurs), so definiendum_id+language
                    # alone isn't a unique key — include the source text too.
                    translated_ids.add(
                        (record['definiendum_id'], record['source_language'], record['source_text'])
                    )
        print(f"Found {len(translated_ids)} already translated glosses")

    return translated_ids


def filter_pending_glosses(glosses, translated_ids):
    """Filter out already translated glosses."""
    pending = [
        g for g in glosses
        if (g['definiendum_id'], g['language'], g['definition']) not in translated_ids
    ]
    print(f"Remaining glosses to translate: {len(pending)}")
    return pending


def group_by_language(glosses):
    """Group glosses by language code."""
    by_language = defaultdict(list)
    for gloss in glosses:
        by_language[gloss['language']].append(gloss)
    return by_language


def translate_language_batch(language_code, glosses, output_file):
    """Translate all glosses for a single language and write incrementally."""
    print(f"\n{'=' * 60}")
    print(f"Processing language: {language_code} ({len(glosses)} glosses)")
    print(f"{'=' * 60}")

    # Download and install translation model for this language
    print(f"Installing translation model: {language_code} -> en")
    argostranslate.package.update_package_index()
    available_packages = argostranslate.package.get_available_packages()
    pkg = next((p for p in available_packages if p.from_code == language_code and p.to_code == 'en'), None)

    if not pkg:
        print(f"Warning: No translation package found for {language_code} -> en")
        print(f"Skipping {len(glosses)} glosses in {language_code}")
        return

    argostranslate.package.install_from_path(pkg.download())

    # Get translator for this language
    installed = argostranslate.translate.get_installed_languages()
    from_lang = next((t for t in installed if t.code == language_code), None)
    to_lang = next((t for t in installed if t.code == 'en'), None)

    if not from_lang or not to_lang:
        print(f"Warning: Could not initialize translator for {language_code}")
        return

    translator = from_lang.get_translation(to_lang)

    # Translate and write out incrementally
    print(f"Translating {len(glosses)} glosses...")
    batch_start_time = time.time()

    with open(output_file, 'a', encoding='utf-8') as f:
        for i, gloss in enumerate(glosses):
            if (i + 1) % N_ITER == 0:
                # Calculate time for last 100 elements
                current_time = time.time()
                batch_duration = current_time - batch_start_time

                # Calculate ETA
                remaining_glosses = len(glosses) - (i + 1)
                time_per_gloss = batch_duration / N_ITER
                estimated_seconds_remaining = remaining_glosses * time_per_gloss

                # Format ETA
                minutes = estimated_seconds_remaining / 60
                eta_str = f"{minutes:.1f}m"

                print(f"  Translated {i + 1}/{len(glosses)} glosses for {language_code} | ETA: {eta_str}")

                # Reset timer for next batch
                batch_start_time = current_time

            translated_text = translator.translate(gloss['definition'])

            record = {
                'translated_definition': translated_text,
                'definiendum_id': gloss['definiendum_id'],
                'source_language': language_code,
                'source_text': gloss['definition']
            }

            # Write as single line JSON
            f.write(json.dumps(record, ensure_ascii=False) + '\n')

    print(f"✓ Completed {language_code}: {len(glosses)} glosses translated")


def create_xml_from_translations():
    """Convert translated glosses from JSONL to XML format."""
    print("\n" + "=" * 60)
    print("Creating XML output from translations...")
    print("=" * 60)

    # Read all translations from JSONL
    translations = []
    jsonl_file = Path('bin/translated_glosses.jsonl')

    with open(jsonl_file, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                translations.append(json.loads(line))

    print(f"Loaded {len(translations)} translated glosses")

    # Create XML structure
    root = ET.Element('CygnetResource')
    root.set('id', 'mtg')
    root.set('label', 'Machine Translated Glosses')
    root.set('version', '1.0')

    # Create GlossLayer
    gloss_layer = ET.SubElement(root, 'GlossLayer')

    # Add each translation as a Gloss element
    for trans in translations:
        gloss = ET.SubElement(gloss_layer, 'Gloss')
        gloss.set('definiendum', trans['definiendum_id'])
        gloss.set('language', 'en')  # All translations are to English

        # Create AnnotatedSentence with translated text
        annotated_sentence = ET.SubElement(gloss, 'AnnotatedSentence')
        annotated_sentence.text = trans['translated_definition']

    # Write to XML file with proper formatting
    tree = ET.ElementTree(root)
    ET.indent(tree, space='  ')  # Pretty print with 2-space indentation

    output_xml = Path('bin/cygnets_presynth/mtg-1.0.xml')
    tree.write(output_xml, encoding='utf-8', xml_declaration=True)

    print(f"✓ XML output written to: {output_xml}")
    print(f"  Total glosses: {len(translations)}")
    print("=" * 60)


def main():
    # Step 1: Extract or load glosses
    all_glosses = extract_glosses()

    languages = {g['language'] for g in all_glosses}
    print(f"\nFound {len(languages)} unique languages: {sorted(languages)}")

    # Step 2: Check what's already been translated
    translated_ids = get_already_translated()

    # Step 3: Filter to pending glosses
    pending_glosses = filter_pending_glosses(all_glosses, translated_ids)

    if not pending_glosses:
        print("\n✓ All glosses have already been translated!")
    else:

        # Step 4: Group by language
        by_language = group_by_language(pending_glosses)

        # Step 5: Process each language one at a time
        output_file = Path('bin/translated_glosses.jsonl')
        output_file.parent.mkdir(parents=True, exist_ok=True)

        print(f"\nProcessing {len(by_language)} languages...")
        for i, (lang_code, lang_glosses) in enumerate(sorted(by_language.items()), 1):
            print(f"\n[{i}/{len(by_language)}] ", end='')
            translate_language_batch(lang_code, lang_glosses, output_file)

        print("\n" + "=" * 60)
        print("✓ Translation complete!")
        print(f"Output written to: {output_file}")
        print("=" * 60)

    create_xml_from_translations()


if __name__ == '__main__':
    main()