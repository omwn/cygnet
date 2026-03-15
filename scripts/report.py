#!/usr/bin/env python3
"""Report data-quality issues in a pre-synthesised Cygnet XML wordnet file.

Each issue section explains what Cygnet silently drops or modifies, phrased
for upstream wordnet maintainers who may not know Cygnet's internals.

Usage:
    uv run python scripts/report.py bin/cygnets_presynth/LANG.xml [...]
    uv run python scripts/report.py --all      # scan every pre-synth file
    uv run python scripts/report.py --md wn.xml  # Markdown output
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree as ET

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PRESYNTH_DIR = PROJECT_ROOT / "bin" / "cygnets_presynth"
CONFLICTS_JSON = PROJECT_ROOT / "bin" / "relation_conflicts.json"

HYPERNYM_TYPES = frozenset({"hypernym", "instance_hypernym"})

STANDARD_CONCEPT_RELATIONS = frozenset({
    "hypernym", "hyponym",
    "instance_hypernym", "instance_hyponym",
    "mero_member", "holo_member",
    "mero_part", "holo_part",
    "mero_substance", "holo_substance",
    "antonym", "similar", "also", "attribute",
    "causes", "is_caused_by",
    "entails", "is_entailed_by",
    "domain_topic", "has_domain_topic",
    "domain_region", "has_domain_region",
    "domain_usage", "has_domain_usage",
    "state",
})

STANDARD_SENSE_RELATIONS = frozenset({
    "antonym", "derivation", "pertainym", "participle",
    "also", "similar", "state",
    "domain_topic", "has_domain_topic",
    "domain_region", "has_domain_region",
    "domain_usage", "has_domain_usage",
    "exemplifies", "is_exemplified_by",
})

# Relation types where asserting A→B and B→A simultaneously is a contradiction.
# (Symmetric relations like antonym, where both directions are identical, are excluded.)
DIRECTED_RELATION_TYPES = frozenset({
    "hypernym", "hyponym",
    "instance_hypernym", "instance_hyponym",
    "mero_member", "holo_member",
    "mero_part", "holo_part",
    "mero_substance", "holo_substance",
    "causes", "is_caused_by",
    "entails", "is_entailed_by",
})

MAX_EXAMPLES = 10


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class WordnetData:
    """Parsed contents of one pre-synth Cygnet XML file."""

    resource_id: str = ""
    label: str = ""
    language: str = ""
    version: str = ""

    # concept_id → POS label
    concepts: dict[str, str] = field(default_factory=dict)
    # concept IDs for which at least one <Gloss> is present in this file
    glossed: set[str] = field(default_factory=set)

    # entry_id → language code
    entries: dict[str, str] = field(default_factory=dict)
    # entry IDs that have at least one <Wordform>
    entries_with_forms: set[str] = field(default_factory=set)

    # entry_id → first wordform text
    entry_forms: dict[str, str] = field(default_factory=dict)

    # concept_id → [sense_ids] (in declaration order)
    concept_senses: dict[str, list[str]] = field(default_factory=dict)

    # sense_id → (signifier/entry_id, signified/concept_id)
    senses: dict[str, tuple[str, str]] = field(default_factory=dict)

    # (plain_text_truncated, frozenset_of_annotated_sense_ids)
    examples: list[tuple[str, frozenset[str]]] = field(default_factory=list)

    # (source_id, relation_type, target_id)
    concept_rels: list[tuple[str, str, str]] = field(default_factory=list)
    sense_rels: list[tuple[str, str, str]] = field(default_factory=list)

    duplicate_concept_ids: list[str] = field(default_factory=list)
    duplicate_entry_ids: list[str] = field(default_factory=list)
    duplicate_sense_ids: list[str] = field(default_factory=list)


@dataclass
class Issue:
    severity: str   # CRITICAL | WARNING | INFO
    title: str
    total: int
    explanation: str
    recommendation: str
    items: list[str]  # up to MAX_EXAMPLES representative instances


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_xml(path: Path) -> WordnetData:
    """Stream-parse a pre-synth Cygnet XML file into a WordnetData structure."""
    data = WordnetData()
    root_seen = False

    for event, elem in ET.iterparse(str(path), events=("start", "end")):
        tag = elem.tag
        if event == "start":
            if not root_seen and tag == "CygnetResource":
                root_seen = True
                data.resource_id = elem.get("id", "")
                data.label = elem.get("label", "") or elem.get("id", "")
                data.language = elem.get("language", "")
                data.version = elem.get("version", "")
            continue

        if tag == "Concept":
            cid = elem.get("id", "")
            if cid in data.concepts:
                data.duplicate_concept_ids.append(cid)
            else:
                data.concepts[cid] = elem.get("ontological_category", "")
            elem.clear()

        elif tag == "Gloss":
            data.glossed.add(elem.get("definiendum", ""))
            elem.clear()

        elif tag == "Lexeme":
            eid = elem.get("id", "")
            if eid in data.entries:
                data.duplicate_entry_ids.append(eid)
            else:
                data.entries[eid] = elem.get("language", "")
                wf = elem.find("Wordform")
                if wf is not None:
                    data.entries_with_forms.add(eid)
                    data.entry_forms[eid] = wf.get("form", "")
            elem.clear()

        elif tag == "Sense":
            sid = elem.get("id", "")
            if sid in data.senses:
                data.duplicate_sense_ids.append(sid)
            else:
                signifier = elem.get("signifier", "")
                signified = elem.get("signified", "")
                data.senses[sid] = (signifier, signified)
                data.concept_senses.setdefault(signified, []).append(sid)
            elem.clear()

        elif tag == "ConceptRelation":
            data.concept_rels.append((
                elem.get("source", ""),
                elem.get("relation_type", ""),
                elem.get("target", ""),
            ))
            elem.clear()

        elif tag == "SenseRelation":
            data.sense_rels.append((
                elem.get("source", ""),
                elem.get("relation_type", ""),
                elem.get("target", ""),
            ))
            elem.clear()

        elif tag == "Example":
            ann = elem.find("AnnotatedSentence")
            if ann is not None:
                text = "".join(ann.itertext()).strip()[:80]
                sense_ids = frozenset(
                    child.get("sense") for child in ann if child.get("sense")
                )
                data.examples.append((text, sense_ids))
            elem.clear()

    return data


# ---------------------------------------------------------------------------
# Label helper
# ---------------------------------------------------------------------------

def label_concept(concept_id: str, data: WordnetData) -> str:
    """Return 'concept_id [local/en]' (or just 'concept_id' if no senses found).

    The local word is the first wordform in the wordnet's own language; the
    English word is the first wordform from an English sense.  If the wordnet
    language is already English, only one word is shown.

    Args:
        concept_id: The concept identifier to label (e.g. 'cili.i1234').
        data: Parsed wordnet data containing sense and entry information.

    Returns:
        Human-readable label string, e.g. 'cili.i1234 [anjing/dog]'.
    """
    wn_lang = data.language
    local_form: str | None = None
    en_form: str | None = None

    for sid in data.concept_senses.get(concept_id, []):
        eid, _ = data.senses[sid]
        form = data.entry_forms.get(eid, "")
        if not form:
            continue
        lang = data.entries.get(eid, "")
        if local_form is None and lang == wn_lang:
            local_form = form
        if en_form is None and lang == "en":
            en_form = form
        if local_form is not None and en_form is not None:
            break

    if local_form and en_form and wn_lang != "en":
        label = f"{local_form}/{en_form}"
    elif local_form:
        label = local_form
    elif en_form:
        label = en_form
    else:
        return concept_id
    return f"{concept_id} [{label}]"


# ---------------------------------------------------------------------------
# Cycle detection (Kahn's topological sort + DFS path reconstruction)
# ---------------------------------------------------------------------------

def _find_hypernym_cycles(data: WordnetData) -> list[list[str]]:
    """Return one representative cycle path per cyclic SCC in the hypernym graph."""
    hypernyms: dict[str, list[str]] = defaultdict(list)
    all_nodes: set[str] = set()
    for src, rel, tgt in data.concept_rels:
        if rel in HYPERNYM_TYPES:
            hypernyms[src].append(tgt)
            all_nodes.update((src, tgt))

    if not all_nodes:
        return []

    # Kahn's: propagate in-degree removals to find un-processable (cyclic) nodes
    in_degree: dict[str, int] = defaultdict(int)
    for targets in hypernyms.values():
        for tgt in targets:
            in_degree[tgt] += 1

    queue = [n for n in all_nodes if in_degree[n] == 0]
    acyclic: set[str] = set()
    while queue:
        node = queue.pop()
        acyclic.add(node)
        for tgt in hypernyms.get(node, []):
            in_degree[tgt] -= 1
            if in_degree[tgt] == 0:
                queue.append(tgt)

    cyclic = all_nodes - acyclic
    if not cyclic:
        return []

    # Reconstruct one path per connected component of cyclic nodes
    cycles: list[list[str]] = []
    visited: set[str] = set()
    for start in sorted(cyclic):
        if start in visited:
            continue
        path: list[str] = []
        in_path: dict[str, int] = {}
        node = start
        while node not in in_path and node in cyclic:
            in_path[node] = len(path)
            path.append(node)
            nxt = next((t for t in hypernyms.get(node, []) if t in cyclic), None)
            if nxt is None:
                break
            node = nxt
        if node in in_path:
            cycle = path[in_path[node]:] + [node]
            cycles.append(cycle)
            visited.update(cycle[:-1])

    return cycles


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_empty_entries(data: WordnetData) -> Issue | None:
    empty = sorted(data.entries.keys() - data.entries_with_forms)
    if not empty:
        return None

    # Count senses that would also be lost
    entry_set = set(empty)
    lost_senses = sum(
        1 for _sid, (eid, _cid) in data.senses.items() if eid in entry_set
    )

    return Issue(
        severity="CRITICAL",
        title="Word entries with no wordforms",
        total=len(empty),
        explanation=(
            f"Cygnet skips any <Lexeme> that has no <Wordform> children, "
            f"silently discarding {lost_senses} sense(s) that reference them."
        ),
        recommendation=(
            "Add at least one <Wordform form=\"...\"/> to each <Lexeme>. "
            "If the entry is intentionally empty, remove it and its <Sense> elements."
        ),
        items=empty[:MAX_EXAMPLES],
    )


def check_unglosssed_concepts(data: WordnetData) -> Issue | None:
    """Concepts declared in this file that have no <Gloss> in this file."""
    missing = sorted(cid for cid in data.concepts if cid not in data.glossed)
    if not missing:
        return None

    concept_sense_count: dict[str, int] = defaultdict(int)
    for _sid, (_eid, cid) in data.senses.items():
        if cid in data.concepts and cid not in data.glossed:
            concept_sense_count[cid] += 1
    affected_senses = sum(concept_sense_count.values())

    items = [
        f"{label_concept(cid, data)} ({data.concepts[cid]}, {concept_sense_count.get(cid, 0)} sense(s) lost)"
        for cid in missing[:MAX_EXAMPLES]
    ]
    return Issue(
        severity="CRITICAL",
        title="Concepts without definitions",
        total=len(missing),
        explanation=(
            "Cygnet requires every concept (synset) to have a definition. "
            f"These {len(missing)} concept(s) have no <Gloss> in this file and will be "
            f"removed, taking {affected_senses} sense(s) with them."
        ),
        recommendation=(
            "Add a <Gloss definiendum=\"CONCEPT_ID\" language=\"...\"> for each concept, "
            "or remove the concept and its senses if it is genuinely unused."
        ),
        items=items,
    )


def check_hypernym_cycles(data: WordnetData) -> Issue | None:
    cycles = _find_hypernym_cycles(data)
    if not cycles:
        return None

    all_cyclic: set[str] = set()
    for cycle in cycles:
        all_cyclic.update(cycle[:-1])

    items = [
        " → ".join(label_concept(node, data) for node in cycle)
        for cycle in cycles[:MAX_EXAMPLES]
    ]

    return Issue(
        severity="CRITICAL",
        title="Hypernym loops (cycles in the is-a hierarchy)",
        total=len(cycles),
        explanation=(
            f"A concept cannot be its own ancestor. "
            f"Cygnet finds {len(all_cyclic)} concept(s) involved in {len(cycles)} "
            f"hypernym cycle(s) and removes the relation that closes each loop. "
            "This causes silent data loss."
        ),
        recommendation=(
            "Check the hypernym direction for the concepts below. "
            "The correct direction is more-specific → more-general "
            "(e.g., 'dog hypernym animal', not 'animal hypernym dog'). "
            "Each line shows one complete cycle (last node = first node)."
        ),
        items=items,
    )


def check_self_loops(data: WordnetData) -> Issue | None:
    loops = sorted(
        {src for src, _rel, tgt in data.concept_rels if src == tgt}
        | {src for src, _rel, tgt in data.sense_rels if src == tgt}
    )
    if not loops:
        return None
    items = [
        label_concept(x, data) if x in data.concepts else x
        for x in loops[:MAX_EXAMPLES]
    ]
    return Issue(
        severity="WARNING",
        title="Self-referential relations",
        total=len(loops),
        explanation=(
            "These concepts or senses have a relation pointing to themselves. "
            "Self-loops are meaningless and will be silently skipped by Cygnet."
        ),
        recommendation="Remove any <ConceptRelation> or <SenseRelation> where source == target.",
        items=items,
    )


def check_internal_reversed_relations(data: WordnetData) -> Issue | None:
    """Detect cases where this file asserts both A→B and B→A for a directed relation."""
    seen: set[tuple[str, str, str]] = set()
    conflicts: list[str] = []
    already_reported: set[frozenset[str]] = set()

    for src, rel, tgt in data.concept_rels:
        if rel not in DIRECTED_RELATION_TYPES:
            continue
        key = frozenset({src, tgt})
        if (tgt, rel, src) in seen and key not in already_reported:
            conflicts.append(f"{src} {rel} {tgt}  ←→  {tgt} {rel} {src}")
            already_reported.add(key)
        seen.add((src, rel, tgt))

    if not conflicts:
        return None

    return Issue(
        severity="WARNING",
        title="Contradictory relation directions within this file",
        total=len(conflicts),
        explanation=(
            "This file asserts a directed relation and its reverse between the same pair "
            "of concepts — e.g., both 'A hypernym B' and 'B hypernym A'. "
            "Cygnet accepts the first and skips the second with a warning."
        ),
        recommendation=(
            "Pick one direction per pair. "
            "For hypernym/hyponym: more-specific → more-general (dog hypernym animal). "
            "Only assert one direction; Cygnet auto-generates the inverse."
        ),
        items=conflicts[:MAX_EXAMPLES],
    )


def check_dangling_senses(data: WordnetData) -> Issue | None:
    """Senses whose signifier (entry ID) is not declared in this file."""
    dangling = sorted(
        f"{sid}: signifier '{eid}' not declared in this file"
        for sid, (eid, _cid) in data.senses.items()
        if eid and eid not in data.entries
    )
    if not dangling:
        return None
    return Issue(
        severity="WARNING",
        title="Senses referencing undeclared entries",
        total=len(dangling),
        explanation=(
            "These senses reference a lexeme (word entry) ID that does not exist in this file. "
            "Cygnet will silently skip any sense whose entry cannot be resolved."
        ),
        recommendation=(
            "Ensure every <Sense signifier=\"...\"> value exactly matches the id= attribute "
            "of a <Lexeme> in this file. Check for typos or stale IDs."
        ),
        items=dangling[:MAX_EXAMPLES],
    )


def check_unmatched_examples(data: WordnetData) -> Issue | None:
    """Example sentences whose sense annotations don't reference any sense in this file."""
    known_senses = set(data.senses.keys())
    unmatched: list[str] = []
    for text, sense_ids in data.examples:
        if not sense_ids:
            unmatched.append(f'(no annotations) "{text}"')
        elif not (sense_ids & known_senses):
            unknown = ", ".join(sorted(sense_ids)[:3])
            unmatched.append(f'(unknown senses: {unknown}) "{text}"')
    if not unmatched:
        return None
    return Issue(
        severity="WARNING",
        title="Example sentences with no matching sense annotations",
        total=len(unmatched),
        explanation=(
            "Cygnet only stores an example sentence if at least one <AnnotatedToken sense=\"...\"> "
            "within it refers to a sense defined in this file. "
            "These examples have no matching annotations and will be discarded."
        ),
        recommendation=(
            "Annotate the key word in each sentence with the correct sense ID, "
            "for example: <AnnotatedToken sense=\"SENSE_ID\">word</AnnotatedToken>. "
            "If the example cannot be linked to a specific sense, remove it."
        ),
        items=unmatched[:MAX_EXAMPLES],
    )


