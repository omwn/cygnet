#!/usr/bin/env python3
"""
Batch relation-direction check: run scripts/check_relation_directions.py's
comparison over every candidate wordnet already downloaded in bin/raw_wns,
against a single reference (default: OEWN), to find any resource -- not
just plWordnet -- with a relation type that looks systematically reversed.

Unlike the single-file script, this:
  - loads the reference once and computes its per-relation-type ILI pairs
    once, reusing them as the comparison denominator for every candidate
    (the reference side of the comparison never changes between files);
  - loads every candidate into one shared --data-dir instead of a fresh
    tempdir per file, so repeated runs (e.g. after adding a new wordnet)
    don't have to re-add resources that haven't changed;
  - keeps going past a single file that fails to load, reporting the
    failure, rather than aborting the whole batch;
  - optionally caches each file's per-relation stats by (path, mtime,
    size) with --cache, so an unchanged file skips the slow wn.add() /
    comparison step on the next run (the cache is invalidated wholesale
    if the reference file itself changes);
  - optionally writes a swap config with --write-swap-config: for every
    flagged (resource, relation type), the type AND its GWA inverse (e.g.
    mero_part + holo_part) are marked for swapping, since a source wordnet
    that declares a reversed mero_part typically declares the equally
    reversed holo_part too, and swapping only one side lets per-file
    inverse-generation regenerate the original bugged direction from the
    untouched other side. cyg/converters.py reads this file to decide
    which relation types to swap, per resource, instead of a hand-curated
    dict.

Usage:
    uv run python scripts/check_all_relation_directions.py \\
        [--raw-wns-dir bin/raw_wns] \\
        [--reference bin/raw_wns/english-wordnet-2025-plus.xml.gz] \\
        [--data-dir DIR] [--min-pairs 20] [--threshold 0.9] \\
        [--out results.json] [--cache cache.json] \\
        [--write-swap-config bin/cygnets_presynth/relation_direction_swaps.json]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import wn

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_relation_directions as crd  # noqa: E402

# Relative to the current working directory, like 2_batch_convert_lmfs.py's
# own --raw-wns-dir default -- build.sh invokes both after cd'ing into the
# build's data dir (the project dir, or --work-dir's target), not this
# script's own location, so --work-dir builds resolve these correctly.
DEFAULT_RAW_WNS_DIR = Path('bin/raw_wns')
DEFAULT_REFERENCE = Path('bin/raw_wns/english-wordnet-2025-plus.xml.gz')


def add_all_lexicons(path: Path) -> list[str]:
    """Add *path* to the wn database and return every new 'id:version' it
    introduced (a single physical file occasionally bundles more than one
    lexicon)."""
    before = {(lx.id, lx.version) for lx in wn.lexicons()}
    wn.add(str(path), progress_handler=None)
    new = {(lx.id, lx.version) for lx in wn.lexicons()} - before
    return [f'{lid}:{ver}' for lid, ver in sorted(new)]


def _file_stat_key(path: Path) -> dict:
    st = path.stat()
    return {'mtime': st.st_mtime, 'size': st.st_size}


def load_cache(cache_path: Path | None, reference_path: Path) -> dict[str, dict]:
    """Return {file_path: cache_entry}, or {} if there's no usable cache.

    The whole cache is discarded (not just the stale parts) if the
    reference file itself has changed, since every entry's stats were
    computed against it.
    """
    if cache_path is None or not cache_path.exists():
        return {}
    try:
        data = json.loads(cache_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if data.get('reference') != {'path': str(reference_path), **_file_stat_key(reference_path)}:
        return {}
    return data.get('files', {})


def compute_candidate_stats(
    path: Path, reference_pairs: dict[str, set[tuple[str, str]]],
) -> tuple[dict[str, dict[str, crd.DirectionStats]], list[tuple[str, str]]]:
    """Load *path*'s lexicon(s) and compute per-relation DirectionStats
    against *reference_pairs* for each.

    Returns ({lexicon_spec: {relation_type: DirectionStats}}, failures).
    """
    per_lexicon: dict[str, dict[str, crd.DirectionStats]] = {}
    failures: list[tuple[str, str]] = []
    try:
        specs = add_all_lexicons(path)
    except Exception as e:
        failures.append((path.name, str(e)))
        return per_lexicon, failures
    for spec in specs:
        try:
            candidate = wn.Wordnet(lexicon=spec)
        except Exception as e:
            failures.append((spec, str(e)))
            continue
        per_rel = {}
        for rel in crd.DIRECTED_RELATION_TYPES:
            cand_pairs = crd.ili_relation_pairs(candidate, rel)
            if not cand_pairs:
                continue
            per_rel[rel] = crd.compute_direction_stats(
                rel, cand_pairs, reference_pairs.get(rel, set()))
        per_lexicon[spec] = per_rel
    return per_lexicon, failures


def build_swap_config(
    flagged: list[tuple[str, str, crd.DirectionStats]],
) -> dict[str, dict[str, dict]]:
    """For every flagged (lexicon_spec, file, stats), mark both the
    relation type and its GWA inverse as swapped for that lexicon's bare
    id (no version -- matches cyg.converters's self.lexicon_id).

    The directly-measured side carries its stats; a paired inverse added
    only because its forward type was flagged carries null stats, unless
    it was *also* independently flagged (then its own stats are kept).
    """
    config: dict[str, dict[str, dict]] = {}

    def mark(lexicon_id: str, rel_type: str, stats: crd.DirectionStats | None) -> None:
        entry = config.setdefault(lexicon_id, {})
        if rel_type not in entry or stats is not None:
            entry[rel_type] = {
                'reversed': stats.reversed if stats else None,
                'comparable': stats.comparable if stats else None,
                'reversed_share': stats.reversed_share if stats else None,
                'swapped': True,
            }

    for spec, _fname, stats in flagged:
        lexicon_id = spec.split(':', 1)[0]
        mark(lexicon_id, stats.relation_type, stats)
        inverse = crd.INVERSE_CONCEPT_RELATIONS.get(stats.relation_type)
        if inverse and inverse != stats.relation_type:
            mark(lexicon_id, inverse, None)

    return config


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--raw-wns-dir', type=Path, default=DEFAULT_RAW_WNS_DIR)
    parser.add_argument('--reference', type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument('--data-dir', type=Path, default=None,
                         help='wn database directory (default: a fresh temp dir)')
    parser.add_argument('--min-pairs', type=int, default=20)
    parser.add_argument('--threshold', type=float, default=0.9)
    parser.add_argument('--out', type=Path, default=None,
                         help='Write full per-resource/per-relation JSON results here')
    parser.add_argument('--cache', type=Path, default=None,
                         help='Cache per-file stats here, keyed by (path, mtime, size); '
                              'skips recomputing unchanged files on the next run')
    parser.add_argument('--write-swap-config', type=Path, default=None,
                         help='Write a direction-swap config for cyg/converters.py to read')
    args = parser.parse_args()

    if not args.raw_wns_dir.exists():
        parser.error(f'raw-wns-dir not found: {args.raw_wns_dir}')
    if not args.reference.exists():
        parser.error(f'reference file not found: {args.reference}')

    tmp_ctx = None
    if args.data_dir is None:
        tmp_ctx = tempfile.TemporaryDirectory(prefix='cygnet-wn-check-all-')
        data_dir = Path(tmp_ctx.name)
    else:
        data_dir = args.data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
    wn.config.data_directory = str(data_dir)

    print(f'Loading reference ({args.reference.name})...')
    ref_specs = add_all_lexicons(args.reference)
    if len(ref_specs) != 1:
        raise RuntimeError(f'expected exactly one lexicon from the reference file, '
                            f'got {ref_specs}')
    reference_spec = ref_specs[0]
    reference = wn.Wordnet(lexicon=reference_spec)
    print(f'  -> {reference_spec}')

    print('Computing reference relation pairs (once, reused for every candidate)...')
    reference_pairs = {
        rel: crd.ili_relation_pairs(reference, rel) for rel in crd.DIRECTED_RELATION_TYPES
    }
    for rel, pairs in reference_pairs.items():
        if pairs:
            print(f'  {rel}: {len(pairs)} pairs')

    candidate_files = sorted(
        p for p in args.raw_wns_dir.iterdir()
        if p.is_file() and p.resolve() != args.reference.resolve()
    )
    print(f'\n{len(candidate_files)} candidate file(s) to check\n')

    file_cache = load_cache(args.cache, args.reference)
    new_cache_files: dict[str, dict] = {}

    all_results: dict[str, dict] = {}
    flagged: list[tuple[str, str, crd.DirectionStats]] = []
    failed: list[tuple[str, str]] = []

    for i, path in enumerate(candidate_files, 1):
        stat_key = _file_stat_key(path)
        cached = file_cache.get(str(path))
        if cached and cached.get('mtime') == stat_key['mtime'] and cached.get('size') == stat_key['size']:
            print(f'[{i}/{len(candidate_files)}] {path.name} (cached)', flush=True)
            new_cache_files[str(path)] = cached
            per_lexicon = {
                spec: {
                    rel: crd.DirectionStats(rel, d['agree'], d['reversed'], d['no_overlap'])
                    for rel, d in rel_data.items()
                }
                for spec, rel_data in cached['lexicons'].items()
            }
        else:
            print(f'[{i}/{len(candidate_files)}] {path.name} ...', flush=True)
            per_lexicon, failures = compute_candidate_stats(path, reference_pairs)
            failed.extend(failures)
            for name, err in failures:
                print(f'  FAILED: {name}: {err}')
            new_cache_files[str(path)] = {
                **stat_key,
                'lexicons': {
                    spec: {
                        rel: {'agree': s.agree, 'reversed': s.reversed, 'no_overlap': s.no_overlap}
                        for rel, s in per_rel.items()
                    }
                    for spec, per_rel in per_lexicon.items()
                },
            }

        for spec, per_rel in per_lexicon.items():
            all_results[spec] = {
                'file': path.name,
                'relations': {
                    rel: {'agree': s.agree, 'reversed': s.reversed, 'no_overlap': s.no_overlap}
                    for rel, s in per_rel.items()
                },
            }
            for rel, stats in per_rel.items():
                if stats.is_flagged(args.min_pairs, args.threshold):
                    flagged.append((spec, path.name, stats))
                    print(f'  {rel}: {stats.reversed}/{stats.comparable} comparable '
                          f'reversed ({100 * stats.reversed_share:.1f}%)  <-- FLAGGED')

    print()
    print('=' * 72)
    if flagged:
        print(f'{len(flagged)} (resource, relation type) pair(s) look systematically '
              f'reversed against {reference_spec}:\n')
        for spec, fname, s in sorted(flagged, key=lambda t: -t[2].comparable):
            print(f'  {spec:<28} {fname:<42} {s.relation_type:<18} '
                  f'{s.reversed}/{s.comparable} reversed ({100 * s.reversed_share:.1f}%)')
    else:
        print(f'No resource looks systematically reversed on any relation type '
              f'(threshold={args.threshold:.0%}, min_pairs={args.min_pairs}).')

    if failed:
        print(f'\n{len(failed)} file(s)/lexicon(s) failed to load:')
        for name, err in failed:
            print(f'  {name}: {err}')

    if args.out:
        args.out.write_text(json.dumps({
            'reference': reference_spec,
            'min_pairs': args.min_pairs,
            'threshold': args.threshold,
            'results': all_results,
            'flagged': [
                {'lexicon': spec, 'file': fname, 'relation_type': s.relation_type,
                 'agree': s.agree, 'reversed': s.reversed, 'comparable': s.comparable,
                 'reversed_share': s.reversed_share}
                for spec, fname, s in flagged
            ],
            'failed': [{'name': n, 'error': e} for n, e in failed],
        }, indent=2))
        print(f'\nFull results written to {args.out}')

    if args.cache:
        args.cache.write_text(json.dumps({
            'reference': {'path': str(args.reference), **_file_stat_key(args.reference)},
            'files': new_cache_files,
        }))
        print(f'Cache written to {args.cache}')

    if args.write_swap_config:
        swap_config = build_swap_config(flagged)
        args.write_swap_config.write_text(json.dumps(swap_config, indent=2))
        print(f'Swap config for {len(swap_config)} resource(s) written to '
              f'{args.write_swap_config}')

    if tmp_ctx:
        tmp_ctx.cleanup()
    return 0


if __name__ == '__main__':
    sys.exit(main())
