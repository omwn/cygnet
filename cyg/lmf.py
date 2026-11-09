"""What both sides of the pipeline know about WN-LMF.

The relation-type tables, the confidence threshold and the
relation-direction swap config are needed by the per-source cleaning
step (today ``cyg.converters``) and by the merge (``cyg.merge``) alike, and
by the exporter; they live here so that neither imports the other for
them.  See TODO.md, "Replace the Cygnet XML schema with GWA WN-LMF".
"""

from __future__ import annotations

import json
from pathlib import Path

from lxml import etree

from cyg.multilex import dc_attr, fix_language

__all__ = [
    'CONCEPT_INVERSE_ONLY_TYPES',
    'CONCEPT_REVERSE_RELATIONS',
    'CONCEPT_SYMMETRIC_TYPES',
    'CONFIDENCE_THRESHOLD',
    'DIRECTION_SWAP_CONFIG_FILENAME',
    'INVERSE_CONCEPT_RELATIONS',
    'INVERSE_SENSE_RELATIONS',
    'SENSE_INVERSE_ONLY_TYPES',
    'SENSE_REVERSE_RELATIONS',
    'SENSE_SYMMETRIC_TYPES',
    'below_confidence_threshold',
    'fix_language',
    'inverse_only_types',
    'is_forward_relation',
    'load_direction_swap_config',
    'symmetric_types',
]

# Maps each forward relation type to its inverse (or itself if symmetric, None if no inverse).
# Used to deduplicate, add missing inverses, and identify canonical direction.
# Aligned with wn.constants.REVERSE_RELATIONS — see https://github.com/goodmami/wn

SENSE_REVERSE_RELATIONS: dict[str, str | None] = {
    # Symmetric
    'antonym':           'antonym',
    'also':              'also',
    'similar':           'similar',
    'derivation':        'derivation',
    'anto_gradable':     'anto_gradable',
    'anto_simple':       'anto_simple',
    'anto_converse':     'anto_converse',
    # Asymmetric
    'domain_topic':      'has_domain_topic',
    'domain_region':     'has_domain_region',
    'exemplifies':       'is_exemplified_by',
    'feminine':          'has_feminine',
    'masculine':         'has_masculine',
    'young':             'has_young',
    'diminutive':        'has_diminutive',
    'augmentative':      'has_augmentative',
    'simple_aspect_ip':  'simple_aspect_pi',
    'secondary_aspect_ip': 'secondary_aspect_pi',
    'metaphor':          'has_metaphor',
    'metonym':           'has_metonym',
    # No inverse
    'pertainym':         None,
    'participle':        None,
    'agent':             None,
    'body_part':         None,
    'by_means_of':       None,
    'destination':       None,
    'event':             None,
    'instrument':        None,
    'location':          None,
    'material':          None,
    'property':          None,
    'result':            None,
    'state':             None,
    'undergoer':         None,
    'uses':              None,
    'vehicle':           None,
    'other':             None,
}

