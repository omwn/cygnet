#!/usr/bin/env python3
"""
Generate cygnet.xml and cygnet_small.xml from cygnet.db + provenance.db,
then validate both against cygnet.xsd.

Replaces the old pipeline steps 7 (validity_checker) and 8 (compress).
"""

import sqlite3
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from lxml import etree as ET

from cyg.merge import build_annotated_sentence

# Only forward (non-inverse) relation types are written to XML
FORWARD_SENSE_RELS: set[str] = {'antonym', 'derivation', 'pertainym', 'participle'}
FORWARD_CONCEPT_RELS: set[str] = {
    'class_hypernym', 'instance_hypernym',
    'member_meronym', 'part_meronym', 'substance_meronym',
    'opposite', 'causes', 'entails',
    'agent_of_action', 'patient_of_action', 'result_of_action',
    'instrument_of_agent', 'instrument_of_action', 'result_of_agent',
    'patient_of_agent', 'instrument_of_patient', 'instrument_of_result',
    'patient_of_result',
}
# Symmetric relations: only write where source_rowid < target_rowid
SYMMETRIC_RELS: set[str] = {'antonym', 'opposite'}


def load_provenance(
    prov_conn: sqlite3.Connection,
) -> dict[tuple[str, int], list[tuple[str, str | None, str]]]:
    """
    Load all provenance rows into a dict keyed by (table_name, item_rowid).
    Returns {(table_name, item_rowid): [(resource_code, version, original_id)]}.
    """
    prov: dict[tuple[str, int], list] = defaultdict(list)
    rows = prov_conn.execute("""
        SELECT pt.name, p.item_rowid, pr.code, p.version, p.original_id
        FROM provenance p
        JOIN prov_tables pt   ON p.table_rowid    = pt.rowid
        JOIN prov_resources pr ON p.resource_rowid = pr.rowid
    """).fetchall()
    for table_name, item_rowid, code, version, original_id in rows:
        prov[(table_name, item_rowid)].append((code, version, original_id))
    return prov


def add_provenance(parent, prov: dict, table: str, rowid: int) -> None:
    """Append Provenance child elements from the pre-loaded prov dict."""
    for code, version, original_id in prov.get((table, rowid), ()):
        attrs = {'resource': code, 'original_id': original_id}
        if version:
            attrs['version'] = version
        ET.SubElement(parent, 'Provenance', **attrs)


