"""Test fixtures written in the old Cygnet XML vocabulary, turned into WN-LMF.

The pipeline merges WN-LMF only, but the test suite's inline fixtures were
written as Cygnet XML (Concept / Lexeme / Sense / Gloss / Example /
ConceptRelation / SenseRelation with Provenance children), a compact way
to state exactly what a merge should see.  cygnet_to_lmf() maps that
vocabulary onto WN-LMF so those fixtures stay readable:

- a Concept is a Synset; its id is the Provenance original_id when there
  is one (so deep links by original id keep working), else the concept id;
  ``cili.<ili>`` gives the ILI, any other id a local synset (``ili=""``);
  a ``cili.*`` concept only referred to gets a stub Synset with that ILI;
- a Lexeme is a LexicalEntry (first Wordform the Lemma, the rest Forms),
  in a Lexicon for its language: the resource's own language keeps the
  resource id, another language gets ``<id>-<lang>``;
- a Sense goes under its Lexeme, Glosses under their Synset (a
  ``translated_from`` attribute becomes ``note="translated_from=.."``);
- an Example goes under the Sense of its first AnnotatedToken, the span
  in ``note="offsets=start,end"``; one with no annotation is dropped, as
  the old merge dropped it;
- a relation goes under its source as ``relType`` (or ``other`` +
  ``dc:type`` for a type outside the DTD), ``subtype`` to ``dc:type`` and
  ``subtype_label`` to ``dc:description``.
"""

from __future__ import annotations

from collections import defaultdict

from lxml import etree

from cyg.lmf import (
    DC_NS,
    DOCTYPE,
    DTD_SENSE_RELATION_TYPES,
    DTD_SYNSET_RELATION_TYPES,
    XML_DECL,
    format_offsets,
    pos_code,
    relation_element,
    set_note_key,
)

HEADER = f'{XML_DECL}\n{DOCTYPE}\n'


def _original_id(elem: etree._Element, fallback: str) -> str:
    prov = elem.find('Provenance')
    return (prov.get('original_id') if prov is not None else None) or fallback


def _relation(tag: str, elem: etree._Element, target: str, allowed: frozenset) -> etree._Element:
    return relation_element(tag, elem.get('relation_type', ''), target, elem.get('subtype'),
                            elem.get('subtype_label'), allowed)