def check_non_standard_relations(data: WordnetData) -> Issue | None:
    unknown = sorted(
        {f"concept relation '{r}'" for _, r, _ in data.concept_rels
         if r not in STANDARD_CONCEPT_RELATIONS}
        | {f"sense relation '{r}'" for _, r, _ in data.sense_rels
           if r not in STANDARD_SENSE_RELATIONS}
    )
    if not unknown:
        return None
    return Issue(
        severity="INFO",
        title="Non-standard relation types",
        total=len(unknown),
        explanation=(
            "These relation types are not in the standard Global WordNet Association (GWA) set. "
            "Cygnet stores them as-is but they may not be understood by other tools "
            "and will not appear in relation-type filters."
        ),
        recommendation=(
            "Where possible, map to standard GWA relation types "
            "(hypernym, hyponym, mero_member, antonym, derivation, …). "
            "See https://globalwordnet.github.io/schemas/ for the full list."
        ),
        items=unknown[:MAX_EXAMPLES],
    )


def check_duplicate_ids(data: WordnetData) -> list[Issue]:
    issues: list[Issue] = []
    for kind, ids in [
        ("concept", data.duplicate_concept_ids),
        ("entry",   data.duplicate_entry_ids),
        ("sense",   data.duplicate_sense_ids),
    ]:
        if not ids:
            continue
        unique_ids = sorted(set(ids))
        issues.append(Issue(
            severity="CRITICAL",
            title=f"Duplicate {kind} IDs",
            total=len(unique_ids),
            explanation=(
                f"The same {kind} ID appears more than once. "
                "Duplicate concept IDs cause a build crash; "
                "duplicate entry and sense IDs are silently merged or skipped."
            ),
            recommendation=f"Ensure every <{kind.capitalize()}> element has a unique id= attribute.",
            items=unique_ids[:MAX_EXAMPLES],
        ))
    return issues


