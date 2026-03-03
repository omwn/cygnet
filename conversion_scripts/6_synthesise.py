#!/usr/bin/env python3
"""
Merge multiple Cygnet XML files into a single consolidated resource.
"""

import os
import json
from pathlib import Path
from collections import defaultdict
from lxml import etree

# Namespace (empty for this schema)
NS = {}


class MergeStats:
    """Track statistics during merge process."""

    def __init__(self):
        self.files_processed = 0
        self.concepts_total = 0
        self.lexemes_total = 0
        self.lexemes_merged = 0
        self.senses_total = 0
        self.senses_deduplicated = 0
        self.glosses_total = 0
        self.glosses_discarded = defaultdict(lambda: defaultdict(int))  # file -> language -> count
        self.concepts_missing_english = defaultdict(int)  # file -> count
        self.examples_total = 0
        self.sense_relations_total = 0
        self.sense_relations_deduplicated = 0
        self.concept_relations_total = 0
        self.concept_relations_deduplicated = 0
        self.concepts_removed_no_gloss = 0
        self.senses_removed_no_concept = 0
        self.sense_relations_removed_cascade = 0
        self.concept_relations_removed_cascade = 0
        self.annotated_tokens_removed_glosses = 0
        self.annotated_tokens_removed_examples = 0
        self.lexemes_removed_orphaned = 0
        self.examples_removed_no_annotations = 0
        self.lexemes_case_merged = 0
        self.senses_case_deduplicated = 0
        self.sense_relations_updated_case_merge = 0
        self.annotated_tokens_updated_glosses_case_merge = 0
        self.annotated_tokens_updated_examples_case_merge = 0
        self.concept_relations_pos_violations = 0

    def to_dict(self):
        """Convert stats to dictionary for JSON serialization."""
        return {
            'files_processed': self.files_processed,
            'concepts_total': self.concepts_total,
            'lexemes_total': self.lexemes_total,
            'lexemes_merged': self.lexemes_merged,
            'senses_total': self.senses_total,
            'senses_deduplicated': self.senses_deduplicated,
            'glosses_total': self.glosses_total,
            'glosses_discarded_per_file': {
                file: dict(langs) for file, langs in self.glosses_discarded.items()
            },
            'concepts_missing_english_per_file': dict(self.concepts_missing_english),
            'examples_total': self.examples_total,
            'sense_relations_total': self.sense_relations_total,
            'sense_relations_deduplicated': self.sense_relations_deduplicated,
            'concept_relations_total': self.concept_relations_total,
            'concept_relations_deduplicated': self.concept_relations_deduplicated,
            'concepts_removed_no_gloss': self.concepts_removed_no_gloss,
            'senses_removed_no_concept': self.senses_removed_no_concept,
            'sense_relations_removed_cascade': self.sense_relations_removed_cascade,
            'concept_relations_removed_cascade': self.concept_relations_removed_cascade,
            'annotated_tokens_removed_glosses': self.annotated_tokens_removed_glosses,
            'annotated_tokens_removed_examples': self.annotated_tokens_removed_examples,
            'lexemes_removed_orphaned': self.lexemes_removed_orphaned,
            'examples_removed_no_annotations': self.examples_removed_no_annotations,
            'lexemes_case_merged': self.lexemes_case_merged,
            'senses_case_deduplicated': self.senses_case_deduplicated,
            'sense_relations_updated_case_merge': self.sense_relations_updated_case_merge,
            'annotated_tokens_updated_glosses_case_merge': self.annotated_tokens_updated_glosses_case_merge,
            'annotated_tokens_updated_examples_case_merge': self.annotated_tokens_updated_examples_case_merge,
            'concept_relations_pos_violations': self.concept_relations_pos_violations
        }


def get_provenance_elements(element):
    """Extract all Provenance elements from an element."""
    return element.findall('Provenance')


def merge_wordform(existing_wf, new_wf):
    """Merge Scripts and Pronunciations from new_wf into existing_wf."""
    # Get existing scripts
    existing_scripts = {s.text for s in existing_wf.findall('Script') if s.text}

    # Add new scripts
    for script in new_wf.findall('Script'):
        if script.text and script.text not in existing_scripts:
            script_elem = etree.SubElement(existing_wf, 'Script')
            script_elem.text = script.text
            existing_scripts.add(script.text)

    # Get existing pronunciations as (text, variety) tuples
    existing_prons = set()
    for pron in existing_wf.findall('Pronunciation'):
        pron_text = pron.text or ''
        pron_variety = pron.get('variety')
        existing_prons.add((pron_text, pron_variety))

    # Add new pronunciations
    for pron in new_wf.findall('Pronunciation'):
        pron_text = pron.text or ''
        pron_variety = pron.get('variety')
        if (pron_text, pron_variety) not in existing_prons:
            pron_elem = etree.SubElement(existing_wf, 'Pronunciation')
            pron_elem.text = pron_text
            if pron_variety:
                pron_elem.set('variety', pron_variety)
            existing_prons.add((pron_text, pron_variety))


