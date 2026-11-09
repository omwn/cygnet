#!/usr/bin/env python3
"""
Batch relation-direction check: run scripts/check_relation_directions.py's
comparison over every candidate wordnet already downloaded in bin/raw_wns,
against a single reference (default: OEWN), to find any resource -- not
just plWordnet -- with a relation type that looks systematically reversed.

Unlike the single-file script, this:
  - reads the reference once and computes its per-relation-type ILI pairs
    once, reusing them as the comparison denominator for every candidate
    (the reference side of the comparison never changes between files);
  - keeps going past a single file that fails to parse, reporting the
    failure, rather than aborting the whole batch;
  - optionally caches with --cache: each file's per-relation stats, keyed
    by (path, mtime, size), so an unchanged file is not re-read on the
    next run, and the reference's own pairs, so an unchanged reference is
    not re-read either (the whole cache is invalidated if the reference
    file changes);
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
        [--raw-wns-dir bin/raw_wns] [--wordnets-toml wordnets.toml] \\
        [--reference bin/raw_wns/english-wordnet-2025-plus.xml.gz] \\
        [--min-pairs 20] [--threshold 0.9] \\
        [--out results.json] [--cache cache.json] \\
        [--write-swap-config bin/cygnets_presynth/relation_direction_swaps.json]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_relation_directions as crd  # noqa: E402

PROJECT_DIR = Path(__file__).resolve().parent.parent
LMF_SUFFIXES = ('.xml', '.xml.gz', '.xml.xz')

# Relative to the current working directory, like 2_batch_convert_lmfs.py's
# own --raw-wns-dir default -- build.sh invokes both after cd'ing into the
# build's data dir (the project dir, or --work-dir's target), not this
# script's own location, so --work-dir builds resolve these correctly.
DEFAULT_RAW_WNS_DIR = Path('bin/raw_wns')
DEFAULT_REFERENCE = Path('bin/raw_wns/english-wordnet-2025-plus.xml.gz')


def _step2_file_selection():
    """conversion_scripts/2_batch_convert_lmfs.py's collect_xml_files(),
    loaded by path since the module name starts with a digit."""
    script = PROJECT_DIR / 'conversion_scripts' / '2_batch_convert_lmfs.py'
    spec = importlib.util.spec_from_file_location('batch_convert_lmfs', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.collect_xml_files


def candidate_files(
    raw_wns_dir: Path, reference: Path, wordnets_toml: Path | None = None,
) -> list[Path]:
    """The files to check: the LMF files in *raw_wns_dir* other than the
    reference.

    With *wordnets_toml*, only the files Step 2 would convert from it (the
    same selection as 2_batch_convert_lmfs.py makes). The swap config is
    keyed by lexicon id, so a file lying in the directory but not listed
    in wordnets.toml -- an old release, a local test export -- would
    otherwise have its reversed relations swapped in the release the
    build actually uses, which may be perfectly fine.

    Args:
        raw_wns_dir: Directory of downloaded wordnet files.
        reference: The reference file, never a candidate.
        wordnets_toml: The build's wordnets.toml, if the selection should
            follow it.

    Returns:
        Sorted candidate paths.
    """
    if wordnets_toml is not None:
        files = _step2_file_selection()(wordnets_toml, raw_wns_dir)
    else:
        files = _lmf_files_in(raw_wns_dir)
    ref = reference.resolve()
    return sorted(
        p for p in files
        if p.name.endswith(LMF_SUFFIXES) and p.resolve() != ref
    )


def _lmf_files_in(raw_wns_dir: Path) -> list[Path]:
    return [p for p in raw_wns_dir.iterdir()
            if p.is_file() and p.name.endswith(LMF_SUFFIXES)]


def unlisted_files(
    raw_wns_dir: Path, reference: Path, candidates: list[Path],
) -> list[Path]:
    """The LMF files lying in *raw_wns_dir* that are neither the reference
    nor among *candidates*: with --wordnets-toml, the files the build
    ignores, worth a warning since they are usually leftovers."""
    chosen = {p.resolve() for p in candidates} | {reference.resolve()}
    return sorted(p for p in _lmf_files_in(raw_wns_dir) if p.resolve() not in chosen)


def _file_stat_key(path: Path) -> dict:
    st = path.stat()
    return {'mtime': st.st_mtime, 'size': st.st_size}


def _read_cache(cache_path: Path | None, reference_path: Path) -> dict:
    """The parsed cache file, or {} if there is none, it is unreadable, or
    it was computed against a different reference file.

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
    return data


def load_cache(cache_path: Path | None, reference_path: Path) -> dict[str, dict]:
    """Return {file_path: cache_entry} for the per-file stats, or {} if
    there's no usable cache (see _read_cache)."""
    return _read_cache(cache_path, reference_path).get('files', {})


