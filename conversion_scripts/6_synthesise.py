#!/usr/bin/env python3
"""
Stream all pre-synthesised Cygnet XML files directly into SQLite.

Each source file is parsed, merged into the databases, and freed before
the next is loaded — so peak memory is one file at a time plus the
ID-mapping dictionaries (~300 MB), not the entire merged dataset.
"""

import logging
import sys
from pathlib import Path

from lxml import etree

from cyg.merge import LOW_TRUST_RESOURCES, MergeBuilder


MANIFEST_NAME = 'manifest.txt'  # written by 2_batch_convert_lmfs.py


def select_files(input_dir: Path) -> list[Path]:
    """Pre-synth files to merge: CILI, this build's Step 2 outputs, and mtg.

    Step 2 lists the outputs it planned in manifest.txt; any other wordnet
    output in the directory is left over from an earlier build and ignored.
    Without a manifest (Step 2 not run), every file is used.

    Args:
        input_dir: The pre-synth directory

    Returns:
        Files to merge, sorted by name
    """
    all_files = sorted(input_dir.glob('*.xml'))
    manifest = input_dir / MANIFEST_NAME
    if not manifest.exists():
        print(f'  Warning: no {MANIFEST_NAME}; merging every file in {input_dir}')
        return all_files
    planned = set(manifest.read_text(encoding='utf-8').split())
    keep, ignored = [], []
    for f in all_files:
        if f.name in planned or 'cili' in f.stem or 'mtg' in f.stem:
            keep.append(f)
        else:
            ignored.append(f.name)
    if ignored:
        print(f'  Ignoring {len(ignored)} file(s) not planned by Step 2: '
              f'{" ".join(ignored)}')
    missing = planned - {f.name for f in all_files}
    if missing:
        print(f'  Warning: planned but missing: {" ".join(sorted(missing))}')
    return keep


def is_concepts_lexicon(path: Path) -> bool:
    """True if a pre-synth file is a 'mul' concepts lexicon.

    Its member lexicons' senses point at its concepts, and _do_sense() drops
    a sense whose concept is not registered yet, so it must be merged first.
    """
    for _event, elem in etree.iterparse(str(path), events=('start',)):
        return elem.get('language') == 'mul'
    return False


def low_trust_rank(path: Path) -> int | None:
    """Position of the file's resource in LOW_TRUST_RESOURCES, or None.

    A pre-synth stem is the resource code plus a version ('UzWordnet-uz-1.0').
    """
    for i, code in enumerate(LOW_TRUST_RESOURCES):
        if path.stem == code or path.stem.startswith(code + '-'):
            return i
    return None


