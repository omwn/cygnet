#!/usr/bin/env python3
"""
Convert Cygnet XML lexical resource to SQLite database for web interface.

Schema is based on the Python `wn` module's SQLite schema, with modifications
for Cygnet's merged multilingual structure. Text IDs are omitted from the
web-serving DB to save space (they can be reconstructed from the XML).

Uses iterparse for streaming XML processing to avoid loading the entire
~800 MB XML file into memory.
"""

import json
import sqlite3
import subprocess
import unicodedata
from lxml import etree as ET
from pathlib import Path

SCHEMA = """
-- Lookup table for relation type strings (same as wn module)
CREATE TABLE relation_types (
    rowid INTEGER PRIMARY KEY,
    type TEXT NOT NULL UNIQUE
);

-- Lookup table for language codes
CREATE TABLE languages (
    rowid INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE
);

-- Synsets (≈ wn synsets, = Cygnet Concepts)
-- No text id column (saves ~90 MB with autoindex); use ili for display
CREATE TABLE synsets (
    rowid INTEGER PRIMARY KEY,
    ili TEXT,
    pos TEXT NOT NULL
);

-- Entries (≈ wn entries, = Cygnet Lexemes)
-- No text id column (saves ~80 MB with autoindex)
-- language_rowid FK replaces language TEXT (~20 MB saved)
CREATE TABLE entries (
    rowid INTEGER PRIMARY KEY,
    language_rowid INTEGER NOT NULL REFERENCES languages(rowid),
    pos TEXT NOT NULL
);

-- Forms (≈ wn forms, = Cygnet Wordforms)
-- No UNIQUE constraint (saves ~35 MB autoindex; dupes filtered at insert)
CREATE TABLE forms (
    rowid INTEGER PRIMARY KEY,
    entry_rowid INTEGER NOT NULL REFERENCES entries(rowid),
    form TEXT NOT NULL,
    normalized_form TEXT,
    rank INTEGER DEFAULT 1
);

-- Senses (≈ wn senses)
-- No text id column (saves ~149 MB with autoindex)
CREATE TABLE senses (
    rowid INTEGER PRIMARY KEY,
    entry_rowid INTEGER NOT NULL REFERENCES entries(rowid),
    synset_rowid INTEGER NOT NULL REFERENCES synsets(rowid),
    sense_index INTEGER DEFAULT 1
);

-- Definitions (≈ wn definitions, = Cygnet Glosses)
-- language_rowid FK replaces language TEXT
CREATE TABLE definitions (
    rowid INTEGER PRIMARY KEY,
    synset_rowid INTEGER NOT NULL REFERENCES synsets(rowid),
    definition TEXT,
    language_rowid INTEGER REFERENCES languages(rowid)
);

-- Synset relations (≈ wn synset_relations, = Cygnet ConceptRelations)
-- Stores both forward and inverse relations explicitly
CREATE TABLE synset_relations (
    rowid INTEGER PRIMARY KEY,
    source_rowid INTEGER NOT NULL REFERENCES synsets(rowid),
    target_rowid INTEGER NOT NULL REFERENCES synsets(rowid),
    type_rowid INTEGER NOT NULL REFERENCES relation_types(rowid)
);

-- Sense relations (≈ wn sense_relations)
-- Stores both forward and inverse relations explicitly
CREATE TABLE sense_relations (
    rowid INTEGER PRIMARY KEY,
    source_rowid INTEGER NOT NULL REFERENCES senses(rowid),
    target_rowid INTEGER NOT NULL REFERENCES senses(rowid),
    type_rowid INTEGER NOT NULL REFERENCES relation_types(rowid)
);

-- Examples stored once each (differs from wn: normalized into separate table)
CREATE TABLE examples (
    rowid INTEGER PRIMARY KEY,
    example TEXT NOT NULL
);

-- Junction: which senses are exemplified by which examples
CREATE TABLE sense_examples (
    rowid INTEGER PRIMARY KEY,
    sense_rowid INTEGER NOT NULL REFERENCES senses(rowid),
    example_rowid INTEGER NOT NULL REFERENCES examples(rowid)
);

-- Annotations in definitions (AnnotatedTokens in Cygnet glosses)
CREATE TABLE definition_annotations (
    rowid INTEGER PRIMARY KEY,
    definition_rowid INTEGER NOT NULL REFERENCES definitions(rowid),
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    sense_rowid INTEGER NOT NULL REFERENCES senses(rowid)
);

-- Annotations in examples (AnnotatedTokens in Cygnet examples)
CREATE TABLE example_annotations (
    rowid INTEGER PRIMARY KEY,
    example_rowid INTEGER NOT NULL REFERENCES examples(rowid),
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    sense_rowid INTEGER NOT NULL REFERENCES senses(rowid)
);

-- Source wordnet resource metadata
CREATE TABLE resources (
    rowid INTEGER PRIMARY KEY,
    code TEXT NOT NULL,
    version TEXT,
    label TEXT,
    language_rowid INTEGER REFERENCES languages(rowid),
    url TEXT,
    citation TEXT,
    licence TEXT,
    email TEXT,
    status TEXT,
    confidence_score REAL,
    extra TEXT
);
"""

