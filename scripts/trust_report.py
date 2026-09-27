#!/usr/bin/env python3
"""
Report candidates for cyg.merge.LOW_TRUST_RESOURCES.

Reads bin/relation_conflicts.json (written by conversion_scripts/6_synthesise.py)
plus web/cygnet.db and web/provenance.db from the last build, and prints:

  1. An aggregate problem-rate ranking per non-English resource, to suggest
     WHICH resources might belong in LOW_TRUST_RESOURCES.
  2. A full edge-by-edge breakdown of every residual cross-resource cycle
     found in the last build, to let a human confirm WHICH resource's edge
     is actually the reversed one before adding it.

Table 1 alone is not sufficient to decide. A resource can accumulate a high
raw count simply by intersecting a lot with one genuinely bad resource — it
does not follow that it, too, has a directionality bug. Concretely: in this
project's data, odwn-nl ranked #3 by aggregate rate, close behind the two
confirmed-bad resources. But every residual SCC it appears in also contains
an oewn (or estwn) edge agreeing with its direction, with UzWordnet-uz's
edge the lone contradiction — odwn-nl is an innocent bystander that happens
to often complete a triangle with UzWordnet-uz's already-reversed edge.
Naively adding it to LOW_TRUST_RESOURCES does not just do nothing: because
the residual-cycle tie-break falls back to merge-order rowid within the
low-trust group, and UzWordnet-uz happens to merge earlier than odwn-nl (an
accident of alphabetical filename sort), doing so would have *flipped*
which edge gets removed in all 8 existing residual cycles — sacrificing
correct odwn-nl edges to protect the actual reversed UzWordnet-uz ones.

Only add a resource to LOW_TRUST_RESOURCES after reading Table 2 for every
SCC it appears in and confirming its edge is the outlier, not the majority.
Re-run this report after each such change (and after any build that adds
new source wordnets) — a resource that looks fine today can start showing
up once a new resource supplies the missing link that closes a cycle
against it.
"""

import json
import sqlite3
from collections import Counter, defaultdict
from itertools import pairwise
from pathlib import Path

from cyg.merge import LOW_TRUST_RESOURCES

CONFLICTS_PATH = Path('bin/relation_conflicts.json')
DB_PATH = Path('web/cygnet.db')
PROV_DB_PATH = Path('web/provenance.db')


def resolve_resource_code(stem: str, codes_by_length: list[str]) -> str:
    """Map an xml_stem like 'UzWordnet-uz-1.0' back to its resources.code."""
    for code in codes_by_length:
        if stem == code or stem.startswith(code + '-'):
            return code
    return stem


def load_resources(cur: sqlite3.Cursor) -> list[tuple[str, int | None, int | None]]:
    return cur.execute(
        'SELECT code, language_rowid, synset_count FROM resources'
    ).fetchall()


def english_language_rowid(cur: sqlite3.Cursor) -> int | None:
    row = cur.execute("SELECT rowid FROM languages WHERE code = 'en'").fetchone()
    return row[0] if row else None


def print_rate_table(conflicts: dict, resources: list) -> None:
    codes_by_length = sorted({r[0] for r in resources}, key=len, reverse=True)
    size_of = {r[0]: (r[2] or 0) for r in resources}
    lang_of = {r[0]: r[1] for r in resources}

    con = sqlite3.connect(DB_PATH)
    en_rowid = english_language_rowid(con.cursor())
    con.close()

    file_cycles: Counter = Counter()
    residual_cycles: Counter = Counter()
    for rec in conflicts['cycles']:
        code = resolve_resource_code(rec['xml_stem'], codes_by_length)
        if rec.get('residual'):
            residual_cycles[code] += 1
        else:
            file_cycles[code] += 1

    reversed_rel: Counter = Counter()
    for rec in conflicts['reversed_relations']:
        rid = rec.get('resource_id')
        if not rid:
            continue
        reversed_rel[resolve_resource_code(rid, codes_by_length)] += 1

    all_codes = set(file_cycles) | set(residual_cycles) | set(reversed_rel)
    rows = []
    for code in all_codes:
        if lang_of.get(code) == en_rowid:
            continue
        size = size_of.get(code) or 1
        total = file_cycles[code] + residual_cycles[code] + reversed_rel[code]
        rank = LOW_TRUST_RESOURCES.index(code) + 1 if code in LOW_TRUST_RESOURCES else None
        rows.append((total / size, total, file_cycles[code], residual_cycles[code],
                     reversed_rel[code], size, code, rank))

    rows.sort(reverse=True)
    print(f"{'rate':>8} {'total':>6} {'file-cyc':>8} {'resid-cyc':>9} "
          f"{'reversed':>8} {'synsets':>8}  resource")
    for rate, total, filecyc, residcyc, rev, size, code, rank in rows:
        tag = f'  <- LOW_TRUST_RESOURCES #{rank}' if rank else ''
        print(f'{rate:8.4f} {total:6d} {filecyc:8d} {residcyc:9d} '
              f'{rev:8d} {size:8d}  {code}{tag}')


