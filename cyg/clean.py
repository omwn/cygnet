"""Clean one source wordnet into the best WN-LMF 1.4 file we can make of it.

Step 2 of the pipeline (TODO.md, "Replace the Cygnet XML schema with GWA
WN-LMF").  The cleaner edits the source rather than rebuilding it: anything
it has no opinion on (``status``, ``confidenceScore``, ``dc:*``, ``Tag``,
``Count``, ``SyntacticBehaviour``, existing notes ...) passes through.  It
makes two streaming passes so that a 100 MB source never becomes a 2 GB
tree: pass A (:meth:`LmfCleaner._index`) reads everything a decision needs
and decides, pass B (:meth:`LmfCleaner._write`) re-streams the file and
writes each element out, edited or dropped as decided.  Between them the
examples are linked to senses with :class:`cyg.linker.ExampleLinker`.

The passes, in order of importance (later ones see clean input):

1. metadata (language fix or override, known licence errors, the exact
   header ``wn`` requires);
2. text normalisation (NFC, whitespace, double-encoded HTML entities,
   blank Definitions and Examples);
3. structure (invalid POS -> ``u``, entries without a lemma, senses whose
   synset is missing, explicit Definition language);
4. dedup and trimming (entries with the same forms merged, duplicate
   senses collapsed, low-confidence elements dropped, repeated or unknown
   ILIs cleared, a mul member's ILIs filled in from its concepts lexicon);
5. relations (direction swaps, forward direction only, symmetric once,
   duplicates, self-loops and dangling targets removed);
6. example linking (a synset-level Example moves under the Sense whose
   word it contains, the word's span in ``note="offsets=start,end"``);
7. version and write (``.🦢`` appended to the version of a changed file,
   a pass-through copied as-is).
"""

from __future__ import annotations

import argparse
import copy
import html
import json
import logging
import shutil
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from cyg.cili import CiliIndex
from cyg.linker import EXAMPLE_BATCH_SIZE, ExampleLinker
from cyg.lmf import (
    CONCEPT_FORWARD_OF_INVERSE,
    CONCEPT_REVERSE_RELATIONS,
    DC_NS,
    DOCTYPE,
    LEXICON_TAGS,
    SENSE_FORWARD_OF_INVERSE,
    SENSE_REVERSE_RELATIONS,
    XML_DECL,
    below_confidence_threshold,
    collapse_whitespace,
    entry_written_forms,
    format_offsets,
    is_forward_relation,
    iter_lmf,
    parse_note,
    pos_label,
    relation_element,
    serialize,
    set_note_key,
    shorten_uri,
    start_tag,
)
from cyg.multilex import (
    DC_NAMESPACES,
    SharedConcepts,
    dc_attr,
    dc_identifier,
    fix_language,
    open_xml,
)
from cyg.presynth import log_path

logger = logging.getLogger(__name__)

# Appended to Lexicon@version when the cleaner changed the file.
CHANGED_SUFFIX = '.🦢'
LEXICON_NOTE = ('cleaned by Cygnet; inverse relations omitted; '
                'changes listed in {stem}_log.json')
FORMAT = 'wn-lmf-1.4'
# Known errors in source headers, by lexicon id.  Remove an entry once the
# source is fixed upstream.
METADATA_FIXES: dict[str, dict[str, str]] = {
    # Confirmed with the authors: CC BY-SA 4.0.
    'wordnet_lv': {'license': 'https://creativecommons.org/licenses/by-sa/4.0/'},
}
# DC attributes the cleaner carries over when it moves a relation.
_REL_KEEP = {'relType', 'target'}


def normalise_text(text: str | None) -> str:
    """A text as the cleaned file stores it: NFC, single spaces, no
    double-encoded HTML entities.

    Args:
        text: The source text (None for an empty element).

    Returns:
        The normalised text ('' for nothing).
    """
    if not text:
        return ''
    return collapse_whitespace(html.unescape(unicodedata.normalize('NFC', text)))


@dataclass
class CleanOptions:
    """How one source is cleaned beyond the defaults.

    Attributes:
        language: Language code to write instead of the source's (a member
            of a mul release takes the code the build uses for its language).
        shared: The concepts of a mul release, for its concepts lexicon or a
            member.
        requires: (id, version) of the concepts lexicon a member depends on,
            written as a ``Requires`` element.
        direction_swaps: Relation types whose source and target this
            lexicon has systematically reversed.
        swap_stats: The measurements behind *direction_swaps*, for the log.
    """

    language: str | None = None
    shared: SharedConcepts | None = None
    requires: tuple[str, str] | None = None
    direction_swaps: frozenset[str] = frozenset()
    swap_stats: dict[str, dict] = field(default_factory=dict)


@dataclass(slots=True)
class _Synset:
    id: str
    ili: str                # as it will be written (decided in pass 4)
    pos: str                # partOfSpeech code as it will be written
    kept: bool
    identifier: str | None
    language: str           # the lexicon's language, for definitions without one
    senses: list[str] = field(default_factory=list)   # surviving sense ids


@dataclass(slots=True)
class _Entry:
    id: str
    forms: list[str]        # normalised; lemma first, the rest sorted
    pos: str                # POS code as written in the cleaned file
    survivor: str | None    # this id, the entry it merges into, or None if dropped
    senses: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _Sense:
    id: str
    entry: str
    synset: str
    survivor: str | None    # this id, the sense it collapses into, or None


