"""The machine-translated glosses sidecar (Step 5): which definitions need
translating, and the ``mtg.tsv`` the merge reads.

Definitions are read from the pre-synth files of either format.  For a
WN-LMF file the concept a definition belongs to is named the way the merge
names it (:func:`cyg.merge.lmf_concept_index`), so the translations land on
the same concepts.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path

from lxml import etree

from cyg.merge import lmf_concept_index, root_tag
from cyg.multilex import open_xml

MTG_TSV_COLUMNS = ('concept', 'language', 'translated_from', 'definition')
MTG_TSV_NAME = 'mtg.tsv'


def glosses_to_translate(presynth_dir: Path) -> list[dict]:
    """The non-English definitions of non-CILI concepts in a pre-synth
    directory, as {'definition', 'definiendum_id', 'language'} records.

    Args:
        presynth_dir: bin/lmf_presynth or bin/cygnets_presynth.

    Returns:
        The records, in file order.
    """
    glosses: list[dict] = []
    mul: set[str] = set()
    for path in sorted(presynth_dir.glob('*.xml')):
        if root_tag(path) == 'LexicalResource':
            glosses.extend(_lmf_glosses(path, mul))
        else:
            glosses.extend(_cygnet_glosses(path))
    return glosses


def _lmf_glosses(path: Path, mul: set[str]) -> list[dict]:
    lexicons, index = lmf_concept_index(path, mul)
    mul.update(lx['id'] for lx in lexicons if lx['language'] == 'mul')
    language = lexicons[0]['language'] if lexicons else None
    out = []
    for _event, elem in etree.iterparse(open_xml(path), events=('end',), tag='Synset',
                                        load_dtd=False, no_network=True,
                                        resolve_entities=False):
        cid, _pos = index[elem.get('id')]
        if not cid.startswith('cili.'):
            for definition in elem.iterchildren('Definition'):
                lang = definition.get('language') or language
                text = ' '.join((definition.text or '').split())
                if lang != 'en' and text:
                    out.append({'definition': text, 'definiendum_id': cid, 'language': lang})
        elem.clear()
        while elem.getprevious() is not None:
            del elem.getparent()[0]
    return out


def _cygnet_glosses(path: Path) -> list[dict]:
    out = []
    for _event, elem in etree.iterparse(str(path), events=('end',), tag='Gloss'):
        language = elem.get('language')
        cid = elem.get('definiendum') or ''
        if language != 'en' and not cid.startswith('cili'):
            sentence = elem.find('AnnotatedSentence')
            text = ''.join(sentence.itertext()).strip() if sentence is not None else ''
            if text:
                out.append({'definition': text, 'definiendum_id': cid, 'language': language})
        elem.clear()
        while elem.getprevious() is not None:
            del elem.getparent()[0]
    return out


def write_mtg_tsv(translations: Iterable[dict], path: Path) -> int:
    """Write the translated glosses as the merge reads them.

    Args:
        translations: Records with 'definiendum_id', 'source_language' and
            'translated_definition' (the translated_glosses.jsonl format).
        path: The TSV to write (mtg.tsv in the pre-synth directory).

    Returns:
        The number of rows written.
    """
    n = 0
    with open(path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t', lineterminator='\n')
        writer.writerow(MTG_TSV_COLUMNS)
        for record in translations:
            text = ' '.join((record.get('translated_definition') or '').split())
            if not text:
                continue
            writer.writerow((record['definiendum_id'], 'en', record['source_language'], text))
            n += 1
    return n