def check_glossed_concepts_without_senses(data: WordnetData) -> Issue | None:
    """Concepts in this file that have a gloss but no senses pointing to them."""
    has_sense: set[str] = {cid for _sid, (_eid, cid) in data.senses.items()}
    orphan = sorted(
        cid for cid in data.concepts
        if cid in data.glossed and cid not in has_sense
    )
    if not orphan:
        return None
    return Issue(
        severity="INFO",
        title="Defined concepts with no senses in this file",
        total=len(orphan),
        explanation=(
            "These concepts have a definition but no word sense in this file links to them. "
            "If no other loaded wordnet supplies a sense either, they will exist as "
            "definition-only synsets (concepts with no associated words)."
        ),
        recommendation=(
            "If another wordnet is expected to provide senses for these concepts, "
            "this is likely fine. Otherwise, add <Sense> elements or remove the concepts."
        ),
        items=[label_concept(cid, data) for cid in orphan[:MAX_EXAMPLES]],
    )


# ---------------------------------------------------------------------------
# Log-file loading and parsing
# ---------------------------------------------------------------------------

def load_json_log(xml_path: Path) -> dict:
    """Load the per-file converter log (``{stem}_log.json``) alongside *xml_path*."""
    log_path = xml_path.with_name(xml_path.stem + "_log.json")
    if not log_path.exists():
        return {}
    with open(log_path) as f:
        return json.load(f)


