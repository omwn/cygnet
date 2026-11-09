"""The machine-translated glosses sidecar (Step 5): which definitions need
translating, and the ``mtg.tsv`` the merge reads.

The concept a definition belongs to is named the way the merge names it
(:func:`cyg.merge.synset_concept_id`), so the translations land on the
same concepts.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path

from cyg.lmf import LEXICON_TAGS, collapse_whitespace, iter_lmf
from cyg.merge import synset_concept_id

MTG_TSV_COLUMNS = ('concept', 'language', 'translated_from', 'definition')
MTG_TSV_NAME = 'mtg.tsv'


def glosses_to_translate(presynth_dir: Path) -> list[dict]:
    """The non-English definitions of non-CILI concepts in the pre-synth
    directory, as {'definition', 'definiendum_id', 'language'} records.

    Args:
        presynth_dir: bin/cygnets_presynth.

    Returns:
        The records, in file order.
    """
    glosses: list[dict] = []
    for path in sorted(presynth_dir.glob('*.xml')):
        glosses.extend(_lmf_glosses(path))
    return glosses


def _lmf_glosses(path: Path) -> list[dict]:
    out = []
    lexicon = language = concepts_owner = None
    for event, elem in iter_lmf(path, (*LEXICON_TAGS, 'Requires', 'Synset')):
        if event == 'start':
            if elem.tag in LEXICON_TAGS:
                lexicon, language = elem.get('id'), elem.get('language')
                concepts_owner = lexicon if language == 'mul' else None
            continue
        if elem.tag == 'Requires':
            concepts_owner = concepts_owner or elem.get('ref')
            continue
        if elem.tag != 'Synset':
            continue
        cid = synset_concept_id(elem, lexicon, concepts_owner)
        if cid.startswith('cili.'):
            continue
        for definition in elem.iterchildren('Definition'):
            lang = definition.get('language') or language
            text = collapse_whitespace(definition.text)
            if lang != 'en' and text:
                out.append({'definition': text, 'definiendum_id': cid, 'language': lang})
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
            text = collapse_whitespace(record.get('translated_definition'))
            if not text:
                continue
            writer.writerow((record['definiendum_id'], 'en', record['source_language'], text))
            n += 1
    return n
