#!/usr/bin/env python3
"""
Step 4: turn the GlossTag sense annotations of PWN 3.0 glosses (Step 3's
bin/concepts_to_definitions.json) into the sidecar the merge reads,
bin/cygnets_presynth/cili_glosstag.json: {ili: [[start, end, OEWN sense
id], ...]}.

Each GlossTag definition is keyed by an NLTK synset name ('able.a.01'),
which NLTK's WordNet maps to a PWN 3.0 synset id, which bin/cili.tsv maps
to an ILI.  Its annotations name senses by sense key
('necessary%3:00:00::'); OEWN writes the same key as its sense id
('oewn-necessary__3.00.00..'), so the merge can resolve them once OEWN is
merged (MergeBuilder.resolve_pending_annotations()).  A definition whose
text differs from CILI's is kept with a warning, since the offsets are
into the GlossTag text; overlapping annotations are skipped.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from nltk.corpus import wordnet as wn

from cyg.cili import read_cili_tsv

SIDECAR_NAME = 'cili_glosstag.json'


def sense_key_to_oewn_id(sense_key: str) -> str:
    """OEWN's sense id for a PWN sense key: 'necessary%3:00:00::' ->
    'oewn-necessary__3.00.00..'.

    Args:
        sense_key: A PWN 3.0 sense key.

    Returns:
        The OEWN sense id.
    """
    lemma, _sep, rest = sense_key.partition('%')
    return f"oewn-{lemma}__{rest.replace(':', '.')}"


def synset_name_to_pwn_id(name: str) -> str:
    """A PWN 3.0 synset id ('00128262-s') for an NLTK synset name ('able.a.01').

    Args:
        name: The NLTK synset name.

    Returns:
        The eight-digit offset and POS.

    Raises:
        ValueError: If NLTK does not know the name.
    """
    try:
        synset = wn.synset(name)
    except Exception as exc:
        raise ValueError(f'unknown synset {name}: {exc}') from exc
    return f'{synset.offset():08d}-{synset.pos()}'


def overlapping(annotations: list) -> bool:
    """Whether any two (start, end, sense) spans overlap."""
    spans = sorted((a[0], a[1]) for a in annotations)
    return any(spans[i][1] > spans[i + 1][0] for i in range(len(spans) - 1))


def build_sidecar(definitions: dict, cili_tsv: Path) -> tuple[dict[str, list], dict[str, int]]:
    """The sidecar's content for GlossTag's definitions.

    Args:
        definitions: {synset name: {'string': gloss, 'annotations': [[start, end, sense key], ...]}}.
        cili_tsv: bin/cili.tsv, for the PWN id -> ILI mapping and the gloss text.

    Returns:
        ({ili: [[start, end, OEWN sense id], ...]}, counts of what was skipped).
    """
    rows = {row.original_id: row for row in read_cili_tsv(cili_tsv)}
    sidecar: dict[str, list] = {}
    stats = {'definitions': 0, 'annotations': 0, 'unknown synset': 0,
             'not in cili': 0, 'gloss differs': 0, 'overlapping': 0}
    for name, data in definitions.items():
        try:
            pwn_id = synset_name_to_pwn_id(name)
        except ValueError:
            stats['unknown synset'] += 1
            continue
        row = rows.get(pwn_id)
        if row is None:
            stats['not in cili'] += 1
            continue
        if ' '.join(data['string'].split()) != row.definition:
            stats['gloss differs'] += 1
        annotations = data.get('annotations', [])
        if overlapping(annotations):
            stats['overlapping'] += 1
            continue
        spans = [[start, end, sense_key_to_oewn_id(key)] for start, end, key in annotations]
        if spans:
            sidecar[row.ili] = sorted(spans)
            stats['definitions'] += 1
            stats['annotations'] += len(spans)
    return sidecar, stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--definitions', type=Path, default=Path('bin/concepts_to_definitions.json'))
    parser.add_argument('--cili', type=Path, default=Path('bin/cili.tsv'))
    parser.add_argument('--out', type=Path, default=Path('bin/cygnets_presynth') / SIDECAR_NAME)
    args = parser.parse_args()
    definitions = json.loads(args.definitions.read_text(encoding='utf-8'))
    print(f'{len(definitions):,} GlossTag definitions')
    sidecar, stats = build_sidecar(definitions, args.cili)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(sidecar, ensure_ascii=False), encoding='utf-8')
    for key, n in stats.items():
        print(f'  {key}: {n:,}')
    print(f'Written: {args.out}')


if __name__ == '__main__':
    sys.exit(main())