def parse_conflicts_json(resource_id: str, xml_stem: str) -> tuple[list[dict], list[dict]]:
    """Load conflict records for this resource from the structured JSON conflict log.

    Args:
        resource_id: The wordnet's resource ID (e.g. ``'dn'``).
        xml_stem: The pre-synth filename stem (e.g. ``'dn-2025-07-03'``).

    Returns:
        ``(reversed_rels, cycles)`` — lists of record dicts for this resource.
    """
    if not CONFLICTS_JSON.exists():
        return [], []

    with open(CONFLICTS_JSON, encoding='utf-8') as fh:
        data = json.load(fh)

    reversed_rels = [
        r for r in data.get('reversed_relations', [])
        if r.get('resource_id') == resource_id
    ]
    cycles = [
        c for c in data.get('cycles', [])
        if c.get('xml_stem') == xml_stem
    ]
    return reversed_rels, cycles


# ---------------------------------------------------------------------------
# Checks derived from log files
# ---------------------------------------------------------------------------

def issues_from_json_log(log: dict) -> list[Issue]:
    """Turn converter-log entries into Issue objects."""
    if not log:
        return []

    issues: list[Issue] = []

    def _add(issue: Issue | None) -> None:
        if issue is not None:
            issues.append(issue)

    # POS mismatches between the synset's declared POS and the CILI concept's POS
    sc_mm = log.get("synset_concept_pos_mismatches", {})
    if sc_mm.get("total_count", 0):
        by_pair = sc_mm.get("by_pos_pair", {})
        items = [f"{pair}: {count} case(s)" for pair, count in sorted(by_pair.items())]
        _add(Issue(
            severity="WARNING",
            title="POS mismatches: synset vs CILI concept",
            total=sc_mm["total_count"],
            explanation=(
                "These synsets declare a part-of-speech that differs from what the "
                "Collaborative Interlingual Index (CILI) records for the same concept. "
                "Cygnet stores the CILI POS; the wordnet's POS is ignored."
            ),
            recommendation=(
                "Check whether the POS tags in your source data are correct. "
                "If the CILI record is wrong, consider filing a bug at "
                "https://github.com/globalwordnet/cili."
            ),
            items=items[:MAX_EXAMPLES],
        ))

    # POS mismatches between a lexeme's POS and its linked concept's POS
    lc_mm = log.get("lexeme_concept_pos_mismatches", {})
    if lc_mm.get("total_count", 0):
        by_pair = lc_mm.get("by_pos_pair", {})
        items = [f"{pair}: {count} case(s)" for pair, count in sorted(by_pair.items())]
        _add(Issue(
            severity="WARNING",
            title="POS mismatches: lexeme vs its concept",
            total=lc_mm["total_count"],
            explanation=(
                "These word entries (lexemes) are tagged with a part-of-speech that "
                "differs from the concept they sense. For example, a VERB entry linked "
                "to a NOUN concept. Cygnet stores the entry as-is but the mismatch "
                "may indicate an incorrect sense link."
            ),
            recommendation=(
                "Review entries whose POS does not match their concept. "
                "Either correct the POS tag on the lexeme or re-link it to the right concept."
            ),
            items=items[:MAX_EXAMPLES],
        ))

    # Relations dropped because the same relation was already established by another wordnet
    rp = log.get("relation_processing", {})
    skipped = rp.get("skipped_existing_relations", {}).get("concept_relations", {}).get("count", 0)
    if skipped:
        _add(Issue(
            severity="INFO",
            title="Concept relations already covered by another wordnet",
            total=skipped,
            explanation=(
                f"{skipped:,} concept relation(s) from this file were skipped because an "
                "identical relation was already loaded from a different wordnet (e.g. OEWN). "
                "This is usually harmless — Cygnet stores the relation once."
            ),
            recommendation=(
                "No action required unless you believe your relation direction differs "
                "from the one already in Cygnet. In that case, see the reversed-relation "
                "section of this report."
            ),
            items=[],
        ))

    # Category mismatches in relations (source/target have incompatible POS for the relation type)
    cat_mm = rp.get("ontological_category_mismatches", {}).get("count", 0)
    if cat_mm:
        _add(Issue(
            severity="WARNING",
            title="Relations with incompatible POS categories",
            total=cat_mm,
            explanation=(
                f"{cat_mm:,} relation(s) were skipped because the source and target concepts "
                "have part-of-speech categories that are incompatible with the relation type "
                "(e.g., a hypernym between a NOUN and a VERB)."
            ),
            recommendation=(
                "Review relations whose source and target are from different POS categories. "
                "Hypernym/hyponym chains should connect concepts of the same POS."
            ),
            items=[],
        ))

    # Synsets with no CILI mapping (stored under a wordnet-local ID)
    missing_cili = log.get("missing_cili_concepts", {}).get("count", 0)
    if missing_cili:
        _add(Issue(
            severity="INFO",
            title="Synsets without a CILI mapping",
            total=missing_cili,
            explanation=(
                f"{missing_cili:,} synset(s) in this wordnet have no entry in the "
                "Collaborative Interlingual Index (CILI) and are stored under a "
                "wordnet-local identifier. They will not be linked to concepts from "
                "other wordnets."
            ),
            recommendation=(
                "To interlink these synsets, submit them to CILI: "
                "https://github.com/globalwordnet/cili"
            ),
            items=[],
        ))

    # Senses whose synset could not be resolved during conversion
    sense_miss = log.get("sense_missing_synset", {}).get("count", 0)
    if sense_miss:
        _add(Issue(
            severity="WARNING",
            title="Senses with unresolvable synset (conversion time)",
            total=sense_miss,
            explanation=(
                f"{sense_miss:,} sense(s) were dropped during conversion because "
                "their synset ID could not be resolved. These senses are absent from "
                "the pre-synthesised file entirely."
            ),
            recommendation=(
                "Check your source LMF file for sense elements whose synset= attribute "
                "does not match any declared synset ID."
            ),
            items=[],
        ))

    # Example sentences that couldn't be matched to a sense during conversion
    ex_stats = log.get("statistics", {}).get("examples", {})
    ex_skipped = ex_stats.get("skipped", 0)
    if ex_skipped:
        failed = ex_stats.get("failed_matches", [])
        items = []
        for entry in failed[:MAX_EXAMPLES]:
            text = entry.get("text", "")[:70]
            forms = entry.get("candidate_wordforms", [])
            forms_str = ", ".join(f'"{f}"' for f in forms[:3])
            items.append(f'"{text}" (looked for: {forms_str})')
        _add(Issue(
            severity="WARNING",
            title="Example sentences not matched to a sense (conversion time)",
            total=ex_skipped,
            explanation=(
                f"{ex_skipped:,} example sentence(s) were discarded during conversion "
                "because the target word could not be found in the sentence text "
                "after morphological analysis. Note that some of these mismatches "
                "are due to limitations in our morphological analyser rather than "
                "errors in your data — we apologise for the false positives. "
                "These examples are absent from the pre-synthesised file."
            ),
            recommendation=(
                "Where the word (or an inflected form) genuinely appears in the "
                "sentence, no action is needed on your side — this is a known "
                "limitation of our analyser. Otherwise, check that the sentence "
                "has not been paraphrased or truncated."
            ),
            items=items,
        ))

    return issues


