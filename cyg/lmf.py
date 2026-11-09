"""What both sides of the pipeline know about WN-LMF.

The relation-type tables, the confidence threshold and the
relation-direction swap config are needed by the per-source cleaning
step (today ``cyg.converters``) and by the merge (``cyg.merge``) alike, and
by the exporter; they live here so that neither imports the other for
them.  See TODO.md, "Replace the Cygnet XML schema with GWA WN-LMF".
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from lxml import etree

from cyg.multilex import dc_attr, encode_for_xml_id, fix_language

__all__ = [
    'CONCEPT_FORWARD_OF_INVERSE',
    'CONCEPT_INVERSE_ONLY_TYPES',
    'CONCEPT_REVERSE_RELATIONS',
    'CONCEPT_SYMMETRIC_TYPES',
    'CONFIDENCE_THRESHOLD',
    'DIRECTION_SWAP_CONFIG_FILENAME',
    'DTD_SENSE_RELATION_TYPES',
    'DTD_SYNSET_RELATION_TYPES',
    'INVERSE_CONCEPT_RELATIONS',
    'INVERSE_SENSE_RELATIONS',
    'POS_CODES',
    'POS_LABELS',
    'SENSE_FORWARD_OF_INVERSE',
    'SENSE_INVERSE_ONLY_TYPES',
    'SENSE_REVERSE_RELATIONS',
    'SENSE_SYMMETRIC_TYPES',
    'below_confidence_threshold',
    'canonical_relation',
    'entry_written_forms',
    'fix_language',
    'forward_of_inverse',
    'inverse_only_types',
    'is_forward_relation',
    'lexeme_id',
    'load_direction_swap_config',
    'note_offsets',
    'parse_note',
    'pos_code',
    'pos_label',
    'relation_subtype',
    'relation_type',
    'shorten_uri',
    'symmetric_types',
]

# The relType values the WN-LMF 1.4 DTD allows; any other type is written
# as relType="other" with the type in dc:type (see relation_type()).
DTD_SYNSET_RELATION_TYPES = frozenset("""
agent also attribute be_in_state causes classified_by classifies co_agent_instrument
co_agent_patient co_agent_result co_instrument_agent co_instrument_patient
co_instrument_result co_patient_agent co_patient_instrument co_result_agent
co_result_instrument co_role direction domain_region domain_topic exemplifies entails
eq_synonym has_domain_region has_domain_topic is_exemplified_by holo_location holo_member
holo_part holo_portion holo_substance holonym hypernym hyponym in_manner instance_hypernym
instance_hyponym instrument involved involved_agent involved_direction involved_instrument
involved_location involved_patient involved_result involved_source_direction
involved_target_direction is_caused_by is_entailed_by location manner_of mero_location
mero_member mero_part mero_portion mero_substance meronym similar other patient
restricted_by restricts result role source_direction state_of target_direction subevent
is_subevent_of antonym feminine has_feminine masculine has_masculine young has_young
diminutive has_diminutive augmentative has_augmentative anto_gradable anto_simple
anto_converse ir_synonym
""".split())
DTD_SENSE_RELATION_TYPES = frozenset("""
antonym also participle pertainym derivation domain_topic has_domain_topic domain_region
has_domain_region exemplifies is_exemplified_by similar other simple_aspect_ip
secondary_aspect_ip simple_aspect_pi secondary_aspect_pi feminine has_feminine masculine
has_masculine young has_young diminutive has_diminutive augmentative has_augmentative
anto_gradable anto_simple anto_converse metaphor has_metaphor metonym has_metonym agent
material event instrument location by_means_of undergoer property result state uses
destination body_part vehicle
""".split())

# WN-LMF partOfSpeech code -> the label the database stores.  's' (adjective
# satellite) is an adjective; anything else unknown is 'u'.
POS_LABELS = {
    'n': 'NOUN',
    'v': 'VERB',
    'a': 'ADJ',
    'r': 'ADV',
    's': 'ADJ',
    'c': 'CONJ',
    'p': 'ADP',
    'x': 'NREF',
    'u': 'UNK',
}


POS_CODES = {label: code for code, label in POS_LABELS.items() if code != 's'}


def pos_code(label: str | None) -> str:
    """The LMF partOfSpeech code for a database label ('NOUN' -> 'n').

    Args:
        label: The label as stored.

    Returns:
        The code; 'u' for an unknown label.
    """
    return POS_CODES.get(label or '', 'u')


def pos_label(pos: str | None) -> str:
    """The database's label for an LMF partOfSpeech code.

    Args:
        pos: The code as written in the file ('n', 's', '' ...).

    Returns:
        The label; 'UNK' for a missing or unknown code.
    """
    return POS_LABELS.get(pos or '', POS_LABELS['u'])


def entry_written_forms(entry: etree._Element) -> list[str]:
    """A LexicalEntry's distinct written forms, lemma first, the rest
    sorted, as they identify the entry.

    Args:
        entry: A LexicalEntry element (Lemma, Form and ExternalForm children).

    Returns:
        The forms; empty if the entry has no lemma with a written form.
    """
    lemma = entry.find('Lemma')
    lemma_form = lemma.get('writtenForm') if lemma is not None else None
    if not lemma_form:
        return []
    others = {
        form.get('writtenForm')
        for form in entry.iterchildren('Form', 'ExternalForm')
        if form.get('writtenForm')
    }
    others.discard(lemma_form)
    return [lemma_form, *sorted(others)]


def lexeme_id(language: str, pos: str, forms: list[str]) -> str:
    """The id under which the merge unifies entries across files: one per
    (language, POS label, set of written forms).

    Args:
        language: The lexicon's language code.
        pos: The POS label (see pos_label()).
        forms: The written forms, as entry_written_forms() orders them.

    Returns:
        An id such as 'en.NOUN.dog-domestic_dog'.
    """
    return f"{language}.{pos}.{'-'.join(encode_for_xml_id(f) for f in forms)}"


def shorten_uri(value: str) -> str:
    """The local name of a URI-shaped value: its last path segment or
    fragment (e.g. 'https://w3id.org/own/schema/agent' -> 'agent').

    Args:
        value: A relation type as written by the source.

    Returns:
        The local name, or *value* unchanged if it isn't URI-shaped.
    """
    if '://' not in value:
        return value
    return re.split(r'[/#]', value.rstrip('/'))[-1]


def relation_type(rel: etree._Element) -> str:
    """A relation's type: its dc:type when relType is 'other' and one is given.

    LMF expresses relations outside its fixed list as relType="other" with
    dc:type naming the real relation (e.g. "possessive").  Keeping only
    'other' would merge distinct relations and make inverse pairs look like
    reversed duplicates.  A source that uses its own ontology's URI instead
    of a bare word (own-pt's "https://w3id.org/own/schema/agent") is
    shortened to its local name, so it still matches the catalog.

    Args:
        rel: A SynsetRelation or SenseRelation element.

    Returns:
        The type name.
    """
    rel_type = rel.get('relType')
    if rel_type != 'other':
        return rel_type
    raw = dc_attr(rel, 'type')
    return shorten_uri(raw) if raw else rel_type


def relation_subtype(rel: etree._Element, resolved_type: str) -> tuple[str | None, str | None]:
    """A finer-grained sub-relation code and/or human-readable label the
    source gives beyond *resolved_type*, or (None, None) when there is none.

    Some sources attach a dc:type more specific than their relType
    (plWordnet's relType="diminutive" dc:type="plwn:56", a particular
    diminutive subtype), which relation_type() does not surface since it
    only substitutes dc:type for relType="other".  dc:description (or
    dc:relation, for the one case that names the general relation family
    instead of the specific sub-code -- plWordnet's "unresolved aspect
    orientation" export-compatibility relations) gives a label for it, in
    the source's own language.  A URI-shaped dc:type (own-pt's ontology) is
    another spelling of the type itself, not a finer subtype.

    Args:
        rel: A SynsetRelation or SenseRelation element.
        resolved_type: What relation_type() returned for it.

    Returns:
        (subtype, label), each None when absent.
    """
    raw = dc_attr(rel, 'type')
    subtype = raw if raw and '://' not in raw and raw != resolved_type else None
    label = dc_attr(rel, 'description') or dc_attr(rel, 'relation')
    return subtype, label


def parse_note(note: str | None) -> dict[str, str]:
    """The key=value pairs of a note attribute, split on ';'.

    Cygnet writes its own keys ('offsets', 'merged') after whatever the
    source had (TUFS uses "R=...;F=...", EstWN "@is_primary").  Parts
    without '=' are not keys and are ignored; a repeated key keeps its first
    value, the source's.

    Args:
        note: The note attribute, or None.

    Returns:
        {key: value}.
    """
    pairs: dict[str, str] = {}
    for part in (note or '').split(';'):
        key, sep, value = part.strip().partition('=')
        if sep and key and key not in pairs:
            pairs[key] = value.strip()
    return pairs


def note_offsets(note: str | None, text: str) -> list[tuple[int, int]]:
    """The spans an Example's note marks as the annotated sense's word.

    'offsets' is a space-separated list of 'start,end' pairs: 0-based code
    point offsets into *text*, end exclusive.  A malformed or out-of-range
    span is dropped.

    Args:
        note: The Example's note attribute.
        text: The Example's text.

    Returns:
        The valid (start, end) spans, in the order written.
    """
    raw = parse_note(note).get('offsets')
    if not raw:
        return []
    spans = []
    for pair in raw.split():
        start, sep, end = pair.partition(',')
        if not sep or not start.isdigit() or not end.isdigit():
            continue
        start_i, end_i = int(start), int(end)
        if start_i < end_i <= len(text):
            spans.append((start_i, end_i))
    return spans

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


def forward_of_inverse(reverse: dict[str, str | None]) -> dict[str, str]:
    """Inverse-only type -> its forward type ('hyponym' -> 'hypernym').

    Args:
        reverse: SENSE_REVERSE_RELATIONS or CONCEPT_REVERSE_RELATIONS.

    Returns:
        The mapping, for the inverse-only types (see inverse_only_types()).
    """
    only = inverse_only_types(reverse)
    return {inv: fwd for fwd, inv in reverse.items() if inv in only}


SENSE_FORWARD_OF_INVERSE = forward_of_inverse(SENSE_REVERSE_RELATIONS)
CONCEPT_FORWARD_OF_INVERSE = forward_of_inverse(CONCEPT_REVERSE_RELATIONS)


def canonical_relation(
    rel_type: str, source, target, forward_of: dict[str, str],
) -> tuple[str, object, object]:
    """A relation in the forward direction: an inverse-only type is turned
    round ('A hyponym B' becomes 'B hypernym A'), anything else is kept.

    Args:
        rel_type: The relation type as written.
        source: The source id.
        target: The target id.
        forward_of: SENSE_FORWARD_OF_INVERSE or CONCEPT_FORWARD_OF_INVERSE.

    Returns:
        (type, source, target).
    """
    forward = forward_of.get(rel_type)
    if forward is None:
        return rel_type, source, target
    return forward, target, source
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
