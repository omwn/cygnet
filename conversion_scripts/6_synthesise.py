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

from cyg.merge import MergeBuilder

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

    all_files = sorted(input_dir.glob('*.xml'))
    cili_files = [f for f in all_files if 'cili' in f.stem]
    oewn_files = [f for f in all_files if 'oewn' in f.stem]
    other_files = [f for f in all_files if 'cili' not in f.stem and 'oewn' not in f.stem]
    xml_files = cili_files + oewn_files + other_files
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

    print('\nPhase 1b: Validating no cycles remain...')
    n_cycles = builder.detect_cycles()
    if n_cycles:
        print(f'  WARNING: {n_cycles:,} residual cycle(s) found')
    else:
        print('  OK — no cycles.')

    print(f'\nWriting conflict log to {conflicts_path}...')
    builder.write_conflicts_json(conflicts_path)

    print('\nPhase 2: Merging case-variant lexemes...')
    removed = builder.merge_case_variants()
    print(f'  Removed {removed:,} duplicate senses from case-variant merge')

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

    print('\nFinalising...')
    builder.finalize(db_path, prov_db_path)
    print('\nDone.')


if __name__ == '__main__':
    main()
