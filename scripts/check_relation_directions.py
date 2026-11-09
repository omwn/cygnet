#!/usr/bin/env python3
"""
Pre-merge relation-direction check: compare a candidate wordnet's relations
against a reference (default: OEWN), relation type by relation type, to
catch a systematically reversed relation type *before* merging it into
Cygnet.

Motivation: plWordnet's 2026-10-14 export had mero_part/mero_member/
mero_substance relations with source and target swapped relative to every
other wordnet in Cygnet. We found this by hand -- comparing every pair
where plwn and OEWN both declared a relation on the same ILI pair, 100% of
1,476 comparable pairs were reversed, 0 agreed (see
cyg.converters.MERONYM_DIRECTION_SWAP). This script generalises that check
so it can run routinely on any new candidate, for every directed relation
type, before a build is attempted.

Method: for a directed concept-relation type R (e.g. hypernym), a pair
(A, B) is "comparable" if both the candidate and the reference declare an
R relation between the same two ILI-linked concepts, in either direction.
Among comparable pairs:
  - agree:    both declare A --R--> B
  - reversed: the candidate declares A --R--> B, the reference declares
              B --R--> A
A relation type is flagged when comparable pairs are frequent enough to be
meaningful (>= --min-pairs) AND the reversed share is high enough
(>= --threshold) to look systematic rather than scattered annotation
noise -- see the project's own 0%/100% split on plwn's meronyms, versus
near-100%/0% on its hypernyms, for calibration.

Both files are read straight from their WN-LMF XML (one streaming pass
each, no wn database): only Synset@ili and SynsetRelation@relType/@target
matter here, and loading a wordnet into wn is far slower than reading them.

Usage:
    uv run python scripts/check_relation_directions.py <candidate.xml[.gz|.xz]> \\
        [--reference bin/raw_wns/english-wordnet-2025-plus.xml.gz] \\
        [--min-pairs 20] [--threshold 0.9]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from cyg.merge import INVERSE_CONCEPT_RELATIONS
from cyg.multilex import open_xml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REFERENCE = PROJECT_ROOT / 'bin' / 'raw_wns' / 'english-wordnet-2025-plus.xml.gz'

# Directed (asymmetric) concept relation types worth checking: forward !=
# inverse. Symmetric ones (antonym, similar, 'other', ...) can't be
# "reversed" in a meaningful sense -- a pair declared either way is
# consistent, so checking them would only ever report spurious agreement.
DIRECTED_RELATION_TYPES = tuple(sorted(
    fwd for fwd, inv in INVERSE_CONCEPT_RELATIONS.items() if fwd != inv
))


@dataclass
class DirectionStats:
    relation_type: str
    agree: int
    reversed: int
    no_overlap: int

    @property
    def comparable(self) -> int:
        return self.agree + self.reversed

    @property
    def reversed_share(self) -> float | None:
        return self.reversed / self.comparable if self.comparable else None

    def is_flagged(self, min_pairs: int, threshold: float) -> bool:
        share = self.reversed_share
        return self.comparable >= min_pairs and share is not None and share >= threshold


def compute_direction_stats(
    relation_type: str,
    candidate_pairs: set[tuple[str, str]],
    reference_pairs: set[tuple[str, str]],
) -> DirectionStats:
    """Classify each candidate (source_ili, target_ili) pair against the
    reference's own declared pairs for the same relation type.

    Pure function -- no wn/IO dependency, easily unit-tested.
    """
    agree = reversed_ = no_overlap = 0
    for pair in candidate_pairs:
        if pair in reference_pairs:
            agree += 1
        elif (pair[1], pair[0]) in reference_pairs:
            reversed_ += 1
        else:
            no_overlap += 1
    return DirectionStats(relation_type, agree, reversed_, no_overlap)


# Pairs per lexicon per relation type: {'id:version': {relType: {(ili, ili)}}}
PairsByLexicon = dict[str, dict[str, set[tuple[str, str]]]]


def _ili_of(elem: etree._Element) -> str | None:
    """A synset's ILI id, or None when it has none or only a proposed one
    (ili="in") -- the same reading wn gives Synset.ili."""
    ili = elem.get('ili')
    return ili if ili and ili != 'in' else None


def lmf_ili_relation_pairs(
    path: Path, relation_types: tuple[str, ...] = DIRECTED_RELATION_TYPES,
) -> PairsByLexicon:
    """Every (source_ili, target_ili) pair each lexicon in *path* declares
    for each of *relation_types*, read straight from the WN-LMF XML.

    Pairs are restricted to those where both synsets have an ILI. The
    target may sit in another lexicon of the same file (an extension's
    relations into its base, say); a target not in the file at all is
    skipped, as are synsets without an ILI.

    Args:
        path: A WN-LMF file (.xml, .xml.gz or .xml.xz).
        relation_types: The SynsetRelation relTypes to collect.

    Returns:
        {'id:version': {relType: {(source_ili, target_ili), ...}}}, with a
        key for every Lexicon/LexiconExtension in the file (possibly with
        no relations at all).
    """
    wanted = frozenset(relation_types)
    ili_by_id: dict[str, str | None] = {}
    # (lexicon spec, source ili, relType, target id) for every relation
    # of a wanted type whose source has an ILI; resolved after the parse,
    # since a target may be declared later in the file.
    relations: list[tuple[str, str, str, str]] = []
    by_lexicon: PairsByLexicon = {}
    spec = ''
    with open_xml(path) as fh:
        for event, elem in etree.iterparse(
                fh, events=('start', 'end'),
                tag=('Lexicon', 'LexiconExtension', 'Synset', 'ExternalSynset'),
                resolve_entities=False, load_dtd=False, no_network=True):
            if event == 'start':
                if elem.tag != 'Synset' and elem.tag != 'ExternalSynset':
                    spec = f"{elem.get('id')}:{elem.get('version')}"
                    by_lexicon.setdefault(spec, {})
                continue
            if elem.tag == 'Synset' or elem.tag == 'ExternalSynset':
                sid = elem.get('id')
                ili = _ili_of(elem)
                if sid:
                    ili_by_id[sid] = ili
                if ili:
                    for rel in elem.iterchildren('SynsetRelation'):
                        rtype = rel.get('relType')
                        target = rel.get('target')
                        if rtype in wanted and target:
                            relations.append((spec, ili, rtype, target))
                elem.clear(keep_tail=True)
            # Lexicon ends are left alone: clearing them would drop nothing
            # of use (their children were cleared as they ended).
    for spec, src_ili, rtype, target in relations:
        tgt_ili = ili_by_id.get(target)
        if tgt_ili:
            by_lexicon[spec].setdefault(rtype, set()).add((src_ili, tgt_ili))
    return by_lexicon


def single_lexicon_pairs(path: Path) -> tuple[str, dict[str, set[tuple[str, str]]]]:
    """*path*'s one lexicon's spec and its pairs; raises if the file holds
    several lexicons, since the comparison is per lexicon."""
    by_lexicon = lmf_ili_relation_pairs(path)
    if len(by_lexicon) != 1:
        raise RuntimeError(
            f'{path} holds {len(by_lexicon)} lexicons ({", ".join(by_lexicon)}); '
            f'expected exactly one')
    (spec, pairs), = by_lexicon.items()
    return spec, pairs


def run(candidate_path: Path, reference_path: Path,
        min_pairs: int, threshold: float) -> list[DirectionStats]:
    """Compare *candidate_path*'s directed relations with *reference_path*'s
    and print a per-relation-type report; returns the stats, most
    comparable first."""
    print(f'Reading reference ({reference_path.name})...')
    reference_spec, reference_pairs = single_lexicon_pairs(reference_path)
    print(f'  -> {reference_spec}')

    print(f'Reading candidate ({candidate_path.name})...')
    candidate_spec, candidate_pairs = single_lexicon_pairs(candidate_path)
    print(f'  -> {candidate_spec}')

    results = [
        compute_direction_stats(rel, pairs, reference_pairs.get(rel, set()))
        for rel, pairs in candidate_pairs.items()
    ]

    results.sort(key=lambda s: s.comparable, reverse=True)

    print()
    print(f'{"relation":<20} {"candidate":>10} {"comparable":>11} {"agree":>7} {"reversed":>9} {"reversed %":>11}')
    flagged = []
    for s in results:
        if s.comparable == 0:
            continue
        pct = f'{100 * s.reversed_share:.1f}%' if s.reversed_share is not None else '-'
        flag = '  <-- FLAGGED' if s.is_flagged(min_pairs, threshold) else ''
        print(f'{s.relation_type:<20} {s.agree + s.reversed + s.no_overlap:>10} '
              f'{s.comparable:>11} {s.agree:>7} {s.reversed:>9} {pct:>11}{flag}')
        if s.is_flagged(min_pairs, threshold):
            flagged.append(s)

    print()
    if flagged:
        print(f'{len(flagged)} relation type(s) look systematically reversed against '
              f'{reference_spec}:')
        for s in flagged:
            print(f'  - {s.relation_type}: {s.reversed}/{s.comparable} comparable pairs '
                  f'reversed ({100 * s.reversed_share:.1f}%)')
        print()
        print('Suggested fix: add an entry to MERONYM_DIRECTION_SWAP-style config in '
              'cyg/converters.py (rename/generalise it if this is not about meronyms) '
              'scoping the source/target swap to this resource and these relation types '
              'only, then re-run this check against the corrected export.')
    else:
        print('No relation type looks systematically reversed '
              f'(threshold={threshold:.0%}, min_pairs={min_pairs}).')

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('candidate', type=Path, help='Candidate wordnet LMF file (.xml/.gz/.xz)')
    parser.add_argument('--reference', type=Path, default=DEFAULT_REFERENCE,
                         help=f'Reference wordnet LMF file (default: {DEFAULT_REFERENCE})')
    parser.add_argument('--min-pairs', type=int, default=20,
                         help='Minimum comparable pairs before a relation type can be flagged (default: 20)')
    parser.add_argument('--threshold', type=float, default=0.9,
                         help='Minimum reversed share to flag a relation type (default: 0.9)')
    args = parser.parse_args()

    if not args.candidate.exists():
        parser.error(f'candidate file not found: {args.candidate}')
    if not args.reference.exists():
        parser.error(f'reference file not found: {args.reference}')

    run(args.candidate, args.reference, args.min_pairs, args.threshold)
    return 0


if __name__ == '__main__':
    sys.exit(main())
