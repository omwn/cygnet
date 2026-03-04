#!/usr/bin/env python3
"""
Stream all pre-synthesised Cygnet XML files directly into SQLite.

Each source file is parsed, merged into the databases, and freed before
the next is loaded — so peak memory is one file at a time plus the
ID-mapping dictionaries (~300 MB), not the entire merged dataset.

Replaces the old two-step pipeline (old 6_synthesise → 10_sqlite_conversion).
"""

import json
import re
import sqlite3
import subprocess
import sys
import unicodedata
from pathlib import Path

import langcodes
from lxml import etree as ET

_COMBINING_RE = re.compile(r'[\u0300-\u036f\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f]')

# ---------------------------------------------------------------------------
# Schema constants (identical to the old 10_sqlite_conversion.py)
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE relation_types (
    rowid INTEGER PRIMARY KEY,
    type  TEXT NOT NULL UNIQUE
);
CREATE TABLE languages (
    rowid INTEGER PRIMARY KEY,
    code  TEXT NOT NULL UNIQUE,
    name  TEXT
);
CREATE TABLE synsets (
    rowid INTEGER PRIMARY KEY,
    ili   TEXT,
    pos   TEXT NOT NULL
);
CREATE TABLE entries (
    rowid          INTEGER PRIMARY KEY,
    language_rowid INTEGER NOT NULL REFERENCES languages(rowid),
    pos            TEXT NOT NULL
);
CREATE TABLE forms (
    rowid           INTEGER PRIMARY KEY,
    entry_rowid     INTEGER NOT NULL REFERENCES entries(rowid),
    form            TEXT NOT NULL,
    normalized_form TEXT,
    rank            INTEGER DEFAULT 1
);
CREATE TABLE pronunciations (
    rowid         INTEGER PRIMARY KEY,
    form_rowid    INTEGER NOT NULL REFERENCES forms(rowid),
    variety       TEXT,
    pronunciation TEXT NOT NULL
);
CREATE TABLE senses (
    rowid        INTEGER PRIMARY KEY,
    entry_rowid  INTEGER NOT NULL REFERENCES entries(rowid),
    synset_rowid INTEGER NOT NULL REFERENCES synsets(rowid),
    sense_index  INTEGER DEFAULT 1
);
CREATE TABLE definitions (
    rowid          INTEGER PRIMARY KEY,
    synset_rowid   INTEGER NOT NULL REFERENCES synsets(rowid),
    definition     TEXT,
    language_rowid INTEGER REFERENCES languages(rowid)
);
CREATE TABLE synset_relations (
    rowid        INTEGER PRIMARY KEY,
    source_rowid INTEGER NOT NULL REFERENCES synsets(rowid),
    target_rowid INTEGER NOT NULL REFERENCES synsets(rowid),
    type_rowid   INTEGER NOT NULL REFERENCES relation_types(rowid)
);
CREATE TABLE sense_relations (
    rowid        INTEGER PRIMARY KEY,
    source_rowid INTEGER NOT NULL REFERENCES senses(rowid),
    target_rowid INTEGER NOT NULL REFERENCES senses(rowid),
    type_rowid   INTEGER NOT NULL REFERENCES relation_types(rowid)
);
CREATE TABLE examples (
    rowid   INTEGER PRIMARY KEY,
    example TEXT NOT NULL
);
CREATE TABLE sense_examples (
    rowid         INTEGER PRIMARY KEY,
    sense_rowid   INTEGER NOT NULL REFERENCES senses(rowid),
    example_rowid INTEGER NOT NULL REFERENCES examples(rowid)
);
CREATE TABLE definition_annotations (
    rowid            INTEGER PRIMARY KEY,
    definition_rowid INTEGER NOT NULL REFERENCES definitions(rowid),
    start_offset     INTEGER NOT NULL,
    end_offset       INTEGER NOT NULL,
    sense_rowid      INTEGER NOT NULL REFERENCES senses(rowid)
);
CREATE TABLE example_annotations (
    rowid         INTEGER PRIMARY KEY,
    example_rowid INTEGER NOT NULL REFERENCES examples(rowid),
    start_offset  INTEGER NOT NULL,
    end_offset    INTEGER NOT NULL,
    sense_rowid   INTEGER NOT NULL REFERENCES senses(rowid)
);
CREATE TABLE resources (
    rowid            INTEGER PRIMARY KEY,
    code             TEXT NOT NULL,
    version          TEXT,
    label            TEXT,
    language_rowid   INTEGER REFERENCES languages(rowid),
    url              TEXT,
    citation         TEXT,
    licence          TEXT,
    email            TEXT,
    status           TEXT,
    confidence_score REAL,
    extra            TEXT
);
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

INDEXES = """
CREATE INDEX idx_forms_normalized ON forms(normalized_form);
CREATE INDEX idx_senses_entry     ON senses(entry_rowid);
CREATE INDEX idx_senses_synset    ON senses(synset_rowid);
CREATE INDEX idx_definitions_synset        ON definitions(synset_rowid);
CREATE INDEX idx_synset_relations_source   ON synset_relations(source_rowid);
CREATE INDEX idx_sense_relations_source    ON sense_relations(source_rowid);
CREATE INDEX idx_sense_examples_sense      ON sense_examples(sense_rowid);
CREATE INDEX idx_example_annotations_example ON example_annotations(example_rowid);
"""

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