def merge_order(all_files: list[Path]) -> list[Path]:
    """Order pre-synth files for merging.

    When two wordnets disagree, the one merged first wins: a reversed
    relation is skipped, and a cycle is broken by removing the edge from the
    file that closed it. So the order is: CILI, the English base (most
    trusted), the 'mul' concept lexicons (their members need their concepts),
    every other wordnet, then LOW_TRUST_RESOURCES with the worst last, and
    finally the machine-translated glosses (mtg).
    """
    cili_files = [f for f in all_files if 'cili' in f.stem]
    oewn_files = [f for f in all_files if 'oewn' in f.stem]
    # Machine-translated glosses (mtg) reference definiendum concepts from
    # every other resource — _do_gloss() silently drops a Gloss if its
    # concept isn't registered yet, so mtg must be processed last.
    mtg_files = [f for f in all_files if 'mtg' in f.stem]
    rest = [
        f for f in all_files
        if f not in cili_files and f not in oewn_files and f not in mtg_files
    ]
    concept_files = [f for f in rest if is_concepts_lexicon(f)]
    rest = [f for f in rest if f not in concept_files]
    low_trust = sorted(
        (f for f in rest if low_trust_rank(f) is not None),
        key=lambda f: -low_trust_rank(f),  # list is worst first; merge worst last
    )
    other_files = [f for f in rest if f not in low_trust]
    return (cili_files + oewn_files + concept_files + other_files
            + low_trust + mtg_files)


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)

    conflicts_path = Path('bin/relation_conflicts.json')
    logging.basicConfig(
        level=logging.WARNING,
        format='%(message)s',
        handlers=[logging.StreamHandler()],
    )

    input_dir = Path('bin/cygnets_presynth')
    db_path = Path('web/cygnet.db')
    prov_db_path = Path('web/provenance.db')

    db_path.parent.mkdir(exist_ok=True)

    xml_files = merge_order(select_files(input_dir))
    print(f'Found {len(xml_files)} pre-synth files to merge')

    builder = MergeBuilder(db_path, prov_db_path)

    n_cycles_removed = 0
    print('\nPhase 1: Streaming files into SQLite...')
    for xml_file in xml_files:
        print(f'  {xml_file.name}', flush=True)
        first_new_rowid = builder._next_synset_rel_id
        builder.process_file(xml_file)
        resource_code = xml_file.stem
        removed = builder.check_and_remove_new_cycles(resource_code, first_new_rowid)
        if removed:
            print(f'    Removed {removed} cycle-causing relation(s) from {resource_code}',
                  flush=True)
            n_cycles_removed += removed

    print(f'\n  Synsets: {builder.n_synsets:,}')
    print(f'  Entries: {builder.n_entries:,},  Forms: {builder.n_forms:,},'
          f'  Pronunciations: {builder.n_pronunciations:,}')
    print(f'  Languages: {len(builder._lang_cache)}')
    print(f'  Senses: {builder.n_senses:,}')
    print(f'  Definitions: {builder.n_defs:,}')
    print(f'  Examples: {builder.n_examples:,}')
    print(f'  Sense relations: {builder.n_sense_rels:,}')
    print(f'  Synset relations: {builder.n_synset_rels:,}')
    print(f'  Provenance rows: {builder.n_prov:,}')
    if builder.n_rel_conflicts:
        print(f'  Relation conflicts skipped: {builder.n_rel_conflicts:,}'
              f' (see {conflicts_path})')
    if n_cycles_removed:
        print(f'  Cycle-causing relations removed: {n_cycles_removed:,}'
              f' (see {conflicts_path})')

    print('\nCreating indexes...')
    builder.create_indexes()

    # The per-file check above should leave no cycles; this is a release gate.
    print('\nPhase 1b: Checking that no cycles remain...')
    n_cycles = builder.detect_cycles()
    if n_cycles:
        print(f'  ERROR: {n_cycles:,} cycle(s) remain after per-file removal — '
              f'refusing to build a release with a cyclic hypernym hierarchy')
        builder.write_conflicts_json(conflicts_path)
        sys.exit(1)
    print('  OK — no cycles.')

    print(f'\nWriting conflict log to {conflicts_path}...')
    builder.write_conflicts_json(conflicts_path)

    print('\nPhase 2: Merging case-variant lexemes...')
    removed = builder.merge_case_variants()
    print(f'  Removed {removed["identical"]:,} duplicate senses of identical lemmas '
          f'and {removed["variant"]:,} of case/width variants')

    print('\nPhase 3: Cascade-deleting synsets without definitions...')
    counts = builder.cascade_delete()
    for name, n in counts.items():
        if n:
            print(f'  Removed {n:,} {name}')

    print('\nPhase 4: Removing orphaned entries...')
    n_orphans = builder.remove_orphans()
    print(f'  Removed {n_orphans:,} orphaned entries')

    print('\nPhase 5: Computing sense indices...')
    builder.compute_sense_indices()

    print('\nPhase 6: Inserting resource metadata...')
    builder.insert_resources()

    print('\nPhase 7: Loading core synsets...')
    n_core = builder.load_core_synsets()
    print(f'  {n_core:,} core synsets loaded')

    print('\nFinalising...')
    builder.finalize(db_path, prov_db_path)
    print('\nDone.')


if __name__ == '__main__':
    main()