# Resources referenced in provenance but absent from the XML ResourcesLayer.
# pwn:3.0 is hardcoded in 1_extract_cili.py as the origin of all CILI entries.
IMPLICIT_RESOURCES = {
    'pwn': {
        'version': '3.0',
        'label': 'Princeton WordNet',
        'language': 'en',
        'url': 'https://wordnet.princeton.edu/',
        'licence': 'wordnet',
        'citation': (
            'Christiane Fellbaum (Ed.) 1998 '
            'WordNet: An electronic lexical database. MIT Press.'
        ),
    },
}

# Only indexes actually used by the web interface
INDEXES = """
CREATE INDEX idx_forms_normalized ON forms(normalized_form);
CREATE INDEX idx_forms_entry ON forms(entry_rowid);
CREATE INDEX idx_senses_entry ON senses(entry_rowid);
CREATE INDEX idx_senses_synset ON senses(synset_rowid);
CREATE INDEX idx_definitions_synset ON definitions(synset_rowid);
CREATE INDEX idx_synset_relations_source ON synset_relations(source_rowid);
CREATE INDEX idx_sense_relations_source ON sense_relations(source_rowid);
CREATE INDEX idx_sense_examples_sense ON sense_examples(sense_rowid);
CREATE INDEX idx_example_annotations_example ON example_annotations(example_rowid);
"""

PROV_SCHEMA = """
CREATE TABLE prov_resources (
    rowid INTEGER PRIMARY KEY,
    code  TEXT NOT NULL UNIQUE
);
CREATE TABLE prov_tables (
    rowid INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE
);
CREATE TABLE provenance (
    rowid          INTEGER PRIMARY KEY,
    table_rowid    INTEGER NOT NULL REFERENCES prov_tables(rowid),
    item_rowid     INTEGER NOT NULL,
    resource_rowid INTEGER NOT NULL REFERENCES prov_resources(rowid),
    version        TEXT,
    original_id    TEXT NOT NULL
);
CREATE INDEX idx_provenance_lookup ON provenance(table_rowid, item_rowid);
"""

# Inverse relation maps (from relations.xml)
INVERSE_SENSE_RELATIONS = {
    'antonym': 'antonym',
    'derivation': 'derivation_of',
    'pertainym': 'pertainym_of',
    'participle': 'participle_of',
}

INVERSE_CONCEPT_RELATIONS = {
    'class_hypernym': 'class_hyponym',
    'instance_hypernym': 'instance_hyponym',
    'member_meronym': 'member_holonym',
    'part_meronym': 'part_holonym',
    'substance_meronym': 'substance_holonym',
    'opposite': 'opposite',
    'causes': 'caused_by',
    'entails': 'entailed_by',
    'agent_of_action': 'action_of_agent',
    'patient_of_action': 'action_of_patient',
    'result_of_action': 'action_of_result',
    'instrument_of_agent': 'agent_of_instrument',
    'instrument_of_action': 'action_of_instrument',
    'result_of_agent': 'agent_of_result',
    'patient_of_agent': 'agent_of_patient',
    'instrument_of_patient': 'patient_of_instrument',
    'instrument_of_result': 'result_of_instrument',
    'patient_of_result': 'result_of_patient',
}


def remove_accents(text):
    """Remove accents/diacritics from text for search normalization."""
    nfd = unicodedata.normalize('NFD', text)
    return ''.join(c for c in nfd if unicodedata.category(c) != 'Mn')