_CONCEPT_ID_RE = re.compile(r'[A-Za-z0-9_-]+\.i\d+')


def _label_ids_in(text: str, data: WordnetData) -> str:
    """Replace every concept ID in *text* with its labelled form."""
    return _CONCEPT_ID_RE.sub(lambda m: label_concept(m.group(), data), text)


def issues_from_conflicts_log(
    reversed_rels: list[dict], cycles: list[dict], data: WordnetData
) -> list[Issue]:
    """Build Issue objects from structured conflict records."""
    issues: list[Issue] = []

    if reversed_rels:
        other_resources: set[str] = set()
        items: list[str] = []
        for rec in reversed_rels[:MAX_EXAMPLES]:
            src, rel, tgt = rec['src'], rec['rel'], rec['tgt']
            other = rec.get('prior_resource', '?')
            items.append(
                f"{label_concept(src, data)} {rel} {label_concept(tgt, data)}"
                f"  (contradicts [{other}])"
            )
            other_resources.add(other)
        others_str = ", ".join(sorted(other_resources)) or "another wordnet"
        issues.append(Issue(
            severity="CRITICAL",
            title="Relations reversed relative to another wordnet",
            total=len(reversed_rels),
            explanation=(
                f"Cygnet skipped {len(reversed_rels):,} relation(s) from this wordnet "
                f"because they contradict relations already established by {others_str}. "
                "For example, if OEWN asserts 'dog hypernym animal', this wordnet must "
                "not assert 'animal hypernym dog' — that reverses an established fact."
            ),
            recommendation=(
                "Check the direction of the relations listed below. "
                "The standard convention is more-specific → more-general "
                "(hypernym points UP the hierarchy, not down). "
                "If you believe the other wordnet is wrong, contact its maintainers."
            ),
            items=items,
        ))

    if cycles:
        items = []
        for rec in cycles[:MAX_EXAMPLES]:
            src, rel, tgt = rec['src'], rec['rel'], rec['tgt']
            chain_ids = rec.get('chain', [])
            labelled_chain = " → ".join(label_concept(n, data) for n in chain_ids)
            items.append(
                f"{label_concept(src, data)} {rel} {label_concept(tgt, data)}"
                f"  (chain: {labelled_chain})"
            )
        issues.append(Issue(
            severity="CRITICAL",
            title="Hypernym cycles spanning multiple wordnets",
            total=len(cycles),
            explanation=(
                f"Cygnet removed {len(cycles):,} hypernym relation(s) from this wordnet "
                "because, together with relations from other wordnets already in the "
                "database, they formed a cycle in the IS-A hierarchy. "
                "The 'existing chain' shows the path that was already present."
            ),
            recommendation=(
                "For each entry below, the relation on the left was removed. "
                "Check whether the hypernym direction is correct. "
                "If the other wordnet's chain is wrong, report it there."
            ),
            items=items,
        ))

    return issues