CONCEPT_REVERSE_RELATIONS: dict[str, str | None] = {
    # Taxonomic
    'hypernym':          'hyponym',
    'instance_hypernym': 'instance_hyponym',
    # Meronymy
    'meronym':           'holonym',
    'mero_member':       'holo_member',
    'mero_part':         'holo_part',
    'mero_substance':    'holo_substance',
    'mero_location':     'holo_location',
    'mero_portion':      'holo_portion',
    # Causality / entailment
    'causes':            'is_caused_by',
    'entails':           'is_entailed_by',
    'subevent':          'is_subevent_of',
    # State / manner
    'state_of':          'be_in_state',
    'manner_of':         'in_manner',
    # Domain
    'domain_topic':      'has_domain_topic',
    'domain_region':     'has_domain_region',
    'domain':            'has_domain',
    'exemplifies':       'is_exemplified_by',
    # Classification
    'classifies':        'classified_by',
    'restricts':         'restricted_by',
    # Roles
    'role':              'involved',
    'agent':             'involved_agent',
    'patient':           'involved_patient',
    'result':            'involved_result',
    'instrument':        'involved_instrument',
    'location':          'involved_location',
    'direction':         'involved_direction',
    'target_direction':  'involved_target_direction',
    'source_direction':  'involved_source_direction',
    # Co-roles
    'co_agent_patient':      'co_patient_agent',
    'co_agent_instrument':   'co_instrument_agent',
    'co_agent_result':       'co_result_agent',
    'co_patient_instrument': 'co_instrument_patient',
    'co_result_instrument':  'co_instrument_result',
    # Morphological / gender
    'feminine':          'has_feminine',
    'masculine':         'has_masculine',
    'young':             'has_young',
    'diminutive':        'has_diminutive',
    'augmentative':      'has_augmentative',
    # Aspect (Slavic wordnets)
    'simple_aspect_ip':    'simple_aspect_pi',
    'secondary_aspect_ip': 'secondary_aspect_pi',
    # Figurative
    'metaphor':          'has_metaphor',
    'metonym':           'has_metonym',
    # Pronoun possession (omw-pronoun: relType="other" with dc:type)
    'possessive':        'possessor',
    # Symmetric
    'antonym':           'antonym',
    'similar':           'similar',
    'attribute':         'attribute',
    'eq_synonym':        'eq_synonym',
    'ir_synonym':        'ir_synonym',
    'co_role':           'co_role',
    'also':              'also',
    'anto_gradable':     'anto_gradable',
    'anto_simple':       'anto_simple',
    'anto_converse':     'anto_converse',
    'derivation':        'derivation',
    # No inverse
    'pertainym':         None,
    'participle':        None,
    'constitutive':      None,
    'other':             None,
}

# Forward type -> inverse type, for the types that have one.
INVERSE_SENSE_RELATIONS: dict[str, str] = {
    rel: inv for rel, inv in SENSE_REVERSE_RELATIONS.items() if inv is not None
}
INVERSE_CONCEPT_RELATIONS: dict[str, str] = {
    rel: inv for rel, inv in CONCEPT_REVERSE_RELATIONS.items() if inv is not None
}


def inverse_only_types(reverse: dict[str, str | None]) -> frozenset[str]:
    """The relation types that exist only as the inverse of a forward type.

    'hyponym', 'holo_part' or 'has_feminine' are values of *reverse* but not
    keys: a file that stores relations in the forward direction only never
    writes them, and a reader that implies inverses can regenerate them.

    Args:
        reverse: SENSE_REVERSE_RELATIONS or CONCEPT_REVERSE_RELATIONS.

    Returns:
        The inverse-only type names.
    """
    return frozenset(
        inv for fwd, inv in reverse.items()
        if inv is not None and inv != fwd and inv not in reverse
    )


def symmetric_types(reverse: dict[str, str | None]) -> frozenset[str]:
    """The relation types that are their own inverse ('antonym', 'similar').

    Args:
        reverse: SENSE_REVERSE_RELATIONS or CONCEPT_REVERSE_RELATIONS.

    Returns:
        The symmetric type names.
    """
    return frozenset(fwd for fwd, inv in reverse.items() if inv == fwd)


SENSE_INVERSE_ONLY_TYPES = inverse_only_types(SENSE_REVERSE_RELATIONS)
CONCEPT_INVERSE_ONLY_TYPES = inverse_only_types(CONCEPT_REVERSE_RELATIONS)
SENSE_SYMMETRIC_TYPES = symmetric_types(SENSE_REVERSE_RELATIONS)
CONCEPT_SYMMETRIC_TYPES = symmetric_types(CONCEPT_REVERSE_RELATIONS)