def load_cached_reference_pairs(
    cache_path: Path | None, reference_path: Path,
) -> tuple[str, dict[str, set[tuple[str, str]]]] | None:
    """The reference's (spec, {relType: pairs}) from the cache, or None if
    there's no usable cache or it predates reference-pair caching."""
    cached = _read_cache(cache_path, reference_path).get('reference_pairs')
    if not cached:
        return None
    return cached['spec'], {
        rel: {tuple(pair) for pair in pairs} for rel, pairs in cached['pairs'].items()
    }


def reference_pairs_cache_entry(
    spec: str, pairs: dict[str, set[tuple[str, str]]],
) -> dict:
    """The JSON-ready cache record for load_cached_reference_pairs()."""
    return {'spec': spec, 'pairs': {rel: sorted(ps) for rel, ps in pairs.items()}}


def compute_candidate_stats(
    path: Path, reference_pairs: dict[str, set[tuple[str, str]]],
) -> tuple[dict[str, dict[str, crd.DirectionStats]], list[tuple[str, str]]]:
    """Read *path*'s lexicon(s) and compute per-relation DirectionStats
    against *reference_pairs* for each.

    Returns ({lexicon_spec: {relation_type: DirectionStats}}, failures).
    """
    per_lexicon: dict[str, dict[str, crd.DirectionStats]] = {}
    failures: list[tuple[str, str]] = []
    try:
        by_lexicon = crd.lmf_ili_relation_pairs(path)
    except Exception as e:
        failures.append((path.name, str(e)))
        return per_lexicon, failures
    for spec, cand_pairs in by_lexicon.items():
        per_lexicon[spec] = {
            rel: crd.compute_direction_stats(rel, pairs, reference_pairs.get(rel, set()))
            for rel, pairs in cand_pairs.items()
        }
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
    parser.add_argument('--wordnets-toml', type=Path, default=None,
                         help='Check only the files this wordnets.toml selects, as Step 2 '
                              'would (default: every LMF file in --raw-wns-dir)')
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
    if args.wordnets_toml is not None and not args.wordnets_toml.exists():
        parser.error(f'wordnets.toml not found: {args.wordnets_toml}')

    cached_reference = load_cached_reference_pairs(args.cache, args.reference)
    if cached_reference:
        reference_spec, reference_pairs = cached_reference
        print(f'Reference ({args.reference.name}) relation pairs read from cache')
    else:
        print(f'Reading reference ({args.reference.name})...')
        reference_spec, reference_pairs = crd.single_lexicon_pairs(args.reference)
    print(f'  -> {reference_spec}')
    for rel in crd.DIRECTED_RELATION_TYPES:
        pairs = reference_pairs.get(rel)
        if pairs:
            print(f'  {rel}: {len(pairs)} pairs')

    candidates = candidate_files(args.raw_wns_dir, args.reference, args.wordnets_toml)
    print(f'\n{len(candidates)} candidate file(s) to check')
    if args.wordnets_toml is not None:
        ignored = unlisted_files(args.raw_wns_dir, args.reference, candidates)
        if ignored:
            print(f'  ignoring {len(ignored)} file(s) in {args.raw_wns_dir} that '
                  f'{args.wordnets_toml} does not list: '
                  + ', '.join(p.name for p in ignored))
    print()

    file_cache = load_cache(args.cache, args.reference)
    new_cache_files: dict[str, dict] = {}

    all_results: dict[str, dict] = {}
    flagged: list[tuple[str, str, crd.DirectionStats]] = []
    failed: list[tuple[str, str]] = []

    for i, path in enumerate(candidates, 1):
        stat_key = _file_stat_key(path)
        cached = file_cache.get(str(path))
        if cached and cached.get('mtime') == stat_key['mtime'] and cached.get('size') == stat_key['size']:
            print(f'[{i}/{len(candidates)}] {path.name} (cached)', flush=True)
            new_cache_files[str(path)] = cached
            per_lexicon = {
                spec: {
                    rel: crd.DirectionStats(rel, d['agree'], d['reversed'], d['no_overlap'])
                    for rel, d in rel_data.items()
                }
                for spec, rel_data in cached['lexicons'].items()
            }
        else:
            print(f'[{i}/{len(candidates)}] {path.name} ...', flush=True)
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
            'reference_pairs': reference_pairs_cache_entry(reference_spec, reference_pairs),
            'files': new_cache_files,
        }))
        print(f'Cache written to {args.cache}')

    if args.write_swap_config:
        swap_config = build_swap_config(flagged)
        args.write_swap_config.write_text(json.dumps(swap_config, indent=2))
        print(f'Swap config for {len(swap_config)} resource(s) written to '
              f'{args.write_swap_config}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