def merge_lexemes(existing_lexeme, new_lexeme):
    """Merge new_lexeme into existing_lexeme."""
    from copy import deepcopy

    # Build map of form -> Wordform element for existing lexeme
    existing_wordforms = {}
    for wf in existing_lexeme.findall('Wordform'):
        form = wf.get('form')
        if form:
            existing_wordforms[form] = wf

    # Collect new wordforms to add
    new_wordforms_to_add = []
    for new_wf in new_lexeme.findall('Wordform'):
        form = new_wf.get('form')
        if not form:
            continue

        if form in existing_wordforms:
            # Merge into existing wordform
            merge_wordform(existing_wordforms[form], new_wf)
        else:
            # Collect for adding later
            wf_copy = deepcopy(new_wf)
            new_wordforms_to_add.append(wf_copy)
            existing_wordforms[form] = wf_copy  # ← Fixed: track the copy

    # Insert new wordforms before any Provenance elements
    # Find the position of the first Provenance element
    insert_pos = None
    for i, child in enumerate(existing_lexeme):
        if child.tag == 'Provenance':
            insert_pos = i
            break

    # Insert new wordforms at the correct position
    if insert_pos is not None:
        # Insert before Provenance elements
        for wf in reversed(new_wordforms_to_add):
            existing_lexeme.insert(insert_pos, wf)
    else:
        # No Provenance elements yet, just append
        for wf in new_wordforms_to_add:
            existing_lexeme.append(wf)

    # Merge provenance (with duplicate checking)
    existing_prov_set = set()
    for prov in existing_lexeme.findall('Provenance'):
        resource = prov.get('resource', '')
        version = prov.get('version', '')
        original_id = prov.get('original_id', '')
        existing_prov_set.add((resource, version, original_id))

    for prov in new_lexeme.findall('Provenance'):
        resource = prov.get('resource', '')
        version = prov.get('version', '')
        original_id = prov.get('original_id', '')
        prov_key = (resource, version, original_id)

        if prov_key not in existing_prov_set:
            existing_lexeme.append(deepcopy(prov))
            existing_prov_set.add(prov_key)

def canonicalize_relation(rel_type, source, target):
    """Canonicalize symmetric relations to have source < target alphabetically."""
    symmetric_relations = {'opposite', 'antonym'}
    if rel_type in symmetric_relations and source > target:
        return target, source
    return source, target

def get_concept_category(concept_id, concepts):
    """Get the ontological_category attribute from a concept."""
    concept = concepts.get(concept_id)
    assert concept is not None
    return concept.get('ontological_category')

def create_merged_lexeme_id(lexeme_ids):
    """
    Create a merged lexeme ID from multiple case-variant lexeme IDs.
    Extracts the forms from each ID, combines them, and reconstructs the ID.

    Args:
        lexeme_ids: List of lexeme IDs in order of appearance

    Returns:
        New lexeme ID with combined forms
    """
    # Parse first lexeme ID to get language and category
    first_id = lexeme_ids[0]
    parts = first_id.split('.')
    language = parts[0]
    category = parts[1]

    # Collect all forms from all lexemes
    all_forms = []
    for lex_id in lexeme_ids:
        forms_part = lex_id.split('.', 2)[2]  # Everything after language.category
        forms = forms_part.split('-')
        all_forms.extend(forms)

    # Create merged ID
    merged_id = f"{language}.{category}.{'-'.join(all_forms)}"
    return merged_id

def remove_annotated_tokens_for_deleted_senses(element, deleted_senses, stats_counter):
    """
    Remove AnnotatedToken elements whose sense references a deleted sense.
    Replace them with their text content.
    Returns the number of tokens removed.
    """
    count = 0
    annotated_sentence = element.find('AnnotatedSentence')
    if annotated_sentence is None:
        return count

    # Find all AnnotatedToken elements
    tokens_to_remove = []
    for token in annotated_sentence.findall('AnnotatedToken'):
        sense_ref = token.get('sense')
        if sense_ref in deleted_senses:
            tokens_to_remove.append(token)

    # Remove tokens and replace with text content
    for token in tokens_to_remove:
        # Get the text content
        text_content = token.text or ''

        # Find the position of this token in the parent
        parent = token.getparent()
        index = list(parent).index(token)

        # Get the tail (text after this element)
        tail = token.tail or ''

        # If there's a previous sibling, append to its tail
        if index > 0:
            prev = parent[index - 1]
            prev.tail = (prev.tail or '') + text_content + tail
        else:
            # Otherwise, append to parent's text
            parent.text = (parent.text or '') + text_content + tail

        # Remove the token
        parent.remove(token)
        count += 1

    return count

