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

Both variants are written in one pass: each element is built once,
written with its ``dc:source`` attributes to the full file, then without
them to the small one.  The sources themselves are concatenated by SQLite
from the attached provenance database, so no Python copy of its millions
of rows is needed.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from lxml import etree

from cyg.lmf import (
    CONCEPT_REVERSE_RELATIONS,
    DC,
    DC_NS,
    DOCTYPE,
    DTD_SENSE_RELATION_TYPES,
    DTD_SYNSET_RELATION_TYPES,
    SENSE_REVERSE_RELATIONS,
    XML_DECL,
    format_offsets,
    is_forward_relation,
    pos_code,
    relation_element,
    set_note_key,
    start_tag,
)

DTD_PATH = Path(__file__).with_name('WN-LMF-1.4.dtd')
CONCEPTS_ID = 'cygnet'
EMAIL = 'bond@ieee.org'
LICENSE = 'https://creativecommons.org/licenses/by/4.0/'
URL = 'https://omwn.github.io/cygnet/'
CITATION = ('Rowan Hall Maudslay and Francis Bond (2026). Cygnet: Refactoring the '
            'Open Multilingual Wordnet. In Proceedings of LREC 2026.')
PLACEHOLDER_FORM = '(concepts)'
NSMAP = {'dc': DC_NS}
_SOURCE = f'{DC}source'
_XMLNS = f' xmlns:dc="{DC_NS}"'.encode()


def concept_synset_id(rowid: int) -> str:
    """The id of a concept's Synset in the concepts lexicon."""
    return f'{CONCEPTS_ID}-{rowid}'


def language_synset_id(lang: str, rowid: int) -> str:
    """The id of a concept's Synset in a language lexicon."""
    return f'{CONCEPTS_ID}-{lang}-{rowid}'


def source_tokens(rows: Iterable[tuple[str, str | None, str | None]]) -> str:
    """The dc:source value for an item: 'resource:version:original_id'
    tokens, separated by spaces, parts left empty when unknown."""
    return ' '.join(':'.join(part or '' for part in row).rstrip(':') for row in rows)


def _grouped(rows: Iterable[tuple], key_index: int = 0) -> dict:
    """Rows grouped by one column into lists of the remaining columns."""
    out: dict = defaultdict(list)
    for row in rows:
        out[row[key_index]].append(row[:key_index] + row[key_index + 1:])
    return out