def parse_annotated_sentence(sentence_elem):
    """Parse an AnnotatedSentence element, returning plain text and annotations."""
    parts = []
    annotations = []
    offset = 0

    if sentence_elem.text:
        parts.append(sentence_elem.text)
        offset += len(sentence_elem.text)

    for child in sentence_elem:
        if child.tag == 'AnnotatedToken':
            token_text = child.text or ''
            sense_id = child.get('sense')
            if sense_id:
                annotations.append({
                    'sense': sense_id,
                    'start': offset,
                    'end': offset + len(token_text),
                })
            parts.append(token_text)
            offset += len(token_text)

        if child.tail:
            parts.append(child.tail)
            offset += len(child.tail)

    return ''.join(parts), annotations


def main():
    xml_path = 'cygnet.xml'
    db_path = Path('web/cygnet.db')
    db_path.parent.mkdir(exist_ok=True)

    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # Optimize for bulk loading
    cur.execute('PRAGMA journal_mode=OFF')
    cur.execute('PRAGMA synchronous=OFF')
    cur.execute('PRAGMA cache_size=-200000')  # 200MB cache

    cur.executescript(SCHEMA)

    prov_db_path = Path('web/provenance.db')
    if prov_db_path.exists():
        prov_db_path.unlink()
    prov_conn = sqlite3.connect(str(prov_db_path))
    prov_cur = prov_conn.cursor()
    prov_cur.execute('PRAGMA journal_mode=OFF')
    prov_cur.execute('PRAGMA synchronous=OFF')
    prov_cur.execute('PRAGMA cache_size=-100000')
    prov_cur.executescript(PROV_SCHEMA)

    # --- Lookup caches ---
    lang_code_to_rowid = {}
    rel_type_cache = {}

    def get_lang_rowid(code):
        if code not in lang_code_to_rowid:
            cur.execute('INSERT INTO languages (code) VALUES (?)', (code,))
            lang_code_to_rowid[code] = cur.lastrowid
        return lang_code_to_rowid[code]

    def get_rel_type_rowid(rel_type):
        if rel_type not in rel_type_cache:
            cur.execute('INSERT OR IGNORE INTO relation_types (type) VALUES (?)', (rel_type,))
            cur.execute('SELECT rowid FROM relation_types WHERE type = ?', (rel_type,))
            rel_type_cache[rel_type] = cur.fetchone()[0]
        return rel_type_cache[rel_type]

    prov_resource_cache = {}
    prov_table_cache = {}

    def get_prov_resource_rowid(code):
        if code not in prov_resource_cache:
            prov_cur.execute('INSERT OR IGNORE INTO prov_resources (code) VALUES (?)', (code,))
            prov_cur.execute('SELECT rowid FROM prov_resources WHERE code = ?', (code,))
            prov_resource_cache[code] = prov_cur.fetchone()[0]
        return prov_resource_cache[code]

    def get_prov_table_rowid(name):
        if name not in prov_table_cache:
            prov_cur.execute('INSERT OR IGNORE INTO prov_tables (name) VALUES (?)', (name,))
            prov_cur.execute('SELECT rowid FROM prov_tables WHERE name = ?', (name,))
            prov_table_cache[name] = prov_cur.fetchone()[0]
        return prov_table_cache[name]

    def insert_provenance(table_name, item_rowid, prov_elems):
        """Insert Provenance child elements into provenance.db; return row count."""
        table_rowid = get_prov_table_rowid(table_name)
        count = 0
        for prov in prov_elems:
            resource = prov.get('resource')
            original_id = prov.get('original_id')
            if resource and original_id:
                resource_rowid = get_prov_resource_rowid(resource)
                prov_cur.execute(
                    'INSERT INTO provenance '
                    '(table_rowid, item_rowid, resource_rowid, version, original_id) '
                    'VALUES (?, ?, ?, ?, ?)',
                    (table_rowid, item_rowid, resource_rowid,
                     prov.get('version'), original_id)
                )
                count += 1
        return count

    # --- ID maps (built during streaming, used for cross-references) ---
    synset_id_to_rowid = {}
    entry_id_to_rowid = {}
    sense_id_to_rowid = {}

    # --- Counters ---
    n_synsets = n_entries = n_forms = n_senses = 0
    n_defs = n_def_anns = 0
    n_examples = n_ex_links = n_ex_anns = 0
    n_sense_rels = n_synset_rels = 0
    n_prov = 0

    # Batch buffers for forms (to deduplicate)
    current_entry_rowid = None
    current_entry_forms = set()

    print(f"Streaming {xml_path}...")

    # The XML has a predictable order:
    #   ConceptLayer > Concept
    #   LexemeLayer > Lexeme > Wordform
    #   SenseLayer > Sense
    #   GlossLayer > Gloss > AnnotatedSentence
    #   ExampleLayer > Example > AnnotatedSentence
    #   SenseRelationLayer > SenseRelation
    #   ConceptRelationLayer > ConceptRelation

    context = ET.iterparse(xml_path, events=('end',),
                           tag=('Concept', 'Lexeme', 'Sense',
                                'Gloss', 'Example',
                                'SenseRelation', 'ConceptRelation', 'Resource'))

    for event, elem in context:
        tag = elem.tag

        if tag == 'Concept':
            cid = elem.get('id')
            ili = cid.replace('cili.', '') if cid.startswith('cili.') else None
            pos = elem.get('ontological_category')
            cur.execute('INSERT INTO synsets (ili, pos) VALUES (?, ?)', (ili, pos))
            synset_rowid = cur.lastrowid
            synset_id_to_rowid[cid] = synset_rowid
            n_prov += insert_provenance('synsets', synset_rowid, elem.findall('Provenance'))
            n_synsets += 1
            elem.clear()

        elif tag == 'Lexeme':
            lid = elem.get('id')
            lang = elem.get('language')
            pos = elem.get('grammatical_category')
            lang_rowid = get_lang_rowid(lang)
            cur.execute('INSERT INTO entries (language_rowid, pos) VALUES (?, ?)',
                        (lang_rowid, pos))
            entry_rowid = cur.lastrowid
            entry_id_to_rowid[lid] = entry_rowid
            n_prov += insert_provenance('entries', entry_rowid, elem.findall('Provenance'))
            n_entries += 1

            # Insert forms, deduplicating per entry
            seen = set()
            for rank, wf in enumerate(elem.findall('Wordform')):
                form = wf.get('form')
                if form not in seen:
                    seen.add(form)
                    normalized = remove_accents(form.lower())
                    cur.execute(
                        'INSERT INTO forms (entry_rowid, form, normalized_form, rank) '
                        'VALUES (?, ?, ?, ?)',
                        (entry_rowid, form, normalized, rank)
                    )
                    n_forms += 1

            elem.clear()

        elif tag == 'Sense':
            sid = elem.get('id')
            entry_rowid = entry_id_to_rowid.get(elem.get('signifier'))
            synset_rowid = synset_id_to_rowid.get(elem.get('signified'))
            if entry_rowid and synset_rowid:
                cur.execute(
                    'INSERT INTO senses (entry_rowid, synset_rowid) VALUES (?, ?)',
                    (entry_rowid, synset_rowid)
                )
                sense_rowid = cur.lastrowid
                sense_id_to_rowid[sid] = sense_rowid
                n_prov += insert_provenance('senses', sense_rowid, elem.findall('Provenance'))
                n_senses += 1
            elem.clear()

        elif tag == 'Gloss':
            synset_rowid = synset_id_to_rowid.get(elem.get('definiendum'))
            if synset_rowid:
                language = elem.get('language')
                lang_rowid = get_lang_rowid(language)
                sentence = elem.find('AnnotatedSentence')
                text, annotations = parse_annotated_sentence(sentence)
                cur.execute(
                    'INSERT INTO definitions (synset_rowid, definition, language_rowid) '
                    'VALUES (?, ?, ?)',
                    (synset_rowid, text, lang_rowid)
                )
                def_rowid = cur.lastrowid
                n_prov += insert_provenance('definitions', def_rowid, elem.findall('Provenance'))
                n_defs += 1

                for ann in annotations:
                    ann_sense_rowid = sense_id_to_rowid.get(ann['sense'])
                    if ann_sense_rowid:
                        cur.execute(
                            'INSERT INTO definition_annotations '
                            '(definition_rowid, start_offset, end_offset, sense_rowid) '
                            'VALUES (?, ?, ?, ?)',
                            (def_rowid, ann['start'], ann['end'], ann_sense_rowid)
                        )
                        n_def_anns += 1
            elem.clear()

        elif tag == 'Example':
            sentence = elem.find('AnnotatedSentence')
            text, annotations = parse_annotated_sentence(sentence)

            referenced_senses = set()
            for ann in annotations:
                ann_sense_rowid = sense_id_to_rowid.get(ann['sense'])
                if ann_sense_rowid:
                    referenced_senses.add(ann_sense_rowid)

            if referenced_senses:
                cur.execute('INSERT INTO examples (example) VALUES (?)', (text,))
                example_rowid = cur.lastrowid
                n_prov += insert_provenance('examples', example_rowid, elem.findall('Provenance'))
                n_examples += 1

                for ann_sense_rowid in referenced_senses:
                    cur.execute(
                        'INSERT INTO sense_examples (sense_rowid, example_rowid) '
                        'VALUES (?, ?)',
                        (ann_sense_rowid, example_rowid)
                    )
                    n_ex_links += 1

                for ann in annotations:
                    ann_sense_rowid = sense_id_to_rowid.get(ann['sense'])
                    if ann_sense_rowid:
                        cur.execute(
                            'INSERT INTO example_annotations '
                            '(example_rowid, start_offset, end_offset, sense_rowid) '
                            'VALUES (?, ?, ?, ?)',
                            (example_rowid, ann['start'], ann['end'], ann_sense_rowid)
                        )
                        n_ex_anns += 1
            elem.clear()

        elif tag == 'SenseRelation':
            rel_type = elem.get('relation_type')
            source = sense_id_to_rowid.get(elem.get('source'))
            target = sense_id_to_rowid.get(elem.get('target'))
            if source and target:
                type_rowid = get_rel_type_rowid(rel_type)
                cur.execute(
                    'INSERT INTO sense_relations (source_rowid, target_rowid, type_rowid) '
                    'VALUES (?, ?, ?)',
                    (source, target, type_rowid)
                )
                forward_rowid = cur.lastrowid
                n_prov += insert_provenance('sense_relations', forward_rowid,
                                            elem.findall('Provenance'))
                n_sense_rels += 1

                inverse = INVERSE_SENSE_RELATIONS.get(rel_type)
                if inverse:
                    inv_rowid = get_rel_type_rowid(inverse)
                    cur.execute(
                        'INSERT INTO sense_relations (source_rowid, target_rowid, type_rowid) '
                        'VALUES (?, ?, ?)',
                        (target, source, inv_rowid)
                    )
                    n_sense_rels += 1
            elem.clear()

        elif tag == 'ConceptRelation':
            rel_type = elem.get('relation_type')
            source = synset_id_to_rowid.get(elem.get('source'))
            target = synset_id_to_rowid.get(elem.get('target'))
            if source and target:
                type_rowid = get_rel_type_rowid(rel_type)
                cur.execute(
                    'INSERT INTO synset_relations (source_rowid, target_rowid, type_rowid) '
                    'VALUES (?, ?, ?)',
                    (source, target, type_rowid)
                )
                forward_rowid = cur.lastrowid
                n_prov += insert_provenance('synset_relations', forward_rowid,
                                            elem.findall('Provenance'))
                n_synset_rels += 1

                inverse = INVERSE_CONCEPT_RELATIONS.get(rel_type)
                if inverse:
                    inv_rowid = get_rel_type_rowid(inverse)
                    cur.execute(
                        'INSERT INTO synset_relations (source_rowid, target_rowid, type_rowid) '
                        'VALUES (?, ?, ?)',
                        (target, source, inv_rowid)
                    )
                    n_synset_rels += 1
            elem.clear()

        elif tag == 'Resource':
            known = {'id', 'version', 'label', 'language', 'url', 'citation',
                     'license', 'email', 'status', 'confidenceScore'}
            attrib = dict(elem.attrib)
            extra_attrs = {k: v for k, v in attrib.items() if k not in known}
            lang_code = attrib.get('language')
            lang_rowid = get_lang_rowid(lang_code) if lang_code else None
            score_raw = attrib.get('confidenceScore')
            score = float(score_raw) if score_raw else None
            cur.execute(
                'INSERT INTO resources '
                '(code, version, label, language_rowid, url, citation, licence, '
                'email, status, confidence_score, extra) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    attrib.get('id'), attrib.get('version'), attrib.get('label'),
                    lang_rowid, attrib.get('url'), attrib.get('citation'),
                    attrib.get('license'), attrib.get('email'), attrib.get('status'),
                    score,
                    json.dumps(extra_attrs) if extra_attrs else None,
                )
            )
            elem.clear()

        # Free preceding siblings to keep memory bounded
        while elem.getprevious() is not None:
            del elem.getparent()[0]

    print(f"  Synsets: {n_synsets:,}")
    print(f"  Entries: {n_entries:,}, Forms: {n_forms:,}")
    print(f"  Languages: {len(lang_code_to_rowid)}")
    print(f"  Senses: {n_senses:,}")
    print(f"  Definitions: {n_defs:,}, definition annotations: {n_def_anns:,}")
    print(f"  Examples: {n_examples:,}, links: {n_ex_links:,}, annotations: {n_ex_anns:,}")
    print(f"  Sense relations: {n_sense_rels:,}")
    print(f"  Synset relations: {n_synset_rels:,}")
    print(f"  Provenance rows: {n_prov:,}")

    # Compute sense_index: position among senses with same (language, lemma_lower, concept_pos)
    print("\nComputing sense indices...")
    cur.execute('''
        UPDATE senses SET sense_index = sub.idx FROM (
            SELECT s.rowid as sid,
                   ROW_NUMBER() OVER (
                       PARTITION BY e.language_rowid, LOWER(f.form), syn.pos
                       ORDER BY s.rowid
                   ) as idx
            FROM senses s
            JOIN entries e ON s.entry_rowid = e.rowid
            JOIN forms f ON f.entry_rowid = e.rowid AND f.rank = 0
            JOIN synsets syn ON s.synset_rowid = syn.rowid
        ) sub WHERE senses.rowid = sub.sid
    ''')
    print(f"  Computed indices for {cur.rowcount:,} senses")

    # --- Create indexes ---
    print("\nCreating indexes...")
    cur.executescript(INDEXES)

    # --- Analyze for query planner ---
    print("Running ANALYZE...")
    cur.execute('ANALYZE')

    conn.commit()
    prov_conn.commit()

    # Back-fill any prov_resources codes that have no resources row (e.g. pwn from CILI)
    prov_cur.execute('SELECT code FROM prov_resources')
    prov_codes = {row[0] for row in prov_cur.fetchall()}
    cur.execute('SELECT code FROM resources')
    existing_codes = {row[0] for row in cur.fetchall()}
    for code in sorted(prov_codes - existing_codes):
        meta = IMPLICIT_RESOURCES.get(code, {})
        lang_code = meta.get('language')
        lang_rowid = get_lang_rowid(lang_code) if lang_code else None
        cur.execute(
            'INSERT INTO resources '
            '(code, version, label, language_rowid, url, licence, citation) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (code, meta.get('version'), meta.get('label'),
             lang_rowid, meta.get('url'), meta.get('licence'),
             meta.get('citation'))
        )
        print(f"  Added implicit resource: {code}")
    conn.commit()

    # --- Statistics ---
    tables = [
        'languages', 'synsets', 'entries', 'forms', 'senses', 'definitions',
        'synset_relations', 'sense_relations', 'examples', 'sense_examples',
        'definition_annotations', 'example_annotations', 'relation_types'
    ]
    print("\nTable row counts:")
    for table in tables:
        cur.execute(f'SELECT COUNT(*) FROM {table}')
        count = cur.fetchone()[0]
        print(f"  {table}: {count:,}")

    # Database file size
    conn.close()
    size_mb = db_path.stat().st_size / (1024 * 1024)
    print(f"\n✓ Database written to {db_path} ({size_mb:.1f} MB)")

    # Gzip main DB for web delivery
    gz_path = str(db_path) + '.gz'
    subprocess.run(['gzip', '-k', '-9', '-f', str(db_path)], check=True)
    gz_mb = Path(gz_path).stat().st_size / (1024 * 1024)
    print(f"✓ Compressed to {gz_path} ({gz_mb:.1f} MB)")

    # Gzip provenance DB for web delivery
    prov_size_mb = prov_db_path.stat().st_size / (1024 * 1024)
    print(f"\n✓ Provenance database written to {prov_db_path} ({prov_size_mb:.1f} MB)")
    subprocess.run(['gzip', '-k', '-9', '-f', str(prov_db_path)], check=True)
    prov_gz_path = str(prov_db_path) + '.gz'
    prov_gz_mb = Path(prov_gz_path).stat().st_size / (1024 * 1024)
    print(f"✓ Compressed to {prov_gz_path} ({prov_gz_mb:.1f} MB)")
    prov_conn.close()


if __name__ == '__main__':
    main()