def group_residual_sccs(conflicts: dict) -> list[list[dict]]:
    """Cluster residual-cycle records into the SCC each belongs to."""
    residual = [r for r in conflicts['cycles'] if r.get('residual')]
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for rec in residual:
        nodes = [rec['src'], rec['tgt'], *rec['chain']]
        for a, b in pairwise(nodes):
            union(a, b)

    groups: dict[str, list[dict]] = defaultdict(list)
    for rec in residual:
        groups[find(rec['src'])].append(rec)
    return list(groups.values())


def edge_resources(cur: sqlite3.Cursor, tbl_rowid: int,
                    src_ili: str, tgt_ili: str) -> list[str]:
    """Resource code(s) attributed to the edge src->tgt, via the attached prov DB."""
    src_row = cur.execute(
        'SELECT rowid FROM synsets WHERE ili = ?', (src_ili.replace('cili.', ''),)
    ).fetchone()
    tgt_row = cur.execute(
        'SELECT rowid FROM synsets WHERE ili = ?', (tgt_ili.replace('cili.', ''),)
    ).fetchone()
    if not src_row or not tgt_row:
        return []
    edge_rowids = cur.execute(
        'SELECT rowid FROM synset_relations WHERE source_rowid = ? AND target_rowid = ?',
        (src_row[0], tgt_row[0]),
    ).fetchall()
    codes = []
    for (edge_rowid,) in edge_rowids:
        codes.extend(code for (code,) in cur.execute(
            'SELECT pr.code FROM prov.provenance p '
            'JOIN prov.prov_resources pr ON pr.rowid = p.resource_rowid '
            'WHERE p.table_rowid = ? AND p.item_rowid = ?',
            (tbl_rowid, edge_rowid),
        ).fetchall())
    return codes


def print_scc_detail(conflicts: dict) -> None:
    groups = group_residual_sccs(conflicts)
    if not groups:
        print('No residual cross-resource cycles in the last build.')
        return

    con = sqlite3.connect(DB_PATH)
    con.execute(f"ATTACH DATABASE '{PROV_DB_PATH}' AS prov")
    cur = con.cursor()
    tbl_rowid = cur.execute(
        "SELECT rowid FROM prov.prov_tables WHERE name = 'synset_relations'"
    ).fetchone()[0]

    print(f'{len(groups)} residual cross-resource cycle(s) found in the last build:\n')
    for i, recs in enumerate(groups, 1):
        removed_by = {(r['src'], r['tgt']): r['xml_stem'] for r in recs}
        edges: set[tuple[str, str]] = set(removed_by)
        for rec in recs:
            chain = rec['chain'] + [rec['src']]
            for a, b in pairwise(chain):
                if a != b:
                    edges.add((a, b))

        print(f'--- SCC {i} ({len(recs)} edge(s) removed, {len(edges)} edge(s) total) ---')
        for src, tgt in sorted(edges):
            if (src, tgt) in removed_by:
                print(f'  {src:16s} -> {tgt:16s}  [REMOVED]  {removed_by[(src, tgt)]}')
            else:
                codes = edge_resources(cur, tbl_rowid, src, tgt)
                print(f'  {src:16s} -> {tgt:16s}  [kept]     {",".join(codes) or "?"}')
        print()


def main() -> None:
    if not CONFLICTS_PATH.exists():
        raise SystemExit(f'{CONFLICTS_PATH} not found — run a build first.')
    conflicts = json.loads(CONFLICTS_PATH.read_text(encoding='utf-8'))

    con = sqlite3.connect(DB_PATH)
    resources = load_resources(con.cursor())
    con.close()

    print('=== Table 1: aggregate problem rate per non-English resource ===')
    print('(problems / synset_count contributed — a starting point, not a verdict;')
    print(' see Table 2 and this script\'s module docstring before acting on it)\n')
    print_rate_table(conflicts, resources)

    print('\n=== Table 2: residual cross-resource cycle detail ===')
    print('(every edge in every residual SCC, with resource attribution —')
    print(' use this to confirm which resource is actually the outlier)\n')
    print_scc_detail(conflicts)


if __name__ == '__main__':
    main()