class LmfExporter:
    """Writes the database as WN-LMF; see the module docstring."""

    def __init__(self, conn: sqlite3.Connection, prov_db: Path | None = None) -> None:
        """Set up an exporter.

        Args:
            conn: The cygnet database.
            prov_db: The provenance database; its sources go into dc:source
                of the full variant.  None writes neither variant's sources.
        """
        self.conn = conn
        self.has_sources = prov_db is not None
        if prov_db is not None:
            self._prepare_sources(prov_db)
        self.version = self._version()
        self.languages: dict[int, tuple[str, str]] = {
            rowid: (code, name or code)
            for rowid, code, name in conn.execute('SELECT rowid, code, name FROM languages')}

    def _version(self) -> str:
        try:
            row = self.conn.execute('SELECT version FROM build_info').fetchone()
        except sqlite3.OperationalError:
            row = None
        return row[0] if row else '1.0'

    def _prepare_sources(self, prov_db: Path) -> None:
        """A temporary table (table name, item rowid) -> dc:source value,
        concatenated by SQLite from the attached provenance database."""
        self.conn.execute('ATTACH DATABASE ? AS prov', (str(prov_db),))
        self.conn.execute("""
            CREATE TEMP TABLE sources AS
            SELECT pt.name AS tbl, p.item_rowid AS rowid_,
                   GROUP_CONCAT(
                       RTRIM(pr.code || ':' || IFNULL(p.version, '') || ':' || IFNULL(p.original_id, ''), ':'),
                       ' ') AS source
            FROM prov.provenance p
            JOIN prov.prov_tables pt ON pt.rowid = p.table_rowid
            JOIN prov.prov_resources pr ON pr.rowid = p.resource_rowid
            GROUP BY pt.name, p.item_rowid
        """)
        self.conn.execute('CREATE INDEX temp.idx_sources ON sources(tbl, rowid_)')

    def _sources(self, table: str, rowids: Iterable[int] | None = None) -> dict[int, str]:
        """{rowid: dc:source} for a table (all its rows, or the given ones)."""
        if not self.has_sources:
            return {}
        if rowids is None:
            return dict(self.conn.execute('SELECT rowid_, source FROM sources WHERE tbl = ?', (table,)))
        rowids = list(rowids)
        if not rowids:
            return {}
        self.conn.execute('CREATE TEMP TABLE IF NOT EXISTS wanted (rowid_ INTEGER PRIMARY KEY)')
        self.conn.execute('DELETE FROM wanted')
        self.conn.executemany('INSERT OR IGNORE INTO wanted VALUES (?)', [(r,) for r in rowids])
        return dict(self.conn.execute(
            'SELECT s.rowid_, s.source FROM sources s JOIN wanted w ON w.rowid_ = s.rowid_ '
            'WHERE s.tbl = ?', (table,)))

    # ------------------------------------------------------------- helpers

    def _lexicon_start(self, lid: str, label: str, language: str,
                       requires: str | None = None) -> bytes:
        elem = etree.Element('Lexicon', nsmap=NSMAP)
        for key, value in (('id', lid), ('label', label), ('language', language),
                           ('email', EMAIL), ('license', LICENSE), ('version', self.version),
                           ('url', URL), ('citation', CITATION)):
            elem.set(key, value)
        text = start_tag(elem)
        if requires:
            text += f'    <Requires ref="{requires}" version="{self.version}"/>\n'
        return text.encode()

    @staticmethod
    def _write(out_full, out_small, elem: etree._Element) -> None:
        """Write an element to both variants: with its dc:source attributes,
        then with them removed."""
        etree.indent(elem, space='  ', level=2)
        out_full.write(b'    ' + etree.tostring(elem).replace(_XMLNS, b'', 1).strip() + b'\n')
        for node in elem.iter():
            node.attrib.pop(_SOURCE, None)
        out_small.write(b'    ' + etree.tostring(elem).replace(_XMLNS, b'', 1).strip() + b'\n')

    # -------------------------------------------------------------- export

    def write(self, full: Path, small: Path) -> dict[str, int]:
        """Write the whole database as two files.

        Args:
            full: The variant with dc:source on every item.
            small: The variant without.

        Returns:
            Counts of what was written.
        """
        counts = {'lexicons': 0, 'synsets': 0, 'entries': 0, 'senses': 0}
        header = f'{XML_DECL}\n{DOCTYPE}\n<LexicalResource xmlns:dc="{DC_NS}">\n'.encode()
        with open(full, 'wb') as out_full, open(small, 'wb') as out_small:
            outs = (out_full, out_small)
            for out in outs:
                out.write(header)
            counts['synsets'] += self._write_concepts(outs)
            counts['lexicons'] += 1
            for lang_rowid, (lang, _name) in sorted(self.languages.items(), key=lambda kv: kv[1][0]):
                n_entries, n_senses = self._write_language(outs, lang_rowid, lang)
                if n_entries:
                    counts['lexicons'] += 1
                    counts['entries'] += n_entries
                    counts['senses'] += n_senses
            for out in outs:
                out.write(b'</LexicalResource>\n')
        return counts

    def _write_concepts(self, outs) -> int:
        """The concepts lexicon: every concept with its definitions and relations."""
        for out in outs:
            out.write(self._lexicon_start(CONCEPTS_ID, 'Cygnet concepts', 'mul'))
        first = self.conn.execute('SELECT MIN(rowid) FROM synsets').fetchone()[0]
        placeholder = etree.Element('LexicalEntry', id=f'{CONCEPTS_ID}-placeholder', nsmap=NSMAP)
        etree.SubElement(placeholder, 'Lemma', writtenForm=PLACEHOLDER_FORM, partOfSpeech='n')
        if first is not None:
            etree.SubElement(placeholder, 'Sense', id=f'{CONCEPTS_ID}-placeholder-s',
                             synset=concept_synset_id(first), lexicalized='false')
        self._write(*outs, placeholder)

        definitions = _grouped(self.conn.execute("""
            SELECT d.synset_rowid, d.rowid, d.definition, l.code, tf.code
            FROM definitions d
            JOIN languages l ON d.language_rowid = l.rowid
            LEFT JOIN languages tf ON d.translated_from_rowid = tf.rowid
            ORDER BY d.synset_rowid, l.code, d.rowid
        """))
        relations = _grouped(
            (source, rowid, target, rel_type, subtype, label)
            for rowid, source, target, rel_type, subtype, label in self.conn.execute("""
                SELECT r.rowid, r.source_rowid, r.target_rowid, rt.type, r.subtype, r.subtype_label
                FROM synset_relations r JOIN relation_types rt ON rt.rowid = r.type_rowid
                ORDER BY r.rowid""")
            if is_forward_relation(rel_type, source, target, CONCEPT_REVERSE_RELATIONS))
        synset_sources = self._sources('synsets')
        definition_sources = self._sources('definitions')
        relation_sources = self._sources('synset_relations')

        n = 0
        for rowid, ili, pos in self.conn.execute('SELECT rowid, ili, pos FROM synsets ORDER BY rowid'):
            synset = etree.Element('Synset', id=concept_synset_id(rowid), ili=ili or '',
                                   partOfSpeech=pos_code(pos), lexicalized='false', nsmap=NSMAP)
            synset.set(f'{DC}identifier', f'cyg:{rowid}')
            if rowid in synset_sources:
                synset.set(_SOURCE, synset_sources[rowid])
            for def_rowid, text, lang, source_lang in definitions.get(rowid, ()):
                definition = etree.SubElement(synset, 'Definition', language=lang)
                definition.text = text
                if source_lang:
                    set_note_key(definition, 'translated_from', source_lang)
                if def_rowid in definition_sources:
                    definition.set(_SOURCE, definition_sources[def_rowid])
            for rel_rowid, target, rel_type, subtype, label in relations.get(rowid, ()):
                rel = relation_element('SynsetRelation', rel_type, concept_synset_id(target),
                                       subtype, label, DTD_SYNSET_RELATION_TYPES)
                if rel_rowid in relation_sources:
                    rel.set(_SOURCE, relation_sources[rel_rowid])
                synset.append(rel)
            self._write(*outs, synset)
            n += 1
        for out in outs:
            out.write(b'  </Lexicon>\n')
        return n

    def _write_language(self, outs, lang_rowid: int, lang: str) -> tuple[int, int]:
        """One language's lexicon: entries, senses, and a Synset per concept
        the language lexicalises, with that language's definitions."""
        entries = self.conn.execute(
            'SELECT rowid, pos FROM entries WHERE language_rowid = ? ORDER BY rowid',
            (lang_rowid,)).fetchall()
        if not entries:
            return 0, 0
        for out in outs:
            out.write(self._lexicon_start(f'{CONCEPTS_ID}-{lang}', f'Cygnet {self.languages[lang_rowid][1]}',
                                          lang, requires=CONCEPTS_ID))
        # Everything the language's entries need, grouped by entry or sense.
        forms = _grouped(self.conn.execute("""
            SELECT f.entry_rowid, f.rowid, f.form FROM forms f
            JOIN entries e ON e.rowid = f.entry_rowid
            WHERE e.language_rowid = ? ORDER BY f.entry_rowid, f.rank, f.rowid""", (lang_rowid,)))
        pronunciations = _grouped(self.conn.execute("""
            SELECT p.form_rowid, p.variety, p.pronunciation, p.audio FROM pronunciations p
            JOIN forms f ON f.rowid = p.form_rowid JOIN entries e ON e.rowid = f.entry_rowid
            WHERE e.language_rowid = ? ORDER BY p.rowid""", (lang_rowid,)))
        senses = _grouped(self.conn.execute("""
            SELECT s.entry_rowid, s.rowid, s.synset_rowid FROM senses s
            JOIN entries e ON e.rowid = s.entry_rowid
            WHERE e.language_rowid = ? ORDER BY s.rowid""", (lang_rowid,)))
        sense_ids = {rowid: f'{CONCEPTS_ID}-s{rowid}'
                     for rows in senses.values() for rowid, _synset in rows}
        relations = _grouped(
            (source, rowid, target, rel_type, subtype, label)
            for rowid, source, target, rel_type, subtype, label in self.conn.execute("""
                SELECT r.rowid, r.source_rowid, r.target_rowid, rt.type, r.subtype, r.subtype_label
                FROM sense_relations r JOIN relation_types rt ON rt.rowid = r.type_rowid
                JOIN senses s ON s.rowid = r.source_rowid JOIN entries e ON e.rowid = s.entry_rowid
                WHERE e.language_rowid = ? ORDER BY r.rowid""", (lang_rowid,))
            # a relation into another language is written from there
            if target in sense_ids
            and is_forward_relation(rel_type, source, target, SENSE_REVERSE_RELATIONS))
        examples = _grouped(self.conn.execute("""
            SELECT se.sense_rowid, ex.rowid, ex.example FROM sense_examples se
            JOIN examples ex ON ex.rowid = se.example_rowid
            JOIN senses s ON s.rowid = se.sense_rowid JOIN entries e ON e.rowid = s.entry_rowid
            WHERE e.language_rowid = ? ORDER BY ex.rowid""", (lang_rowid,)))
        spans = _grouped(self.conn.execute("""
            SELECT a.sense_rowid, a.example_rowid, a.start_offset, a.end_offset
            FROM example_annotations a
            JOIN senses s ON s.rowid = a.sense_rowid JOIN entries e ON e.rowid = s.entry_rowid
            WHERE e.language_rowid = ? ORDER BY a.start_offset""", (lang_rowid,)))
        entry_sources = self._sources('entries', (r for r, _p in entries))
        sense_sources = self._sources('senses', sense_ids)
        relation_sources = self._sources('sense_relations',
                                         (r for rows in relations.values() for r, *_ in rows))
        example_sources = self._sources('examples',
                                        (r for rows in examples.values() for r, _t in rows))

        n_senses = 0
        synsets_used: set[int] = set()
        for entry_rowid, pos in entries:
            entry_forms = forms.get(entry_rowid)
            if not entry_forms:
                continue
            entry = etree.Element('LexicalEntry', id=f'{CONCEPTS_ID}-{lang}-e{entry_rowid}', nsmap=NSMAP)
            if entry_rowid in entry_sources:
                entry.set(_SOURCE, entry_sources[entry_rowid])
            for i, (form_rowid, written) in enumerate(entry_forms):
                if i == 0:
                    form = etree.SubElement(entry, 'Lemma', writtenForm=written, partOfSpeech=pos_code(pos))
                else:
                    form = etree.SubElement(entry, 'Form', writtenForm=written)
                for variety, text, audio in pronunciations.get(form_rowid, ()):
                    pron = etree.SubElement(form, 'Pronunciation')
                    pron.text = text
                    if variety:
                        pron.set('variety', variety)
                    if audio:
                        pron.set('audio', audio)
            for sense_rowid, synset_rowid in senses.get(entry_rowid, ()):
                synsets_used.add(synset_rowid)
                sense = etree.SubElement(entry, 'Sense', id=sense_ids[sense_rowid],
                                         synset=language_synset_id(lang, synset_rowid))
                if sense_rowid in sense_sources:
                    sense.set(_SOURCE, sense_sources[sense_rowid])
                for rel_rowid, target, rel_type, subtype, label in relations.get(sense_rowid, ()):
                    rel = relation_element('SenseRelation', rel_type, sense_ids[target],
                                           subtype, label, DTD_SENSE_RELATION_TYPES)
                    if rel_rowid in relation_sources:
                        rel.set(_SOURCE, relation_sources[rel_rowid])
                    sense.append(rel)
                sense_spans = _grouped(spans.get(sense_rowid, ()))
                for ex_rowid, text in examples.get(sense_rowid, ()):
                    example = etree.SubElement(sense, 'Example')
                    example.text = text
                    if ex_rowid in sense_spans:
                        example.set('note', 'offsets=' + format_offsets(sense_spans[ex_rowid]))
                    if ex_rowid in example_sources:
                        example.set(_SOURCE, example_sources[ex_rowid])
                n_senses += 1
            self._write(*outs, entry)

        synset_info = dict((rowid, (ili, pos)) for rowid, ili, pos in self.conn.execute(
            'SELECT rowid, ili, pos FROM synsets')) if synsets_used else {}
        own_definitions = _grouped(self.conn.execute("""
            SELECT d.synset_rowid, d.definition FROM definitions d
            WHERE d.language_rowid = ? ORDER BY d.rowid""", (lang_rowid,)))
        for synset_rowid in sorted(synsets_used):
            ili, pos = synset_info[synset_rowid]
            synset = etree.Element('Synset', id=language_synset_id(lang, synset_rowid), ili=ili or '',
                                   partOfSpeech=pos_code(pos), nsmap=NSMAP)
            synset.set(f'{DC}identifier', f'cyg:{synset_rowid}')
            for text, in own_definitions.get(synset_rowid, ()):
                etree.SubElement(synset, 'Definition', language=lang).text = text
            self._write(*outs, synset)
        for out in outs:
            out.write(b'  </Lexicon>\n')
        return len(entries), n_senses


def validate_dtd(xml_path: Path, dtd_path: Path = DTD_PATH) -> list[str]:
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