@dataclass(slots=True)
class _Relation:
    rel_type: str           # relType as written
    dc_type: str | None
    target: str
    attrs: dict[str, str] | None   # attributes beyond relType and target, if any
    kept: bool              # above the confidence threshold


@dataclass(slots=True)
class CleanResult:
    """What cleaning one source produced."""

    output: Path
    log: Path
    changed: bool


class LmfCleaner:
    """Cleans one WN-LMF source file; see the module docstring."""

    def __init__(self, source: Path, cili: CiliIndex,
                 options: CleanOptions | None = None) -> None:
        """Set up a cleaner.

        Args:
            source: The source file (.xml, .xml.gz or .xml.xz).
            cili: The ILIs that exist, with their parts of speech.
            options: Per-source options.
        """
        self.source = Path(source)
        self.cili = cili
        self.options = options or CleanOptions()
        self.log: dict = self._new_log()
        self.changes: dict[str, int] = defaultdict(int)
        # Pass A results
        self.lexicons: list[dict] = []          # one dict of header facts per Lexicon
        self.synsets: dict[str, _Synset] = {}
        self.entries: dict[str, _Entry] = {}
        self.senses: dict[str, _Sense] = {}
        self.synset_rels: dict[str, list[_Relation]] = defaultdict(list)
        self.sense_rels: dict[str, list[_Relation]] = defaultdict(list)
        self.final_synset_rels: dict[str, dict[tuple, tuple]] = {}
        self.final_sense_rels: dict[str, dict[tuple, tuple]] = {}
        self._entry_survivors: dict[tuple, str] = {}
        self.absorbed_entries: dict[str, list[str]] = defaultdict(list)
        self.collapsed_senses: dict[str, list[str]] = defaultdict(list)
        # Children stashed from absorbed duplicates, for the survivor
        self.extra_senses: dict[str, list[etree._Element]] = defaultdict(list)
        self.extra_sense_children: dict[str, list[tuple[str, etree._Element]]] = defaultdict(list)
        self.extra_form_children: dict[tuple[str, str], list[etree._Element]] = defaultdict(list)
        self.extra_scripts: dict[tuple[str, str], str] = {}
        # Examples: (owner id, index) -> normalised text, and linking results
        self.synset_examples: list[tuple[str, int, str]] = []
        self.sense_examples: list[tuple[str, int, str]] = []
        self.moved_examples: dict[str, list[tuple[str, int, int]]] = defaultdict(list)
        self.moved_from_synset: set[tuple[str, int]] = set()
        self.sense_offsets: dict[tuple[str, int], tuple[int, int]] = {}
        # offsets the source (or an earlier cleaning) already wrote
        self.given_offsets: dict[tuple[str, int], str] = {}
        self.dc_ns = DC_NS
        self.header_ok = False

    # ------------------------------------------------------------------ log

    @staticmethod
    def _new_log() -> dict:
        return {
            'format': FORMAT,
            'unchanged': False,
            'invalid_pos_values': {},
            'synset_concept_pos_mismatches': {'total_count': 0, 'by_pos_pair': {}},
            'lexeme_concept_pos_mismatches': {'total_count': 0, 'by_pos_pair': {}},
            'lexeme_merging': {'total_merges': 0},
            'relation_processing': {
                'unknown_relation_types': {'sense_relations': {'count': 0},
                                           'concept_relations': {'count': 0}},
                'duplicates_removed': {'sense_relations': {'count': 0},
                                       'concept_relations': {'count': 0}},
                'self_loops_removed': {'sense_relations': {'count': 0},
                                       'concept_relations': {'count': 0}},
                'dangling_removed': {'sense_relations': {'count': 0},
                                     'concept_relations': {'count': 0}},
                'inverse_direction_turned': {'sense_relations': {'count': 0},
                                             'concept_relations': {'count': 0}},
                'direction_swapped': {'sense_relations': {'count': 0},
                                      'concept_relations': {'count': 0}},
            },
            'missing_cili_concepts': {'count': 0},
            'repeated_ili': {'count': 0, 'synsets': {}},
            'missing_lemmas': {'count': 0},
            'entries_without_senses': 0,
            'low_confidence_skipped': {'synsets': 0, 'senses': 0, 'definitions': 0,
                                       'relations': 0},
            'shared_concept_not_found': {'count': 0},
            'sense_missing_synset': {'count': 0},
            'synset_not_found': {'count': 0},
            'duplicate_senses': {'count': 0},
            'blank_removed': {'definitions': 0, 'examples': 0},
            'text_normalised': {'forms': 0, 'definitions': 0, 'examples': 0,
                                'pronunciations': 0},
            'metadata_changes': {},
            'statistics': {
                'concepts': {'newly_created': 0, 'from_cili': 0, 'total': 0},
                'lexemes': {'created': 0},
                'senses': {'created': 0},
                'glosses': {'created': 0},
                'relations': {'sense_relations_created': 0,
                              'concept_relations_created': 0},
                'examples': {'total_found': 0, 'processed': 0, 'skipped': 0,
                             'success_rate_pct': 0.0, 'failed_matches': [],
                             'moved_to_sense': 0, 'left_on_synset': 0},
                'html_entity_fixes': {'count': 0},
                'direction_swaps': {},
            },
        }

    def _changed(self, what: str, n: int = 1) -> None:
        self.changes[what] += n

    # --------------------------------------------------------------- driver

    def clean(self, out_dir: Path) -> CleanResult:
        """Clean the source into *out_dir*.

        Args:
            out_dir: Where the cleaned file and its log go.

        Returns:
            The paths written and whether the file changed.
        """
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        self._index()
        self._decide_synsets()
        self._decide_relations()
        self._link_examples()
        lexicon = self.lexicons[0]
        version = (lexicon['version'] or '').removesuffix(CHANGED_SUFFIX)
        stem = f"{lexicon['id']}-{version}"
        output = out_dir / f'{stem}.xml'
        changed = bool(self.changes) or not self.header_ok
        if changed:
            self._write(output, stem)
        else:
            with open_xml(self.source) as src, open(output, 'wb') as dst:
                shutil.copyfileobj(src, dst)
        self.log['unchanged'] = not changed
        self.log['changes'] = dict(sorted(self.changes.items()))
        self.log['output'] = output.name
        log_file = log_path(output)
        log_file.write_text(json.dumps(self.log, indent=2, ensure_ascii=False),
                            encoding='utf-8')
        return CleanResult(output, log_file, changed)

    # --------------------------------------------------------------- pass A

    def _check_header(self) -> None:
        """Note whether the source already has the header wn requires."""
        with open_xml(self.source) as f:
            head = f.read(400).decode('utf-8', 'replace')
        lines = [line.strip() for line in head.splitlines() if line.strip()]
        self.header_ok = (len(lines) >= 2 and lines[0] == XML_DECL
                          and lines[1] in (DOCTYPE, DOCTYPE.replace('https://', 'http://')))
        if not self.header_ok:
            self.log['metadata_changes']['header'] = 'XML declaration and DOCTYPE written as wn requires'

    def _index(self) -> None:
        """Pass A: read the headers, synsets, entries, senses, relations and
        example texts, and decide what merges, moves or goes."""
        self._check_header()
        current: dict | None = None
        requires_version: str | None = None   # of the source's Requires for the concepts lexicon
        for event, elem in iter_lmf(self.source, (*LEXICON_TAGS, 'Requires', 'LexicalEntry', 'Synset')):
            if event == 'start':
                if elem.tag in LEXICON_TAGS:
                    current = self._index_lexicon(elem)
                continue
            if elem.getparent() is None or elem.getparent().tag not in LEXICON_TAGS:
                continue
            if elem.tag == 'Requires':
                if self.options.requires and elem.get('ref') == self.options.requires[0]:
                    requires_version = elem.get('version') or ''
            elif elem.tag == 'LexicalEntry':
                self._index_entry(elem, current)
            elif elem.tag == 'Synset':
                self._index_synset(elem, current)
        if not self.lexicons:
            raise ValueError(f'{self.source}: no Lexicon found')
        if self.options.requires and requires_version != self.options.requires[1]:
            self._changed('metadata.requires')
            self.log['metadata_changes']['requires'] = {
                'from': None if requires_version is None else f'{self.options.requires[0]} {requires_version}',
                'to': '%s %s' % self.options.requires}

    def _index_lexicon(self, elem: etree._Element) -> dict:
        for ns in DC_NAMESPACES:
            if ns in elem.nsmap.values():
                self.dc_ns = ns
                break
        lexicon_id = elem.get('id')
        language = fix_language(elem.get('language'))
        if self.options.language:
            language = self.options.language
        attrs = dict(elem.attrib)
        metadata: dict[str, tuple[str | None, str]] = {}
        if language != elem.get('language'):
            metadata['language'] = (elem.get('language'), language)
        for key, value in METADATA_FIXES.get(lexicon_id, {}).items():
            if elem.get(key) != value:
                metadata[key] = (elem.get(key), value)
        for key, (_old, new) in metadata.items():
            attrs[key] = new
            self._changed(f'metadata.{key}')
        if metadata:
            self.log['metadata_changes'].update(
                {k: {'from': old, 'to': new} for k, (old, new) in metadata.items()})
        info = {'id': lexicon_id, 'version': elem.get('version'), 'language': language,
                'attrs': attrs, 'tag': elem.tag}
        self.lexicons.append(info)
        return info

    def _index_synset(self, elem: etree._Element, lexicon: dict) -> None:
        sid = elem.get('id')
        kept = not below_confidence_threshold(elem)
        if not kept:
            self.log['low_confidence_skipped']['synsets'] += 1
            self._changed('synsets.low_confidence')
        info = _Synset(sid, elem.get('ili') or '', elem.get('partOfSpeech') or '', kept,
                       dc_identifier(elem), lexicon['language'])
        self.synsets[sid] = info
        if not kept:
            return
        for definition in elem.iterchildren('Definition'):
            text = definition.text or ''
            norm = normalise_text(text)
            if below_confidence_threshold(definition):
                self.log['low_confidence_skipped']['definitions'] += 1
                self._changed('definitions.low_confidence')
            elif not norm:
                self.log['blank_removed']['definitions'] += 1
                self._changed('definitions.blank')
            else:
                self._note_normalised(text, norm, 'definitions')
                self.log['statistics']['glosses']['created'] += 1
                if not definition.get('language'):
                    self._changed('definitions.language_added')
                elif fix_language(definition.get('language')) != definition.get('language'):
                    self._changed('definitions.language_fixed')
        if info.ili not in ('', 'in') and elem.find('ILIDefinition') is not None:
            self._changed('synsets.ili_definition_spurious')
        for rel in elem.iterchildren('SynsetRelation'):
            self.synset_rels[sid].append(self._relation(rel))
        n = 0
        for example in elem.iterchildren('Example'):
            text = example.text or ''
            norm = normalise_text(text)
            if not norm:
                self.log['blank_removed']['examples'] += 1
                self._changed('examples.blank')
                continue
            self._note_normalised(text, norm, 'examples')
            self.synset_examples.append((sid, n, norm))
            n += 1

    def _relation(self, rel: etree._Element) -> _Relation:
        kept = not below_confidence_threshold(rel)
        attrs = {k: v for k, v in rel.attrib.items() if k not in _REL_KEEP}
        return _Relation(rel.get('relType') or '', dc_attr(rel, 'type'),
                         rel.get('target') or '', attrs or None, kept)

    def _note_normalised(self, text: str, norm: str, what: str) -> None:
        if text == norm:
            return
        self.log['text_normalised'][what] += 1
        self._changed(f'{what}.normalised')
        if html.unescape(text) != text:
            self.log['statistics']['html_entity_fixes']['count'] += 1

    def _index_entry(self, elem: etree._Element, lexicon: dict) -> None:
        eid = elem.get('id')
        lemma = elem.find('Lemma')
        raw_forms = entry_written_forms(elem)
        if lemma is None or not raw_forms:
            self.log['missing_lemmas']['count'] += 1
            self._changed('entries.no_lemma')
            return
        forms = []
        for form in raw_forms:
            norm = normalise_text(form)
            self._note_normalised(form, norm, 'forms')
            if norm and norm not in forms:
                forms.append(norm)
        for pron in elem.iter('Pronunciation'):
            text = pron.text or ''
            if text != text.strip():
                self.log['text_normalised']['pronunciations'] += 1
                self._changed('pronunciations.normalised')
        pos = lemma.get('partOfSpeech') or ''
        if pos_label(pos) == 'UNK' and pos != 'u':
            self.log['invalid_pos_values'][pos] = self.log['invalid_pos_values'].get(pos, 0) + 1
            self._changed('pos.invalid')
            pos = 'u'
        key = (lexicon['id'], pos, frozenset(forms))
        survivor = self._entry_survivors.get(key)
        if survivor is None:
            self._entry_survivors[key] = eid
            survivor = eid
            self.log['statistics']['lexemes']['created'] += 1
        else:
            self.log['lexeme_merging']['total_merges'] += 1
            self._changed('entries.merged')
            self.absorbed_entries[survivor].append(eid)
            self._stash_forms(elem, survivor)
        entry = _Entry(eid, forms, pos, survivor)
        self.entries[eid] = entry
        for sense in elem.iterchildren('Sense'):
            self._index_sense(sense, entry)

    def _stash_forms(self, elem: etree._Element, survivor: str) -> None:
        """Keep an absorbed entry's pronunciations, tags and scripts for the
        survivor's matching Lemma or Form."""
        for form in elem.iterchildren('Lemma', 'Form'):
            written = normalise_text(form.get('writtenForm'))
            for child in form:
                self.extra_form_children[(survivor, written)].append(copy.deepcopy(child))
            if form.get('script'):
                self.extra_scripts.setdefault((survivor, written), form.get('script'))

    def _index_sense(self, sense: etree._Element, entry: _Entry) -> None:
        sid = sense.get('id')
        synset = sense.get('synset') or ''
        info = _Sense(sid, entry.id, synset, None)
        self.senses[sid] = info
        if below_confidence_threshold(sense):
            self.log['low_confidence_skipped']['senses'] += 1
            self._changed('senses.low_confidence')
            return
        if not synset:
            self.log['sense_missing_synset']['count'] += 1
            self._changed('senses.no_synset')
            return
        for rel in sense.iterchildren('SenseRelation'):
            self.sense_rels[sid].append(self._relation(rel))
        n = 0
        for example in sense.iterchildren('Example'):
            text = example.text or ''
            norm = normalise_text(text)
            if not norm:
                self.log['blank_removed']['examples'] += 1
                self._changed('examples.blank')
                continue
            self._note_normalised(text, norm, 'examples')
            self.sense_examples.append((sid, n, norm))
            given = parse_note(example.get('note')).get('offsets')
            if given is not None:
                self.given_offsets[(sid, n)] = given
            n += 1
        # A sense of the same entry (or of an entry merged into it) for the
        # same synset already exists: collapse into it.
        survivor_entry = self.entries[entry.survivor]
        for other_id in survivor_entry.senses:
            if self.senses[other_id].synset == synset:
                info.survivor = other_id
                self.log['duplicate_senses']['count'] += 1
                self._changed('senses.duplicate')
                self.collapsed_senses[other_id].append(sid)
                self.extra_sense_children[other_id].extend(
                    (sid, copy.deepcopy(child)) for child in sense)
                return
        info.survivor = sid
        survivor_entry.senses.append(sid)
        if entry.survivor != entry.id:
            self.extra_senses[entry.survivor].append(copy.deepcopy(sense))

    # ------------------------------------------------------------ decisions

    def _decide_synsets(self) -> None:
        """Pass 4 for synsets: validate ILIs, fill a member's in, fix POS."""
        counts: dict[str, list[str]] = defaultdict(list)
        for info in self.synsets.values():
            if info.kept and info.ili not in ('', 'in'):
                counts[info.ili].append(info.id)
        repeated = {ili: ids for ili, ids in counts.items() if len(ids) > 1}
        if repeated and self.options.shared is None:
            self.log['repeated_ili'] = {'count': len(repeated), 'synsets': repeated}
            for ili, ids in repeated.items():
                logger.warning('  ILI %s shared by %s: cleared', ili, ', '.join(ids))
        shared = self.options.shared
        for info in self.synsets.values():
            if not info.kept:
                continue
            ili = info.ili
            if shared is not None and info.identifier:
                if info.identifier not in shared.concepts:
                    self.log['shared_concept_not_found']['count'] += 1
                elif shared.concepts[info.identifier] in self.cili:
                    ili = shared.concepts[info.identifier]
            if ili not in ('', 'in'):
                if shared is None and ili in repeated:
                    ili = ''
                    self._changed('synsets.ili_repeated')
                elif ili not in self.cili:
                    self.log['missing_cili_concepts']['count'] += 1
                    ili = ''
                    self._changed('synsets.ili_unknown')
            if ili and ili != info.ili:
                self._changed('synsets.ili_filled')
            info.ili = ili
            if pos_label(info.pos) == 'UNK' and info.pos != 'u':
                self.log['invalid_pos_values'][info.pos] = self.log['invalid_pos_values'].get(info.pos, 0) + 1
                self._changed('pos.invalid')
                info.pos = 'u'
            stats = self.log['statistics']['concepts']
            if ili and ili != 'in':
                stats['from_cili'] += 1
                cili_pos = self.cili.pos(ili) or ''
                if pos_label(info.pos) != cili_pos:
                    mm = self.log['synset_concept_pos_mismatches']
                    mm['total_count'] += 1
                    pair = f'synset_{pos_label(info.pos)}-cili_{cili_pos}'
                    mm['by_pos_pair'][pair] = mm['by_pos_pair'].get(pair, 0) + 1
            else:
                stats['newly_created'] += 1
            stats['total'] += 1
        # Senses: drop those whose synset is unknown or dropped; record the
        # survivors on their synsets (for example linking).
        for sense in self.senses.values():
            if sense.survivor is None:
                continue
            synset = self.synsets.get(sense.synset)
            if synset is None:
                self.log['synset_not_found']['count'] += 1
                self._changed('senses.unknown_synset')
                sense.survivor = None
            elif not synset.kept:
                self.log['synset_not_found']['count'] += 1
                self._changed('senses.synset_dropped')
                sense.survivor = None
            elif sense.survivor == sense.id:
                synset.senses.append(sense.id)
                self.log['statistics']['senses']['created'] += 1
                entry = self.entries[sense.entry]
                if pos_label(entry.pos) != pos_label(synset.pos):
                    mm = self.log['lexeme_concept_pos_mismatches']
                    mm['total_count'] += 1
                    pair = f'lexeme_{pos_label(entry.pos)}-concept_{pos_label(synset.pos)}'
                    mm['by_pos_pair'][pair] = mm['by_pos_pair'].get(pair, 0) + 1
        for sense in self.senses.values():
            if sense.survivor is not None and sense.survivor != sense.id:
                # collapsed into a sense that may itself have been dropped
                target = self.senses[sense.survivor]
                if target.survivor is None:
                    sense.survivor = None
        self._prune_dropped_senses()

    def _prune_dropped_senses(self) -> None:
        """Take dropped senses off their entries' survivor lists, and drop a
        surviving entry left with no sense at all (wn's W201)."""
        for entry in self.entries.values():
            entry.senses = [s for s in entry.senses if self.senses[s].survivor == s]
        for entry in self.entries.values():
            if entry.survivor == entry.id and not entry.senses:
                entry.survivor = None
                self.log['entries_without_senses'] += 1
                self._changed('entries.no_senses')

    def _sense_target(self, sid: str) -> str | None:
        info = self.senses.get(sid)
        return info.survivor if info else None

    def _synset_target(self, sid: str) -> str | None:
        info = self.synsets.get(sid)
        return sid if info and info.kept else None

    def _decide_relations(self) -> None:
        """Pass 5: the relations each surviving synset and sense ends up with."""
        self.final_synset_rels = self._clean_relations(
            self.synset_rels, self._synset_target, CONCEPT_REVERSE_RELATIONS,
            CONCEPT_FORWARD_OF_INVERSE, 'concept_relations')
        self.final_sense_rels = self._clean_relations(
            self.sense_rels, self._sense_target, SENSE_REVERSE_RELATIONS,
            SENSE_FORWARD_OF_INVERSE, 'sense_relations')
        self.synset_rels.clear()
        self.sense_rels.clear()
        stats = self.log['statistics']['relations']
        stats['concept_relations_created'] = sum(len(r) for r in self.final_synset_rels.values())
        stats['sense_relations_created'] = sum(len(r) for r in self.final_sense_rels.values())
        if self.options.direction_swaps:
            self.log['statistics']['direction_swaps'] = {
                rel: self.options.swap_stats.get(rel)
                for rel in sorted(self.options.direction_swaps)}

    def _clean_relations(self, collected, target_of, reverse, forward_of, key
                         ) -> dict[str, dict[tuple, tuple]]:
        rp = self.log['relation_processing']
        final: dict[str, dict[tuple, tuple]] = defaultdict(dict)
        for source_id, rels in collected.items():
            source = target_of(source_id)
            for rel in rels:
                if not rel.kept:
                    self.log['low_confidence_skipped']['relations'] += 1
                    self._changed('relations.low_confidence')
                    continue
                if source is None:
                    self._changed('relations.of_removed')
                    continue
                target = target_of(rel.target)
                if target is None:
                    rp['dangling_removed'][key]['count'] += 1
                    self._changed('relations.dangling')
                    continue
                resolved = rel.rel_type
                if resolved == 'other' and rel.dc_type:
                    resolved = shorten_uri(rel.dc_type)
                if resolved not in reverse and resolved not in forward_of:
                    rp['unknown_relation_types'][key]['count'] += 1
                src, tgt = source, target
                attrs = rel.attrs
                rel_type = rel.rel_type
                if resolved in self.options.direction_swaps:
                    src, tgt = tgt, src
                    rp['direction_swapped'][key]['count'] += 1
                    self._changed('relations.swapped')
                if resolved in forward_of:
                    resolved = forward_of[resolved]
                    src, tgt = tgt, src
                    rel_type = resolved
                    if attrs:
                        attrs = {k: v for k, v in attrs.items()
                                 if not (k.endswith('}type') and v == rel.dc_type)} or None
                    rp['inverse_direction_turned'][key]['count'] += 1
                    self._changed('relations.inverse_turned')
                if src == tgt:
                    rp['self_loops_removed'][key]['count'] += 1
                    self._changed('relations.self_loop')
                    continue
                if not is_forward_relation(resolved, src, tgt, reverse):
                    # symmetric, declared from the higher id: keep it on the lower
                    src, tgt = tgt, src
                    self._changed('relations.symmetric_turned')
                if (resolved, tgt) in final[src]:
                    rp['duplicates_removed'][key]['count'] += 1
                    self._changed('relations.duplicate')
                    continue
                final[src][(resolved, tgt)] = (rel_type, attrs)
        return final

    # ------------------------------------------------------- example linking

    def _link_examples(self) -> None:
        """Pass 6: move synset-level examples under the sense whose word
        they contain, and locate each sense example's word."""
        stats = self.log['statistics']['examples']
        total = len(self.synset_examples) + len(self.sense_examples)
        stats['total_found'] = total
        if not total:
            return
        language = self.lexicons[0]['language']
        linker = ExampleLinker(language)
        linker.load()
        forms_of_sense = self._forms_of_senses()
        jobs: list[tuple[str, int, str, list[tuple[str, str]], bool]] = []
        for sid, n, text in self.synset_examples:
            candidates = [(sense, form) for sense in self.synsets[sid].senses
                          for form in forms_of_sense.get(sense, ())]
            jobs.append((sid, n, text, candidates, True))
        for sense_id, n, text in self.sense_examples:
            sense = self._sense_target(sense_id)
            candidates = [(sense, form) for form in forms_of_sense.get(sense, ())] if sense else []
            jobs.append((sense_id, n, text, candidates, False))
        for start in range(0, len(jobs), EXAMPLE_BATCH_SIZE):
            batch = jobs[start:start + EXAMPLE_BATCH_SIZE]
            linker.prefetch_docs([(text, candidates) for _o, _n, text, candidates, _s in batch])
            for owner, n, text, candidates, is_synset in batch:
                located = linker.locate(text, candidates) if candidates else None
                if located is None:
                    if not is_synset and (owner, n) in self.given_offsets:
                        stats['processed'] += 1   # the source already located it
                        continue
                    stats['skipped'] += 1
                    if len(stats['failed_matches']) < 1000:
                        stats['failed_matches'].append(
                            {'text': text, 'candidate_wordforms': [f for _s, f in candidates]})
                    if is_synset:
                        stats['left_on_synset'] += 1
                    continue
                sense, a, b = located
                stats['processed'] += 1
                if is_synset:
                    self.moved_examples[sense].append((text, a, b))
                    self.moved_from_synset.add((owner, n))
                    stats['moved_to_sense'] += 1
                    self._changed('examples.moved_to_sense')
                else:
                    given = self.given_offsets.get((owner, n))
                    if given is None:
                        self.sense_offsets[(owner, n)] = (a, b)
                        self._changed('examples.offsets_added')
                    elif given != f'{a},{b}':
                        stats['offsets_kept_from_source'] = stats.get('offsets_kept_from_source', 0) + 1
            if (start + EXAMPLE_BATCH_SIZE) % 10000 == 0:
                logger.info('    Linked %d/%d examples', start + EXAMPLE_BATCH_SIZE, total)
        linker.prefetched_docs = {}
        if total:
            stats['success_rate_pct'] = round(100 * stats['processed'] / total, 1)

    def _forms_of_senses(self) -> dict[str, list[str]]:
        """Surviving sense id -> written forms of its (merged) entry."""
        return {
            sense.id: self._entry_of(sense).forms
            for sense in self.senses.values() if sense.survivor == sense.id
        }

    def _entry_of(self, sense: _Sense) -> _Entry:
        """The entry a surviving sense is written under."""
        entry = self.entries[sense.entry]
        return self.entries[entry.survivor] if entry.survivor else entry

    # --------------------------------------------------------------- pass B

    def _write(self, output: Path, stem: str) -> None:
        """Pass B: re-stream the source, writing each element edited or not.

        Elements are re-indented by lxml as they are written, so the
        formatting of what the cleaner touched is uniform.
        """
        tmp = output.with_suffix('.xml.part')
        lexicon_index = -1
        requires_due = False
        with open(tmp, 'w', encoding='utf-8', newline='') as out:
            out.write(f'{XML_DECL}\n{DOCTYPE}\n<LexicalResource xmlns:dc="{self.dc_ns}">\n')
            for event, elem in iter_lmf(self.source, (*LEXICON_TAGS, 'Requires', 'LexicalEntry',
                                                      'Synset', 'SyntacticBehaviour')):
                if event == 'start':
                    if elem.tag in LEXICON_TAGS:
                        lexicon_index += 1
                        out.write(self._lexicon_start(self.lexicons[lexicon_index], stem))
                        requires_due = self.options.requires is not None
                    continue
                if elem.tag in LEXICON_TAGS:
                    out.write(f'  </{elem.tag}>\n')
                    continue
                if elem.getparent() is None or elem.getparent().tag not in LEXICON_TAGS:
                    continue
                if requires_due and (elem.tag != 'Requires' or elem.get('ref') == self.options.requires[0]):
                    # written at the version the cleaned concepts lexicon carries,
                    # in place of the source's own Requires for it if there is one
                    ref, version = self.options.requires
                    out.write(serialize(etree.Element('Requires', ref=ref, version=version)))
                    requires_due = False
                    if elem.tag == 'Requires':
                        continue
                piece = self._rewrite(elem)
                if piece is not None:
                    out.write(piece)
            out.write('</LexicalResource>\n')
        tmp.replace(output)

    def _lexicon_start(self, lexicon: dict, stem: str) -> str:
        attrs = dict(lexicon['attrs'])
        version = attrs.get('version') or ''
        if not version.endswith(CHANGED_SUFFIX):
            attrs['version'] = version + CHANGED_SUFFIX
        note = LEXICON_NOTE.format(stem=stem)
        existing = attrs.get('note')
        if existing and 'cleaned by Cygnet' not in existing:
            note = f'{existing}; {note}'
        attrs['note'] = note
        elem = etree.Element(lexicon['tag'], nsmap={'dc': self.dc_ns})
        for key, value in attrs.items():
            elem.set(key, value)
        return start_tag(elem)

    def _serialize(self, elem: etree._Element) -> str:
        etree.indent(elem, space='  ', level=2)
        return serialize(elem)

    def _rewrite(self, elem: etree._Element) -> str | None:
        """The text to write for one top-level child of a Lexicon, or None
        to drop it."""
        if elem.tag == 'LexicalEntry':
            return self._rewrite_entry(elem)
        if elem.tag == 'Synset':
            return self._rewrite_synset(elem)
        if elem.tag == 'SyntacticBehaviour' and elem.get('senses'):
            kept = dict.fromkeys(s for s in map(self._sense_target, elem.get('senses').split()) if s)
            elem.set('senses', ' '.join(kept))
        return self._serialize(elem)

    def _rewrite_entry(self, elem: etree._Element) -> str | None:
        eid = elem.get('id')
        entry = self.entries.get(eid)
        if entry is None or entry.survivor != eid:
            return None
        elem.find('Lemma').set('partOfSpeech', entry.pos)
        for form in elem.iterchildren('Lemma', 'Form'):
            written = normalise_text(form.get('writtenForm'))
            form.set('writtenForm', written)
            for pron in form.iterchildren('Pronunciation'):
                if pron.text:
                    pron.text = pron.text.strip()
            self._merge_form_extras(form, (eid, written))
        merged = self.absorbed_entries.get(eid)
        if merged:
            set_note_key(elem, 'merged', ' '.join(merged))
        for sense in list(elem.iterchildren('Sense')):
            if not self._rewrite_sense(sense):
                elem.remove(sense)
        for extra in self.extra_senses.get(eid, ()):
            if self._rewrite_sense(extra):
                self._insert_sense(elem, extra)
        return self._serialize(elem)

    def _merge_form_extras(self, form: etree._Element, key: tuple[str, str]) -> None:
        """Add an absorbed entry's pronunciations and tags to a form."""
        extras = self.extra_form_children.get(key)
        if key in self.extra_scripts and not form.get('script'):
            form.set('script', self.extra_scripts[key])
        if not extras:
            return
        existing = {(c.tag, c.get('variety'), (c.text or '').strip()) for c in form}
        for child in extras:
            sig = (child.tag, child.get('variety'), (child.text or '').strip())
            if sig in existing:
                continue
            if child.tag == 'Pronunciation' and child.get('audio') and not (child.text or '').strip():
                without = [c for c in form.iterchildren('Pronunciation') if not c.get('audio')]
                if len(without) == 1:
                    without[0].set('audio', child.get('audio'))
                    continue
            existing.add(sig)
            tags = [c for c in form if c.tag == 'Tag']
            if child.tag == 'Pronunciation' and tags:
                tags[0].addprevious(child)      # DTD order: Pronunciation*, Tag*
            else:
                form.append(child)

    @staticmethod
    def _insert_sense(entry: etree._Element, sense: etree._Element) -> None:
        """Append a Sense before any SyntacticBehaviour (DTD order)."""
        frames = [c for c in entry if c.tag == 'SyntacticBehaviour']
        if frames:
            frames[0].addprevious(sense)
        else:
            entry.append(sense)

    def _rewrite_sense(self, sense: etree._Element) -> bool:
        """Edit a Sense in place; False if it is dropped.

        Relations are written from the cleaned set (pass 5), then the
        examples: the sense's own and those of senses collapsed into it,
        normalised and with offsets where the word was found, then the
        synset examples moved here (pass 6); Counts stay last, as the DTD
        wants.
        """
        sid = sense.get('id')
        info = self.senses.get(sid)
        if info is None or info.survivor != sid:
            return False
        own_examples = [c for c in sense if c.tag == 'Example']
        for child in list(sense):
            if child.tag in ('SenseRelation', 'Example'):
                sense.remove(child)
        extras = self.extra_sense_children.get(sid, ())
        sense.extend(child for _dup, child in extras if child.tag not in ('SenseRelation', 'Example'))
        pieces = [relation_element('SenseRelation', rel_type, tgt, attrs=attrs)
                  for (_resolved, tgt), (rel_type, attrs)
                  in self.final_sense_rels.get(sid, {}).items()]
        pieces += self._sense_examples(sid, own_examples)
        for dup in self.collapsed_senses.get(sid, ()):
            pieces += self._sense_examples(
                dup, [child for d, child in extras if d == dup and child.tag == 'Example'])
        for text, a, b in self.moved_examples.get(sid, ()):
            example = etree.Element('Example')
            example.text = text
            set_note_key(example, 'offsets', format_offsets([(a, b)]))
            pieces.append(example)
        counts = [c for c in sense if c.tag == 'Count']
        for piece in pieces:
            if counts:
                counts[0].addprevious(piece)
            else:
                sense.append(piece)
        return True

    def _sense_examples(self, owner: str, examples: list[etree._Element]
                        ) -> list[etree._Element]:
        """A sense's own Example elements, normalised, blank ones gone, with
        offsets where pass 6 found the word (keyed as pass A numbered them)."""
        kept = [(ex, normalise_text(ex.text)) for ex in examples]
        kept = [(ex, text) for ex, text in kept if text]
        for n, (example, text) in enumerate(kept):
            example.text = text
            span = self.sense_offsets.get((owner, n))
            if span:
                set_note_key(example, 'offsets', format_offsets([span]))
        return [example for example, _text in kept]

    def _rewrite_synset(self, elem: etree._Element) -> str | None:
        sid = elem.get('id')
        info = self.synsets.get(sid)
        if info is None or not info.kept:
            return None
        elem.set('ili', info.ili)
        elem.set('partOfSpeech', info.pos)
        for definition in list(elem.iterchildren('Definition')):
            text = normalise_text(definition.text)
            if below_confidence_threshold(definition) or not text:
                elem.remove(definition)
                continue
            definition.text = text
            definition.set('language', fix_language(definition.get('language')) or info.language)
        for ili_def in list(elem.iterchildren('ILIDefinition')):
            # an ILIDefinition proposes a definition for a *new* ILI; one on a
            # synset that has an ILI is spurious (wn's W304)
            if info.ili not in ('', 'in'):
                elem.remove(ili_def)
            elif ili_def.text:
                ili_def.text = normalise_text(ili_def.text)
        if elem.get('members'):
            kept = dict.fromkeys(m for m in map(self._sense_target, elem.get('members').split()) if m)
            elem.set('members', ' '.join(kept))
        for rel in list(elem.iterchildren('SynsetRelation')):
            elem.remove(rel)
        n = 0   # counts the non-blank examples, as _index_synset() numbered them
        for example in list(elem.iterchildren('Example')):
            text = normalise_text(example.text)
            if not text:
                elem.remove(example)
                continue
            if (sid, n) in self.moved_from_synset:
                elem.remove(example)
            else:
                example.text = text
            n += 1
        remaining = [c for c in elem if c.tag == 'Example']
        for (_resolved, tgt), (rel_type, attrs) in self.final_synset_rels.get(sid, {}).items():
            rel = relation_element('SynsetRelation', rel_type, tgt, attrs=attrs)
            if remaining:
                remaining[0].addprevious(rel)
            else:
                elem.append(rel)
        return self._serialize(elem)