def merge_cygnet_files(input_dir, output_file, log_file):
    """Merge all Cygnet XML files in input_dir into a single output file."""

    stats = MergeStats()

    # Get all XML files sorted alphabetically
    input_path = Path(input_dir)
    xml_files = sorted(input_path.glob('*.xml'))

    print(f"Found {len(xml_files)} XML files to merge")

    # PHASE 1: Load all data from all files
    print("\nPhase 1: Loading all data...")

    all_concepts = []  # list of (element, source_file)
    all_lexemes = []  # list of (element, source_file)
    all_senses = []  # list of (element, source_file)
    all_glosses = []  # list of (element, source_file)
    all_examples = []  # list of (element, source_file)
    all_sense_relations = []  # list of (element, source_file)
    all_concept_relations = []  # list of (element, source_file)
    resources = {}  # id -> attrib dict

    for xml_file in xml_files:
        print(f"  Loading: {xml_file.name}")
        stats.files_processed += 1
        current_file = xml_file.name

        tree = etree.parse(str(xml_file))
        root = tree.getroot()

        # Collect source resource metadata keyed by id
        res_id = root.get('id')
        if res_id and res_id not in resources:
            resources[res_id] = dict(root.attrib)

        # Load ConceptLayer
        concept_layer = root.find('ConceptLayer')
        if concept_layer is not None:
            for concept in concept_layer.findall('Concept'):
                all_concepts.append((concept, current_file))

        # Load LexemeLayer
        lexeme_layer = root.find('LexemeLayer')
        if lexeme_layer is not None:
            for lexeme in lexeme_layer.findall('Lexeme'):
                all_lexemes.append((lexeme, current_file))

        # Load SenseLayer
        sense_layer = root.find('SenseLayer')
        if sense_layer is not None:
            for sense in sense_layer.findall('Sense'):
                all_senses.append((sense, current_file))

        # Load GlossLayer
        gloss_layer = root.find('GlossLayer')
        if gloss_layer is not None:
            for gloss in gloss_layer.findall('Gloss'):
                all_glosses.append((gloss, current_file))

        # Load ExampleLayer
        example_layer = root.find('ExampleLayer')
        if example_layer is not None:
            for example in example_layer.findall('Example'):
                all_examples.append((example, current_file))

        # Load SenseRelationLayer
        sense_relation_layer = root.find('SenseRelationLayer')
        if sense_relation_layer is not None:
            for relation in sense_relation_layer.findall('SenseRelation'):
                all_sense_relations.append((relation, current_file))

        # Load ConceptRelationLayer
        concept_relation_layer = root.find('ConceptRelationLayer')
        if concept_relation_layer is not None:
            for relation in concept_relation_layer.findall('ConceptRelation'):
                all_concept_relations.append((relation, current_file))

    # PHASE 2: Process and merge all data
    print("\nPhase 2: Processing and merging data...")

    # Process concepts
    print("  Processing concepts...")
    concepts = {}  # id -> element
    for concept, source_file in all_concepts:
        concept_id = concept.get('id')
        assert concept_id not in concepts, \
            f"Duplicate concept ID '{concept_id}' found in {source_file}"
        concepts[concept_id] = concept
        stats.concepts_total += 1

    # Process lexemes
    print("  Processing lexemes...")
    lexemes = {}  # id -> element
    for lexeme, source_file in all_lexemes:
        lexeme_id = lexeme.get('id')
        if lexeme_id in lexemes:
            # Merge lexemes
            merge_lexemes(lexemes[lexeme_id], lexeme)
            stats.lexemes_merged += 1
        else:
            lexemes[lexeme_id] = lexeme
            stats.lexemes_total += 1

    # Process senses
    print("  Processing senses...")
    senses = {}  # id -> element
    sense_keys = {}  # (signifier, signified) -> id
    for sense, source_file in all_senses:
        sense_id = sense.get('id')
        signifier = sense.get('signifier')
        signified = sense.get('signified')

        sense_key = (signifier, signified)

        if sense_id in senses:
            # Duplicate sense ID - check it's identical
            existing_sense = senses[sense_id]
            assert existing_sense.get('signifier') == signifier, \
                f"Sense {sense_id} has conflicting signifier in {source_file}"
            assert existing_sense.get('signified') == signified, \
                f"Sense {sense_id} has conflicting signified in {source_file}"

            # Combine provenance (with duplicate checking)
            existing_prov_set = set()
            for prov in existing_sense.findall('Provenance'):
                resource = prov.get('resource', '')
                version = prov.get('version', '')
                original_id = prov.get('original_id', '')
                existing_prov_set.add((resource, version, original_id))

            for prov in get_provenance_elements(sense):
                resource = prov.get('resource', '')
                version = prov.get('version', '')
                original_id = prov.get('original_id', '')
                prov_key = (resource, version, original_id)

                if prov_key not in existing_prov_set:
                    existing_sense.append(prov)
                    existing_prov_set.add(prov_key)

            stats.senses_deduplicated += 1

        elif sense_key in sense_keys:
            # Same signifier/signified but different ID - this is an error
            existing_id = sense_keys[sense_key]
            assert False, \
                f"Sense with key {sense_key} exists with different IDs: {existing_id} and {sense_id} in {source_file}"
        else:
            senses[sense_id] = sense
            sense_keys[sense_key] = sense_id
            stats.senses_total += 1

    # Process glosses
    print("  Processing glosses...")
    glosses = {}  # (concept_id, language) -> element
    concept_glosses = defaultdict(set)  # concept_id -> set of languages
    for gloss, source_file in all_glosses:
        definiendum = gloss.get('definiendum')
        language = gloss.get('language')
        gloss_key = (definiendum, language)

        if gloss_key in glosses:
            # Discard duplicate gloss
            stats.glosses_discarded[source_file][language] += 1
        else:
            glosses[gloss_key] = gloss
            concept_glosses[definiendum].add(language)
            stats.glosses_total += 1

    # Process examples (no deduplication)
    print("  Processing examples...")
    examples = [example for example, _ in all_examples]
    stats.examples_total = len(examples)

    # Process sense relations
    print("  Processing sense relations...")
    sense_relations = {}  # (relation_type, source, target) -> element
    for relation, source_file in all_sense_relations:
        rel_type = relation.get('relation_type')
        source = relation.get('source')
        target = relation.get('target')

        # Canonicalize symmetric relations
        source, target = canonicalize_relation(rel_type, source, target)

        rel_key = (rel_type, source, target)

        if rel_key in sense_relations:
            # Combine provenance (with duplicate checking)
            existing = sense_relations[rel_key]
            existing_prov_set = set()
            for prov in existing.findall('Provenance'):
                resource = prov.get('resource', '')
                version = prov.get('version', '')
                original_id = prov.get('original_id', '')
                existing_prov_set.add((resource, version, original_id))

            for prov in get_provenance_elements(relation):
                resource = prov.get('resource', '')
                version = prov.get('version', '')
                original_id = prov.get('original_id', '')
                prov_key = (resource, version, original_id)

                if prov_key not in existing_prov_set:
                    existing.append(prov)
                    existing_prov_set.add(prov_key)
            stats.sense_relations_deduplicated += 1
        else:
            # Update relation with canonicalized source/target
            relation.set('source', source)
            relation.set('target', target)
            sense_relations[rel_key] = relation
            stats.sense_relations_total += 1

    # Process concept relations
    print("  Processing concept relations...")
    concept_relations = {}  # (relation_type, source, target) -> element
    for relation, source_file in all_concept_relations:
        rel_type = relation.get('relation_type')
        source = relation.get('source')
        target = relation.get('target')

        # Canonicalize symmetric relations
        source, target = canonicalize_relation(rel_type, source, target)

        rel_key = (rel_type, source, target)

        if rel_key in concept_relations:
            # Combine provenance (with duplicate checking)
            existing = concept_relations[rel_key]
            existing_prov_set = set()
            for prov in existing.findall('Provenance'):
                resource = prov.get('resource', '')
                version = prov.get('version', '')
                original_id = prov.get('original_id', '')
                existing_prov_set.add((resource, version, original_id))

            for prov in get_provenance_elements(relation):
                resource = prov.get('resource', '')
                version = prov.get('version', '')
                original_id = prov.get('original_id', '')
                prov_key = (resource, version, original_id)

                if prov_key not in existing_prov_set:
                    existing.append(prov)
                    existing_prov_set.add(prov_key)
            stats.concept_relations_deduplicated += 1
        else:
            # Update relation with canonicalized source/target
            relation.set('source', source)
            relation.set('target', target)
            concept_relations[rel_key] = relation
            stats.concept_relations_total += 1

    # PHASE 3: Merge case-variant lexemes with overlapping concepts
    print("\nPhase 3: Merging case-variant lexemes...")

    # Group senses by (normalized_signifier, signified)
    concept_sense_groups = defaultdict(list)  # (normalized_signifier, signified) -> [sense_ids]
    for sense_id, sense in senses.items():
        signifier = sense.get('signifier')
        signified = sense.get('signified')
        normalized = signifier.lower() if signifier else None
        concept_sense_groups[(normalized, signified)].append(sense_id)

    # Process groups with 2+ senses (case variants pointing to same concept)
    senses_to_delete_case_merge = set()
    new_senses_to_add = {}  # sense_id -> sense_element
    sense_id_mapping = {}  # old_sense_id -> new_sense_id

    for (normalized_signifier, signified), sense_ids in concept_sense_groups.items():
        if len(sense_ids) < 2:
            continue

        # Get the constituent lexeme IDs in order of appearance
        constituent_lexeme_ids = []
        for sense_id in sense_ids:
            signifier = senses[sense_id].get('signifier')
            if signifier not in constituent_lexeme_ids:
                constituent_lexeme_ids.append(signifier)

        # Create merged lexeme ID
        merged_lexeme_id = create_merged_lexeme_id(constituent_lexeme_ids)

        # Create or get merged lexeme
        if merged_lexeme_id not in lexemes:
            # Create new merged lexeme
            # Create new merged lexeme
            merged_lexeme = etree.Element('Lexeme')
            merged_lexeme.set('id', merged_lexeme_id)

            # Copy required attributes from first constituent lexeme
            first_constituent = lexemes[constituent_lexeme_ids[0]]
            merged_lexeme.set('language', first_constituent.get('language'))
            merged_lexeme.set('grammatical_category', first_constituent.get('grammatical_category'))

            # Merge wordforms and provenance from all constituent lexemes
            for constituent_id in constituent_lexeme_ids:
                if constituent_id in lexemes:
                    merge_lexemes(merged_lexeme, lexemes[constituent_id])

            lexemes[merged_lexeme_id] = merged_lexeme
            stats.lexemes_case_merged += 1

        # Create new sense from merged lexeme to concept
        new_sense_id = f"sense_{merged_lexeme_id}_{signified}"

        # Build mapping from old sense IDs to new sense ID
        for old_sense_id in sense_ids:
            sense_id_mapping[old_sense_id] = new_sense_id

        if new_sense_id not in senses and new_sense_id not in new_senses_to_add:
            new_sense = etree.Element('Sense')
            new_sense.set('id', new_sense_id)
            new_sense.set('signifier', merged_lexeme_id)
            new_sense.set('signified', signified)

            # Combine provenance from all deleted senses
            combined_prov = set()
            for old_sense_id in sense_ids:
                old_sense = senses[old_sense_id]
                for prov in old_sense.findall('Provenance'):
                    resource = prov.get('resource', '')
                    version = prov.get('version', '')
                    original_id = prov.get('original_id', '')
                    prov_key = (resource, version, original_id)
                    if prov_key not in combined_prov:
                        new_sense.append(prov)
                        combined_prov.add(prov_key)

            new_senses_to_add[new_sense_id] = new_sense

        # Mark old senses for deletion
        for old_sense_id in sense_ids:
            senses_to_delete_case_merge.add(old_sense_id)
            stats.senses_case_deduplicated += 1

    # Delete old senses
    for sense_id in senses_to_delete_case_merge:
        if sense_id in senses:
            signifier = senses[sense_id].get('signifier')
            signified = senses[sense_id].get('signified')
            sense_key = (signifier, signified)
            if sense_key in sense_keys:
                del sense_keys[sense_key]
            del senses[sense_id]

    # Add new merged senses
    for sense_id, sense in new_senses_to_add.items():
        senses[sense_id] = sense
        signifier = sense.get('signifier')
        signified = sense.get('signified')
        sense_keys[(signifier, signified)] = sense_id

    # Update SenseRelations to point to merged senses
    print("    Updating sense relations...")
    for rel_key, relation in list(sense_relations.items()):
        rel_type, source, target = rel_key
        new_source = sense_id_mapping.get(source, source)
        new_target = sense_id_mapping.get(target, target)

        if new_source != source or new_target != target:
            # Need to update the relation
            # Remove old relation
            del sense_relations[rel_key]

            # Canonicalize new relation
            new_source, new_target = canonicalize_relation(rel_type, new_source, new_target)
            new_rel_key = (rel_type, new_source, new_target)

            # Check if this relation already exists
            if new_rel_key in sense_relations:
                # Merge provenance
                existing = sense_relations[new_rel_key]
                existing_prov_set = set()
                for prov in existing.findall('Provenance'):
                    resource = prov.get('resource', '')
                    version = prov.get('version', '')
                    original_id = prov.get('original_id', '')
                    existing_prov_set.add((resource, version, original_id))

                for prov in relation.findall('Provenance'):
                    resource = prov.get('resource', '')
                    version = prov.get('version', '')
                    original_id = prov.get('original_id', '')
                    prov_key = (resource, version, original_id)
                    if prov_key not in existing_prov_set:
                        existing.append(prov)
                        existing_prov_set.add(prov_key)
            else:
                # Update relation attributes and add to dict
                relation.set('source', new_source)
                relation.set('target', new_target)
                sense_relations[new_rel_key] = relation

            stats.sense_relations_updated_case_merge += 1

    # Update AnnotatedTokens in glosses
    print("    Updating annotated tokens in glosses...")
    for gloss, source_file in all_glosses:
        annotated_sentence = gloss.find('AnnotatedSentence')
        if annotated_sentence is not None:
            for token in annotated_sentence.findall('AnnotatedToken'):
                sense_ref = token.get('sense')
                if sense_ref in sense_id_mapping:
                    token.set('sense', sense_id_mapping[sense_ref])
                    stats.annotated_tokens_updated_glosses_case_merge += 1

    # Update AnnotatedTokens in examples
    print("    Updating annotated tokens in examples...")
    for example, source_file in all_examples:
        annotated_sentence = example.find('AnnotatedSentence')
        if annotated_sentence is not None:
            for token in annotated_sentence.findall('AnnotatedToken'):
                sense_ref = token.get('sense')
                if sense_ref in sense_id_mapping:
                    token.set('sense', sense_id_mapping[sense_ref])
                    stats.annotated_tokens_updated_examples_case_merge += 1

    print(f"    Created {stats.lexemes_case_merged} merged lexemes")
    print(f"    Deduplicated {stats.senses_case_deduplicated} case-variant senses")
    print(f"    Updated {stats.sense_relations_updated_case_merge} sense relations")
    print(f"    Updated {stats.annotated_tokens_updated_glosses_case_merge} annotated tokens in glosses")
    print(f"    Updated {stats.annotated_tokens_updated_examples_case_merge} annotated tokens in examples")

    # PHASE 4: Validation - check for concepts missing English glosses
    print("\nPhase 4: Validation...")
    print("  Checking for concepts missing English glosses...")

    # Build map of concept_id -> source_file for better error reporting
    concept_source_map = {}
    for concept, source_file in all_concepts:
        concept_id = concept.get('id')
        concept_source_map[concept_id] = source_file

    for concept_id in concepts.keys():
        if 'en' not in concept_glosses[concept_id]:
            # Only warn if the concept has OTHER glosses (will be removed in Phase 5 if no glosses at all)
            if len(concept_glosses[concept_id]) > 0:
                source_file = concept_source_map.get(concept_id, 'unknown')
                print(f"  WARNING: Concept {concept_id} missing English gloss (from {source_file})")
                stats.concepts_missing_english[source_file] += 1

    # PHASE 5: Remove concepts without glosses and cascade deletions
    print("\nPhase 5: Removing concepts without glosses...")

    # Step 4.1: Identify concepts with glosses
    concepts_with_glosses = set()
    for (concept_id, language), gloss in glosses.items():
        concepts_with_glosses.add(concept_id)

    # Identify concepts to delete
    concepts_to_delete = set()
    for concept_id in concepts.keys():
        if concept_id not in concepts_with_glosses:
            concepts_to_delete.add(concept_id)
            stats.concepts_removed_no_gloss += 1

    print(f"  Found {len(concepts_to_delete)} concepts without glosses")

    # Step 4.2: Delete concepts
    for concept_id in concepts_to_delete:
        del concepts[concept_id]

    # Step 4.3: Delete ConceptRelations referencing deleted concepts
    concept_relations_to_delete = []
    for rel_key, relation in concept_relations.items():
        rel_type, source, target = rel_key
        if source in concepts_to_delete or target in concepts_to_delete:
            concept_relations_to_delete.append(rel_key)
            stats.concept_relations_removed_cascade += 1

    for rel_key in concept_relations_to_delete:
        del concept_relations[rel_key]

    print(f"  Removed {stats.concept_relations_removed_cascade} concept relations")

    # Step 4.4: Delete Senses referencing deleted concepts
    senses_to_delete = set()
    for sense_id, sense in list(senses.items()):
        signified = sense.get('signified')
        if signified in concepts_to_delete:
            senses_to_delete.add(sense_id)
            del senses[sense_id]
            stats.senses_removed_no_concept += 1

            # Also remove from sense_keys
            signifier = sense.get('signifier')
            sense_key = (signifier, signified)
            if sense_key in sense_keys:
                del sense_keys[sense_key]

    print(f"  Removed {stats.senses_removed_no_concept} senses")

    # Step 4.5: Delete SenseRelations referencing deleted senses
    sense_relations_to_delete = []
    for rel_key, relation in sense_relations.items():
        rel_type, source, target = rel_key
        if source in senses_to_delete or target in senses_to_delete:
            sense_relations_to_delete.append(rel_key)
            stats.sense_relations_removed_cascade += 1

    for rel_key in sense_relations_to_delete:
        del sense_relations[rel_key]

    print(f"  Removed {stats.sense_relations_removed_cascade} sense relations")

    # Step 4.6: Clean AnnotatedTokens in Glosses
    print("  Cleaning AnnotatedTokens in glosses...")
    for gloss in glosses.values():
        count = remove_annotated_tokens_for_deleted_senses(gloss, senses_to_delete, stats)
        stats.annotated_tokens_removed_glosses += count

    print(f"  Removed {stats.annotated_tokens_removed_glosses} annotated tokens from glosses")

    # Step 4.7: Clean AnnotatedTokens in Examples and remove empty examples
    print("  Cleaning AnnotatedTokens in examples...")
    for example in examples:
        count = remove_annotated_tokens_for_deleted_senses(example, senses_to_delete, stats)
        stats.annotated_tokens_removed_examples += count

    print(f"  Removed {stats.annotated_tokens_removed_examples} annotated tokens from examples")

    # Step 4.8: Remove examples with no AnnotatedTokens
    print("  Removing examples with no annotated tokens...")
    examples_to_keep = []
    for example in examples:
        annotated_sentence = example.find('AnnotatedSentence')
        if annotated_sentence is not None:
            # Check if there are any AnnotatedToken elements
            if len(annotated_sentence.findall('AnnotatedToken')) > 0:
                examples_to_keep.append(example)
            else:
                stats.examples_removed_no_annotations += 1
        else:
            # Keep examples without AnnotatedSentence (shouldn't happen per schema)
            examples_to_keep.append(example)

    examples = examples_to_keep
    print(f"  Removed {stats.examples_removed_no_annotations} examples with no annotations")

    # PHASE 6: Remove orphaned lexemes
    print("\nPhase 6: Removing orphaned lexemes...")

    # Step 5.1: Identify lexemes referenced by senses
    referenced_lexemes = set()
    for sense in senses.values():
        signifier = sense.get('signifier')
        if signifier:
            referenced_lexemes.add(signifier)

    # Step 5.2: Delete unreferenced lexemes
    lexemes_to_delete = []
    for lexeme_id in lexemes.keys():
        if lexeme_id not in referenced_lexemes:
            lexemes_to_delete.append(lexeme_id)
            stats.lexemes_removed_orphaned += 1

    for lexeme_id in lexemes_to_delete:
        del lexemes[lexeme_id]

    print(f"  Removed {stats.lexemes_removed_orphaned} orphaned lexemes")

    # PHASE 7: Sanity check concept relations for POS violations
    print("\nPhase 7: Checking concept relations for POS violations...")

    # Define hypernym/meronym relation types that should not cross POS boundaries
    pos_restricted_relations = {
        'class_hypernym', 'class_hyponym',
        'instance_hypernym', 'instance_hyponym',
        'member_meronym', 'member_holonym',
        'part_meronym', 'part_holonym',
        'substance_meronym', 'substance_holonym'
    }

    for rel_key, relation in concept_relations.items():
        rel_type, source, target = rel_key

        if rel_type in pos_restricted_relations:
            source_pos = get_concept_category(source, concepts)
            target_pos = get_concept_category(target, concepts)

            if source_pos != target_pos:
                print(f'Warning: {rel_type} ontological mismatch between {source} ({source_pos}) and {target} ({target_pos})')
                stats.concept_relations_pos_violations += 1

    if not stats.concept_relations_pos_violations:
        print(f"  ✓ No POS violations found")

    print("\nBuilding merged XML...")

    # Build the merged XML
    root = etree.Element('CygnetResource')
    root.set('id', 'cyg')
    root.set('label', 'Cygnet')
    root.set('version', '1.0')

    # Add ConceptLayer
    if concepts:
        concept_layer = etree.SubElement(root, 'ConceptLayer')
        for concept in concepts.values():
            concept_layer.append(concept)

    # Add LexemeLayer
    if lexemes:
        lexeme_layer = etree.SubElement(root, 'LexemeLayer')
        for lexeme in lexemes.values():
            lexeme_layer.append(lexeme)

    # Add SenseLayer
    if senses:
        sense_layer = etree.SubElement(root, 'SenseLayer')
        for sense in senses.values():
            sense_layer.append(sense)

    # Add GlossLayer
    if glosses:
        gloss_layer = etree.SubElement(root, 'GlossLayer')
        for gloss in glosses.values():
            gloss_layer.append(gloss)

    # Add ExampleLayer
    if examples:
        example_layer = etree.SubElement(root, 'ExampleLayer')
        for example in examples:
            example_layer.append(example)

    # Add SenseRelationLayer
    if sense_relations:
        sense_relation_layer = etree.SubElement(root, 'SenseRelationLayer')
        for relation in sense_relations.values():
            sense_relation_layer.append(relation)

    # Add ConceptRelationLayer
    if concept_relations:
        concept_relation_layer = etree.SubElement(root, 'ConceptRelationLayer')
        for relation in concept_relations.values():
            concept_relation_layer.append(relation)

    # Add ResourcesLayer with metadata from each source file
    if resources:
        resources_layer = etree.SubElement(root, 'ResourcesLayer')
        for res in resources.values():
            etree.SubElement(resources_layer, 'Resource', **res)

    # Write output XML
    tree = etree.ElementTree(root)
    tree.write(output_file, pretty_print=True, xml_declaration=True, encoding='UTF-8')
    print(f"\nMerged XML written to: {output_file}")

    # Write log file
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, 'w') as f:
        json.dump(stats.to_dict(), f, indent=2)
    print(f"Log written to: {log_file}")

    # Print summary
    print("\n=== Merge Summary ===")
    print(f"Files processed: {stats.files_processed}")
    print(f"Concepts: {stats.concepts_total}")
    print(f"Lexemes: {stats.lexemes_total} (merged: {stats.lexemes_merged})")
    print(f"Senses: {stats.senses_total} (deduplicated: {stats.senses_deduplicated})")
    print(f"Glosses: {stats.glosses_total}")
    print(f"Examples: {stats.examples_total}")
    print(f"Sense Relations: {stats.sense_relations_total} (deduplicated: {stats.sense_relations_deduplicated})")
    print(f"Concept Relations: {stats.concept_relations_total} (deduplicated: {stats.concept_relations_deduplicated})")

    print(f"\n=== Cleanup Summary ===")
    print(f"Concepts removed (no glosses): {stats.concepts_removed_no_gloss}")
    print(f"Senses removed (concept deleted): {stats.senses_removed_no_concept}")
    print(f"Concept relations removed (cascade): {stats.concept_relations_removed_cascade}")
    print(f"Sense relations removed (cascade): {stats.sense_relations_removed_cascade}")
    print(f"Annotated tokens removed from glosses: {stats.annotated_tokens_removed_glosses}")
    print(f"Annotated tokens removed from examples: {stats.annotated_tokens_removed_examples}")
    print(f"Examples removed (no annotations): {stats.examples_removed_no_annotations}")
    print(f"Lexemes removed (orphaned): {stats.lexemes_removed_orphaned}")
    print(f"Glosses discarded (duplicates): {sum(sum(langs.values()) for langs in stats.glosses_discarded.values())}")
    print(f"Concepts missing English glosses (but have other glosses): {sum(stats.concepts_missing_english.values())}")
    print(f"Lexemes (case-merged): {stats.lexemes_case_merged}")
    print(f"Senses (case-deduplicated): {stats.senses_case_deduplicated}")
    print(f"Sense relations updated (case merge): {stats.sense_relations_updated_case_merge}")
    print(f"Annotated tokens updated in glosses (case merge): {stats.annotated_tokens_updated_glosses_case_merge}")
    print(f"Annotated tokens updated in examples (case merge): {stats.annotated_tokens_updated_examples_case_merge}")
    print(f"Concept relations ontology violations: {stats.concept_relations_pos_violations}")

if __name__ == '__main__':
    input_dir = 'bin/cygnets_presynth/'
    output_file = 'cygnet.xml'
    log_file = 'cygnet_log.json'

    merge_cygnet_files(input_dir, output_file, log_file)