# ---------------------------------------------------------------------------
# Runner and formatter
# ---------------------------------------------------------------------------

def run_checks(data: WordnetData) -> list[Issue]:
    issues: list[Issue] = []

    def _add(issue: Issue | None) -> None:
        if issue is not None:
            issues.append(issue)

    _add(check_empty_entries(data))
    _add(check_unglosssed_concepts(data))
    _add(check_hypernym_cycles(data))
    _add(check_self_loops(data))
    _add(check_internal_reversed_relations(data))
    _add(check_dangling_senses(data))
    _add(check_unmatched_examples(data))
    _add(check_non_standard_relations(data))
    issues.extend(check_duplicate_ids(data))
    _add(check_glossed_concepts_without_senses(data))

    return issues


def _fmt(n: int) -> str:
    return f"{n:,}"


def format_report(
    path: Path, data: WordnetData, issues: list[Issue], markdown: bool
) -> str:
    lines: list[str] = []
    title = f"{data.label} ({data.resource_id}) v{data.version} — language: {data.language}"
    stats = (
        f"Entries: {_fmt(len(data.entries))}  |  "
        f"Senses: {_fmt(len(data.senses))}  |  "
        f"Concepts: {_fmt(len(data.concepts))}  |  "
        f"Relations: {_fmt(len(data.concept_rels))}"
    )

    if markdown:
        lines += [f"# {title}", f"\n**File:** `{path}`\n", stats + "\n"]
    else:
        w = 70
        lines += ["═" * w, title, "═" * w, f"File: {path}", stats]

    if not issues:
        lines.append("\n✓  No issues found." if not markdown else "\n✓ No issues found.")
        return "\n".join(lines)

    for sev in ("CRITICAL", "WARNING", "INFO"):
        group = [i for i in issues if i.severity == sev]
        if not group:
            continue

        if markdown:
            lines.append(f"\n## {sev}")
        else:
            lines += [f"\n{'─' * 70}", f"  {sev}", f"{'─' * 70}"]

        for issue in group:
            remaining = issue.total - len(issue.items)
            if markdown:
                lines.append(f"\n### {issue.title} ({_fmt(issue.total)})")
                lines.append(f"\n{issue.explanation}")
                lines.append(f"\n**Recommendation:** {issue.recommendation}")
                if issue.items:
                    lines.append("\n**Examples:**")
                    for item in issue.items:
                        lines.append(f"- `{item}`")
                    if remaining > 0:
                        lines.append(f"- _(and {_fmt(remaining)} more…)_")
            else:
                lines.append(f"\n  [{_fmt(issue.total)}]  {issue.title}")
                lines.append(f"  {issue.explanation}")
                lines.append(f"  → {issue.recommendation}")
                if issue.items:
                    for item in issue.items:
                        lines.append(f"      • {item}")
                    if remaining > 0:
                        lines.append(f"      • … and {_fmt(remaining)} more")

    return "\n".join(lines)


