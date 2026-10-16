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

Usage:
    uv run python scripts/check_relation_directions.py <candidate.xml[.gz|.xz]> \\
        [--reference bin/raw_wns/english-wordnet-2025-plus.xml.gz] \\
        [--data-dir DIR] [--min-pairs 20] [--threshold 0.9]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import wn

from cyg.merge import INVERSE_CONCEPT_RELATIONS

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


def ili_relation_pairs(wordnet: wn.Wordnet, relation_type: str) -> set[tuple[str, str]]:
    """All (source_ili, target_ili) pairs this wordnet declares for
    *relation_type*, restricted to pairs where both ends have an ILI."""
    pairs = set()
    for ss in wordnet.synsets():
        if not ss.ili:
            continue
        for target in ss.get_related(relation_type):
            if target.ili:
                pairs.add((ss.ili, target.ili))
    return pairs


def load_lexicon(path: Path) -> str:
    """Add *path* to the wn database and return its 'id:version' spec.

    Assumes a fresh database (see --data-dir): if *path* was already
    loaded, wn.add() is a no-op and no new lexicon appears, so this
    raises rather than silently guessing which existing lexicon to use.
    """
    before = {(lx.id, lx.version) for lx in wn.lexicons()}
    wn.add(str(path), progress_handler=None)
    new = {(lx.id, lx.version) for lx in wn.lexicons()} - before
    if not new:
        raise RuntimeError(
            f'{path} added no new lexicon to the wn database -- is it '
            f'already loaded? Use a fresh --data-dir for a clean run.'
        )
    lid, ver = next(iter(new))
    return f'{lid}:{ver}'


def run(candidate_path: Path, reference_path: Path, data_dir: Path,
        min_pairs: int, threshold: float) -> list[DirectionStats]:
    data_dir.mkdir(parents=True, exist_ok=True)
    wn.config.data_directory = str(data_dir)

    print(f'Loading reference ({reference_path.name})...')
    reference_spec = load_lexicon(reference_path)
    reference = wn.Wordnet(lexicon=reference_spec)
    print(f'  -> {reference_spec}')

    print(f'Loading candidate ({candidate_path.name})...')
    candidate_spec = load_lexicon(candidate_path)
    candidate = wn.Wordnet(lexicon=candidate_spec)
    print(f'  -> {candidate_spec}')

    results = []
    for rel in DIRECTED_RELATION_TYPES:
        candidate_pairs = ili_relation_pairs(candidate, rel)
        if not candidate_pairs:
            continue
        reference_pairs = ili_relation_pairs(reference, rel)
        results.append(compute_direction_stats(rel, candidate_pairs, reference_pairs))

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
    parser.add_argument('--data-dir', type=Path, default=None,
                         help='wn database directory (default: a fresh temp dir)')
    parser.add_argument('--min-pairs', type=int, default=20,
                         help='Minimum comparable pairs before a relation type can be flagged (default: 20)')
    parser.add_argument('--threshold', type=float, default=0.9,
                         help='Minimum reversed share to flag a relation type (default: 0.9)')
    args = parser.parse_args()

    if not args.candidate.exists():
        parser.error(f'candidate file not found: {args.candidate}')
    if not args.reference.exists():
        parser.error(f'reference file not found: {args.reference}')

    if args.data_dir is not None:
        run(args.candidate, args.reference, args.data_dir, args.min_pairs, args.threshold)
        return 0

    with tempfile.TemporaryDirectory(prefix='cygnet-wn-check-') as tmp:
        run(args.candidate, args.reference, Path(tmp), args.min_pairs, args.threshold)
    return 0


if __name__ == '__main__':
    sys.exit(main())