def is_forward_relation(
    rel_type: str, source, target, reverse: dict[str, str | None],
) -> bool:
    """Whether a stored relation is the one copy to write when inverses
    are implied by the reader.

    The database holds both directions of every reversible relation.  A
    file that stores one direction only writes a relation unless its type
    is inverse-only (the other direction carries it), and writes a
    symmetric relation once, from the lower id.  A type the tables do not
    know (a source-specific code such as ``plwn:19``) has no inverse to
    rely on and is written.

    Args:
        rel_type: The relation type as stored.
        source: The source's id, comparable with *target* (rowids, say).
        target: The target's id.
        reverse: SENSE_REVERSE_RELATIONS or CONCEPT_REVERSE_RELATIONS.

    Returns:
        True if this copy should be written.
    """
    inv = reverse.get(rel_type)
    if inv is None:
        return rel_type not in inverse_only_types(reverse)
    if inv == rel_type:
        return source < target
    return True


# Default location of the direction-swap config written by
# scripts/check_all_relation_directions.py --write-swap-config, read by
# WordNetToCygnetConverter when its caller doesn't pass an explicit config.
DIRECTION_SWAP_CONFIG_FILENAME = 'relation_direction_swaps.json'


def load_direction_swap_config(
    path: Path,
) -> tuple[dict[str, frozenset[str]], dict[str, dict[str, dict]]]:
    """Load a direction-swap config written by
    scripts/check_all_relation_directions.py --write-swap-config.

    Several resources' exports have a relation type (sometimes several)
    with source and target systematically swapped relative to every other
    wordnet in Cygnet -- e.g. plWordnet's mero_part/mero_member (925/935
    and 398/398 comparable pairs reversed vs OEWN) or own-pt's hypernym
    (30,496/30,498 reversed). scripts/check_all_relation_directions.py
    finds these automatically by comparing each candidate wordnet's
    relations against OEWN on shared ILI concepts, and writes the
    resources/relation-types that cleared its reversal threshold here
    (including each flagged type's GWA inverse, e.g. mero_part's
    holo_part, which must be swapped alongside it or per-file
    inverse-generation regenerates the original bugged direction from the
    untouched side -- see build_swap_config() in that script).

    Args:
        path: Path to the swap config JSON. Missing is not an error (e.g.
            running the converter standalone, without the pre-check step)
            -- it just means nothing gets swapped.

    Returns:
        (swap_sets, stats): swap_sets maps a lexicon id to the relation
        types to swap source/target for; stats maps a lexicon id to
        {relation_type: {reversed, comparable, reversed_share}} for the
        types that were directly measured (not the paired inverse added
        only because its forward type was flagged), for logging.
    """
    if not path.exists():
        return {}, {}
    data = json.loads(path.read_text(encoding='utf-8'))
    swap_sets = {
        lexicon_id: frozenset(
            rel_type for rel_type, info in relations.items() if info.get('swapped')
        )
        for lexicon_id, relations in data.items()
    }
    stats = {
        lexicon_id: {
            rel_type: {k: v for k, v in info.items() if k != 'swapped'}
            for rel_type, info in relations.items()
            if info.get('comparable') is not None
        }
        for lexicon_id, relations in data.items()
    }
    return swap_sets, stats


# A source's own confidenceScore on a Synset/Sense/Relation (0-1, defaults to
# 1.0 when unset -- most sources never set it) below this is dropped. A
# simple hard cutoff, not configurable per resource/UI yet: some sources
# (e.g. Estonian wordnet) put it on nearly every synset with real variance
# (0.5 vs 1.0), others barely use it or only ever report 1.0. Revisit once/if
# cygnet moves to an LMF-based intermediate format that can carry per-row
# confidence through to the DB for a client-side filter instead (see
# TODO.md's "Replace the Cygnet XML schema with GWA WN-LMF").
CONFIDENCE_THRESHOLD = 0.6


def below_confidence_threshold(elem: etree.Element) -> bool:
    """True if *elem*'s own confidenceScore (or dc:confidence) is set and
    below CONFIDENCE_THRESHOLD. Unset or unparsable defaults to 1.0 (never
    below threshold) -- most sources never set it at all.
    """
    raw = elem.get('confidenceScore') or dc_attr(elem, 'confidence')
    if not raw:
        return False
    try:
        return float(raw) < CONFIDENCE_THRESHOLD
    except ValueError:
        return False