def generate_xml(
    conn: sqlite3.Connection,
    prov: dict,
    out_path: Path,
    include_provenance: bool = True,
) -> None:
    """Stream cygnet.xml (or cygnet_small.xml) from the database."""
    cur = conn.cursor()

    # Build ID maps: rowid → string identifier
    # Synsets: cili.{ili} if available, else syn.{rowid}
    synset_id: dict[int, str] = {}
    for rowid, ili in cur.execute('SELECT rowid, ili FROM synsets').fetchall():
        synset_id[rowid] = f'cili.{ili}' if ili else f'syn.{rowid}'

    # Entries: lex.{rowid} — valid xs:ID for all lemmas including multi-word
    entry_id: dict[int, str] = {
        rowid: f'lex.{rowid}'
        for (rowid,) in cur.execute('SELECT rowid FROM entries').fetchall()
    }

    # Senses: sense.{rowid} — simple and guaranteed unique
    sense_id: dict[int, str] = {
        rowid: f'sense.{rowid}'
        for (rowid,) in cur.execute('SELECT rowid FROM senses').fetchall()
    }

    root = ET.Element('CygnetResource', id='cyg', label='Cygnet', version='1.0')

    # ---- ConceptLayer ----
    concept_layer = ET.SubElement(root, 'ConceptLayer')
    for rowid, ili, pos in cur.execute(
        'SELECT rowid, ili, pos FROM synsets ORDER BY rowid'
    ).fetchall():
        cid = synset_id[rowid]
        attrs = {'id': cid, 'ontological_category': pos}
        elem = ET.SubElement(concept_layer, 'Concept', **attrs)
        if include_provenance:
            add_provenance(elem, prov, 'synsets', rowid)

    # Pre-load pronunciations keyed by form_rowid (small: ~43K rows from OEWN)
    form_pronunciations: dict[int, list[tuple]] = defaultdict(list)
    for form_rowid, variety, pron_text in cur.execute(
        'SELECT form_rowid, variety, pronunciation FROM pronunciations ORDER BY rowid'
    ).fetchall():
        form_pronunciations[form_rowid].append((variety, pron_text))

    # ---- LexemeLayer ----
    lexeme_layer = ET.SubElement(root, 'LexemeLayer')
    for entry_rowid, lang, pos in cur.execute("""
        SELECT e.rowid, l.code, e.pos
        FROM entries e
        JOIN languages l ON e.language_rowid = l.rowid
        ORDER BY e.rowid
    """).fetchall():
        eid = entry_id[entry_rowid]
        lex = ET.SubElement(
            lexeme_layer, 'Lexeme',
            id=eid, language=lang, grammatical_category=pos,
        )
        for form_rowid, form in cur.execute(
            'SELECT rowid, form FROM forms WHERE entry_rowid = ? ORDER BY rank',
            (entry_rowid,),
        ).fetchall():
            wf = ET.SubElement(lex, 'Wordform', form=form)
            for variety, pron_text in form_pronunciations.get(form_rowid, ()):
                attrs = {'variety': variety} if variety else {}
                pron = ET.SubElement(wf, 'Pronunciation', **attrs)
                pron.text = pron_text
        if include_provenance:
            add_provenance(lex, prov, 'entries', entry_rowid)

    # ---- SenseLayer ----
    sense_layer = ET.SubElement(root, 'SenseLayer')
    for sense_rowid, entry_rowid, synset_rowid in cur.execute(
        'SELECT rowid, entry_rowid, synset_rowid FROM senses ORDER BY rowid'
    ).fetchall():
        eid = entry_id.get(entry_rowid)
        sid = synset_id.get(synset_rowid)
        if not eid or not sid:
            continue
        sense_elem = ET.SubElement(
            sense_layer, 'Sense',
            id=sense_id[sense_rowid], signifier=eid, signified=sid,
        )
        if include_provenance:
            add_provenance(sense_elem, prov, 'senses', sense_rowid)

    # ---- GlossLayer ----
    gloss_layer = ET.SubElement(root, 'GlossLayer')
    for def_rowid, synset_rowid, definition, lang in cur.execute("""
        SELECT d.rowid, d.synset_rowid, d.definition, l.code
        FROM definitions d
        JOIN languages l ON d.language_rowid = l.rowid
        ORDER BY d.synset_rowid, l.code
    """).fetchall():
        sid = synset_id.get(synset_rowid)
        if not sid:
            continue
        gloss = ET.SubElement(gloss_layer, 'Gloss', definiendum=sid, language=lang)
        anns = [
            (start, end, sense_id[s_rowid])
            for start, end, s_rowid in cur.execute("""
                SELECT start_offset, end_offset, sense_rowid
                FROM definition_annotations
                WHERE definition_rowid = ?
                ORDER BY start_offset
            """, (def_rowid,)).fetchall()
            if s_rowid in sense_id
        ]
        build_annotated_sentence(gloss, definition or '', anns)
        if include_provenance:
            add_provenance(gloss, prov, 'definitions', def_rowid)

    # ---- ExampleLayer ----
    example_layer = ET.SubElement(root, 'ExampleLayer')
    for ex_rowid, example_text in cur.execute(
        'SELECT rowid, example FROM examples ORDER BY rowid'
    ).fetchall():
        ex_elem = ET.SubElement(example_layer, 'Example')
        anns = [
            (start, end, sense_id[s_rowid])
            for start, end, s_rowid in cur.execute("""
                SELECT start_offset, end_offset, sense_rowid
                FROM example_annotations
                WHERE example_rowid = ?
                ORDER BY start_offset
            """, (ex_rowid,)).fetchall()
            if s_rowid in sense_id
        ]
        build_annotated_sentence(ex_elem, example_text, anns)
        if include_provenance:
            add_provenance(ex_elem, prov, 'examples', ex_rowid)

    # ---- SenseRelationLayer ----
    sense_rel_layer = ET.SubElement(root, 'SenseRelationLayer')
    for sr_rowid, source_rowid, target_rowid, rel_type in cur.execute("""
        SELECT sr.rowid, sr.source_rowid, sr.target_rowid, rt.type
        FROM sense_relations sr
        JOIN relation_types rt ON sr.type_rowid = rt.rowid
        ORDER BY sr.rowid
    """).fetchall():
        if rel_type not in FORWARD_SENSE_RELS:
            continue
        if rel_type in SYMMETRIC_RELS and source_rowid >= target_rowid:
            continue
        src = sense_id.get(source_rowid)
        tgt = sense_id.get(target_rowid)
        if not src or not tgt:
            continue
        rel = ET.SubElement(
            sense_rel_layer, 'SenseRelation',
            relation_type=rel_type, source=src, target=tgt,
        )
        if include_provenance:
            add_provenance(rel, prov, 'sense_relations', sr_rowid)

    # ---- ConceptRelationLayer ----
    concept_rel_layer = ET.SubElement(root, 'ConceptRelationLayer')
    for cr_rowid, source_rowid, target_rowid, rel_type in cur.execute("""
        SELECT sr.rowid, sr.source_rowid, sr.target_rowid, rt.type
        FROM synset_relations sr
        JOIN relation_types rt ON sr.type_rowid = rt.rowid
        ORDER BY sr.rowid
    """).fetchall():
        if rel_type not in FORWARD_CONCEPT_RELS:
            continue
        if rel_type in SYMMETRIC_RELS and source_rowid >= target_rowid:
            continue
        src = synset_id.get(source_rowid)
        tgt = synset_id.get(target_rowid)
        if not src or not tgt:
            continue
        rel = ET.SubElement(
            concept_rel_layer, 'ConceptRelation',
            relation_type=rel_type, source=src, target=tgt,
        )
        if include_provenance:
            add_provenance(rel, prov, 'synset_relations', cr_rowid)

    # ---- ResourcesLayer ----
    resources_layer = ET.SubElement(root, 'ResourcesLayer')
    for row in cur.execute("""
        SELECT r.code, r.version, r.label, l.code, r.url, r.licence,
               r.citation, r.email, r.status, r.confidence_score, r.extra
        FROM resources r
        LEFT JOIN languages l ON r.language_rowid = l.rowid
        ORDER BY r.rowid
    """).fetchall():
        code, version, label, lang, url, licence, citation, email, status, score, extra = row
        attrs = {'id': code}
        for k, v in [('version', version), ('label', label), ('language', lang),
                     ('url', url), ('license', licence), ('citation', citation),
                     ('email', email), ('status', status)]:
            if v:
                attrs[k] = v
        if score is not None:
            attrs['confidenceScore'] = str(score)
        if extra:
            import json
            for k, v in json.loads(extra).items():
                attrs[k] = v
        ET.SubElement(resources_layer, 'Resource', **attrs)

    # Write to file
    tree = ET.ElementTree(root)
    tree.write(str(out_path), pretty_print=True,
               xml_declaration=True, encoding='UTF-8')
    print(f'  Written: {out_path} ({out_path.stat().st_size / 1024**2:.1f} MB)')