INVERSE_SENSE_RELATIONS: dict[str, str] = {
    'antonym':    'antonym',
    'derivation': 'derivation_of',
    'pertainym':  'pertainym_of',
    'participle': 'participle_of',
}

INVERSE_CONCEPT_RELATIONS: dict[str, str] = {
    'class_hypernym':       'class_hyponym',
    'instance_hypernym':    'instance_hyponym',
    'member_meronym':       'member_holonym',
    'part_meronym':         'part_holonym',
    'substance_meronym':    'substance_holonym',
    'opposite':             'opposite',
    'causes':               'caused_by',
    'entails':              'entailed_by',
    'agent_of_action':      'action_of_agent',
    'patient_of_action':    'action_of_patient',
    'result_of_action':     'action_of_result',
    'instrument_of_agent':  'agent_of_instrument',
    'instrument_of_action': 'action_of_instrument',
    'result_of_agent':      'agent_of_result',
    'patient_of_agent':     'agent_of_patient',
    'instrument_of_patient':'patient_of_instrument',
    'instrument_of_result': 'result_of_instrument',
    'patient_of_result':    'result_of_patient',
}

BATCH_SIZE = 50_000

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def remove_accents(text: str) -> str:
    """Strip combining diacritics for accent-insensitive search."""
    if text.isascii():
        return text
    return _COMBINING_RE.sub('', unicodedata.normalize('NFD', text))


def parse_annotated_sentence(elem) -> tuple[str, list[dict]]:
    """Return (plain_text, annotations) from an AnnotatedSentence element."""
    if not len(elem):
        return (elem.text or ''), []

    parts: list[str] = []
    annotations: list[dict] = []
    offset = 0

    if elem.text:
        parts.append(elem.text)
        offset += len(elem.text)

    for child in elem:
        token = child.text or ''
        sense_id = child.get('sense')
        if sense_id:
            end = offset + len(token)
            annotations.append({'sense': sense_id, 'start': offset, 'end': end})
            offset = end
        else:
            offset += len(token)
        parts.append(token)
        if child.tail:
            parts.append(child.tail)
            offset += len(child.tail)

    return ''.join(parts), annotations


# ---------------------------------------------------------------------------
# Main builder class
# ---------------------------------------------------------------------------