def report_file(path: Path, markdown: bool = False) -> None:
    """Parse, check, and print a report for one XML file.

    Returns without printing anything for the CILI file, which is an
    infrastructure resource rather than a wordnet.
    """
    data = parse_xml(path)
    if data.resource_id == "cili":
        return
    issues = run_checks(data)

    # Augment with converter log (created at pre-synth time)
    json_log = load_json_log(path)
    issues.extend(issues_from_json_log(json_log))

    # Augment with merge-time relation conflicts log
    reversed_rels, cycles = parse_conflicts_json(data.resource_id, path.stem)
    issues.extend(issues_from_conflicts_log(reversed_rels, cycles, data))

    print(format_report(path, data, issues, markdown))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Report data-quality issues in a pre-synthesised Cygnet XML wordnet file.",
    )
    parser.add_argument("files", nargs="*", type=Path, help="XML file(s) to check")
    parser.add_argument("--all", action="store_true",
                        help=f"Check all *.xml files in {PRESYNTH_DIR}")
    parser.add_argument("--md", action="store_true", help="Output in Markdown format")
    args = parser.parse_args()

    paths: list[Path] = list(args.files)
    if args.all:
        paths = sorted(PRESYNTH_DIR.glob("*.xml"))

    if not paths:
        parser.print_help()
        sys.exit(0)

    for i, p in enumerate(paths):
        if not p.exists():
            print(f"File not found: {p}", file=sys.stderr)
            continue
        if i > 0:
            print()
        report_file(p, args.md)


if __name__ == "__main__":
    main()
