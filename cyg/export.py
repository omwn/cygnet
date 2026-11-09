"""Export the merged database as WN-LMF 1.4 (Step 7).

The file is a multi-lexicon release of the kind Cygnet itself merges: a
concepts lexicon ``cygnet`` (language ``mul``) holds every concept once,
with its definitions in every language and its relations, and one lexicon
``cygnet-<lang>`` per language holds that language's entries and senses,
with a Synset per concept the language lexicalises.  The synsets of a
concept share its ILI, and, for a concept without one, the identifier
``cyg:<rowid>`` in ``dc:identifier``; each language lexicon ``Requires`` the
concepts lexicon.  Only the forward direction of a reversible relation is
written, symmetric relations once; an example sits under its Sense with the
sense's word at ``note="offsets=start,end"``.  The provenance variant puts
every contributing source in ``dc:source`` as space-separated
``resource:version:original_id`` tokens.

The WN-LMF DTD wants at least one LexicalEntry in a Lexicon, so the
concepts lexicon carries one placeholder entry, as the OMW pronoun and
kinship releases do.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from lxml import etree

from cyg.lmf import (
    CONCEPT_REVERSE_RELATIONS,
    DTD_SENSE_RELATION_TYPES,
    DTD_SYNSET_RELATION_TYPES,
    SENSE_REVERSE_RELATIONS,
    is_forward_relation,
    pos_code,
)

XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>'
DTD_URL = 'https://globalwordnet.github.io/schemas/WN-LMF-1.4.dtd'
DOCTYPE = f'<!DOCTYPE LexicalResource SYSTEM "{DTD_URL}">'
DC_NS = 'https://globalwordnet.github.io/schemas/dc/'
DC = f'{{{DC_NS}}}'
NSMAP = {'dc': DC_NS}
CONCEPTS_ID = 'cygnet'
EMAIL = 'bond@ieee.org'
LICENSE = 'https://creativecommons.org/licenses/by/4.0/'
URL = 'https://omwn.github.io/cygnet/'
CITATION = ('Rowan Hall Maudslay and Francis Bond (2026). Cygnet: Refactoring the '
            'Open Multilingual Wordnet. In Proceedings of LREC 2026.')
PLACEHOLDER_FORM = '(concepts)'

Prov = dict[tuple[str, int], list[tuple[str, str | None, str | None]]]


def load_provenance(prov_conn: sqlite3.Connection) -> Prov:
    """Every provenance row, keyed by (table name, item rowid).

    Args:
        prov_conn: The provenance database.

    Returns:
        {(table, rowid): [(resource, version, original_id), ...]}.
    """
    prov: Prov = defaultdict(list)
    for table, rowid, code, version, original_id in prov_conn.execute("""
        SELECT pt.name, p.item_rowid, pr.code, p.version, p.original_id
        FROM provenance p
        JOIN prov_tables pt ON p.table_rowid = pt.rowid
        JOIN prov_resources pr ON p.resource_rowid = pr.rowid
    """):
        prov[(table, rowid)].append((code, version, original_id))
    return prov


def source_tokens(rows: Iterable[tuple[str, str | None, str | None]]) -> str:
    """The dc:source value for an item: 'resource:version:original_id'
    tokens, separated by spaces, parts left empty when unknown."""
    return ' '.join(':'.join(part or '' for part in row).rstrip(':') for row in rows)


class LmfExporter:
    """Writes the database as WN-LMF; see the module docstring."""

    def __init__(self, conn: sqlite3.Connection, prov: Prov | None = None) -> None:
        """Set up an exporter.

        Args:
            conn: The cygnet database.
            prov: Provenance rows (load_provenance()); None omits dc:source.
        """
        self.conn = conn
        self.prov = prov
        self.version = self._version()
        self.languages: dict[int, str] = dict(conn.execute('SELECT rowid, code FROM languages'))

    def _version(self) -> str:
        try:
            row = self.conn.execute('SELECT version FROM build_info').fetchone()
        except sqlite3.OperationalError:
            row = None
        return row[0] if row else '1.0'

    # ------------------------------------------------------------ helpers

    def _source(self, elem: etree._Element, table: str, rowid: int) -> None:
        if self.prov is None:
            return
        rows = self.prov.get((table, rowid))
        if rows:
            elem.set(f'{DC}source', source_tokens(rows))

    @staticmethod
    def _synset_id(lang: str | None, rowid: int) -> str:
        return f'{CONCEPTS_ID}-{rowid}' if lang is None else f'{CONCEPTS_ID}-{lang}-{rowid}'

    @staticmethod
    def _relation(tag: str, rel_type: str, target: str, subtype: str | None,
                  label: str | None, allowed: frozenset[str]) -> etree._Element:
        rel = etree.Element(tag, nsmap=NSMAP)
        if rel_type in allowed:
            rel.set('relType', rel_type)
            if subtype:
                rel.set(f'{DC}type', subtype)
        else:
            rel.set('relType', 'other')
            rel.set(f'{DC}type', subtype or rel_type)
        rel.set('target', target)
        if label:
            rel.set(f'{DC}description', label)
        return rel

    def _lexicon_start(self, lid: str, label: str, language: str,
                       requires: str | None = None) -> str:
        elem = etree.Element('Lexicon', nsmap={'dc': DC_NS})
        for key, value in (('id', lid), ('label', label), ('language', language),
                           ('email', EMAIL), ('license', LICENSE), ('version', self.version),
                           ('url', URL), ('citation', CITATION)):
            elem.set(key, value)
        tag = etree.tostring(elem, encoding='unicode').replace(f' xmlns:dc="{DC_NS}"', '')
        text = '  ' + tag[:-2] + '>\n'
        if requires:
            text += f'    <Requires ref="{requires}" version="{self.version}"/>\n'
        return text

    @staticmethod
    def _write(out, elem: etree._Element) -> None:
        text = etree.tostring(elem, encoding='unicode', pretty_print=True)
        text = text.replace(f' xmlns:dc="{DC_NS}"', '', 1)
        out.write(''.join('    ' + line + '\n' for line in text.rstrip('\n').split('\n')))

    # ------------------------------------------------------------- export

    def write(self, path: Path) -> dict[str, int]:
        """Write the whole database to *path*.

        Args:
            path: The .xml file to write.

        Returns:
            Counts of what was written.
        """
        counts = {'lexicons': 0, 'synsets': 0, 'entries': 0, 'senses': 0}
        with open(path, 'w', encoding='utf-8', newline='') as out:
            out.write(f'{XML_DECL}\n{DOCTYPE}\n<LexicalResource xmlns:dc="{DC_NS}">\n')
            counts['synsets'] += self._write_concepts(out)
            counts['lexicons'] += 1
            for lang_rowid, lang in sorted(self.languages.items(), key=lambda kv: kv[1]):
                n_entries, n_senses = self._write_language(out, lang_rowid, lang)
                if n_entries:
                    counts['lexicons'] += 1
                    counts['entries'] += n_entries
                    counts['senses'] += n_senses
            out.write('</LexicalResource>\n')
        return counts

    def _write_concepts(self, out) -> int:
        """The concepts lexicon: every concept with its definitions and relations."""
        out.write(self._lexicon_start(CONCEPTS_ID, 'Cygnet concepts', 'mul'))
        first = self.conn.execute('SELECT MIN(rowid) FROM synsets').fetchone()[0]
        placeholder = etree.Element('LexicalEntry', id=f'{CONCEPTS_ID}-placeholder', nsmap=NSMAP)
        etree.SubElement(placeholder, 'Lemma', writtenForm=PLACEHOLDER_FORM, partOfSpeech='n')
        if first is not None:
            etree.SubElement(placeholder, 'Sense', id=f'{CONCEPTS_ID}-placeholder-s',
                             synset=self._synset_id(None, first), lexicalized='false')
        self._write(out, placeholder)

        definitions: dict[int, list[tuple]] = defaultdict(list)
        for rowid, synset, text, lang, source_lang in self.conn.execute("""
            SELECT d.rowid, d.synset_rowid, d.definition, l.code, tf.code
            FROM definitions d
            JOIN languages l ON d.language_rowid = l.rowid
            LEFT JOIN languages tf ON d.translated_from_rowid = tf.rowid
            ORDER BY d.synset_rowid, l.code, d.rowid
        """):
            definitions[synset].append((rowid, text, lang, source_lang))
        relations: dict[int, list[tuple]] = defaultdict(list)
        for rowid, source, target, rel_type, subtype, label in self.conn.execute("""
            SELECT r.rowid, r.source_rowid, r.target_rowid, rt.type, r.subtype, r.subtype_label
            FROM synset_relations r JOIN relation_types rt ON rt.rowid = r.type_rowid
            ORDER BY r.rowid
        """):
            if is_forward_relation(rel_type, source, target, CONCEPT_REVERSE_RELATIONS):
                relations[source].append((rowid, target, rel_type, subtype, label))

        n = 0
        for rowid, ili, pos in self.conn.execute('SELECT rowid, ili, pos FROM synsets ORDER BY rowid'):
            synset = etree.Element('Synset', id=self._synset_id(None, rowid), ili=ili or '',
                                   partOfSpeech=pos_code(pos), lexicalized='false', nsmap=NSMAP)
            synset.set(f'{DC}identifier', f'cyg:{rowid}')
            self._source(synset, 'synsets', rowid)
            for def_rowid, text, lang, source_lang in definitions.get(rowid, ()):
                definition = etree.SubElement(synset, 'Definition', language=lang)
                definition.text = text
                if source_lang:
                    definition.set('note', f'translated_from={source_lang}')
                self._source(definition, 'definitions', def_rowid)
            for rel_rowid, target, rel_type, subtype, label in relations.get(rowid, ()):
                rel = self._relation('SynsetRelation', rel_type, self._synset_id(None, target),
                                     subtype, label, DTD_SYNSET_RELATION_TYPES)
                self._source(rel, 'synset_relations', rel_rowid)
                synset.append(rel)
            self._write(out, synset)
            n += 1
        out.write('  </Lexicon>\n')
        return n

    def _write_language(self, out, lang_rowid: int, lang: str) -> tuple[int, int]:
        """One language's lexicon: entries, senses, and a Synset per concept
        the language lexicalises, with that language's definitions."""
        entries = self.conn.execute(
            'SELECT rowid, pos FROM entries WHERE language_rowid = ? ORDER BY rowid',
            (lang_rowid,)).fetchall()
        if not entries:
            return 0, 0
        name = self.conn.execute('SELECT name FROM languages WHERE rowid = ?',
                                 (lang_rowid,)).fetchone()[0] or lang
        out.write(self._lexicon_start(f'{CONCEPTS_ID}-{lang}', f'Cygnet {name}', lang,
                                      requires=CONCEPTS_ID))
        sense_ids: dict[int, str] = {}
        synsets_used: set[int] = set()
        for rowid, synset in self.conn.execute("""
            SELECT s.rowid, s.synset_rowid FROM senses s
            JOIN entries e ON e.rowid = s.entry_rowid WHERE e.language_rowid = ?
        """, (lang_rowid,)):
            sense_ids[rowid] = f'{CONCEPTS_ID}-s{rowid}'
            synsets_used.add(synset)

        n_senses = 0
        for entry_rowid, pos in entries:
            entry = etree.Element('LexicalEntry', id=f'{CONCEPTS_ID}-{lang}-e{entry_rowid}', nsmap=NSMAP)
            self._source(entry, 'entries', entry_rowid)
            forms = self.conn.execute(
                'SELECT rowid, form FROM forms WHERE entry_rowid = ? ORDER BY rank, rowid',
                (entry_rowid,)).fetchall()
            if not forms:
                continue
            for i, (form_rowid, written) in enumerate(forms):
                if i == 0:
                    form = etree.SubElement(entry, 'Lemma', writtenForm=written,
                                            partOfSpeech=pos_code(pos))
                else:
                    form = etree.SubElement(entry, 'Form', writtenForm=written)
                for variety, text, audio in self.conn.execute(
                        'SELECT variety, pronunciation, audio FROM pronunciations '
                        'WHERE form_rowid = ? ORDER BY rowid', (form_rowid,)):
                    pron = etree.SubElement(form, 'Pronunciation')
                    pron.text = text
                    if variety:
                        pron.set('variety', variety)
                    if audio:
                        pron.set('audio', audio)
            for sense_rowid, synset in self.conn.execute(
                    'SELECT rowid, synset_rowid FROM senses WHERE entry_rowid = ? ORDER BY rowid',
                    (entry_rowid,)):
                sense = etree.SubElement(entry, 'Sense', id=sense_ids[sense_rowid],
                                         synset=self._synset_id(lang, synset))
                self._source(sense, 'senses', sense_rowid)
                for rel_rowid, target, rel_type, subtype, label in self.conn.execute("""
                    SELECT r.rowid, r.target_rowid, rt.type, r.subtype, r.subtype_label
                    FROM sense_relations r JOIN relation_types rt ON rt.rowid = r.type_rowid
                    WHERE r.source_rowid = ? ORDER BY r.rowid""", (sense_rowid,)):
                    if target not in sense_ids:
                        continue   # a relation into another language: written from there
                    if not is_forward_relation(rel_type, sense_rowid, target, SENSE_REVERSE_RELATIONS):
                        continue
                    rel = self._relation('SenseRelation', rel_type, sense_ids[target],
                                         subtype, label, DTD_SENSE_RELATION_TYPES)
                    self._source(rel, 'sense_relations', rel_rowid)
                    sense.append(rel)
                for ex_rowid, text in self.conn.execute("""
                    SELECT e.rowid, e.example FROM sense_examples se
                    JOIN examples e ON e.rowid = se.example_rowid
                    WHERE se.sense_rowid = ? ORDER BY e.rowid""", (sense_rowid,)):
                    example = etree.SubElement(sense, 'Example')
                    example.text = text
                    spans = self.conn.execute("""
                        SELECT start_offset, end_offset FROM example_annotations
                        WHERE example_rowid = ? AND sense_rowid = ? ORDER BY start_offset""",
                        (ex_rowid, sense_rowid)).fetchall()
                    if spans:
                        example.set('note', 'offsets=' + ' '.join(f'{a},{b}' for a, b in spans))
                    self._source(example, 'examples', ex_rowid)
                n_senses += 1
            self._write(out, entry)

        for synset_rowid in sorted(synsets_used):
            ili, pos = self.conn.execute('SELECT ili, pos FROM synsets WHERE rowid = ?',
                                         (synset_rowid,)).fetchone()
            synset = etree.Element('Synset', id=self._synset_id(lang, synset_rowid), ili=ili or '',
                                   partOfSpeech=pos_code(pos), nsmap=NSMAP)
            synset.set(f'{DC}identifier', f'cyg:{synset_rowid}')
            for text, in self.conn.execute("""
                SELECT d.definition FROM definitions d
                WHERE d.synset_rowid = ? AND d.language_rowid = ? ORDER BY d.rowid""",
                (synset_rowid, lang_rowid)):
                etree.SubElement(synset, 'Definition', language=lang).text = text
            self._write(out, synset)
        out.write('  </Lexicon>\n')
        return len(entries), n_senses


def validate_dtd(xml_path: Path, dtd_path: Path) -> list[str]:
    """Validate an exported file against the WN-LMF DTD with lxml.

    Args:
        xml_path: The file to check.
        dtd_path: The DTD (cyg/WN-LMF-1.4.dtd).

    Returns:
        Error messages; empty when valid.
    """
    dtd = etree.DTD(str(dtd_path))
    doc = etree.parse(str(xml_path), etree.XMLParser(load_dtd=False, no_network=True,
                                                     resolve_entities=False, huge_tree=True))
    if dtd.validate(doc):
        return []
    return [f'line {e.line}: {e.message}' for e in dtd.error_log][:50]