class MergeBuilder:
    """
    Accumulates multiple pre-synth Cygnet XML files into a single SQLite DB.
    Each file is parsed, inserted, and freed before the next is loaded.
    """

    def __init__(self, db_path: Path, prov_db_path: Path) -> None:
        for p in (db_path, prov_db_path):
            if p.exists():
                p.unlink()

        self.conn = sqlite3.connect(str(db_path))
        self.cur = self.conn.cursor()
        for pragma in ('PRAGMA journal_mode=OFF',
                       'PRAGMA synchronous=OFF',
                       'PRAGMA cache_size=-200000'):
            self.cur.execute(pragma)
        self.cur.executescript(SCHEMA)
        # Create this index immediately (empty table = zero cost) so that
        # the duplicate-lexeme lookup 'SELECT form FROM forms WHERE entry_rowid=?'
        # is O(log n) rather than O(n) for all subsequent duplicate checks.
        self.cur.execute('CREATE INDEX idx_forms_entry ON forms(entry_rowid)')

        self.prov_conn = sqlite3.connect(str(prov_db_path))
        self.prov_cur = self.prov_conn.cursor()
        for pragma in ('PRAGMA journal_mode=OFF',
                       'PRAGMA synchronous=OFF',
                       'PRAGMA cache_size=-100000'):
            self.prov_cur.execute(pragma)
        self.prov_cur.executescript(PROV_SCHEMA)

        # Lookup caches (string → rowid)
        self._lang_cache: dict[str, int] = {}
        self._rel_type_cache: dict[str, int] = {}
        self._prov_resource_cache: dict[str, int] = {}
        self._prov_table_cache: dict[str, int] = {}

        # Cross-file ID → rowid maps (persist for the entire build)
        self.synset_id_to_rowid: dict[str, int] = {}
        self.entry_id_to_rowid: dict[str, int] = {}
        self.sense_id_to_rowid: dict[str, int] = {}

        # Deduplication sets (avoid re-inserting identical rows)
        self._gloss_keys: set[tuple[int, int]] = set()
        self._sense_keys: set[tuple[int, int]] = set()
        self._synset_rel_keys: set[tuple[int, int, int]] = set()
        self._sense_rel_keys: set[tuple[int, int, int]] = set()

        # Resource metadata collected from file root attributes
        self._resources: dict[str, dict] = {}

        # Pre-assigned rowid counters (avoid lastrowid round-trips)
        self._next_synset_id = 1
        self._next_entry_id = 1
        self._next_form_id = 1
        self._next_pronunciation_id = 1
        self._next_sense_id = 1
        self._next_def_id = 1
        self._next_example_id = 1
        self._next_sense_rel_id = 1
        self._next_synset_rel_id = 1

        # Per-file INSERT buffers (flushed via executemany after each file)
        self._synsets_buf: list[tuple] = []
        self._entries_buf: list[tuple] = []
        self._forms_buf: list[tuple] = []
        self._pronunciations_buf: list[tuple] = []
        self._senses_buf: list[tuple] = []
        self._defs_buf: list[tuple] = []
        self._def_ann_buf: list[tuple] = []
        self._examples_buf: list[tuple] = []
        self._ex_ann_buf: list[tuple] = []
        self._sense_ex_buf: list[tuple] = []
        self._sense_rels_buf: list[tuple] = []
        self._synset_rels_buf: list[tuple] = []
        self._prov_buf: list[tuple] = []

        # Counters for progress reporting
        self.n_synsets = self.n_entries = self.n_forms = self.n_pronunciations = 0
        self.n_senses = self.n_defs = self.n_examples = 0
        self.n_sense_rels = self.n_synset_rels = self.n_prov = 0

    # --- Lookup helpers ---

    def _lang_rowid(self, code: str) -> int:
        if code not in self._lang_cache:
            try:
                name = langcodes.Language.get(code).display_name()
            except Exception:
                name = None
            self.cur.execute(
                'INSERT INTO languages (code, name) VALUES (?, ?)', (code, name)
            )
            self._lang_cache[code] = self.cur.lastrowid
        return self._lang_cache[code]

    def _rel_type_rowid(self, rel_type: str) -> int:
        if rel_type not in self._rel_type_cache:
            self.cur.execute(
                'INSERT OR IGNORE INTO relation_types (type) VALUES (?)', (rel_type,)
            )
            self.cur.execute(
                'SELECT rowid FROM relation_types WHERE type = ?', (rel_type,)
            )
            self._rel_type_cache[rel_type] = self.cur.fetchone()[0]
        return self._rel_type_cache[rel_type]

    def _prov_resource_rowid(self, code: str) -> int:
        if code not in self._prov_resource_cache:
            self.prov_cur.execute(
                'INSERT OR IGNORE INTO prov_resources (code) VALUES (?)', (code,)
            )
            self.prov_cur.execute(
                'SELECT rowid FROM prov_resources WHERE code = ?', (code,)
            )
            self._prov_resource_cache[code] = self.prov_cur.fetchone()[0]
        return self._prov_resource_cache[code]

    def _prov_table_rowid(self, name: str) -> int:
        if name not in self._prov_table_cache:
            self.prov_cur.execute(
                'INSERT OR IGNORE INTO prov_tables (name) VALUES (?)', (name,)
            )
            self.prov_cur.execute(
                'SELECT rowid FROM prov_tables WHERE name = ?', (name,)
            )
            self._prov_table_cache[name] = self.prov_cur.fetchone()[0]
        return self._prov_table_cache[name]

    def _insert_prov(self, table_name: str, item_rowid: int, prov_elems) -> int:
        table_rowid = self._prov_table_rowid(table_name)
        count = 0
        for p in prov_elems:
            resource = p.get('resource')
            original_id = p.get('original_id')
            if resource and original_id:
                self._prov_buf.append((
                    table_rowid, item_rowid,
                    self._prov_resource_rowid(resource),
                    p.get('version'), original_id,
                ))
                count += 1
        if len(self._prov_buf) >= BATCH_SIZE:
            self._flush_prov()
        return count

    # --- Element processors ---

    def _do_concept(self, elem) -> None:
        cid = elem.get('id')
        if cid in self.synset_id_to_rowid:
            raise ValueError(f'Duplicate concept id: {cid}')
        ili = cid.removeprefix('cili.') if cid.startswith('cili.') else None
        rowid = self._next_synset_id
        self._next_synset_id += 1
        self._synsets_buf.append((rowid, ili, elem.get('ontological_category')))
        self.synset_id_to_rowid[cid] = rowid
        self.n_prov += self._insert_prov('synsets', rowid, elem.findall('Provenance'))
        self.n_synsets += 1

    def _do_lexeme(self, elem) -> None:
        lid = elem.get('id')
        if lid in self.entry_id_to_rowid:
            # Merge: same lexeme in a second wordnet — add any new wordforms
            entry_rowid = self.entry_id_to_rowid[lid]
            existing = set(
                r[0] for r in self.cur.execute(
                    'SELECT form FROM forms WHERE entry_rowid = ?', (entry_rowid,)
                ).fetchall()
            )
            new_rank = max((r for r, in self.cur.execute(
                'SELECT rank FROM forms WHERE entry_rowid = ?', (entry_rowid,)
            ).fetchall()), default=-1) + 1
            for wf in elem.findall('Wordform'):
                form = wf.get('form')
                if form and form not in existing:
                    existing.add(form)
                    fid = self._next_form_id
                    self._next_form_id += 1
                    self._forms_buf.append(
                        (fid, entry_rowid, form, remove_accents(form.lower()), new_rank)
                    )
                    new_rank += 1
                    self.n_forms += 1
                    for p in wf.findall('Pronunciation'):
                        if p.text:
                            pid = self._next_pronunciation_id
                            self._next_pronunciation_id += 1
                            self._pronunciations_buf.append(
                                (pid, fid, p.get('variety'), p.text)
                            )
                            self.n_pronunciations += 1
            self.n_prov += self._insert_prov(
                'entries', entry_rowid, elem.findall('Provenance')
            )
            return

        # Build form rows first; skip entry entirely if no valid forms
        seen: set[str] = set()
        form_rows: list[tuple] = []
        pron_rows: list[tuple] = []  # (fid, variety, text)
        for wf in elem.findall('Wordform'):
            form = wf.get('form')
            if form and form not in seen:
                seen.add(form)
                fid = self._next_form_id
                self._next_form_id += 1
                form_rows.append(
                    (fid, 0, form, remove_accents(form.lower()), len(form_rows))
                )
                for p in wf.findall('Pronunciation'):
                    if p.text:
                        pron_rows.append((fid, p.get('variety'), p.text))
        if not form_rows:
            return

        lang_rowid = self._lang_rowid(elem.get('language'))
        entry_rowid = self._next_entry_id
        self._next_entry_id += 1
        self._entries_buf.append((entry_rowid, lang_rowid, elem.get('grammatical_category')))
        self.entry_id_to_rowid[lid] = entry_rowid
        self.n_prov += self._insert_prov(
            'entries', entry_rowid, elem.findall('Provenance')
        )
        self.n_entries += 1

        for fid, _, form, norm, rank in form_rows:
            self._forms_buf.append((fid, entry_rowid, form, norm, rank))
        self.n_forms += len(form_rows)
        if len(self._forms_buf) >= BATCH_SIZE:
            self._flush_forms()

        for fid, variety, text in pron_rows:
            pid = self._next_pronunciation_id
            self._next_pronunciation_id += 1
            self._pronunciations_buf.append((pid, fid, variety, text))
        self.n_pronunciations += len(pron_rows)

    def _do_sense(self, elem) -> None:
        entry_rowid = self.entry_id_to_rowid.get(elem.get('signifier'))
        synset_rowid = self.synset_id_to_rowid.get(elem.get('signified'))
        if not entry_rowid or not synset_rowid:
            return
        key = (entry_rowid, synset_rowid)
        if key in self._sense_keys:
            return
        self._sense_keys.add(key)
        sense_rowid = self._next_sense_id
        self._next_sense_id += 1
        self._senses_buf.append((sense_rowid, entry_rowid, synset_rowid))
        self.sense_id_to_rowid[elem.get('id')] = sense_rowid
        self.n_prov += self._insert_prov(
            'senses', sense_rowid, elem.findall('Provenance')
        )
        self.n_senses += 1
        if len(self._senses_buf) >= BATCH_SIZE:
            self._flush_senses()

    def _do_gloss(self, elem) -> None:
        synset_rowid = self.synset_id_to_rowid.get(elem.get('definiendum'))
        if not synset_rowid:
            return
        lang_rowid = self._lang_rowid(elem.get('language'))
        key = (synset_rowid, lang_rowid)
        if key in self._gloss_keys:
            return
        self._gloss_keys.add(key)

        sentence = elem.find('AnnotatedSentence')
        text, annotations = parse_annotated_sentence(sentence)
        def_rowid = self._next_def_id
        self._next_def_id += 1
        self._defs_buf.append((def_rowid, synset_rowid, text, lang_rowid))
        self.n_prov += self._insert_prov(
            'definitions', def_rowid, elem.findall('Provenance')
        )
        self.n_defs += 1

        for ann in annotations:
            sense_rowid = self.sense_id_to_rowid.get(ann['sense'])
            if sense_rowid:
                self._def_ann_buf.append(
                    (def_rowid, ann['start'], ann['end'], sense_rowid)
                )

    def _do_example(self, elem) -> None:
        sentence = elem.find('AnnotatedSentence')
        text, annotations = parse_annotated_sentence(sentence)

        referenced = {
            self.sense_id_to_rowid[a['sense']]
            for a in annotations
            if a['sense'] in self.sense_id_to_rowid
        }
        if not referenced:
            return

        ex_rowid = self._next_example_id
        self._next_example_id += 1
        self._examples_buf.append((ex_rowid, text))
        self.n_prov += self._insert_prov(
            'examples', ex_rowid, elem.findall('Provenance')
        )
        self.n_examples += 1

        for sense_rowid in referenced:
            self._sense_ex_buf.append((sense_rowid, ex_rowid))
        for ann in annotations:
            sense_rowid = self.sense_id_to_rowid.get(ann['sense'])
            if sense_rowid:
                self._ex_ann_buf.append(
                    (ex_rowid, ann['start'], ann['end'], sense_rowid)
                )

    def _do_sense_relation(self, elem) -> None:
        rel_type = elem.get('relation_type')
        source = self.sense_id_to_rowid.get(elem.get('source'))
        target = self.sense_id_to_rowid.get(elem.get('target'))
        if not source or not target:
            return

        type_rowid = self._rel_type_rowid(rel_type)
        key = (source, target, type_rowid)
        if key not in self._sense_rel_keys:
            self._sense_rel_keys.add(key)
            sr_rowid = self._next_sense_rel_id
            self._next_sense_rel_id += 1
            self._sense_rels_buf.append((sr_rowid, source, target, type_rowid))
            self.n_prov += self._insert_prov(
                'sense_relations', sr_rowid, elem.findall('Provenance')
            )
            self.n_sense_rels += 1

        inv = INVERSE_SENSE_RELATIONS.get(rel_type)
        if inv:
            inv_type = self._rel_type_rowid(inv)
            inv_key = (target, source, inv_type)
            if inv_key not in self._sense_rel_keys:
                self._sense_rel_keys.add(inv_key)
                inv_rowid = self._next_sense_rel_id
                self._next_sense_rel_id += 1
                self._sense_rels_buf.append((inv_rowid, target, source, inv_type))
                self.n_sense_rels += 1

    def _do_concept_relation(self, elem) -> None:
        rel_type = elem.get('relation_type')
        source = self.synset_id_to_rowid.get(elem.get('source'))
        target = self.synset_id_to_rowid.get(elem.get('target'))
        if not source or not target:
            return

        type_rowid = self._rel_type_rowid(rel_type)
        key = (source, target, type_rowid)
        if key not in self._synset_rel_keys:
            self._synset_rel_keys.add(key)
            cr_rowid = self._next_synset_rel_id
            self._next_synset_rel_id += 1
            self._synset_rels_buf.append((cr_rowid, source, target, type_rowid))
            self.n_prov += self._insert_prov(
                'synset_relations', cr_rowid, elem.findall('Provenance')
            )
            self.n_synset_rels += 1

        inv = INVERSE_CONCEPT_RELATIONS.get(rel_type)
        if inv:
            inv_type = self._rel_type_rowid(inv)
            inv_key = (target, source, inv_type)
            if inv_key not in self._synset_rel_keys:
                self._synset_rel_keys.add(inv_key)
                inv_rowid = self._next_synset_rel_id
                self._next_synset_rel_id += 1
                self._synset_rels_buf.append((inv_rowid, target, source, inv_type))
                self.n_synset_rels += 1

    # --- Flush helpers ---

    def _flush_synsets(self) -> None:
        if self._synsets_buf:
            self.cur.executemany(
                'INSERT INTO synsets (rowid, ili, pos) VALUES (?, ?, ?)',
                self._synsets_buf,
            )
            self._synsets_buf.clear()

    def _flush_entries(self) -> None:
        if self._entries_buf:
            self.cur.executemany(
                'INSERT INTO entries (rowid, language_rowid, pos) VALUES (?, ?, ?)',
                self._entries_buf,
            )
            self._entries_buf.clear()

    def _flush_forms(self) -> None:
        if self._forms_buf:
            self.cur.executemany(
                'INSERT INTO forms '
                '(rowid, entry_rowid, form, normalized_form, rank) VALUES (?, ?, ?, ?, ?)',
                self._forms_buf,
            )
            self._forms_buf.clear()

    def _flush_pronunciations(self) -> None:
        if self._pronunciations_buf:
            self.cur.executemany(
                'INSERT INTO pronunciations '
                '(rowid, form_rowid, variety, pronunciation) VALUES (?, ?, ?, ?)',
                self._pronunciations_buf,
            )
            self._pronunciations_buf.clear()

    def _flush_senses(self) -> None:
        if self._senses_buf:
            self.cur.executemany(
                'INSERT INTO senses (rowid, entry_rowid, synset_rowid) VALUES (?, ?, ?)',
                self._senses_buf,
            )
            self._senses_buf.clear()

    def _flush_defs(self) -> None:
        if self._defs_buf:
            self.cur.executemany(
                'INSERT INTO definitions '
                '(rowid, synset_rowid, definition, language_rowid) VALUES (?, ?, ?, ?)',
                self._defs_buf,
            )
            self._defs_buf.clear()

    def _flush_def_ann(self) -> None:
        if self._def_ann_buf:
            self.cur.executemany(
                'INSERT INTO definition_annotations '
                '(definition_rowid, start_offset, end_offset, sense_rowid) '
                'VALUES (?, ?, ?, ?)',
                self._def_ann_buf,
            )
            self._def_ann_buf.clear()

    def _flush_examples(self) -> None:
        if self._examples_buf:
            self.cur.executemany(
                'INSERT INTO examples (rowid, example) VALUES (?, ?)',
                self._examples_buf,
            )
            self._examples_buf.clear()

    def _flush_ex_ann(self) -> None:
        if self._ex_ann_buf:
            self.cur.executemany(
                'INSERT INTO example_annotations '
                '(example_rowid, start_offset, end_offset, sense_rowid) '
                'VALUES (?, ?, ?, ?)',
                self._ex_ann_buf,
            )
            self._ex_ann_buf.clear()

    def _flush_sense_ex(self) -> None:
        if self._sense_ex_buf:
            self.cur.executemany(
                'INSERT INTO sense_examples (sense_rowid, example_rowid) VALUES (?, ?)',
                self._sense_ex_buf,
            )
            self._sense_ex_buf.clear()

    def _flush_sense_rels(self) -> None:
        if self._sense_rels_buf:
            self.cur.executemany(
                'INSERT INTO sense_relations '
                '(rowid, source_rowid, target_rowid, type_rowid) VALUES (?, ?, ?, ?)',
                self._sense_rels_buf,
            )
            self._sense_rels_buf.clear()

    def _flush_synset_rels(self) -> None:
        if self._synset_rels_buf:
            self.cur.executemany(
                'INSERT INTO synset_relations '
                '(rowid, source_rowid, target_rowid, type_rowid) VALUES (?, ?, ?, ?)',
                self._synset_rels_buf,
            )
            self._synset_rels_buf.clear()

    def _flush_prov(self) -> None:
        if self._prov_buf:
            self.prov_cur.executemany(
                'INSERT INTO provenance '
                '(table_rowid, item_rowid, resource_rowid, version, original_id) '
                'VALUES (?, ?, ?, ?, ?)',
                self._prov_buf,
            )
            self._prov_buf.clear()

    def _flush_all(self) -> None:
        """Flush all per-file INSERT buffers using executemany."""
        self._flush_synsets()
        self._flush_entries()
        self._flush_forms()
        self._flush_pronunciations()
        self._flush_senses()
        self._flush_defs()
        self._flush_def_ann()
        self._flush_examples()
        self._flush_ex_ann()
        self._flush_sense_ex()
        self._flush_sense_rels()
        self._flush_synset_rels()
        self._flush_prov()

    # --- File processing ---

    def process_file(self, xml_file: Path) -> None:
        """Stream one pre-synth XML file into the DB using iterparse."""
        tag_to_handler = {
            'Concept':         self._do_concept,
            'Lexeme':          self._do_lexeme,
            'Sense':           self._do_sense,
            'Gloss':           self._do_gloss,
            'Example':         self._do_example,
            'SenseRelation':   self._do_sense_relation,
            'ConceptRelation': self._do_concept_relation,
        }
        root_seen = False
        for event, elem in ET.iterparse(str(xml_file), events=('start', 'end')):
            if event == 'start' and not root_seen:
                root_seen = True
                res_id = elem.get('id')
                if res_id and res_id not in self._resources:
                    self._resources[res_id] = dict(elem.attrib)
            elif event == 'end':
                handler = tag_to_handler.get(elem.tag)
                if handler is not None:
                    handler(elem)
                    elem.clear()
                    while elem.getprevious() is not None:
                        del elem.getparent()[0]
        self._flush_all()

    # --- Post-processing (run after all files are loaded) ---

    def merge_case_variants(self) -> int:
        """
        Consolidate senses where different-case lexemes map to the same synset.

        E.g. 'Bank' (entry A) and 'bank' (entry B) both having a sense for the
        same concept are merged: all wordforms move to the lower-rowid entry,
        the duplicate sense is deleted, and all references updated.

        Returns the number of senses removed.
        """
        groups = self.cur.execute("""
            SELECT LOWER(f.form) AS norm_lemma,
                   e.language_rowid, e.pos, s.synset_rowid,
                   MIN(s.rowid)   AS keep_sense,
                   MIN(e.rowid)   AS keep_entry
            FROM senses s
            JOIN entries e ON s.entry_rowid = e.rowid
            JOIN forms   f ON f.entry_rowid = e.rowid AND f.rank = 0
            GROUP BY norm_lemma, e.language_rowid, e.pos, s.synset_rowid
            HAVING COUNT(*) > 1
        """).fetchall()

        removed = 0
        prov_senses_rowid = self._prov_table_rowid('senses')

        for norm_lemma, lang_rowid, pos, synset_rowid, keep_sense, keep_entry in groups:
            variants = self.cur.execute("""
                SELECT s.rowid, e.rowid
                FROM senses s
                JOIN entries e ON s.entry_rowid = e.rowid
                JOIN forms   f ON f.entry_rowid = e.rowid AND f.rank = 0
                WHERE LOWER(f.form) = ? AND e.language_rowid = ?
                  AND e.pos = ? AND s.synset_rowid = ?
            """, (norm_lemma, lang_rowid, pos, synset_rowid)).fetchall()

            existing_forms = set(
                r[0] for r in self.cur.execute(
                    'SELECT form FROM forms WHERE entry_rowid = ?', (keep_entry,)
                ).fetchall()
            )

            for sense_rowid, entry_rowid in variants:
                if sense_rowid == keep_sense:
                    continue

                # Move wordforms from the non-canonical entry to keep_entry
                for old_form_rowid, form, norm_form, rank in self.cur.execute(
                    'SELECT rowid, form, normalized_form, rank FROM forms '
                    'WHERE entry_rowid = ?',
                    (entry_rowid,),
                ).fetchall():
                    if form not in existing_forms:
                        self.cur.execute(
                            'INSERT INTO forms '
                            '(entry_rowid, form, normalized_form, rank) '
                            'VALUES (?, ?, ?, ?)',
                            (keep_entry, form, norm_form, rank),
                        )
                        new_form_rowid = self.cur.lastrowid
                        existing_forms.add(form)
                        self.cur.execute(
                            'INSERT INTO pronunciations (form_rowid, variety, pronunciation)'
                            ' SELECT ?, variety, pronunciation'
                            ' FROM pronunciations WHERE form_rowid = ?',
                            (new_form_rowid, old_form_rowid),
                        )

                # Merge provenance into the surviving sense
                existing_prov = set(
                    r[0] for r in self.prov_cur.execute(
                        'SELECT original_id FROM provenance '
                        'WHERE table_rowid = ? AND item_rowid = ?',
                        (prov_senses_rowid, keep_sense),
                    ).fetchall()
                )
                for res_rowid, version, orig_id in self.prov_cur.execute(
                    'SELECT resource_rowid, version, original_id FROM provenance '
                    'WHERE table_rowid = ? AND item_rowid = ?',
                    (prov_senses_rowid, sense_rowid),
                ).fetchall():
                    if orig_id not in existing_prov:
                        self.prov_cur.execute(
                            'INSERT INTO provenance '
                            '(table_rowid, item_rowid, resource_rowid, version, original_id) '
                            'VALUES (?, ?, ?, ?, ?)',
                            (prov_senses_rowid, keep_sense, res_rowid, version, orig_id),
                        )
                        existing_prov.add(orig_id)

                # Redirect all references to the surviving sense
                for table, col in [
                    ('sense_relations',       'source_rowid'),
                    ('sense_relations',       'target_rowid'),
                    ('example_annotations',   'sense_rowid'),
                    ('definition_annotations','sense_rowid'),
                    ('sense_examples',        'sense_rowid'),
                ]:
                    self.cur.execute(
                        f'UPDATE {table} SET {col} = ? WHERE {col} = ?',
                        (keep_sense, sense_rowid),
                    )

                self.cur.execute(
                    'DELETE FROM senses WHERE rowid = ?', (sense_rowid,)
                )
                self.prov_cur.execute(
                    'DELETE FROM provenance WHERE table_rowid = ? AND item_rowid = ?',
                    (prov_senses_rowid, sense_rowid),
                )
                removed += 1

        # Dedup any sense_relations that became identical after the redirect
        self.cur.execute("""
            DELETE FROM sense_relations WHERE rowid NOT IN (
                SELECT MIN(rowid)
                FROM sense_relations
                GROUP BY source_rowid, target_rowid, type_rowid
            )
        """)
        return removed

    def cascade_delete(self) -> dict[str, int]:
        """Remove synsets with no definitions and cascade to dependent rows."""
        counts: dict[str, int] = {}

        # Synsets with no definitions
        self.cur.execute("""
            DELETE FROM synsets
            WHERE rowid NOT IN (SELECT synset_rowid FROM definitions)
        """)
        counts['synsets'] = self.cur.rowcount

        # Senses pointing to deleted synsets
        self.cur.execute("""
            DELETE FROM senses
            WHERE synset_rowid NOT IN (SELECT rowid FROM synsets)
        """)
        counts['senses'] = self.cur.rowcount

        # Sense relations referencing deleted senses
        self.cur.execute("""
            DELETE FROM sense_relations
            WHERE source_rowid NOT IN (SELECT rowid FROM senses)
               OR target_rowid NOT IN (SELECT rowid FROM senses)
        """)
        counts['sense_relations'] = self.cur.rowcount

        # Synset relations referencing deleted synsets
        self.cur.execute("""
            DELETE FROM synset_relations
            WHERE source_rowid NOT IN (SELECT rowid FROM synsets)
               OR target_rowid NOT IN (SELECT rowid FROM synsets)
        """)
        counts['synset_relations'] = self.cur.rowcount

        # Sense_examples and annotations referencing deleted senses
        self.cur.execute("""
            DELETE FROM sense_examples
            WHERE sense_rowid NOT IN (SELECT rowid FROM senses)
        """)
        self.cur.execute("""
            DELETE FROM example_annotations
            WHERE sense_rowid NOT IN (SELECT rowid FROM senses)
        """)
        self.cur.execute("""
            DELETE FROM definition_annotations
            WHERE sense_rowid NOT IN (SELECT rowid FROM senses)
        """)

        # Definitions referencing deleted synsets
        self.cur.execute("""
            DELETE FROM definitions
            WHERE synset_rowid NOT IN (SELECT rowid FROM synsets)
        """)
        counts['definitions'] = self.cur.rowcount

        # Examples with no remaining sense_examples
        self.cur.execute("""
            DELETE FROM examples
            WHERE rowid NOT IN (SELECT example_rowid FROM sense_examples)
        """)
        counts['examples'] = self.cur.rowcount

        # Clean orphaned example_annotations
        self.cur.execute("""
            DELETE FROM example_annotations
            WHERE example_rowid NOT IN (SELECT rowid FROM examples)
        """)
        return counts

    def remove_orphans(self) -> int:
        """Remove entries (and their forms/pronunciations) that have no remaining senses."""
        self.cur.execute("""
            DELETE FROM entries
            WHERE rowid NOT IN (SELECT DISTINCT entry_rowid FROM senses)
        """)
        n = self.cur.rowcount
        self.cur.execute("""
            DELETE FROM forms
            WHERE entry_rowid NOT IN (SELECT rowid FROM entries)
        """)
        self.cur.execute("""
            DELETE FROM pronunciations
            WHERE form_rowid NOT IN (SELECT rowid FROM forms)
        """)
        return n

    def compute_sense_indices(self) -> None:
        """Assign sense_index: position among senses sharing (language, lemma_lower, pos)."""
        self.cur.execute("""
            UPDATE senses SET sense_index = sub.idx FROM (
                SELECT s.rowid AS sid,
                       ROW_NUMBER() OVER (
                           PARTITION BY e.language_rowid, LOWER(f.form), syn.pos
                           ORDER BY s.rowid
                       ) AS idx
                FROM senses s
                JOIN entries e  ON s.entry_rowid  = e.rowid
                JOIN forms   f  ON f.entry_rowid  = e.rowid AND f.rank = 0
                JOIN synsets syn ON s.synset_rowid = syn.rowid
            ) sub WHERE senses.rowid = sub.sid
        """)

    def insert_resources(self) -> None:
        """Write collected resource metadata and back-fill implicit resources."""
        known_lmf = {'id', 'version', 'label', 'language', 'url', 'citation',
                     'license', 'email', 'status', 'confidenceScore'}
        for attrib in self._resources.values():
            extra = {k: v for k, v in attrib.items() if k not in known_lmf}
            lang_code = attrib.get('language')
            score_raw = attrib.get('confidenceScore')
            self.cur.execute(
                'INSERT INTO resources '
                '(code, version, label, language_rowid, url, citation, '
                'licence, email, status, confidence_score, extra) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    attrib.get('id'),
                    attrib.get('version'),
                    attrib.get('label'),
                    self._lang_rowid(lang_code) if lang_code else None,
                    attrib.get('url'),
                    attrib.get('citation'),
                    attrib.get('license'),
                    attrib.get('email'),
                    attrib.get('status'),
                    float(score_raw) if score_raw else None,
                    json.dumps(extra) if extra else None,
                ),
            )

        # Back-fill resources that appear in provenance but have no LMF metadata
        self.prov_cur.execute('SELECT code FROM prov_resources')
        prov_codes = {r[0] for r in self.prov_cur.fetchall()}
        self.cur.execute('SELECT code FROM resources')
        existing = {r[0] for r in self.cur.fetchall()}
        for code in sorted(prov_codes - existing):
            meta = IMPLICIT_RESOURCES.get(code, {})
            lang = meta.get('language')
            self.cur.execute(
                'INSERT INTO resources '
                '(code, version, label, language_rowid, url, licence, citation) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (code, meta.get('version'), meta.get('label'),
                 self._lang_rowid(lang) if lang else None,
                 meta.get('url'), meta.get('licence'), meta.get('citation')),
            )
            print(f'  Added implicit resource: {code}')

    def create_indexes(self) -> None:
        """Create indexes before post-processing queries (critical for performance)."""
        self.cur.executescript(INDEXES)

    def finalize(self, db_path: Path, prov_db_path: Path) -> None:
        """Run ANALYZE, commit, and gzip both databases."""
        print('Running ANALYZE...')
        self.cur.execute('ANALYZE')

        self.conn.commit()
        self.prov_conn.commit()
        self.conn.close()
        self.prov_conn.close()

        for path in (db_path, prov_db_path):
            size = path.stat().st_size / 1024 ** 2
            print(f'  {path} ({size:.1f} MB)')
            subprocess.run(['gzip', '-k', '-9', '-f', str(path)], check=True)
            gz = path.with_suffix(path.suffix + '.gz')
            gz_size = gz.stat().st_size / 1024 ** 2
            print(f'  {gz} ({gz_size:.1f} MB compressed)')


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)

    input_dir = Path('bin/cygnets_presynth')
    db_path = Path('web/cygnet.db')
    prov_db_path = Path('web/provenance.db')

    db_path.parent.mkdir(exist_ok=True)

    xml_files = sorted(input_dir.glob('*.xml'))
    print(f'Found {len(xml_files)} pre-synth files to merge')

    builder = MergeBuilder(db_path, prov_db_path)

    print('\nPhase 1: Streaming files into SQLite...')
    for xml_file in xml_files:
        print(f'  {xml_file.name}', flush=True)
        builder.process_file(xml_file)

    print(f'\n  Synsets: {builder.n_synsets:,}')
    print(f'  Entries: {builder.n_entries:,},  Forms: {builder.n_forms:,},'
          f'  Pronunciations: {builder.n_pronunciations:,}')
    print(f'  Languages: {len(builder._lang_cache)}')
    print(f'  Senses: {builder.n_senses:,}')
    print(f'  Definitions: {builder.n_defs:,}')
    print(f'  Examples: {builder.n_examples:,}')
    print(f'  Sense relations: {builder.n_sense_rels:,}')
    print(f'  Synset relations: {builder.n_synset_rels:,}')
    print(f'  Provenance rows: {builder.n_prov:,}')

    print('\nCreating indexes...')
    builder.create_indexes()

    print('\nPhase 2: Merging case-variant lexemes...')
    removed = builder.merge_case_variants()
    print(f'  Removed {removed:,} duplicate senses from case-variant merge')

    print('\nPhase 3: Cascade-deleting synsets without definitions...')
    counts = builder.cascade_delete()
    for name, n in counts.items():
        if n:
            print(f'  Removed {n:,} {name}')

    print('\nPhase 4: Removing orphaned entries...')
    n_orphans = builder.remove_orphans()
    print(f'  Removed {n_orphans:,} orphaned entries')

    print('\nPhase 5: Computing sense indices...')
    builder.compute_sense_indices()

    print('\nPhase 6: Inserting resource metadata...')
    builder.insert_resources()

    print('\nFinalising...')
    builder.finalize(db_path, prov_db_path)
    print('\nDone.')


if __name__ == '__main__':
    main()
