"""
Core merge logic for synthesising pre-converted Cygnet XML files into SQLite.
"""

import gzip
import itertools
import json
import logging
import re
import shutil
import sqlite3
import unicodedata
from pathlib import Path

import langcodes
from lxml import etree as ET

from cyg.converters import CONCEPT_REVERSE_RELATIONS, SENSE_REVERSE_RELATIONS

logger = logging.getLogger(__name__)

_COMBINING_RE = re.compile(r'[\u0300-\u036f\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f]')
# Full-width ASCII and half-width katakana: the only ranges fold_lemma()
# NFKC-normalises, so that compatibility characters elsewhere (\u03c1/\u03f1, m\u00b2/\u33a1)
# stay distinct.
_WIDTH_RE = re.compile(r'[\uff01-\uff5e\uff61-\uff9f]+')

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
    pronunciation TEXT NOT NULL,
    audio         TEXT
);
CREATE TABLE senses (
    rowid        INTEGER PRIMARY KEY,
    entry_rowid  INTEGER NOT NULL REFERENCES entries(rowid),
    synset_rowid INTEGER NOT NULL REFERENCES synsets(rowid),
    sense_index  INTEGER DEFAULT 1
);
CREATE TABLE definitions (
    rowid                 INTEGER PRIMARY KEY,
    synset_rowid          INTEGER NOT NULL REFERENCES synsets(rowid),
    definition            TEXT,
    language_rowid        INTEGER REFERENCES languages(rowid),
    translated_from_rowid INTEGER REFERENCES languages(rowid)
);
CREATE TABLE synset_relations (
    rowid         INTEGER PRIMARY KEY,
    source_rowid  INTEGER NOT NULL REFERENCES synsets(rowid),
    target_rowid  INTEGER NOT NULL REFERENCES synsets(rowid),
    type_rowid    INTEGER NOT NULL REFERENCES relation_types(rowid),
    -- A finer-grained sub-relation code the source gives beyond type_rowid
    -- (e.g. plWordnet's dc:type="plwn:56" on a relType="diminutive"
    -- relation), when there is one. NULL for the vast majority of
    -- relations, and for an inverse this builder generated itself.
    subtype       TEXT,
    -- Human-readable label for subtype (dc:description, or dc:relation for
    -- the one case that names the general relation family instead of the
    -- specific sub-code -- see plWordnet's "unresolved aspect orientation"
    -- export-compatibility relations), in the source's own language.
    subtype_label TEXT
);
CREATE TABLE sense_relations (
    rowid         INTEGER PRIMARY KEY,
    source_rowid  INTEGER NOT NULL REFERENCES senses(rowid),
    target_rowid  INTEGER NOT NULL REFERENCES senses(rowid),
    type_rowid    INTEGER NOT NULL REFERENCES relation_types(rowid),
    subtype       TEXT,
    subtype_label TEXT
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
CREATE TABLE core_synsets (
    synset_rowid INTEGER NOT NULL REFERENCES synsets(rowid)
);
CREATE TABLE build_info (
    version TEXT NOT NULL
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
CREATE TABLE build_info (
    version TEXT NOT NULL
);
"""

INDEXES = """
CREATE INDEX idx_forms_normalized  ON forms(normalized_form);
CREATE INDEX idx_forms_lower       ON forms(LOWER(form), rank, entry_rowid);
CREATE INDEX idx_senses_entry      ON senses(entry_rowid);
CREATE INDEX idx_senses_synset    ON senses(synset_rowid);
CREATE INDEX idx_definitions_synset        ON definitions(synset_rowid);
CREATE INDEX idx_synset_relations_source   ON synset_relations(source_rowid);
CREATE INDEX idx_sense_relations_source    ON sense_relations(source_rowid);
CREATE INDEX idx_sense_relations_target    ON sense_relations(target_rowid);
CREATE INDEX idx_sense_examples_sense      ON sense_examples(sense_rowid);
CREATE INDEX idx_example_annotations_example ON example_annotations(example_rowid);
CREATE INDEX idx_example_annotations_sense ON example_annotations(sense_rowid);
CREATE INDEX idx_definition_annotations_sense ON definition_annotations(sense_rowid);
CREATE INDEX idx_arasaac_synset ON arasaac(synset_rowid);
CREATE INDEX idx_core_synsets ON core_synsets(synset_rowid);
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
        'pages 50\u201357, Bucharest, Romania. Global Wordnet Association. '
        'DOI: 10.18653/v1/2016.gwc-1.9'	
    ),
}

INVERSE_SENSE_RELATIONS: dict[str, str] = {
    rel: inv for rel, inv in SENSE_REVERSE_RELATIONS.items() if inv is not None
}

# Resources with a documented, systematic hypernym-direction bug in their
# source data, ordered WORST FIRST, that ISN'T corrected automatically by
# cyg.converters.load_direction_swap_config() (e.g. a resource whose
# reversal rate falls short of that mechanism's threshold, or some other
# low-trust reason unrelated to a clean directional swap). Per-file cycle
# removal already catches most direction bugs when the reversed edge
# closes a cycle against relations from files merged before it; this list
# only affects resolve_residual_cycles()'s tie-break, for the cases that
# only become cyclic once a later file supplies the correct-direction edge
# for the same concept pair.
#
# Previously held ('UzWordnet-uz', 'own-pt'): both had ~100% reversed
# hypernym/instance_hypernym vs OEWN. Once scripts/check_all_relation_
# directions.py's pre-merge check and the automatic swap it drives were
# built (2026-10), both resources' hypernym direction is corrected before
# this stage ever runs, and a full rebuild confirmed neither originates a
# single cycle any more (bin/relation_conflicts.json: 0 entries in
# "cycles" for either, down from thousands) -- so they were removed.
#
# This list is curated by hand, not derived automatically: a resource's
# raw cycle/conflict count is not a reliable trust signal on its own, since
# a resource can rack up a high count simply by frequently intersecting
# with one genuinely bad resource (see scripts/trust_report.py's
# module docstring for a worked example of this trap). Add an entry only
# after reviewing that script's report across multiple builds and manually
# confirming the direction error via the per-SCC edge dump, not from the
# aggregate rate table alone -- and check first whether the automatic
# swap (above) should fix it at the source instead.
LOW_TRUST_RESOURCES: tuple[str, ...] = ()

INVERSE_CONCEPT_RELATIONS: dict[str, str] = {
    rel: inv for rel, inv in CONCEPT_REVERSE_RELATIONS.items() if inv is not None
}

BATCH_SIZE = 50_000

# Relation types that make up the IS-A hierarchy, which must stay acyclic.
HYPERNYM_TYPES = frozenset({'hypernym', 'instance_hypernym'})
HYPONYM_TYPES = frozenset({'hyponym', 'instance_hyponym'})

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def remove_accents(text: str) -> str:
    """Strip combining diacritics for accent-insensitive search."""
    if text.isascii():
        return text
    return _COMBINING_RE.sub('', unicodedata.normalize('NFD', text))


def normalize_form(text: str) -> str:
    """Canonicalize a wordform to NFC so byte-distinct encodings of the same
    text (e.g. precomposed vs. combining-mark diacritics from different
    source wordnets) are treated as identical for storage and deduplication.
    """
    if text.isascii():
        return text
    return unicodedata.normalize('NFC', text)


def fold_lemma(form: str) -> str:
    """Fold a lemma for case/width-variant matching.

    Lower-cases with full Unicode rules (Cyrillic, Greek and accented Latin
    included) and maps full-width ASCII and half-width katakana to their
    usual forms.  Nothing else is folded: accents, hyphens, spaces, kana
    spelling and kanji variants all distinguish forms.

    Args:
        form: The written form.

    Returns:
        The folded form, e.g. 'vip' for 'ＶＩＰ' and 'ガス' for 'ｶﾞｽ'.
    """
    form = _WIDTH_RE.sub(lambda m: unicodedata.normalize('NFKC', m.group()), form)
    return form.lower()


