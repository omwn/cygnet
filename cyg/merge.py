"""
Core merge logic for synthesising pre-converted Cygnet XML files into SQLite.
"""

import json
import logging
import re
import sqlite3
import unicodedata
from pathlib import Path

import langcodes
from lxml import etree as ET

logger = logging.getLogger(__name__)

_COMBINING_RE = re.compile(r'[\u0300-\u036f\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f]')

# ---------------------------------------------------------------------------
# Schema constants
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
    extra            TEXT,
    synset_count     INTEGER,
    sense_count      INTEGER
);
CREATE TABLE arasaac (
    synset_rowid INTEGER NOT NULL REFERENCES synsets(rowid),
    arasaac_id   INTEGER NOT NULL
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
    original_id    TEXT
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
CREATE INDEX idx_arasaac_synset ON arasaac(synset_rowid);
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

CILI_RESOURCE = {
    'code':    'cili',
    'version': '1.0',
    'label':   'Collaborative Interlingual Index (CILI)',
    'url':     'https://github.com/globalwordnet/cili',
    'licence':  'https://creativecommons.org/licenses/by/4.0/',
    'citation': (
        'Francis Bond, Piek Vossen, John McCrae, and Christiane Fellbaum. 2016. '
        'CILI: the Collaborative Interlingual Index. '
        'In *Proceedings of the 8th Global WordNet Conference (GWC)*, '
        'pages 50\u201357, Bucharest, Romania. Global Wordnet Association.'
    ),
}

INVERSE_SENSE_RELATIONS: dict[str, str] = {
    'antonym':           'antonym',
    'also':              'also',
    'similar':           'similar',
    'derivation':        'derivation',
    'anto_gradable':     'anto_gradable',
    'anto_simple':       'anto_simple',
    'anto_converse':     'anto_converse',
    'domain_topic':      'has_domain_topic',
    'domain_region':     'has_domain_region',
    'exemplifies':       'is_exemplified_by',
    'feminine':          'has_feminine',
    'masculine':         'has_masculine',
    'young':             'has_young',
    'diminutive':        'has_diminutive',
    'augmentative':      'has_augmentative',
    'simple_aspect_ip':  'simple_aspect_pi',
    'secondary_aspect_ip': 'secondary_aspect_pi',
    'metaphor':          'has_metaphor',
    'metonym':           'has_metonym',
}

INVERSE_CONCEPT_RELATIONS: dict[str, str] = {
    'hypernym':          'hyponym',
    'instance_hypernym': 'instance_hyponym',
    'meronym':           'holonym',
    'mero_member':       'holo_member',
    'mero_part':         'holo_part',
    'mero_substance':    'holo_substance',
    'mero_location':     'holo_location',
    'mero_portion':      'holo_portion',
    'causes':            'is_caused_by',
    'entails':           'is_entailed_by',
    'subevent':          'is_subevent_of',
    'state_of':          'be_in_state',
    'manner_of':         'in_manner',
    'domain_topic':      'has_domain_topic',
    'domain_region':     'has_domain_region',
    'domain':            'has_domain',
    'exemplifies':       'is_exemplified_by',
    'classifies':        'classified_by',
    'restricts':         'restricted_by',
    'role':              'involved',
    'agent':             'involved_agent',
    'patient':           'involved_patient',
    'result':            'involved_result',
    'instrument':        'involved_instrument',
    'location':          'involved_location',
    'direction':         'involved_direction',
    'target_direction':  'involved_target_direction',
    'source_direction':  'involved_source_direction',
    'co_agent_patient':      'co_patient_agent',
    'co_agent_instrument':   'co_instrument_agent',
    'co_agent_result':       'co_result_agent',
    'co_patient_instrument': 'co_instrument_patient',
    'co_result_instrument':  'co_instrument_result',
    'feminine':          'has_feminine',
    'masculine':         'has_masculine',
    'young':             'has_young',
    'diminutive':        'has_diminutive',
    'augmentative':      'has_augmentative',
    'simple_aspect_ip':  'simple_aspect_pi',
    'secondary_aspect_ip': 'secondary_aspect_pi',
    'metaphor':          'has_metaphor',
    'metonym':           'has_metonym',
    'antonym':           'antonym',
    'similar':           'similar',
    'attribute':         'attribute',
    'eq_synonym':        'eq_synonym',
    'ir_synonym':        'ir_synonym',
    'co_role':           'co_role',
    'also':              'also',
    'anto_gradable':     'anto_gradable',
    'anto_simple':       'anto_simple',
    'anto_converse':     'anto_converse',
    'derivation':        'derivation',
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


def build_annotated_sentence(parent, text: str, annotations: list[tuple]) -> None:
    """Add an AnnotatedSentence child to *parent* with inline AnnotatedToken elements.

    Args:
        parent: lxml element to append to.
        text: plain text of the sentence.
        annotations: list of (start_offset, end_offset, sense_id_str) tuples,
                     sorted by start_offset.
    """
    el = ET.SubElement(parent, 'AnnotatedSentence')
    pos = 0
    prev = None
    for start, end, sense_id in annotations:
        preceding = text[pos:start]
        if prev is None:
            el.text = preceding
        else:
            prev.tail = preceding
        tok = ET.SubElement(el, 'AnnotatedToken', sense=sense_id)
        tok.text = text[start:end]
        prev = tok
        pos = end
    trailing = text[pos:]
    if prev is None:
        el.text = trailing
    else:
        prev.tail = trailing


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
        # Maps (source, target, type_rowid) → resource_code ('' for auto-generated)
        self._synset_rel_keys: dict[tuple[int, int, int], str] = {}
        self._sense_rel_keys: dict[tuple[int, int, int], str] = {}

        # Reverse maps for human-readable conflict log messages
        self._synset_rowid_to_ili: dict[int, str] = {}
        self._sense_rowid_to_id: dict[int, str] = {}

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

        # Structured conflict records (written to JSON at the end of synthesis)
        self._conflicts: dict[str, list] = {
            'reversed_relations': [],
            'cycles': [],
        }

        # Counters for progress reporting
        self.n_synsets = self.n_entries = self.n_forms = self.n_pronunciations = 0
        self.n_senses = self.n_defs = self.n_examples = 0
        self.n_sense_rels = self.n_synset_rels = self.n_prov = self.n_rel_conflicts = 0

    # --- Lookup helpers ---

    @staticmethod
    def _get_or_insert_rowid(
        cache: dict, cur: sqlite3.Cursor,
        table: str, col: str, value: str,
    ) -> int:
        """Return the rowid for *value* in *table.col*, inserting if absent.

        Uses INSERT OR IGNORE so concurrent or repeated inserts are safe.
        Results are cached in *cache* to avoid repeated round-trips.
        """
        if value not in cache:
            cur.execute(f'INSERT OR IGNORE INTO {table} ({col}) VALUES (?)', (value,))
            cur.execute(f'SELECT rowid FROM {table} WHERE {col} = ?', (value,))
            cache[value] = cur.fetchone()[0]
        return cache[value]

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
        return self._get_or_insert_rowid(
            self._rel_type_cache, self.cur, 'relation_types', 'type', rel_type,
        )

    def _prov_resource_rowid(self, code: str) -> int:
        return self._get_or_insert_rowid(
            self._prov_resource_cache, self.prov_cur, 'prov_resources', 'code', code,
        )

    def _prov_table_rowid(self, name: str) -> int:
        return self._get_or_insert_rowid(
            self._prov_table_cache, self.prov_cur, 'prov_tables', 'name', name,
        )

    def _insert_prov(self, table_name: str, item_rowid: int, prov_elems) -> int:
        table_rowid = self._prov_table_rowid(table_name)
        count = 0
        for p in prov_elems:
            resource = p.get('resource')
            if resource:
                self._prov_buf.append((
                    table_rowid, item_rowid,
                    self._prov_resource_rowid(resource),
                    p.get('version'), p.get('original_id'),
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
        self._synset_rowid_to_ili[rowid] = cid
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
        sid = elem.get('id')
        self.sense_id_to_rowid[sid] = sense_rowid
        self._sense_rowid_to_id[sense_rowid] = sid
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

        prov_elems = elem.findall('Provenance')
        current_resource = prov_elems[0].get('resource') if prov_elems else ''
        type_rowid = self._rel_type_rowid(rel_type)
        inv = INVERSE_SENSE_RELATIONS.get(rel_type)

        # Conflict: same relation type exists with source/target reversed
        if inv != rel_type:
            reverse_key = (target, source, type_rowid)
            if reverse_key in self._sense_rel_keys:
                src_id = self._sense_rowid_to_id.get(source, f'#{source}')
                tgt_id = self._sense_rowid_to_id.get(target, f'#{target}')
                prior = self._sense_rel_keys[reverse_key] or 'auto-generated'
                logger.warning(
                    'Reversed sense_relation skipped [%s]: %s %s %s '
                    '(conflicts with %s %s %s from [%s])',
                    current_resource, src_id, rel_type, tgt_id,
                    tgt_id, rel_type, src_id, prior,
                )
                self._conflicts['reversed_relations'].append({
                    'resource_id': current_resource,
                    'kind': 'sense',
                    'src': src_id,
                    'rel': rel_type,
                    'tgt': tgt_id,
                    'prior_resource': prior,
                })
                self.n_rel_conflicts += 1
                return

        key = (source, target, type_rowid)
        if key not in self._sense_rel_keys:
            self._sense_rel_keys[key] = current_resource
            sr_rowid = self._next_sense_rel_id
            self._next_sense_rel_id += 1
            self._sense_rels_buf.append((sr_rowid, source, target, type_rowid))
            self.n_prov += self._insert_prov('sense_relations', sr_rowid, prov_elems)
            self.n_sense_rels += 1

        if inv:
            inv_type = self._rel_type_rowid(inv)
            inv_key = (target, source, inv_type)
            if inv_key not in self._sense_rel_keys:
                self._sense_rel_keys[inv_key] = ''
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

        prov_elems = elem.findall('Provenance')
        current_resource = prov_elems[0].get('resource') if prov_elems else ''
        type_rowid = self._rel_type_rowid(rel_type)
        inv = INVERSE_CONCEPT_RELATIONS.get(rel_type)

        if inv != rel_type:
            reverse_key = (target, source, type_rowid)
            if reverse_key in self._synset_rel_keys:
                src_ili = self._synset_rowid_to_ili.get(source, f'#{source}')
                tgt_ili = self._synset_rowid_to_ili.get(target, f'#{target}')
                prior = self._synset_rel_keys[reverse_key] or 'auto-generated'
                logger.warning(
                    'Reversed synset_relation skipped [%s]: %s %s %s '
                    '(conflicts with %s %s %s from [%s])',
                    current_resource, src_ili, rel_type, tgt_ili,
                    tgt_ili, rel_type, src_ili, prior,
                )
                self._conflicts['reversed_relations'].append({
                    'resource_id': current_resource,
                    'kind': 'synset',
                    'src': src_ili,
                    'rel': rel_type,
                    'tgt': tgt_ili,
                    'prior_resource': prior,
                })
                self.n_rel_conflicts += 1
                return

        key = (source, target, type_rowid)
        if key not in self._synset_rel_keys:
            self._synset_rel_keys[key] = current_resource
            cr_rowid = self._next_synset_rel_id
            self._next_synset_rel_id += 1
            self._synset_rels_buf.append((cr_rowid, source, target, type_rowid))
            self.n_prov += self._insert_prov('synset_relations', cr_rowid, prov_elems)
            self.n_synset_rels += 1

        if inv:
            inv_type = self._rel_type_rowid(inv)
            inv_key = (target, source, inv_type)
            if inv_key not in self._synset_rel_keys:
                self._synset_rel_keys[inv_key] = ''
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

    @staticmethod
    def _tarjan_cyclic_sccs(
        nodes: set[int], children: dict[int, list[int]]
    ) -> list[list[int]]:
        """Iterative Tarjan's SCC; returns only SCCs that contain a cycle."""
        index_counter = [0]
        index: dict[int, int] = {}
        lowlink: dict[int, int] = {}
        on_stack: dict[int, bool] = {}
        scc_stack: list[int] = []
        cyclic_sccs: list[list[int]] = []

        for start in nodes:
            if start in index:
                continue
            dfs_stack: list[tuple[int, object]] = [
                (start, iter(children.get(start, [])))
            ]
            index[start] = lowlink[start] = index_counter[0]
            index_counter[0] += 1
            on_stack[start] = True
            scc_stack.append(start)

            while dfs_stack:
                v, nbrs = dfs_stack[-1]
                advanced = False
                for w in nbrs:
                    if w not in index:
                        index[w] = lowlink[w] = index_counter[0]
                        index_counter[0] += 1
                        on_stack[w] = True
                        scc_stack.append(w)
                        dfs_stack.append((w, iter(children.get(w, []))))
                        advanced = True
                        break
                    elif on_stack.get(w):
                        lowlink[v] = min(lowlink[v], index[w])
                if not advanced:
                    dfs_stack.pop()
                    if dfs_stack:
                        lowlink[dfs_stack[-1][0]] = min(
                            lowlink[dfs_stack[-1][0]], lowlink[v]
                        )
                    if lowlink[v] == index[v]:
                        scc: list[int] = []
                        while True:
                            w = scc_stack.pop()
                            on_stack[w] = False
                            scc.append(w)
                            if w == v:
                                break
                        if len(scc) > 1 or (
                            scc[0] in children and scc[0] in children[scc[0]]
                        ):
                            cyclic_sccs.append(scc)
        return cyclic_sccs

    @staticmethod
    def _bfs_path(
        start: int,
        end: int,
        scc_set: set[int],
        children: dict[int, list[int]],
        exclude_edge: tuple[int, int],
    ) -> list[int]:
        """BFS from start to end within scc_set, skipping one specific edge."""
        from collections import deque
        queue: deque[list[int]] = deque([[start]])
        visited = {start}
        while queue:
            path = queue.popleft()
            node = path[-1]
            for nbr in children.get(node, []):
                if (node, nbr) == exclude_edge:
                    continue
                if nbr == end:
                    return path + [end]
                if nbr in scc_set and nbr not in visited:
                    visited.add(nbr)
                    queue.append(path + [nbr])
        return [start, end]

    def check_and_remove_new_cycles(
        self, resource_code: str, first_new_rowid: int
    ) -> int:
        """Remove hypernym relations introduced by a file that create cycles.

        Identifies edges with rowid >= first_new_rowid that lie within a cyclic
        SCC, removes them and their auto-inverses, and logs a human-readable
        description suitable for filing upstream bug reports.

        Args:
            resource_code: Resource identifier for logging.
            first_new_rowid: Lowest synset_relation rowid assigned to this file.

        Returns:
            Number of cycle-causing edges removed.
        """
        hypernym_types = {r[0]: r[1] for r in self.cur.execute(
            "SELECT rowid, type FROM relation_types "
            "WHERE type IN ('hypernym', 'instance_hypernym')"
        ).fetchall()}
        if not hypernym_types:
            return 0

        ph = ','.join('?' * len(hypernym_types))
        rows = self.cur.execute(
            f"SELECT rowid, source_rowid, target_rowid, type_rowid "
            f"FROM synset_relations WHERE type_rowid IN ({ph})",
            list(hypernym_types),
        ).fetchall()
        if not rows:
            return 0

        children: dict[int, list[int]] = {}
        nodes: set[int] = set()
        edge_info: dict[tuple[int, int], tuple[int, int]] = {}  # (s,t)->(rowid, type_rowid)
        for rowid, src, tgt, tr in rows:
            children.setdefault(src, []).append(tgt)
            nodes.add(src)
            nodes.add(tgt)
            edge_info[(src, tgt)] = (rowid, tr)

        cyclic_sccs = self._tarjan_cyclic_sccs(nodes, children)
        if not cyclic_sccs:
            return 0

        removed = 0
        for scc in cyclic_sccs:
            scc_set = set(scc)
            for src in scc_set:
                for tgt in list(children.get(src, [])):
                    if tgt not in scc_set:
                        continue
                    edge_rowid, type_rowid = edge_info.get((src, tgt), (None, None))
                    if edge_rowid is None or edge_rowid < first_new_rowid:
                        continue
                    # This edge is from the new file and closes a cycle — remove it
                    rel_type = hypernym_types[type_rowid]
                    src_ili = self._synset_rowid_to_ili.get(src, f'#{src}')
                    tgt_ili = self._synset_rowid_to_ili.get(tgt, f'#{tgt}')
                    chain = self._bfs_path(tgt, src, scc_set, children, (src, tgt))
                    chain_str = ' → '.join(
                        self._synset_rowid_to_ili.get(n) or f'#{n}' for n in chain
                    )
                    logger.warning(
                        'Cycle removed [%s]: %s %s %s '
                        '(existing chain: %s → %s)',
                        resource_code, src_ili, rel_type, tgt_ili,
                        chain_str, src_ili,
                    )
                    self._conflicts['cycles'].append({
                        'xml_stem': resource_code,
                        'src': src_ili,
                        'rel': rel_type,
                        'tgt': tgt_ili,
                        'chain': [
                            self._synset_rowid_to_ili.get(n) or f'#{n}'
                            for n in chain
                        ],
                    })
                    # Remove the offending edge and its auto-inverse
                    self.cur.execute(
                        'DELETE FROM synset_relations WHERE rowid = ?',
                        (edge_rowid,),
                    )
                    self._synset_rel_keys.pop((src, tgt, type_rowid), None)
                    inv_name = INVERSE_CONCEPT_RELATIONS.get(rel_type)
                    if inv_name:
                        inv_rows = self.cur.execute(
                            'SELECT rowid FROM relation_types WHERE type = ?',
                            (inv_name,),
                        ).fetchone()
                        if inv_rows:
                            inv_type_rowid = inv_rows[0]
                            self.cur.execute(
                                'DELETE FROM synset_relations '
                                'WHERE source_rowid = ? AND target_rowid = ? '
                                'AND type_rowid = ?',
                                (tgt, src, inv_type_rowid),
                            )
                            self._synset_rel_keys.pop(
                                (tgt, src, inv_type_rowid), None
                            )
                    removed += 1
                    self.n_synset_rels -= 2
        return removed

    def write_conflicts_json(self, path: Path) -> None:
        """Write all collected conflict records to a structured JSON file.

        Args:
            path: Destination path (e.g. ``bin/relation_conflicts.json``).
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(self._conflicts, fh, indent=2, ensure_ascii=False)

    def detect_cycles(self) -> int:
        """Validate that no cycles remain in the IS-A hierarchy.

        Runs Tarjan's SCC on all hypernym edges; logs a WARNING per cyclic SCC.
        Returns the number of cyclic SCCs found (should be 0 after per-file
        cycle removal in Phase 1).
        """
        hypernym_types = {r[0] for r in self.cur.execute(
            "SELECT rowid FROM relation_types "
            "WHERE type IN ('hypernym', 'instance_hypernym')"
        ).fetchall()}
        if not hypernym_types:
            return 0

        ph = ','.join('?' * len(hypernym_types))
        edges = self.cur.execute(
            f"SELECT source_rowid, target_rowid FROM synset_relations "
            f"WHERE type_rowid IN ({ph})",
            list(hypernym_types),
        ).fetchall()

        children: dict[int, list[int]] = {}
        nodes: set[int] = set()
        for src, tgt in edges:
            children.setdefault(src, []).append(tgt)
            nodes.add(src)
            nodes.add(tgt)

        cyclic_sccs = self._tarjan_cyclic_sccs(nodes, children)
        for scc in cyclic_sccs:
            ili_map = dict(self.cur.execute(
                f"SELECT rowid, ili FROM synsets "
                f"WHERE rowid IN ({','.join('?' * len(scc))})",
                scc,
            ).fetchall())
            nodes_str = ', '.join(ili_map.get(n) or str(n) for n in scc)
            logger.warning('Cyclic SCC remaining after cleanup (%d nodes): %s',
                           len(scc), nodes_str)
        return len(cyclic_sccs)

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

        # Compute per-resource synset and sense coverage
        prov_senses_table = self._prov_table_rowid('senses')
        resource_senses: dict[str, list[int]] = {}
        for code, sense_rowid in self.prov_cur.execute(
            'SELECT pr.code, p.item_rowid FROM provenance p '
            'JOIN prov_resources pr ON p.resource_rowid = pr.rowid '
            'WHERE p.table_rowid = ?',
            (prov_senses_table,),
        ):
            resource_senses.setdefault(code, []).append(sense_rowid)

        if resource_senses:
            sense_to_synset = dict(self.cur.execute(
                'SELECT rowid, synset_rowid FROM senses'
            ).fetchall())
            for code, sense_rowids in resource_senses.items():
                synsets = len({
                    sense_to_synset[r] for r in sense_rowids if r in sense_to_synset
                })
                self.cur.execute(
                    'UPDATE resources SET synset_count = ?, sense_count = ? '
                    'WHERE code = ?',
                    (synsets, len(sense_rowids), code),
                )

        # Insert or update CILI resource with synset_count = all ILI-mapped concepts.
        # CILI may already appear in self._resources (from the XML header), so use
        # DELETE + INSERT to ensure the canonical metadata from CILI_RESOURCE wins.
        cili_synset_count = self.cur.execute(
            'SELECT COUNT(*) FROM synsets WHERE ili IS NOT NULL'
        ).fetchone()[0]
        self.cur.execute('DELETE FROM resources WHERE code = ?', (CILI_RESOURCE['code'],))
        self.cur.execute(
            'INSERT INTO resources '
            '(code, version, label, url, licence, citation, synset_count) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (CILI_RESOURCE['code'], CILI_RESOURCE['version'], CILI_RESOURCE['label'],
             CILI_RESOURCE['url'], CILI_RESOURCE['licence'], CILI_RESOURCE['citation'],
             cili_synset_count),
        )

    def create_indexes(self) -> None:
        """Create indexes before post-processing queries (critical for performance)."""
        self.cur.executescript(INDEXES)

    def finalize(self, db_path: Path, prov_db_path: Path) -> None:
        """Run ANALYZE and commit both databases."""
        print('Running ANALYZE...')
        self.cur.execute('ANALYZE')

        self.conn.commit()
        self.prov_conn.commit()
        self.conn.close()
        self.prov_conn.close()

        for path in (db_path, prov_db_path):
            size = path.stat().st_size / 1024 ** 2
            print(f'  {path} ({size:.1f} MB)')


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