def validate_lmf(path: Path, fatal: str = 'E', ignore: tuple[str, ...] = ('W404',)
                 ) -> tuple[list[str], list[str]]:
    """Check a cleaned file with wn: it must load, and its E-code checks
    must pass; W-codes are reported.

    Args:
        path: The cleaned LMF file.
        fatal: Check categories that count as errors.
        ignore: Check codes to leave out (W404, a missing reverse relation,
            is by design: inverses are omitted).

    Returns:
        (errors, warnings), each a list of 'CODE: message (n items)'.
    """
    import wn
    import wn.lmf
    import wn.validate

    errors: list[str] = []
    warnings: list[str] = []
    try:
        resource = wn.lmf.load(str(path), progress_handler=None)
    except Exception as exc:
        return [f'load: {exc}'], []
    for lexicon in resource.get('lexicons', []):
        if lexicon.get('extends'):
            warnings.append('extension: wn cannot validate a LexiconExtension')
            continue
        report = wn.validate.validate(lexicon, progress_handler=None)
        for code, check in report.items():
            if code in ignore or not check['items']:
                continue
            line = f"{code}: {check['message']} ({len(check['items'])} items)"
            (errors if code.startswith(fatal) else warnings).append(line)
    return errors, warnings


def main() -> int:
    """Clean one file from the command line."""
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('source', type=Path)
    parser.add_argument('--cili', type=Path, default=Path('bin/cili.tsv'))
    parser.add_argument('--out-dir', type=Path, default=Path('bin/cygnets_presynth'))
    parser.add_argument('--language', help='language code to write instead of the source\'s')
    parser.add_argument('--validate', action='store_true', help='check the output with wn')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stdout)
    cleaner = LmfCleaner(args.source, CiliIndex.from_tsv(args.cili),
                         CleanOptions(language=args.language))
    result = cleaner.clean(args.out_dir)
    print(f'{result.output} ({"changed" if result.changed else "unchanged"}), log {result.log}')
    if args.validate:
        errors, warnings = validate_lmf(result.output)
        for line in warnings:
            print('  warning', line)
        for line in errors:
            print('  ERROR', line)
        return 1 if errors else 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