def reading_clusters(signatures: dict[int, frozenset]) -> list[list[int]]:
    """Partition entries into groups whose readings are compatible.

    An entry's signature is its set of non-lemma forms and pronunciations.
    Two signatures are compatible when one contains the other (an empty
    signature is compatible with any).  Each entry joins the one maximal
    signature that contains its own; an entry compatible with several
    maximal signatures (e.g. a bare 物 beside 物/もの and 物/ぶつ) is
    ambiguous and only groups with entries of the same signature.

    Args:
        signatures: Entry rowid -> signature.

    Returns:
        Lists of entry rowids, each sorted, in order of their first rowid.
    """
    distinct = set(signatures.values())
    maximal = [s for s in distinct if not any(s < t for t in distinct)]
    clusters: dict[frozenset, list[int]] = {}
    for entry, sig in sorted(signatures.items()):
        homes = [m for m in maximal if sig <= m]
        clusters.setdefault(homes[0] if len(homes) == 1 else sig, []).append(entry)
    return sorted(clusters.values())


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
        # Likewise for the pronunciations of an existing form when a lexeme
        # from a later wordnet merges into one already loaded.
        self.cur.execute(
            'CREATE INDEX idx_pronunciations_form ON pronunciations(form_rowid)'
        )

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
        self._gloss_keys: dict[tuple[int, int], int] = {}
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

        # The hypernym graph, kept in memory so that the per-file cycle check
        # only has to search from the new edges (see check_and_remove_new_cycles).
        # Hypernym rows are deleted during Phase 1 only by that check.
        self._hyp_children: dict[int, list[int]] = {}
        # One concept pair can have several rows (e.g. both 'hypernym' and
        # 'instance_hypernym'): (src, tgt) -> [(rowid, type_rowid), ...]
        self._hyp_pair_rows: dict[tuple[int, int], list[tuple[int, int]]] = {}
        self._hyp_type_names: dict[int, str] = {}
        # Hypernym rows buffered since the last cycle check:
        # (rowid, src, tgt, type_rowid)
        self._hyp_new: list[tuple[int, int, int, int]] = []
        # Rowids of hyponym rows, so that the inverse of a removed hypernym can
        # be deleted by rowid: synset_relations has no index during Phase 1.
        self._hypo_rowids: dict[tuple[int, int, int], int] = {}

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
                # langcodes doesn't raise for a code it can't resolve — it
                # returns a descriptive placeholder string instead (e.g.
                # "Unknown language [ua]"). Treat that the same as an
                # exception rather than storing it as if it were a real
                # name: a source using an invalid/non-standard code (e.g.
                # 'ua' instead of ISO 639-1 'uk' for Ukrainian) would
                # otherwise have that placeholder displayed everywhere.
                if name == f'Unknown language [{code}]':
                    name = None
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
        # Called once per item (~10M times), so look the caches up directly.
        table_rowid = (self._prov_table_cache.get(table_name)
                       or self._prov_table_rowid(table_name))
        resources = self._prov_resource_cache
        append = self._prov_buf.append
        count = 0
        for p in prov_elems:
            resource = p.get('resource')
            if resource:
                append((
                    table_rowid, item_rowid,
                    resources.get(resource) or self._prov_resource_rowid(resource),
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
            # Merge: same lexeme in a second wordnet — add new wordforms and
            # any new pronunciations (including audio) for existing wordforms.
            entry_rowid = self.entry_id_to_rowid[lid]
            existing_rows = self.cur.execute(
                'SELECT rowid, form, rank FROM forms WHERE entry_rowid = ?', (entry_rowid,)
            ).fetchall()
            existing = {form: rowid for rowid, form, _rank in existing_rows}
            new_rank = (max(r for _, _, r in existing_rows) + 1) if existing_rows else 0
            for wf in elem.findall('Wordform'):
                form = wf.get('form')
                if not form:
                    continue
                form = normalize_form(form)
                if form not in existing:
                    fid = self._next_form_id
                    self._next_form_id += 1
                    self._forms_buf.append(
                        (fid, entry_rowid, form, remove_accents(form.lower()), new_rank)
                    )
                    existing[form] = fid
                    new_rank += 1
                    self.n_forms += 1
                    for p in wf.findall('Pronunciation'):
                        audio = p.get('audio')
                        if p.text or audio:
                            pid = self._next_pronunciation_id
                            self._next_pronunciation_id += 1
                            self._pronunciations_buf.append(
                                (pid, fid, p.get('variety'), p.text or '', audio)
                            )
                            self.n_pronunciations += 1
                else:
                    # Form exists — add any pronunciations not already present.
                    # Audio-only records attach to the single existing IPA entry
                    # when unambiguous, rather than adding a redundant record.
                    fid = existing[form]
                    self._flush_pronunciations()
                    existing_prons = self.cur.execute(
                        'SELECT variety, pronunciation, audio FROM pronunciations'
                        ' WHERE form_rowid = ?', (fid,)
                    ).fetchall()
                    for p in wf.findall('Pronunciation'):
                        audio = p.get('audio')
                        ptext = p.text or ''
                        variety = p.get('variety')
                        # Skip if exact record already exists
                        if any(ev == variety and ep == ptext for ev, ep, _ in existing_prons):
                            continue
                        # Audio-only: attach to the single existing entry without audio
                        if audio and not ptext:
                            without_audio = [(ev, ep, ea) for ev, ep, ea in existing_prons
                                             if not ea]
                            if len(without_audio) == 1:
                                self.cur.execute(
                                    'UPDATE pronunciations SET audio = ?'
                                    ' WHERE form_rowid = ? AND variety IS ? AND pronunciation = ?',
                                    (audio, fid, without_audio[0][0], without_audio[0][1])
                                )
                                # Reflect the update in local cache
                                ev, ep, _ = without_audio[0]
                                existing_prons = [(v, t, a if (v != ev or t != ep) else audio)
                                                  for v, t, a in existing_prons]
                                continue
                        if ptext or audio:
                            pid = self._next_pronunciation_id
                            self._next_pronunciation_id += 1
                            self._pronunciations_buf.append((pid, fid, variety, ptext, audio))
                            existing_prons.append((variety, ptext, audio))
                            self.n_pronunciations += 1
            self.n_prov += self._insert_prov(
                'entries', entry_rowid, elem.findall('Provenance')
            )
            return

        # Build form rows first; skip entry entirely if no valid forms
        seen: set[str] = set()
        form_rows: list[tuple] = []
        pron_rows: list[tuple] = []  # (fid, variety, text, audio)
        for wf in elem.findall('Wordform'):
            form = wf.get('form')
            if form:
                form = normalize_form(form)
            if form and form not in seen:
                seen.add(form)
                fid = self._next_form_id
                self._next_form_id += 1
                form_rows.append(
                    (fid, 0, form, remove_accents(form.lower()), len(form_rows))
                )
                for p in wf.findall('Pronunciation'):
                    audio = p.get('audio')
                    if p.text or audio:
                        pron_rows.append((fid, p.get('variety'), p.text or '', audio))
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

        for fid, variety, text, audio in pron_rows:
            pid = self._next_pronunciation_id
            self._next_pronunciation_id += 1
            self._pronunciations_buf.append((pid, fid, variety, text, audio))
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
            self.n_prov += self._insert_prov(
                'definitions', self._gloss_keys[key], elem.findall('Provenance')
            )
            return
        sentence = elem.find('AnnotatedSentence')
        text, annotations = parse_annotated_sentence(sentence)
        def_rowid = self._next_def_id
        self._next_def_id += 1
        self._gloss_keys[key] = def_rowid
        translated_from = elem.get('translated_from')
        translated_from_rowid = self._lang_rowid(translated_from) if translated_from else None
        self._defs_buf.append(
            (def_rowid, synset_rowid, text, lang_rowid, translated_from_rowid)
        )
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

        subtype = elem.get('subtype')
        subtype_label = elem.get('subtype_label')
        prov_elems = elem.findall('Provenance')
        current_resource = prov_elems[0].get('resource') if prov_elems else ''
        type_rowid = self._rel_type_rowid(rel_type)
        # Unknown types (e.g. a resource's own relType="other" + dc:type
        # subtype, like plWordnet's "fuzzynimia"/"bliskoznaczność") default
        # to symmetric, same as 'other' itself (mapped to None -- no fixed
        # inverse -- in SENSE_REVERSE_RELATIONS) -- a type we have no
        # curated inverse for is far more often a resource's own reciprocal
        # sub-relation (declared both ways on purpose) than a genuinely
        # directional one we just haven't catalogued yet.
        inv = INVERSE_SENSE_RELATIONS.get(rel_type, rel_type)

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
            self._sense_rels_buf.append(
                (sr_rowid, source, target, type_rowid, subtype, subtype_label))
            self.n_prov += self._insert_prov('sense_relations', sr_rowid, prov_elems)
            self.n_sense_rels += 1

        if inv:
            inv_type = self._rel_type_rowid(inv)
            inv_key = (target, source, inv_type)
            if inv_key not in self._sense_rel_keys:
                self._sense_rel_keys[inv_key] = ''
                inv_rowid = self._next_sense_rel_id
                self._next_sense_rel_id += 1
                self._sense_rels_buf.append(
                    (inv_rowid, target, source, inv_type, None, None))
                self.n_sense_rels += 1

    def _do_concept_relation(self, elem) -> None:
        rel_type = elem.get('relation_type')
        source = self.synset_id_to_rowid.get(elem.get('source'))
        target = self.synset_id_to_rowid.get(elem.get('target'))
        if not source or not target:
            return

        subtype = elem.get('subtype')
        subtype_label = elem.get('subtype_label')
        prov_elems = elem.findall('Provenance')
        current_resource = prov_elems[0].get('resource') if prov_elems else ''
        type_rowid = self._rel_type_rowid(rel_type)
        # See the matching comment in _do_sense_relation: unknown types
        # default to symmetric rather than directional.
        inv = INVERSE_CONCEPT_RELATIONS.get(rel_type, rel_type)

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
            self._synset_rels_buf.append(
                (cr_rowid, source, target, type_rowid, subtype, subtype_label))
            self.n_prov += self._insert_prov('synset_relations', cr_rowid, prov_elems)
            self.n_synset_rels += 1
            if rel_type in HYPERNYM_TYPES:
                self._track_hypernym(cr_rowid, source, target, type_rowid, rel_type)
            elif rel_type in HYPONYM_TYPES:
                self._hypo_rowids[key] = cr_rowid

        if inv:
            inv_type = self._rel_type_rowid(inv)
            inv_key = (target, source, inv_type)
            if inv_key not in self._synset_rel_keys:
                self._synset_rel_keys[inv_key] = ''
                inv_rowid = self._next_synset_rel_id
                self._next_synset_rel_id += 1
                self._synset_rels_buf.append(
                    (inv_rowid, target, source, inv_type, None, None))
                self.n_synset_rels += 1
                if inv in HYPERNYM_TYPES:
                    self._track_hypernym(inv_rowid, target, source, inv_type, inv)
                elif inv in HYPONYM_TYPES:
                    self._hypo_rowids[inv_key] = inv_rowid

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
                '(rowid, form_rowid, variety, pronunciation, audio) VALUES (?, ?, ?, ?, ?)',
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
                '(rowid, synset_rowid, definition, language_rowid, translated_from_rowid) '
                'VALUES (?, ?, ?, ?, ?)',
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
                '(rowid, source_rowid, target_rowid, type_rowid, subtype, subtype_label) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                self._sense_rels_buf,
            )
            self._sense_rels_buf.clear()

    def _flush_synset_rels(self) -> None:
        if self._synset_rels_buf:
            self.cur.executemany(
                'INSERT INTO synset_relations '
                '(rowid, source_rowid, target_rowid, type_rowid, subtype, subtype_label) '
                'VALUES (?, ?, ?, ?, ?, ?)',
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

    def merge_case_variants(self) -> dict[str, int]:
        """
        Consolidate senses where variant lexemes map to the same synset.

        Entries in the same language and part of speech whose lemmas are
        equal under fold_lemma() (e.g. 'Bank'/'bank', 'ＶＩＰ'/'VIP') and
        that have a sense for the same concept are merged: all wordforms
        move to the lowest-rowid entry, the duplicate sense is deleted, and
        all references are updated.  Entries whose readings differ
        (homographs such as 物 もの/ぶつ) are left apart; see
        reading_clusters().

        Returns:
            Senses removed, as {'identical': n, 'variant': n}: 'identical'
            for entries with exactly the same lemma, 'variant' for case or
            width variants.
        """
        self.conn.create_function('fold_lemma', 1, fold_lemma, deterministic=True)
        groups = self.cur.execute("""
            SELECT GROUP_CONCAT(s.rowid || ':' || e.rowid)
            FROM senses s
            JOIN entries e ON s.entry_rowid = e.rowid
            JOIN forms   f ON f.entry_rowid = e.rowid AND f.rank = 0
            GROUP BY fold_lemma(f.form), e.language_rowid, e.pos, s.synset_rowid
            HAVING COUNT(*) > 1
        """).fetchall()

        removed = {'identical': 0, 'variant': 0}
        for (members,) in groups:
            sense_of: dict[int, int] = {}
            for pair in members.split(','):
                sense_rowid, entry_rowid = map(int, pair.split(':'))
                if sense_rowid < sense_of.get(entry_rowid, sense_rowid + 1):
                    sense_of[entry_rowid] = sense_rowid
            if len(sense_of) < 2:
                continue
            for cluster in reading_clusters(self._reading_signatures(list(sense_of))):
                if len(cluster) < 2:
                    continue
                keep_entry, *others = cluster
                identical, variant = self._merge_variant_senses(
                    keep_entry, sense_of[keep_entry],
                    [(sense_of[e], e) for e in others],
                )
                removed['identical'] += identical
                removed['variant'] += variant

        # Dedup any sense_relations that became identical after the redirect
        self.cur.execute("""
            DELETE FROM sense_relations WHERE rowid NOT IN (
                SELECT MIN(rowid)
                FROM sense_relations
                GROUP BY source_rowid, target_rowid, type_rowid
            )
        """)
        return removed

    def _reading_signatures(self, entries: list[int]) -> dict[int, frozenset]:
        """Return each entry's non-lemma forms and pronunciations.

        Forms that only vary the lemma (e.g. 'bank' moved onto 'Bank' by an
        earlier merge) are not readings and are left out.

        Args:
            entries: Entry rowids.

        Returns:
            Entry rowid -> frozenset of ('form', text) and ('pron', text) items.
        """
        sigs: dict[int, set] = {e: set() for e in entries}
        marks = ','.join('?' * len(entries))
        rows = self.cur.execute(f"""
            SELECT f.entry_rowid, f.form, f.rank, p.pronunciation
            FROM forms f
            LEFT JOIN pronunciations p ON p.form_rowid = f.rowid
            WHERE f.entry_rowid IN ({marks})
        """, entries).fetchall()
        lemma_folds = {(e, fold_lemma(form)) for e, form, rank, _ in rows if rank == 0}
        for entry_rowid, form, rank, pron in rows:
            if rank > 0 and (entry_rowid, fold_lemma(form)) not in lemma_folds:
                sigs[entry_rowid].add(('form', form))
            if pron:
                sigs[entry_rowid].add(('pron', pron))
        return {e: frozenset(s) for e, s in sigs.items()}

    def _merge_variant_senses(
        self, keep_entry: int, keep_sense: int,
        variants: list[tuple[int, int]],
    ) -> tuple[int, int]:
        """Merge variant entries' senses into one surviving sense.

        Args:
            keep_entry: Entry that receives the variants' wordforms.
            keep_sense: keep_entry's sense, which survives.
            variants: (sense rowid, entry rowid) pairs to merge and delete.

        Returns:
            Senses removed whose lemma was identical to keep_entry's, and
            those that were case or width variants of it.
        """
        identical_count = 0
        prov_senses_rowid = self._prov_table_rowid('senses')
        rows = self.cur.execute(
            'SELECT form, rank FROM forms WHERE entry_rowid = ?', (keep_entry,)
        ).fetchall()
        existing_forms = {form for form, _ in rows}
        lemmas = {form for form, rank in rows if rank == 0}
        next_rank = max(rank for _, rank in rows) + 1

        for sense_rowid, entry_rowid in variants:
            forms = self.cur.execute(
                'SELECT rowid, form, normalized_form, rank FROM forms '
                'WHERE entry_rowid = ? ORDER BY rank',
                (entry_rowid,),
            ).fetchall()
            identical_count += any(r == 0 and f in lemmas for _, f, _, r in forms)

            # Move wordforms from the non-canonical entry to keep_entry,
            # ranked after its own so that it keeps a single lemma
            for old_form_rowid, form, norm_form, _rank in forms:
                if form not in existing_forms:
                    self.cur.execute(
                        'INSERT INTO forms '
                        '(entry_rowid, form, normalized_form, rank) '
                        'VALUES (?, ?, ?, ?)',
                        (keep_entry, form, norm_form, next_rank),
                    )
                    next_rank += 1
                    new_form_rowid = self.cur.lastrowid
                    existing_forms.add(form)
                    self.cur.execute(
                        'INSERT INTO pronunciations (form_rowid, variety, pronunciation, audio)'
                        ' SELECT ?, variety, pronunciation, audio'
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
        return identical_count, len(variants) - identical_count

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

    def _track_hypernym(
        self, rowid: int, src: int, tgt: int, type_rowid: int, rel_type: str
    ) -> None:
        """Note a buffered hypernym row for the next cycle check."""
        self._hyp_type_names[type_rowid] = rel_type
        self._hyp_new.append((rowid, src, tgt, type_rowid))

    def check_and_remove_new_cycles(
        self, resource_code: str, first_new_rowid: int
    ) -> int:
        """Remove hypernym relations introduced by a file that create cycles.

        Identifies edges with rowid >= first_new_rowid that lie within a cyclic
        SCC, removes them and their auto-inverses, and logs a human-readable
        description suitable for filing upstream bug reports.

        The graph was acyclic before the file, so every new cycle contains one
        of its edges, and the SCC of that edge is reachable from the edge's
        source.  Tarjan therefore only starts from the new edges' sources,
        rather than covering the whole graph.

        Args:
            resource_code: Resource identifier for logging.
            first_new_rowid: Lowest synset_relation rowid assigned to this file.

        Returns:
            Number of cycle-causing edges removed.
        """
        children, pair_rows = self._hyp_children, self._hyp_pair_rows
        for rowid, src, tgt, type_rowid in self._hyp_new:
            if (src, tgt) not in pair_rows:
                children.setdefault(src, []).append(tgt)
            pair_rows.setdefault((src, tgt), []).append((rowid, type_rowid))
        starts = {src for rowid, src, _, _ in self._hyp_new if rowid >= first_new_rowid}
        self._hyp_new.clear()
        if not starts:
            return 0

        # Log every removal against the graph as it was, then update it.
        doomed: list[tuple[int, int, int, int]] = []
        for scc in self._tarjan_cyclic_sccs(starts, children):
            scc_set = set(scc)
            for src in scc_set:
                for tgt in children.get(src, []):
                    if tgt not in scc_set:
                        continue
                    for edge_rowid, type_rowid in pair_rows[(src, tgt)]:
                        if edge_rowid < first_new_rowid:
                            continue
                        chain = self._bfs_path(tgt, src, scc_set, children, (src, tgt))
                        edge = (edge_rowid, src, tgt, type_rowid)
                        self._remove_cycle_edge(resource_code, edge, chain)
                        doomed.append(edge)
        for edge_rowid, src, tgt, type_rowid in doomed:
            rows = pair_rows[(src, tgt)]
            rows.remove((edge_rowid, type_rowid))
            if not rows:
                del pair_rows[(src, tgt)]
                children[src].remove(tgt)
        return len(doomed)

    def _remove_cycle_edge(
        self, resource_code: str, edge: tuple[int, int, int, int], chain: list[int],
    ) -> None:
        """Delete one cycle-closing hypernym row and its auto-inverse; log it.

        Args:
            resource_code: Resource identifier for logging.
            edge: (rowid, source, target, type_rowid) of the row to delete.
            chain: The existing path from target back to source.
        """
        edge_rowid, src, tgt, type_rowid = edge
        rel_type = self._hyp_type_names[type_rowid]
        src_ili = self._synset_rowid_to_ili.get(src, f'#{src}')
        tgt_ili = self._synset_rowid_to_ili.get(tgt, f'#{tgt}')
        chain_ilis = [self._synset_rowid_to_ili.get(n) or f'#{n}' for n in chain]
        logger.warning(
            'Cycle removed [%s]: %s %s %s (existing chain: %s → %s)',
            resource_code, src_ili, rel_type, tgt_ili,
            ' → '.join(chain_ilis), src_ili,
        )
        self._conflicts['cycles'].append({
            'xml_stem': resource_code,
            'src': src_ili,
            'rel': rel_type,
            'tgt': tgt_ili,
            'chain': chain_ilis,
        })
        self.cur.execute(
            'DELETE FROM synset_relations WHERE rowid = ?', (edge_rowid,)
        )
        self._synset_rel_keys.pop((src, tgt, type_rowid), None)
        self.n_synset_rels -= 1
        inv_name = INVERSE_CONCEPT_RELATIONS.get(rel_type)
        if not inv_name:
            return
        inv_key = (tgt, src, self._rel_type_rowid(inv_name))
        inv_rowid = self._hypo_rowids.pop(inv_key, None)
        if inv_rowid is not None:
            self.cur.execute(
                'DELETE FROM synset_relations WHERE rowid = ?', (inv_rowid,)
            )
            self.n_synset_rels -= self.cur.rowcount
        self._synset_rel_keys.pop(inv_key, None)

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
        Returns the number of cyclic SCCs found. This should always be 0
        after check_and_remove_new_cycles() has run for every file: if the
        graph was acyclic before a file, any new cycle uses one of that
        file's rows, and all such rows are removed.
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

    def load_core_synsets(self, tab_path: Path | None = None) -> int:
        """Populate core_synsets from a tab file of ILI codes (one per line).

        Args:
            tab_path: Path to the ILI tab file. Defaults to
                      ``cyg/data/wn-core-ili.tab`` relative to this file.

        Returns:
            Number of core synsets inserted.
        """
        if tab_path is None:
            tab_path = Path(__file__).parent / 'data' / 'wn-core-ili.tab'
        if not tab_path.exists():
            logger.warning('Core ILI file not found: %s', tab_path)
            return 0
        ilis = {
            line.strip() for line in tab_path.read_text().splitlines() if line.strip()
        }
        rows = self.cur.execute(
            'SELECT rowid FROM synsets WHERE ili IN (%s)' % ','.join('?' * len(ilis)),
            list(ilis),
        ).fetchall()
        self.cur.executemany(
            'INSERT INTO core_synsets (synset_rowid) VALUES (?)',
            rows,
        )
        return len(rows)

    def write_wordnet_summaries(
        self, out_dir: Path, direction_swaps_path: Path | None = None,
    ) -> int:
        """Precompute per-resource stats for the Wordnets-tab detail view.

        This used to be a live query the browser ran every time a visitor
        opened a wordnet's detail page: build a temp table of that
        resource's sense rowids inside the WASM-loaded cygnet.db, having
        first fetched them from the separately-loaded provenance.db, then
        run several window-function queries against it. For a large
        resource (millions of provenance rows) that was slow, and it
        forced loading the (large) provenance database just to view a
        handful of summary numbers that are rarely even looked at.

        Doing it once here, natively, removes both costs: one JSON file
        per resource is written to *out_dir*, and the browser no longer
        needs provenance.db at all for this view.

        Args:
            out_dir: Directory to write ``<resource_code>.json`` files into.
            direction_swaps_path: Optional path to the swap config written
                by scripts/check_all_relation_directions.py
                --write-swap-config (see cyg.converters.
                load_direction_swap_config). When given, each resource's
                summary gets a ``directionSwaps`` list disclosing which
                relation types were auto-corrected before merging, and
                why (reversed/comparable counts vs. OEWN).

        Returns:
            Number of summary files written.
        """
        direction_swaps_by_code: dict[str, list[dict]] = {}
        if direction_swaps_path is not None and direction_swaps_path.exists():
            swap_data = json.loads(direction_swaps_path.read_text(encoding='utf-8'))
            for res_code, relations in swap_data.items():
                direction_swaps_by_code[res_code] = [
                    {
                        'relationType': rel_type,
                        'reversed': info['reversed'],
                        'comparable': info['comparable'],
                        'reversedShare': info['reversed_share'],
                    }
                    for rel_type, info in relations.items()
                    if info.get('comparable') is not None
                ]
        # Symmetric relations (e.g. 'similar': 'similar') map to themselves
        # and must be kept -- only exclude the *other* side of a genuinely
        # directional pair (e.g. 'hypernym': 'hyponym' excludes 'hyponym').
        inverse_synset_rels = {
            v for k, v in INVERSE_CONCEPT_RELATIONS.items() if k != v
        }
        inverse_sense_rels = {
            v for k, v in INVERSE_SENSE_RELATIONS.items() if k != v
        }
        has_core = self.cur.execute(
            'SELECT COUNT(*) FROM core_synsets'
        ).fetchone()[0] > 0
        core_ex_join = (
            'LEFT JOIN core_synsets cs ON cs.synset_rowid = syn.rowid' if has_core else ''
        )
        core_ex_order = '(cs.synset_rowid IS NULL),' if has_core else ''
        core_rel_join = (
            'LEFT JOIN core_synsets cs ON cs.synset_rowid = sr.source_rowid' if has_core else ''
        )
        core_rel_order = '(cs.synset_rowid IS NULL),' if has_core else ''

        senses_table = self._prov_table_rowid('senses')
        defs_table = self._prov_table_rowid('definitions')

        # One pass over each provenance subset, grouped by resource in
        # Python -- avoids re-scanning provenance.db once per resource
        # (there is no index on resource_rowid).
        resource_senses: dict[str, list[int]] = {}
        for code, item_rowid in self.prov_cur.execute(
            'SELECT pr.code, p.item_rowid FROM provenance p '
            'JOIN prov_resources pr ON p.resource_rowid = pr.rowid '
            'WHERE p.table_rowid = ?',
            (senses_table,),
        ):
            resource_senses.setdefault(code, set()).add(item_rowid)

        def_counts: dict[str, int] = dict(self.prov_cur.execute(
            'SELECT pr.code, COUNT(*) FROM provenance p '
            'JOIN prov_resources pr ON p.resource_rowid = pr.rowid '
            'WHERE p.table_rowid = ? GROUP BY pr.code',
            (defs_table,),
        ).fetchall())

        lang_by_code: dict[str, str] = dict(self.cur.execute(
            'SELECT r.code, l.code FROM resources r '
            'LEFT JOIN languages l ON l.rowid = r.language_rowid'
        ).fetchall())

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for stale in out_dir.glob('*.json'):
            stale.unlink()
        self.cur.execute('CREATE TEMP TABLE _sum_senses (id INTEGER PRIMARY KEY)')
        n_written = 0

        for code, sense_rowids in resource_senses.items():
            self.cur.execute('DELETE FROM _sum_senses')
            self.cur.executemany(
                'INSERT OR IGNORE INTO _sum_senses VALUES (?)',
                [(r,) for r in sense_rowids],
            )

            pos_rows = self.cur.execute("""
                WITH src AS (
                    SELECT DISTINCT s.synset_rowid
                    FROM senses s WHERE s.rowid IN (SELECT id FROM _sum_senses)
                )
                SELECT syn.pos, COUNT(*) AS cnt,
                       SUM(CASE WHEN syn.ili IS NOT NULL THEN 1 ELSE 0 END) AS with_ili
                FROM src JOIN synsets syn ON syn.rowid = src.synset_rowid
                GROUP BY syn.pos ORDER BY cnt DESC
            """).fetchall()
            pos_stats = [
                {'pos': pos, 'cnt': cnt, 'withIli': with_ili}
                for pos, cnt, with_ili in pos_rows
            ]
            total = sum(r['cnt'] for r in pos_stats)
            with_ili_total = sum(r['withIli'] for r in pos_stats)
            ili_pct = round(100 * with_ili_total / total) if total else None

            ex_count = self.cur.execute("""
                SELECT COUNT(*) FROM sense_examples se
                WHERE se.sense_rowid IN (SELECT id FROM _sum_senses)
            """).fetchone()[0]

            lang_code = lang_by_code.get(code) or 'en'

            example_rows = self.cur.execute(f"""
                WITH src_senses AS (
                    SELECT s.rowid, s.synset_rowid, s.sense_index, s.entry_rowid
                    FROM senses s WHERE s.rowid IN (SELECT id FROM _sum_senses)
                ),
                ranked AS (
                    SELECT syn.pos, f.form, syn.rowid AS synset_rowid,
                           ROW_NUMBER() OVER (
                               PARTITION BY syn.pos
                               ORDER BY {core_ex_order}
                                        (CASE WHEN UNICODE(SUBSTR(f.form,1,1)) BETWEEN 97 AND 122
                                                   OR UNICODE(SUBSTR(f.form,1,1)) >= 128 THEN 0 ELSE 1 END),
                                        ss.sense_index, f.form
                           ) AS rn
                    FROM src_senses ss
                    JOIN synsets syn ON syn.rowid = ss.synset_rowid
                    JOIN entries e ON e.rowid = ss.entry_rowid
                    JOIN forms f ON f.entry_rowid = e.rowid AND f.rank = 0
                    JOIN languages l ON l.rowid = e.language_rowid
                    {core_ex_join}
                    WHERE l.code = ?
                )
                SELECT pos, form, synset_rowid FROM ranked WHERE rn <= 5 ORDER BY pos, rn
            """, (lang_code,)).fetchall()
            examples = [
                {'pos': pos, 'form': form, 'synset_rowid': syn_rowid}
                for pos, form, syn_rowid in example_rows
            ]

            rel_rows = self.cur.execute(f"""
                WITH src AS (
                    SELECT DISTINCT s.synset_rowid
                    FROM senses s WHERE s.rowid IN (SELECT id FROM _sum_senses)
                ),
                rels AS (
                    SELECT rt.type, sr.source_rowid, sr.target_rowid,
                           COUNT(*) OVER (PARTITION BY rt.type) AS cnt,
                           ROW_NUMBER() OVER (
                               PARTITION BY rt.type
                               ORDER BY {core_rel_order}
                                        (CASE WHEN NOT EXISTS(
                                            SELECT 1 FROM senses s2
                                            JOIN entries e2 ON e2.rowid = s2.entry_rowid
                                            JOIN languages l2 ON l2.rowid = e2.language_rowid
                                            WHERE s2.synset_rowid = sr.target_rowid AND l2.code = ?
                                        ) THEN 1 ELSE 0 END),
                                        sr.rowid
                           ) AS rn
                    FROM synset_relations sr
                    JOIN src ON src.synset_rowid = sr.source_rowid
                    JOIN relation_types rt ON rt.rowid = sr.type_rowid
                    {core_rel_join}
                )
                SELECT type, cnt, source_rowid, target_rowid
                FROM rels WHERE rn = 1
                ORDER BY cnt DESC
            """, (lang_code,)).fetchall()
            rel_dist = [
                {'type': t, 'cnt': cnt, 'ex_src_rowid': src, 'ex_tgt_rowid': tgt}
                for t, cnt, src, tgt in rel_rows if t not in inverse_synset_rels
            ]

            sense_rel_rows = self.cur.execute("""
                WITH rels AS (
                    SELECT rt.type,
                           sr.source_rowid, sr.target_rowid,
                           COUNT(*) OVER (PARTITION BY rt.type) AS cnt,
                           ROW_NUMBER() OVER (PARTITION BY rt.type ORDER BY sr.rowid) AS rn
                    FROM sense_relations sr
                    JOIN _sum_senses ws ON ws.id = sr.source_rowid
                    JOIN relation_types rt ON rt.rowid = sr.type_rowid
                )
                SELECT r.type, r.cnt, r.source_rowid, r.target_rowid,
                       (SELECT f.form FROM forms f WHERE f.entry_rowid =
                           (SELECT entry_rowid FROM senses WHERE rowid = r.source_rowid) AND f.rank = 0 LIMIT 1) AS src_form,
                       (SELECT f.form FROM forms f WHERE f.entry_rowid =
                           (SELECT entry_rowid FROM senses WHERE rowid = r.target_rowid) AND f.rank = 0 LIMIT 1) AS tgt_form
                FROM rels r WHERE rn = 1
                ORDER BY r.cnt DESC
            """).fetchall()
            sense_rel_dist = [
                {
                    'type': t, 'cnt': cnt, 'ex_src_rowid': src, 'ex_tgt_rowid': tgt,
                    'src_form': src_form, 'tgt_form': tgt_form,
                }
                for t, cnt, src, tgt, src_form, tgt_form in sense_rel_rows
                if t not in inverse_sense_rels
            ]

            summary = {
                'posStats': pos_stats,
                'iliPct': ili_pct,
                'defCount': def_counts.get(code),
                'exCount': ex_count,
                'examples': examples,
                'relDist': rel_dist,
                'senseRelDist': sense_rel_dist,
                'directionSwaps': direction_swaps_by_code.get(code, []),
            }
            (out_dir / f'{code}.json').write_text(
                json.dumps(summary, ensure_ascii=False, separators=(',', ':')),
                encoding='utf-8',
            )
            n_written += 1

        self.cur.execute('DROP TABLE _sum_senses')
        return n_written

    def write_search_index(self, out_dir: Path, n_shards: int = 500) -> dict:
        """Precompute a static word-search index for the browser's
        dominant search path, sharded by alphabetic range of
        ``normalized_form`` so a glob query only needs to fetch the
        shards whose range overlaps its prefix.

        Each shard is a JSON object keyed by ``normalized_form``, whose
        values are compact match rows (see ``adaptStaticSearchRow()`` in
        web/index.html for the read-time adapter back to
        ``performSearch()``'s full field names): ``sid`` (sense rowid),
        ``syn`` (synset rowid), ``lg`` (language code), ``pos``, ``ind``
        (sense_index), ``l`` (lemma), ``rk`` (rank).

        A shard boundary only ever falls *between* distinct
        ``normalized_form`` keys -- never splits one form's match list.

        Args:
            out_dir: Directory to write shard files and manifest.json into.
            n_shards: Target shard count (actual count may differ
                slightly to avoid splitting a form's matches).

        Returns:
            Stats dict with ``shards``, ``forms``, and ``rows`` counts.
        """
        rows = self.cur.execute("""
            SELECT f.normalized_form, s.rowid, syn.rowid, l.code, syn.pos,
                   s.sense_index, f.form, f.rank
            FROM forms f
            JOIN entries e ON f.entry_rowid = e.rowid
            JOIN senses s ON s.entry_rowid = e.rowid
            JOIN synsets syn ON s.synset_rowid = syn.rowid
            JOIN languages l ON e.language_rowid = l.rowid
            ORDER BY f.normalized_form
        """).fetchall()

        by_form: dict[str, list[dict]] = {}
        for norm_form, sid, syn, lg, pos, ind, lemma, rank in rows:
            by_form.setdefault(norm_form, []).append({
                'sid': sid, 'syn': syn, 'lg': lg, 'pos': pos,
                'ind': ind, 'l': lemma, 'rk': rank,
            })

        total_rows = len(rows)
        target_per_shard = max(1, total_rows // n_shards) if by_form else 1

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for stale in out_dir.glob('*.json'):
            stale.unlink()

        manifest_shards: list[dict] = []
        shard_idx = 0
        current: dict[str, list[dict]] = {}
        current_rows = 0

        def flush() -> None:
            nonlocal shard_idx, current, current_rows
            if not current:
                return
            fname = f'{shard_idx:04d}.json'
            (out_dir / fname).write_text(
                json.dumps(current, ensure_ascii=False, separators=(',', ':')),
                encoding='utf-8',
            )
            manifest_shards.append({'first': next(iter(current)), 'file': fname})
            shard_idx += 1
            current = {}
            current_rows = 0

        for form in sorted(by_form):
            current[form] = by_form[form]
            current_rows += len(by_form[form])
            if current_rows >= target_per_shard:
                flush()
        flush()

        (out_dir / 'manifest.json').write_text(
            json.dumps({'shards': manifest_shards}, ensure_ascii=False, separators=(',', ':')),
            encoding='utf-8',
        )

        return {'shards': len(manifest_shards), 'forms': len(by_form), 'rows': total_rows}

    def write_concept_details(self, out_dir: Path) -> int:
        """Precompute one JSON file per concept (synset), covering
        everything the live ``queryConcept()``/``querySense()`` functions
        in web/index.html compute via synchronous SQL, so the browser's
        dominant search-and-browse path never needs the full database.

        Relation types are stored **raw** (``relation_types.type``), not
        mapped to ``RELATION_TO_SHORT``'s short codes -- the client
        applies that mapping at read time, matching
        ``write_wordnet_summaries()``'s existing convention. Inverse
        relations are **not** suppressed here (unlike
        ``write_wordnet_summaries()``) -- ``queryConcept()`` returns
        every stored relation live, so the precomputed version must too.

        Sense-relation entries embed the target sense's lemma
        (``toForm``) and owning concept (``toConcept``) directly,
        mirroring ``write_wordnet_summaries()``'s ``src_form``/``tgt_form``
        pattern, so rendering a sense relation never needs a separate
        sense-index lookup on the hot path.

        Generation is set-oriented (a handful of bulk queries grouped in
        Python), not one query per concept -- avoids ~10 round trips x
        hundreds of thousands of concepts. Output is written to a
        sibling temp directory and swapped into place atomically on
        success, so an interrupted build never leaves a partial,
        inconsistent set of concept files behind.

        Args:
            out_dir: Directory to write ``<synset_rowid>.json`` files into.

        Returns:
            Number of concept files written.
        """
        lang_code: dict[int, str] = dict(
            self.cur.execute('SELECT rowid, code FROM languages').fetchall()
        )

        forms_by_entry: dict[int, list[tuple[int, str, int]]] = {}
        for entry_rowid, form_rowid, form, rank in self.cur.execute(
            'SELECT entry_rowid, rowid, form, rank FROM forms ORDER BY entry_rowid, rank'
        ):
            forms_by_entry.setdefault(entry_rowid, []).append((form_rowid, form, rank))

        def lemma_variants_formrowid(entry_rowid: int) -> tuple[str, list[str], int | None]:
            flist = forms_by_entry.get(entry_rowid, [])
            lemma_row = next((f for f in flist if f[2] == 0), None)
            lemma = lemma_row[1] if lemma_row else ''
            lemma_form_rowid = lemma_row[0] if lemma_row else None
            variants = [f[1] for f in flist if f[2] > 0]
            return lemma, variants, lemma_form_rowid

        pron_by_form: dict[int, list[dict]] = {}
        for form_rowid, variety, pronunciation, audio in self.cur.execute(
            'SELECT form_rowid, variety, pronunciation, audio FROM pronunciations ORDER BY rowid'
        ):
            pron_by_form.setdefault(form_rowid, []).append({
                'variety': variety, 'pronunciation': pronunciation, 'audio': audio,
            })

        # Base sense rows, grouped by synset -- and a global sense_rowid ->
        # (synset_rowid, lemma) lookup, since sense relations can target a
        # sense in a different concept.
        sense_base: dict[int, list[tuple]] = {}
        sense_to_synset: dict[int, int] = {}
        sense_to_lemma: dict[int, str] = {}
        for sid, entry_rowid, synset_rowid, sense_index, lang_rowid in self.cur.execute("""
            SELECT s.rowid, s.entry_rowid, s.synset_rowid, s.sense_index, e.language_rowid
            FROM senses s JOIN entries e ON s.entry_rowid = e.rowid
        """):
            sense_base.setdefault(synset_rowid, []).append(
                (sid, entry_rowid, sense_index, lang_rowid)
            )
            sense_to_synset[sid] = synset_rowid
            lemma, _variants, _form_rowid = lemma_variants_formrowid(entry_rowid)
            sense_to_lemma[sid] = lemma

        # Examples per sense -- the same example can be shared by several
        # senses (via sense_examples), with different bold-token offsets
        # per sense, so segments are built per (sense, example) pair, not
        # cached across senses.
        examples_by_sense: dict[int, list[int]] = {}
        example_text: dict[int, str] = {}
        for sense_rowid, example_rowid, example in self.cur.execute("""
            SELECT se.sense_rowid, e.rowid, e.example
            FROM sense_examples se JOIN examples e ON se.example_rowid = e.rowid
        """):
            examples_by_sense.setdefault(sense_rowid, []).append(example_rowid)
            example_text[example_rowid] = example

        anns_by_example: dict[int, list[tuple[int, int, int]]] = {}
        for example_rowid, start_offset, end_offset, ann_sense in self.cur.execute(
            'SELECT example_rowid, start_offset, end_offset, sense_rowid '
            'FROM example_annotations ORDER BY example_rowid, start_offset'
        ):
            anns_by_example.setdefault(example_rowid, []).append(
                (start_offset, end_offset, ann_sense)
            )

        def build_segments(example_rowid: int, owning_sense: int) -> list[dict]:
            text = example_text[example_rowid]
            segments = []
            last_idx = 0
            for start, end, ann_sense in anns_by_example.get(example_rowid, []):
                if start > last_idx:
                    segments.append({'text': text[last_idx:start], 'bold': False})
                segments.append({'text': text[start:end], 'bold': ann_sense == owning_sense})
                last_idx = end
            if last_idx < len(text):
                segments.append({'text': text[last_idx:], 'bold': False})
            return segments

        sense_rels: dict[int, list[dict]] = {}
        for source_rowid, rel_type, target_rowid, rid, subtype, subtype_label in self.cur.execute("""
            SELECT sr.source_rowid, rt.type, sr.target_rowid, sr.rowid,
                   sr.subtype, sr.subtype_label
            FROM sense_relations sr JOIN relation_types rt ON sr.type_rowid = rt.rowid
        """):
            entry = {
                'type': rel_type, 'to': target_rowid, 'rid': rid,
                'toForm': sense_to_lemma.get(target_rowid, ''),
                'toConcept': sense_to_synset.get(target_rowid),
            }
            if subtype:
                entry['subtype'] = subtype
            if subtype_label:
                entry['subtypeLabel'] = subtype_label
            sense_rels.setdefault(source_rowid, []).append(entry)

        concept_rels: dict[int, list[dict]] = {}
        for source_rowid, rel_type, target_rowid, rid, subtype, subtype_label in self.cur.execute("""
            SELECT sr.source_rowid, rt.type, sr.target_rowid, sr.rowid,
                   sr.subtype, sr.subtype_label
            FROM synset_relations sr JOIN relation_types rt ON sr.type_rowid = rt.rowid
        """):
            entry = {'type': rel_type, 'to': target_rowid, 'rid': rid}
            if subtype:
                entry['subtype'] = subtype
            if subtype_label:
                entry['subtypeLabel'] = subtype_label
            concept_rels.setdefault(source_rowid, []).append(entry)

        defs_by_synset: dict[int, list[tuple]] = {}
        for def_rowid, synset_rowid, definition, def_lang_rowid, translated_from in self.cur.execute(
            'SELECT rowid, synset_rowid, definition, language_rowid, translated_from_rowid '
            'FROM definitions ORDER BY language_rowid'
        ):
            defs_by_synset.setdefault(synset_rowid, []).append(
                (def_rowid, definition, def_lang_rowid, translated_from)
            )

        def_anns_by_def: dict[int, list[dict]] = {}
        for definition_rowid, start_offset, end_offset, ann_sense in self.cur.execute(
            'SELECT definition_rowid, start_offset, end_offset, sense_rowid '
            'FROM definition_annotations'
        ):
            def_anns_by_def.setdefault(definition_rowid, []).append(
                {'so': start_offset, 'eo': end_offset, 's': ann_sense}
            )

        arasaac_by_synset: dict[int, list[int]] = {}
        for synset_rowid, arasaac_id in self.cur.execute(
            'SELECT synset_rowid, arasaac_id FROM arasaac'
        ):
            arasaac_by_synset.setdefault(synset_rowid, []).append(arasaac_id)

        core_synsets = {
            r[0] for r in self.cur.execute('SELECT synset_rowid FROM core_synsets')
        }

        out_dir = Path(out_dir)
        tmp_dir = out_dir.parent / f'.{out_dir.name}.tmp'
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True)

        n_written = 0
        for synset_rowid, oc, ili in self.cur.execute('SELECT rowid, pos, ili FROM synsets'):
            senses_out: dict[str, dict] = {}
            s_by_lang: dict[str, list[int]] = {}
            for sid, entry_rowid, sense_index, lang_rowid in sense_base.get(synset_rowid, []):
                lemma, variants, lemma_form_rowid = lemma_variants_formrowid(entry_rowid)
                lg = lang_code.get(lang_rowid)
                prons = pron_by_form.get(lemma_form_rowid, []) if lemma_form_rowid else []
                ex_out = [
                    build_segments(ex_rowid, sid)
                    for ex_rowid in examples_by_sense.get(sid, [])
                ]
                sr_out = sense_rels.get(sid, [])

                senses_out[str(sid)] = {
                    'id': str(sid), 'l': lemma,
                    'v': variants or None,
                    'p': prons or None,
                    'lg': lg, 'c': synset_rowid, 'ind': sense_index,
                    'ex': ex_out or None,
                    'sr': sr_out or None,
                    'ontological_category': oc,
                }
                s_by_lang.setdefault(lg, []).append(sid)

            d: dict[str, str] = {}
            dr: dict[str, int] = {}
            dtf: dict[str, str] = {}
            da = None
            for def_rowid, definition, def_lang_rowid, translated_from in defs_by_synset.get(synset_rowid, []):
                lang = lang_code.get(def_lang_rowid)
                if lang is None:
                    continue
                d[lang] = definition
                dr[lang] = def_rowid
                if translated_from is not None:
                    tf_code = lang_code.get(translated_from)
                    if tf_code:
                        dtf[lang] = tf_code
                if lang == 'en':
                    anns = def_anns_by_def.get(def_rowid)
                    if anns:
                        da = anns

            concept = {
                'oc': oc,
                'ili': ili,
                'd': d or None,
                'dr': dr or None,
                'dtf': dtf or None,
                'da': da,
                's': s_by_lang or None,
                'senses': senses_out or None,
                'cr': concept_rels.get(synset_rowid) or None,
                'ara': arasaac_by_synset.get(synset_rowid) or None,
                'core': synset_rowid in core_synsets,
            }
            (tmp_dir / f'{synset_rowid}.json').write_text(
                json.dumps(concept, ensure_ascii=False, separators=(',', ':')),
                encoding='utf-8',
            )
            n_written += 1

        if out_dir.exists():
            old_dir = out_dir.parent / f'.{out_dir.name}.old'
            if old_dir.exists():
                shutil.rmtree(old_dir)
            out_dir.rename(old_dir)
            tmp_dir.rename(out_dir)
            shutil.rmtree(old_dir)
        else:
            tmp_dir.rename(out_dir)

        return n_written

    def write_labels_index(self, out_dir: Path, bucket_size: int = 20_000) -> int:
        """Precompute a numeric-bucket-sharded, language-aware concept
        label index, so rendering a relation list never needs one fetch
        per related concept just to show its display label.

        Each concept's entry stores every language's rank-0 lemma
        (``byLang``) plus an explicit precomputed fallback
        (``fallback``), reproducing ``getConceptLabel()``'s live
        requested-language -> English -> any-language rule (lowest
        ``sense_index`` across *all* of that concept's senses) --
        a bare ``{lang: label}`` map can't reproduce that fallback
        deterministically, since JS object key order isn't a reliable
        stand-in for "lowest sense_index across languages".

        Args:
            out_dir: Directory to write ``<bucket>.json`` files into.
            bucket_size: Number of synset rowids per bucket
                (bucket = synset_rowid // bucket_size).

        Returns:
            Number of distinct concepts indexed.
        """
        lang_code: dict[int, str] = dict(
            self.cur.execute('SELECT rowid, code FROM languages').fetchall()
        )

        # Rank-0 forms per sense, ordered by sense_index so the first
        # row seen per (synset, language) and per synset overall are
        # exactly getConceptLabel()'s requested-language and
        # any-language picks.
        rows = self.cur.execute("""
            SELECT s.synset_rowid, e.language_rowid, f.form, s.sense_index
            FROM senses s
            JOIN entries e ON s.entry_rowid = e.rowid
            JOIN forms f ON f.entry_rowid = e.rowid AND f.rank = 0
            ORDER BY s.synset_rowid, s.sense_index
        """).fetchall()

        by_lang: dict[int, dict[str, str]] = {}
        fallback: dict[int, dict] = {}
        for synset_rowid, lang_rowid, form, _sense_index in rows:
            lang = lang_code.get(lang_rowid)
            if lang is None:
                continue
            lang_map = by_lang.setdefault(synset_rowid, {})
            if lang not in lang_map:
                lang_map[lang] = form
            if synset_rowid not in fallback:
                fallback[synset_rowid] = {'lang': lang, 'label': form}

        buckets: dict[int, dict[str, dict]] = {}
        for synset_rowid in by_lang:
            bucket = synset_rowid // bucket_size
            buckets.setdefault(bucket, {})[str(synset_rowid)] = {
                'byLang': by_lang[synset_rowid],
                'fallback': fallback[synset_rowid],
            }

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for stale in out_dir.glob('*.json'):
            stale.unlink()
        for bucket, entries in buckets.items():
            (out_dir / f'{bucket}.json').write_text(
                json.dumps(entries, ensure_ascii=False, separators=(',', ':')),
                encoding='utf-8',
            )

        return len(by_lang)

    def write_sense_index(self, out_dir: Path, bucket_size: int = 20_000) -> int:
        """Precompute a numeric-bucket-sharded sense_rowid -> synset_rowid
        index, for the one remaining case that needs it: a direct
        ``#/search?sense=<id>`` URL with no concept ID already in hand.
        Ordinary search results and sense-relation entries both already
        carry their owning concept id directly, so this stays off the
        hot render path.

        Args:
            out_dir: Directory to write ``<bucket>.json`` files into.
            bucket_size: Number of sense rowids per bucket
                (bucket = sense_rowid // bucket_size).

        Returns:
            Number of senses indexed.
        """
        buckets: dict[int, dict[str, int]] = {}
        n = 0
        for sense_rowid, synset_rowid in self.cur.execute(
            'SELECT rowid, synset_rowid FROM senses'
        ):
            bucket = sense_rowid // bucket_size
            buckets.setdefault(bucket, {})[str(sense_rowid)] = synset_rowid
            n += 1

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for stale in out_dir.glob('*.json'):
            stale.unlink()
        for bucket, entries in buckets.items():
            (out_dir / f'{bucket}.json').write_text(
                json.dumps(entries, ensure_ascii=False, separators=(',', ':')),
                encoding='utf-8',
            )

        return n

    def write_ili_index(self, out_dir: Path) -> int:
        """Precompute a flat ILI -> synset_rowid index for ``i123``/
        ``cili.i123`` search queries. Small enough (one entry per
        ILI-mapped synset) to ship as a single file, fetched lazily on
        first use.

        Args:
            out_dir: Directory to write ``ili-index.json`` into.

        Returns:
            Number of ILI-mapped synsets indexed.
        """
        rows = self.cur.execute(
            'SELECT ili, rowid FROM synsets WHERE ili IS NOT NULL'
        ).fetchall()
        index = {ili: rowid for ili, rowid in rows}

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / 'ili-index.json').write_text(
            json.dumps(index, ensure_ascii=False, separators=(',', ':')),
            encoding='utf-8',
        )
        return len(index)

    def write_resource_provenance(self, out_dir: Path, version: str) -> int:
        """Split provenance.db into one small gzipped database per resource.

        A ``#/search?id=<original_id>`` link needs only the provenance rows
        of the one resource that id came from, not the whole provenance
        database (most of it Princeton and OEWN). Each split file carries
        the same schema as provenance.db (rowids preserved, so queries
        written for it work unchanged) plus its own ``build_info``, stamped
        with *version* -- the same string written to ``manifest.json`` by
        ``write_manifest()`` -- so the browser can validate a split file
        against the already-fetched manifest without loading the main
        database just to check it.

        Args:
            out_dir: Directory to write ``<resource_code>.db.gz`` files and
                ``index.json`` into.
            version: Build version string, also written to this file's
                ``build_info`` table (see ``finalize()``).

        Returns:
            Number of resource databases written.
        """
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for stale in out_dir.glob('*.db.gz'):
            stale.unlink()

        tables = self.prov_cur.execute(
            'SELECT rowid, name FROM prov_tables'
        ).fetchall()
        codes = dict(self.prov_cur.execute('SELECT rowid, code FROM prov_resources'))

        rows_by_resource = self.prov_cur.execute("""
            SELECT rowid, table_rowid, item_rowid, resource_rowid, version, original_id
            FROM provenance
            ORDER BY resource_rowid
        """)
        index: dict[str, dict] = {}
        for res_rowid, group in itertools.groupby(rows_by_resource, key=lambda row: row[3]):
            rows = list(group)
            code = codes[res_rowid]
            db_path = out_dir / f'{code}.db'
            dst = sqlite3.connect(db_path)
            dst.executescript(PROV_SCHEMA)
            dst.execute(
                'INSERT INTO prov_resources (rowid, code) VALUES (?, ?)', (res_rowid, code))
            dst.executemany(
                'INSERT INTO prov_tables (rowid, name) VALUES (?, ?)', tables)
            dst.executemany('INSERT INTO provenance VALUES (?, ?, ?, ?, ?, ?)', rows)
            dst.execute('INSERT INTO build_info (version) VALUES (?)', (version,))
            dst.commit()
            dst.execute('VACUUM')
            dst.close()
            gz_path = Path(f'{db_path}.gz')
            with open(db_path, 'rb') as fin, gzip.open(gz_path, 'wb', 9) as fout:
                shutil.copyfileobj(fin, fout)
            db_path.unlink()
            index[code] = {'file': f'{code}.db.gz', 'rows': len(rows)}

        (out_dir / 'index.json').write_text(
            json.dumps({'version': version, 'resources': dict(sorted(index.items()))},
                       ensure_ascii=False, separators=(',', ':')),
            encoding='utf-8',
        )
        return len(index)

    def write_manifest(self, out_dir: Path, version: str) -> None:
        """Write the top-level ``manifest.json`` the browser fetches
        first: the one build-version value (see ``build_info``, written
        to both databases by ``finalize()``) plus everything the browser
        used to populate from an eager DB load -- languages, resources,
        DB-wide stats, and the ``core_synsets``/``translated_from_rowid``
        feature flags -- so dropping eager DB loading doesn't also drop
        the Wordnets tab, About-page stats, language filters, core-concept
        stars, or translated-definition handling.

        Args:
            out_dir: Directory to write ``manifest.json`` into.
            version: The build version string also written to
                ``build_info`` in both databases.
        """
        languages = [
            {'code': code, 'name': name, 'rowid': rowid}
            for rowid, code, name in self.cur.execute(
                'SELECT rowid, code, name FROM languages ORDER BY code'
            )
        ]

        cols = {row[1] for row in self.cur.execute('PRAGMA table_info(resources)')}
        coverage = (
            ', r.synset_count, r.sense_count'
            if 'synset_count' in cols else
            ', NULL AS synset_count, NULL AS sense_count'
        )
        resources = [
            dict(zip((
                'code', 'version', 'label', 'language', 'url', 'citation',
                'licence', 'email', 'status', 'confidence_score', 'extra',
                'synset_count', 'sense_count',
            ), row))
            for row in self.cur.execute(f"""
                SELECT r.code, r.version, r.label, l.code AS language,
                       r.url, r.citation, r.licence, r.email, r.status,
                       r.confidence_score, r.extra{coverage}
                FROM resources r
                LEFT JOIN languages l ON r.language_rowid = l.rowid
                ORDER BY r.label
            """)
        ]

        def count(sql: str) -> int:
            return self.cur.execute(sql).fetchone()[0]

        stats = {
            'synsets': count('SELECT COUNT(*) FROM synsets'),
            'entries': count('SELECT COUNT(*) FROM entries'),
            'senses': count('SELECT COUNT(*) FROM senses'),
            'languages': count('SELECT COUNT(DISTINCT language_rowid) FROM entries'),
            'wordnets': count(
                # 'mtg' is the synthetic machine-translated-glosses pseudo-resource
                # 5_translate_defns.py writes (see conversion_scripts/5_translate_defns.py)
                # -- not a contributed wordnet in its own right, so it's excluded
                # alongside the 'cili'/'pwn' bookkeeping resources.
                "SELECT COUNT(*) FROM resources WHERE code NOT IN ('cili', 'pwn', 'mtg')"
            ),
        }

        has_core_table = count(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='core_synsets'"
        ) > 0
        has_translated_from = any(
            row[1] == 'translated_from_rowid'
            for row in self.cur.execute('PRAGMA table_info(definitions)')
        )

        manifest = {
            'version': version,
            'languages': languages,
            'resources': resources,
            'stats': stats,
            'hasCoreTable': has_core_table,
            'hasTranslatedFrom': has_translated_from,
        }

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / 'manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False, separators=(',', ':')),
            encoding='utf-8',
        )

    def create_indexes(self) -> None:
        """Create indexes before post-processing queries (critical for performance)."""
        self.cur.executescript(INDEXES)
        # Built here rather than in PROV_SCHEMA: nothing reads provenance
        # during Phase 1, and maintaining the index through ~10M inserts
        # costs more than building it once.
        self.prov_cur.execute(
            'CREATE INDEX IF NOT EXISTS idx_provenance_lookup '
            'ON provenance(table_rowid, item_rowid)'
        )

    def finalize(self, db_path: Path, prov_db_path: Path, version: str) -> None:
        """Drop build-only indexes, run ANALYZE/VACUUM, and commit both databases.

        Args:
            db_path: Path the main database is written to.
            prov_db_path: Path the provenance database is written to.
            version: Build version string, written into both databases'
                ``build_info`` table -- lets the browser validate a cached
                database (same-origin or cross-origin, where response
                headers may not be readable) against ``manifest.json``'s
                ``version`` without depending on HTTP headers at all.
        """
        # idx_forms_lower only speeds up merge_case_variants()/compute_sense_indices()
        # during the build itself -- the shipped web UI never queries LOWER(form)
        # (it uses the normalized_form column instead), so keeping this index in
        # the exported database just costs ~87MB for every visitor to download.
        print('Dropping build-only indexes...')
        self.cur.execute('DROP INDEX IF EXISTS idx_forms_lower')

        self.cur.execute('DELETE FROM build_info')
        self.cur.execute('INSERT INTO build_info (version) VALUES (?)', (version,))
        self.prov_cur.execute('DELETE FROM build_info')
        self.prov_cur.execute('INSERT INTO build_info (version) VALUES (?)', (version,))

        print('Running ANALYZE...')
        self.cur.execute('ANALYZE')
        self.conn.commit()
        self.prov_conn.commit()

        print('Running VACUUM...')
        self.conn.isolation_level = None
        self.conn.execute('VACUUM')
        self.prov_conn.isolation_level = None
        self.prov_conn.execute('VACUUM')

        self.conn.close()
        self.prov_conn.close()

        for path in (db_path, prov_db_path):
            size = path.stat().st_size / 1024 ** 2
            print(f'  {path} ({size:.1f} MB)')


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