def cygnet_to_lmf(text: str) -> str:
    """Turn a Cygnet XML document into a WN-LMF 1.4 document.

    Args:
        text: The Cygnet XML.

    Returns:
        The WN-LMF XML, as text with the header wn requires.
    """
    root = etree.fromstring(text.encode('utf-8'))
    resource = root.get('id', 'test')
    language = root.get('language', 'en')
    version = root.get('version', '1.0')

    synset_ids: dict[str, str] = {}
    synsets: dict[str, etree._Element] = {}
    for concept in root.iter('Concept'):
        cid = concept.get('id')
        sid = _original_id(concept, cid)
        synset_ids[cid] = sid
        synset = etree.Element('Synset', id=sid,
                               ili=cid[5:] if cid.startswith('cili.') else '',
                               partOfSpeech=pos_code(concept.get('ontological_category')))
        synsets[cid] = synset
    # A concept this file refers to but declares elsewhere (wn-fr's senses
    # at wn-en's ILIs) gets a stub Synset: an LMF file names every synset
    # its senses and relations point at, and the merge unifies by ILI.
    referenced = ([g.get('definiendum') for g in root.iter('Gloss')]
                  + [s.get('signified') for s in root.iter('Sense')]
                  + [r.get(a) for r in root.iter('ConceptRelation') for a in ('source', 'target')])
    for cid in referenced:
        if cid and cid not in synsets and cid.startswith('cili.'):
            synset_ids[cid] = cid
            synsets[cid] = etree.Element('Synset', id=cid, ili=cid[5:], partOfSpeech='u')
    for gloss in root.iter('Gloss'):
        synset = synsets.get(gloss.get('definiendum'))
        if synset is None:
            continue
        sentence = gloss.find('AnnotatedSentence')
        definition = etree.SubElement(synset, 'Definition', language=gloss.get('language', language))
        definition.text = ''.join(sentence.itertext()) if sentence is not None else ''
        if gloss.get('translated_from'):
            set_note_key(definition, 'translated_from', gloss.get('translated_from'))
    for rel in root.iter('ConceptRelation'):
        source, target = synsets.get(rel.get('source')), synset_ids.get(rel.get('target'))
        if source is not None and target:
            source.append(_relation('SynsetRelation', rel, target, DTD_SYNSET_RELATION_TYPES))

    entries: dict[str, etree._Element] = {}
    entry_language: dict[str, str] = {}
    for lexeme in root.iter('Lexeme'):
        lid = lexeme.get('id')
        entry = etree.Element('LexicalEntry', id=_original_id(lexeme, lid))
        forms = lexeme.findall('Wordform')
        for i, wf in enumerate(forms):
            if i == 0:
                form = etree.SubElement(entry, 'Lemma', writtenForm=wf.get('form', ''),
                                        partOfSpeech=pos_code(lexeme.get('grammatical_category')))
            else:
                form = etree.SubElement(entry, 'Form', writtenForm=wf.get('form', ''))
            for pron in wf.findall('Pronunciation'):
                p = etree.SubElement(form, 'Pronunciation')
                p.text = pron.text or ''
                for attr in ('variety', 'audio'):
                    if pron.get(attr):
                        p.set(attr, pron.get(attr))
        entries[lid] = entry
        entry_language[lid] = lexeme.get('language', language)

    senses: dict[str, etree._Element] = {}
    for sense in root.iter('Sense'):
        entry = entries.get(sense.get('signifier'))
        synset = synset_ids.get(sense.get('signified'))
        if entry is None:
            continue
        sid = _original_id(sense, sense.get('id'))
        element = etree.SubElement(entry, 'Sense', id=sid, synset=synset or sense.get('signified', ''))
        senses[sense.get('id')] = element
    for rel in root.iter('SenseRelation'):
        source, target = senses.get(rel.get('source')), senses.get(rel.get('target'))
        if source is not None and target is not None:
            source.append(_relation('SenseRelation', rel, target.get('id'), DTD_SENSE_RELATION_TYPES))
    for example in root.iter('Example'):
        sentence = example.find('AnnotatedSentence')
        if sentence is None:
            continue
        text_parts, spans = [], []
        offset = len(sentence.text or '')
        text_parts.append(sentence.text or '')
        for token in sentence:
            word = token.text or ''
            if token.get('sense'):
                spans.append((token.get('sense'), offset, offset + len(word)))
            offset += len(word)
            text_parts.append(word)
            text_parts.append(token.tail or '')
            offset += len(token.tail or '')
        if not spans or spans[0][0] not in senses:
            continue
        owner, start, end = spans[0]
        ex = etree.SubElement(senses[owner], 'Example')
        ex.text = ''.join(text_parts)
        set_note_key(ex, 'offsets', format_offsets([(start, end)]))

    # Lexicons: the resource's language first, other languages after it.
    by_language: dict[str, list[etree._Element]] = defaultdict(list)
    for lid, entry in entries.items():
        by_language[entry_language[lid]].append(entry)
    lexicon_langs = [language] + sorted(lang for lang in by_language if lang != language)
    out = etree.Element('LexicalResource', nsmap={'dc': DC_NS})
    for i, lang in enumerate(lexicon_langs):
        lid = resource if lang == language else f'{resource}-{lang}'
        lexicon = etree.SubElement(out, 'Lexicon', id=lid, label=root.get('label', lid),
                                   language=lang, email='test@example.org',
                                   license='https://creativecommons.org/licenses/by/4.0/',
                                   version=version, url='https://example.org/')
        if root.get('citation') and i == 0:
            lexicon.set('citation', root.get('citation'))
        for entry in by_language.get(lang, ()):
            lexicon.append(entry)
        if i == 0:
            for synset in synsets.values():
                lexicon.append(synset)
    etree.indent(out, space='  ')
    return HEADER + etree.tostring(out, encoding='unicode') + '\n'