def _has_xmlstarlet() -> bool:
    return subprocess.run(
        ['which', 'xmlstarlet'], capture_output=True
    ).returncode == 0


def validate(xml_path: Path, xsd_path: Path) -> bool:
    """Validate an XML file against an XSD schema."""
    if _has_xmlstarlet():
        result = subprocess.run(
            ['xmlstarlet', 'val', '--xsd', str(xsd_path), '--err', str(xml_path)],
            capture_output=True, text=True,
        )
        ok = result.returncode == 0
        if ok:
            print(f'  ✓ {xml_path.name} is valid')
        else:
            print(f'  ✗ {xml_path.name} is INVALID')
            for line in (result.stderr or result.stdout).splitlines()[:20]:
                print(f'    {line}')
        return ok

    from lxml import etree as ET  # noqa: PLC0415
    schema = ET.XMLSchema(ET.parse(str(xsd_path)))
    doc = ET.parse(str(xml_path))
    if schema.validate(doc):
        print(f'  ✓ {xml_path.name} is valid')
        return True
    print(f'  ✗ {xml_path.name} is INVALID')
    for err in schema.error_log:
        print(f'    Line {err.line}: {err.message}')
    return False


def main() -> None:
    db_path = Path('web/cygnet.db')
    prov_db_path = Path('web/provenance.db')
    xsd_path = Path('cygnet.xsd')

    if not db_path.exists():
        print(f'Error: {db_path} not found', file=sys.stderr)
        sys.exit(1)

    print('Loading provenance...')
    prov_conn = sqlite3.connect(str(prov_db_path))
    prov = load_provenance(prov_conn)
    prov_conn.close()
    print(f'  {sum(len(v) for v in prov.values()):,} provenance rows')

    conn = sqlite3.connect(str(db_path))

    print('\nGenerating cygnet.xml...')
    generate_xml(conn, prov, Path('cygnet.xml'), include_provenance=True)

    print('\nGenerating cygnet_small.xml...')
    generate_xml(conn, prov, Path('cygnet_small.xml'), include_provenance=False)

    conn.close()

    print('\nValidating...')
    ok1 = validate(Path('cygnet.xml'), xsd_path)
    ok2 = validate(Path('cygnet_small.xml'), xsd_path)

    sys.exit(0 if (ok1 and ok2) else 1)


if __name__ == '__main__':
    main()
