"""
Converters for transforming external lexical resources into Cygnet format.
"""

import argparse
import json
import gzip
import logging
import lzma
import sys
from collections import defaultdict
from lxml import etree
import spacy
import pyinflect
from nltk.stem import WordNetLemmatizer
import nltk

logger = logging.getLogger(__name__)

NEW_POS_LABELS = {
    'n': 'NOUN',
    'v': 'VERB',
    'a': 'ADJ',
    'r': 'ADV',
    's': 'ADJ',  # adjective satellite -> adjective
    'c': 'CONJ',
    'p': 'ADP',
    'x': 'NREF',
    'u': 'UNK'
}

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

CONCEPT_RELATIONS_REQUIRING_SAME_CATEGORY = {
    'hypernym',
    'instance_hypernym',
    'mero_member',
    'mero_part',
    'mero_substance',
    'causes',
    'entails',
}

# Languages where spaCy's primary small model uses the 'web' corpus rather
# than 'news'.  All other languages follow {lang}_core_news_sm.
_SPACY_WEB_LANGS = {'en', 'zh'}
_SPACY_FALLBACK = 'xx_sent_ud_sm'


def _spacy_candidates(lang: str) -> list[str]:
    """Return spaCy model names to try for *lang*, most-preferred first.

    Order: web model (en/zh only) → news model → multilingual fallback.
    Installed models are tried before downloading anything.
    """
    candidates: list[str] = []
    if lang in _SPACY_WEB_LANGS:
        candidates.append(f'{lang}_core_web_sm')
    candidates.append(f'{lang}_core_news_sm')
    candidates.append(_SPACY_FALLBACK)
    return candidates
class WordNetToCygnetConverter:
    """Converts WordNet LMF lexical resources to Cygnet format."""

    def __init__(self, cili_path: str, relations_path: str | None = None, skip_cili_defns=False):

        self.skip_cili_defns = skip_cili_defns

        self.cili_path = cili_path
        self.relations_path = relations_path

        # Mapping dictionaries
        self.synset_to_concept: dict[str, str] = {}
        self.old_sense_to_new_sense: dict[str, str] = {}
        self.lexentry_to_lexeme: dict[str, str] = {}

        # CILI data structures
        self.cili_concepts: dict[str, etree.Element] = {}
        self.cili_glosses: dict[str, str] = {}

        # Relations file data structures
        self.existing_concept_relations: set[tuple[str, str, str]] = set()

        # Output data structures
        self.concepts: list[etree.Element] = []
        self.lexemes: list[etree.Element] = []
        self.senses: list[etree.Element] = []
        self.glosses: list[etree.Element] = []
        self.sense_relations: list[etree.Element] = []
        self.concept_relations: list[etree.Element] = []

        # Logging
        self.log = {
            'synset_concept_pos_mismatches': {
                'total_count': 0,
                'by_pos_pair': {}  # e.g., "synset_r-cili_s": count
            },
            'lexeme_concept_pos_mismatches': {
                'total_count': 0,
                'by_pos_pair': {}  # e.g., "lexeme_a-concept_s": count
            },
            'lexeme_merging': {
                'total_merges': 0
            },
            'relation_processing': {
                'unknown_relation_types': {
                    'sense_relations': {'count': 0},
                    'concept_relations': {'count': 0}
                },
                'filtered_unmapped_relations': {
                    'concept_relations': {'count': 0},
                    'sense_relations': {'count': 0}
                },
                'missing_inverses_added': {
                    'sense_relations': {'count': 0},
                    'concept_relations': {'count': 0}
                },
                'duplicates_removed': {
                    'sense_relations': {'count': 0},
                    'concept_relations': {'count': 0}
                },
                'skipped_existing_relations': {
                    'concept_relations': {'count': 0}
                },
                'ontological_category_mismatches': {
                    'count': 0
                }
            },
            'missing_cili_concepts': {
                'count': 0
            },
            'missing_lemmas': {
                'count': 0
            },
            'sense_missing_synset': {
                'count': 0
            },
            'synset_not_found': {
                'count': 0
            },
            'statistics': {
                'concepts': {
                    'newly_created': 0,
                    'from_cili': 0,
                    'total': 0
                },
                'lexemes': {
                    'created': 0
                },
                'senses': {
                    'created': 0
                },
                'glosses': {
                    'created': 0
                },
                'relations': {
                    'sense_relations_created': 0,
                    'concept_relations_created': 0
                },
                'examples': {
                    'total_found': 0,
                    'processed': 0,
                    'skipped': 0,
                    'success_rate_pct': 0.0,
                    'failed_matches': []
                }
            }
        }

        # Counters
        self.concept_counter = 1
        self.lexicon_id = None
        self.lexicon_language = None

        # Example processing
        self.examples: list[etree.Element] = []

        # NLP tools (loaded in convert())
        self.nlp = None
        self.nltk_lemmatizer = None

        # Caching for performance
        self.doc_cache: dict[str, any] = {}  # spaCy document cache
        self.form_cache: dict[str, set[str]] = {}  # morphological forms cache

        # Build reverse mapping: concept_id -> list of sense_ids
        self.concept_to_senses: dict[str, list[str]] = defaultdict(list)

        # Lookup dictionaries for fast access (populated in pass2)
        self.sense_lookup: dict[str, etree.Element] = {}
        self.lexeme_lookup: dict[str, etree.Element] = {}

        # Lookup dictionary for concept ontological categories (populated in pass1)
        self.concept_categories: dict[str, str] = {}

        self.lexeme_dedup_lookup: dict[
            tuple[tuple, str, str], str] = {}  # (sorted_written_forms, grammatical_category, language) -> lexeme_id
        self.created_senses: set[tuple[str, str]] = set()  # (lexeme_id, concept_id) pairs already created
        self.created_concepts: set[str] = set()  # concept_ids already created

        self.lexicon_version = None
        self.lexicon_attrs: dict[str, str] = {}

    def read_metadata(self, input_path: str) -> tuple[etree.Element, etree.ElementTree]:
        """Parse the input file and extract metadata without full conversion."""
        with self._open_file(input_path) as f:
            tree = etree.parse(f)
        root = tree.getroot()

        # Extract lexicon metadata
        lexicon_elem = root.find('.//{http://globalwordnet.github.io/schemas/}Lexicon')
        if lexicon_elem is None:
            lexicon_elem = root.find('.//Lexicon')

        if lexicon_elem is not None:
            self.lexicon_attrs = dict(lexicon_elem.attrib)
            self.lexicon_id = lexicon_elem.get('id')
            self.lexicon_version = lexicon_elem.get('version')
            self.lexicon_language = lexicon_elem.get('language')

        return root, tree

    def _open_file(self, filepath: str):
        """
        Open a file for reading, automatically handling compression.
        Supports .xml, .xml.gz, and .xml.xz files.
        """
        if filepath.endswith('.gz'):
            return gzip.open(filepath, 'rb')
        elif filepath.endswith('.xz'):
            return lzma.open(filepath, 'rb')
        else:
            return open(filepath, 'rb')

    def _normalize_pos(self, pos: str) -> str:
        """
        Convert old POS codes to new labels using NEW_POS_LABELS dict.
        Should be called after validation.
        """
        if pos not in NEW_POS_LABELS:
            logger.warning('Warning! Invalid POS! Setting to unknown!')
            pos = 'u'
        return NEW_POS_LABELS[pos]

    def _load_spacy_model(self, candidates: list[str], disable: list[str]):
        """Load the best available spaCy model from *candidates*.

        Tries installed models first (no download).  For each candidate:
        - OSError  → model not installed; download then retry once.
        - ImportError → model's language dependency is missing (e.g. pymorphy3
          for Russian, sudachipy for Japanese); skip to the next candidate and
          log a hint about which optional extra to install.
        Falls back to the multilingual model as the final candidate.
        """
        import shutil
        import subprocess
        from spacy.cli.download import get_compatibility, get_version

        installed = set(spacy.util.get_installed_models())

        def _download_and_load(name: str):
            logger.info(f"  Downloading spaCy model '{name}'...")
            try:
                version = get_version(name, get_compatibility())
                wheel_url = (
                    f"https://github.com/explosion/spacy-models/releases/download/"
                    f"{name}-{version}/{name}-{version}-py3-none-any.whl"
                )
                runner = ['uv', 'pip'] if shutil.which('uv') else [sys.executable, '-m', 'pip']
                subprocess.run(
                    [*runner, 'install', wheel_url],
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return spacy.load(name, disable=disable)
            except (Exception, SystemExit) as exc:
                raise OSError(f"Could not download/load '{name}'") from exc

        # Prefer installed candidates; fall back to downloading the first one.
        ordered = [m for m in candidates if m in installed] + \
                  [m for m in candidates if m not in installed]

        for model_name in ordered:
            logger.info(f"  Loading spaCy model '{model_name}'...")
            try:
                nlp = spacy.load(model_name, disable=disable) \
                      if model_name in installed else _download_and_load(model_name)
                return nlp
            except ImportError as exc:
                logger.warning(
                    f"  Skipping '{model_name}': missing language dependency "
                    f"({exc}).  Install the relevant extra, e.g. "
                    f"`uv pip install \"cygnet[{self.lexicon_language}]\"`."
                )
            except OSError:
                logger.warning(f"  Could not load '{model_name}', trying next candidate.")

        raise RuntimeError(f"No usable spaCy model found for language '{self.lexicon_language}'.")

    def _initialize_nlp_tools(self):
        """Initialize spaCy and NLTK tools for example processing."""
        logger.info(f"\nInitializing NLP tools for language '{self.lexicon_language}'...")

        # Select and load a spaCy model using the candidate priority list.
        # Keep tok2vec so morphologizer can assign features the lemmatizer needs
        # (disabling it silently breaks lemmatisation for morphologically rich
        # languages such as Russian, Slovenian, and Portuguese).
        #
        # Some language models require extra pip packages (e.g. pymorphy3 for
        # Russian, sudachipy for Japanese).  If a candidate raises ImportError
        # we log a warning and try the next candidate rather than crashing.
        # Install the relevant extras with e.g. `uv pip install "cygnet[ru]"`.
        disable = ["parser", "ner"]
        self.nlp = self._load_spacy_model(_spacy_candidates(self.lexicon_language), disable)

        # Initialize NLTK lemmatizer
        logger.info("  Loading NLTK WordNet lemmatizer...")
        self.nltk_lemmatizer = WordNetLemmatizer()
        try:
            nltk.data.find('corpora/wordnet')
        except LookupError:
            logger.info("  Downloading NLTK wordnet data...")
            nltk.download('wordnet')

        logger.info("  NLP tools initialized successfully")

    def _merge_wordform_data(self, existing_lexeme: etree.Element, new_lemma, new_forms: list) -> int:
        """
        Merge wordform data (Pronunciations and Scripts) from new_lemma and new_forms into existing_lexeme.
        Returns count of new Pronunciations added.
        """
        added_count = 0

        # Build map: form attribute -> Wordform element
        wordform_map = {}
        for wf in existing_lexeme.findall('Wordform'):
            form_text = wf.get('form')
            if form_text:
                wordform_map[form_text] = wf

        # Helper to get varieties from Pronunciation elements
        def get_varieties(wordform_elem):
            varieties = set()
            for pron in wordform_elem.findall('Pronunciation'):
                variety = pron.get('variety')
                if variety:
                    varieties.add(variety)
            return varieties

        # Helper to add Script to Wordform if not already present
        def merge_script(wordform_elem, new_script):
            if not new_script:
                return
            # Get existing scripts
            existing_scripts = set()
            for script_elem in wordform_elem.findall('Script'):
                if script_elem.text:
                    existing_scripts.add(script_elem.text)
            # Add if new
            if new_script not in existing_scripts:
                script_elem = etree.SubElement(wordform_elem, 'Script')
                script_elem.text = new_script

        # Helper to add Pronunciation to Wordform if not already present
        def merge_pronunciation(wordform_elem, pron_text, pron_variety):
            # Check if this exact pronunciation exists
            for existing_pron in wordform_elem.findall('Pronunciation'):
                if existing_pron.text == pron_text:
                    # Same pronunciation text - check variety
                    existing_variety = existing_pron.get('variety')
                    if existing_variety == pron_variety:
                        return False  # Already exists
                    elif existing_variety is None and pron_variety is None:
                        return False  # Both have no variety

            # Add new pronunciation
            new_pron = etree.SubElement(wordform_elem, 'Pronunciation')
            new_pron.text = pron_text
            if pron_variety:
                new_pron.set('variety', pron_variety)
            return True

        # Process new_lemma
        lemma_written = new_lemma.get('writtenForm', '')
        if lemma_written in wordform_map:
            wordform_elem = wordform_map[lemma_written]

            # Merge Script data
            lemma_script = new_lemma.get('script')
            merge_script(wordform_elem, lemma_script)

            # Merge Pronunciations
            for pronunciation in new_lemma.findall('Pronunciation'):
                pron_text = pronunciation.text or ''
                pron_variety = pronunciation.get('variety')
                if merge_pronunciation(wordform_elem, pron_text, pron_variety):
                    added_count += 1

        # Process new_forms
        for form in new_forms:
            form_written = form.get('writtenForm', '')
            if form_written in wordform_map:
                wordform_elem = wordform_map[form_written]

                # Merge Script data
                form_script = form.get('script')
                merge_script(wordform_elem, form_script)

                # Merge Pronunciations
                for pronunciation in form.findall('Pronunciation'):
                    pron_text = pronunciation.text or ''
                    pron_variety = pronunciation.get('variety')
                    if merge_pronunciation(wordform_elem, pron_text, pron_variety):
                        added_count += 1

        return added_count
    def _get_doc(self, text: str):
        """Get spaCy doc with caching."""
        if text not in self.doc_cache:
            self.doc_cache[text] = self.nlp(text)
        return self.doc_cache[text]


    def _get_all_forms(self, word: str) -> set[str]:
        """
        Get all possible morphological forms/lemmas of a word.
        Uses spaCy, NLTK, and pyinflect for comprehensive coverage.
        """
        word_lower = word.lower()
        if word_lower in self.form_cache:
            return self.form_cache[word_lower]

        forms = set()
        forms.add(word_lower)

        # Get spaCy doc (cached)
        doc = self._get_doc(word_lower)
        if len(doc) > 0:
            token = doc[0]

            # 1. spaCy lemmatization.  Some models (e.g. Korean) output
            # morpheme-split lemmas like "크+다"; add the collapsed form and
            # the leading stem so that "크+다" and "크+ㄴ" can match via "크".
            # Some multilingual models return "" as the lemma — skip these.
            lemma = token.lemma_
            if lemma:
                forms.add(lemma)
            if '+' in lemma:
                forms.add(lemma.replace('+', ''))
                forms.add(lemma.split('+')[0])

            # 2. pyinflect - generate various inflections
            if pyinflect is not None:
                for pos_tag in ['VB', 'VBD', 'VBG', 'VBN', 'VBP', 'VBZ',  # Verbs
                                'NN', 'NNS',  # Nouns
                                'JJ', 'JJR', 'JJS',  # Adjectives
                                'RB', 'RBR', 'RBS']:  # Adverbs
                    inflection = token._.inflect(pos_tag)
                    if inflection:
                        forms.add(inflection.lower())

        # 3. NLTK WordNet lemmatization (all POS)
        if self.nltk_lemmatizer is not None:
            for pos in ['n', 'v', 'a', 'r']:
                lemma = self.nltk_lemmatizer.lemmatize(word_lower, pos=pos)
                forms.add(lemma)

        self.form_cache[word_lower] = forms
        return forms

    def _match_single_word(self, wordform: str, text: str) -> str | None:
        """Match a single word using morphological form matching."""
        wordform_forms = self._get_all_forms(wordform)
        doc = self._get_doc(text)
        for token in doc:
            token_lower = token.text.lower()
            token_lemma = token.lemma_.lower()
            token_candidates = {token_lower}
            if token_lemma:  # skip empty lemmas from multilingual models
                token_candidates.add(token_lemma)
                if '+' in token_lemma:  # Korean morpheme-split lemmas
                    token_candidates.add(token_lemma.replace('+', ''))
                    token_candidates.add(token_lemma.split('+')[0])
            if self.nltk_lemmatizer is not None:
                for pos in ['n', 'v', 'a', 'r']:
                    token_candidates.add(
                        self.nltk_lemmatizer.lemmatize(token_lower, pos=pos)
                    )
            if token_candidates & wordform_forms:
                return token_lower
        return None

    def _match_multi_word(self, form_words: list[str], text: str) -> str | None:
        """Match a multi-word expression using morphological form matching."""
        # Identify content words (not function words)
        function_words = {'a', 'an', 'the', 'of', 'in', 'on', 'at', 'to', 'for', 'with', 'by', 'from'}

        # Get all forms for content words in the MWE
        content_word_forms = []
        for word in form_words:
            word_lower = word.lower()
            if word_lower not in function_words:
                forms = self._get_all_forms(word_lower)
                content_word_forms.append(forms)

        # Look for matching sequences in the document
        doc = self._get_doc(text)
        tokens = list(doc)

        for i in range(len(tokens)):
            # Try to match starting from position i
            content_match_idx = 0
            matched_token_indices = []

            for j in range(i, min(i + len(form_words) + 2, len(tokens))):
                if content_match_idx >= len(content_word_forms):
                    break

                token = tokens[j]
                target_forms = content_word_forms[content_match_idx]

                token_lower = token.text.lower()
                token_lemma = token.lemma_.lower()
                token_candidates = {token_lower}
                if token_lemma:  # skip empty lemmas from multilingual models
                    token_candidates.add(token_lemma)
                    if '+' in token_lemma:  # Korean morpheme-split lemmas
                        token_candidates.add(token_lemma.replace('+', ''))
                        token_candidates.add(token_lemma.split('+')[0])
                if self.nltk_lemmatizer is not None:
                    for pos in ['n', 'v', 'a', 'r']:
                        token_candidates.add(
                            self.nltk_lemmatizer.lemmatize(token_lower, pos=pos)
                        )

                if target_forms & token_candidates:
                    matched_token_indices.append(j)
                    content_match_idx += 1

            # If we matched all content words
            if content_match_idx == len(content_word_forms):
                # Extract the span from first to last matched token
                start_idx = matched_token_indices[0]
                end_idx = matched_token_indices[-1]

                start_token = tokens[start_idx]
                end_token = tokens[end_idx]

                # Get the span from the original text
                start_pos = start_token.idx
                end_pos = end_token.idx + len(end_token.text)

                return text[start_pos:end_pos].lower()

        return None

    def _get_lexeme_written_forms(self, lexeme_id: str) -> list[str]:
        """
        Get all written wordforms for a lexeme.

        Args:
            lexeme_id: The ID of the lexeme

        Returns:
            List of written wordform strings, or empty list if lexeme not found
        """
        lexeme_elem = self.lexeme_lookup.get(lexeme_id)
        if lexeme_elem is None:
            return []
        return [wf.get('form') for wf in lexeme_elem.findall('Wordform') if wf.get('form')]

    def _find_best_match(self, text: str, candidates: list[tuple[str, str]]) -> tuple[str | None, str | None]:
        """
        Find the best matching wordform in the text.

        Args:
            text: The example text to search in
            candidates: List of (sense_id, wordform) tuples

        Returns:
            Tuple of (sense_id, matched_wordform) or (None, None) if no match
        """
        text_lower = text.lower()

        # Sort candidates by length (longest first) to prefer longer matches
        candidates_sorted = sorted(candidates, key=lambda x: len(x[1]), reverse=True)

        # Strategy 1: Try exact match first (CHEAP - no NLP processing)
        for sense_id, form in candidates_sorted:
            if form.lower() in text_lower:
                return sense_id, form

        # Strategy 2: Try simple lowercase token matching (CHEAP - just splitting)
        text_tokens = set(text_lower.split())
        for sense_id, form in candidates_sorted:
            form_words = form.lower().split()

            if len(form_words) == 1:
                # Single word - check if it's in the token set
                if form_words[0] in text_tokens:
                    return sense_id, form
            else:
                # Multi-word - check if all words are present
                if all(word in text_tokens for word in form_words):
                    # Do a more precise check to ensure they're reasonably close
                    text_words = text_lower.split()
                    for i in range(len(text_words)):
                        match_count = 0
                        for j in range(i, min(i + len(form_words) + 2, len(text_words))):
                            if match_count < len(form_words) and text_words[j] == form_words[match_count]:
                                match_count += 1
                        if match_count == len(form_words):
                            return sense_id, form

        # Strategy 3: Try morphological matching with spaCy (EXPENSIVE - full NLP pipeline)
        for sense_id, form in candidates_sorted:
            form_words = form.split()

            if len(form_words) == 1:
                # Single word matching
                result = self._match_single_word(form, text)
                if result:
                    return sense_id, result
            else:
                # Multi-word expression matching
                result = self._match_multi_word(form_words, text)
                if result:
                    return sense_id, result

        return None, None

    def _expand_to_token_boundaries(self, text: str, match_start: int, match_end: int) -> tuple[int, int]:
        """Expand match to complete token boundaries."""
        # Stage 1: Check if already at boundaries (CHEAP)
        token_boundaries = {' ', '.', ',', '!', '?', ';', ':', '"', "'", "'", '"', '"',
                            '(', ')', '[', ']', '{', '}', '-', '—', '–',
                            '\n', '\t', '/', '\\'}

        at_start = (match_start == 0 or text[match_start - 1] in token_boundaries)
        at_end = (match_end >= len(text) or text[match_end] in token_boundaries)

        if at_start and at_end:
            return match_start, match_end

        # Stage 2: Use spaCy to find actual token boundaries (EXPENSIVE)
        doc = self._get_doc(text)

        # Find tokens that overlap with our match
        overlapping_tokens = []
        for token in doc:
            token_start = token.idx
            token_end = token.idx + len(token.text)

            # Check if token overlaps with match range
            if not (token_end <= match_start or token_start >= match_end):
                overlapping_tokens.append(token)

        if not overlapping_tokens:
            return match_start, match_end

        # Expand to full span of all overlapping tokens
        new_start = overlapping_tokens[0].idx
        last_token = overlapping_tokens[-1]
        new_end = last_token.idx + len(last_token.text)

        return new_start, new_end

    def _encode_for_xml_id(self, text: str) -> str:
        """
        Convert any Unicode string to a safe XML ID format with one-to-one correspondence.
        Each unique input string produces a unique output key.

        Encoding rules:
        - ASCII letters (a-z, A-Z): pass through unchanged
        - ASCII digits (0-9): pass through unchanged
        - Everything else (including punctuation, spaces, non-ASCII Unicode):
          encode as xHHHH where HHHH is the 4-digit uppercase hex Unicode codepoint
        """
        encoded = []
        for char in text:
            # Only allow ASCII alphanumeric (a-z, A-Z, 0-9)
            if ('a' <= char <= 'z') or ('A' <= char <= 'Z') or ('0' <= char <= '9'):
                encoded.append(char)
            else:
                # Encode everything else as x followed by 4-digit hex codepoint
                hex_val = f"{ord(char):04X}"
                encoded.append(f"x{hex_val}")
        return ''.join(encoded)

    def _normalize_text(self, text: str) -> str:
        """Normalize text for comparison (whitespace and punctuation)."""
        if not text:
            return ""
        # Strip, collapse whitespace, lowercase
        text = ' '.join(text.split())
        # Remove trailing punctuation for comparison
        text = text.strip().rstrip('.,;:!?')
        return text.lower()

    def load_cili(self):
        """Load and parse the CILI file."""
        logger.info(f"Loading CILI from {self.cili_path}...")
        with self._open_file(self.cili_path) as f:
            tree = etree.parse(f)
        root = tree.getroot()

        # Load concepts
        concept_layer = root.find('ConceptLayer')
        if concept_layer is not None:
            for concept in concept_layer.findall('Concept'):
                concept_id = concept.get('id')
                self.cili_concepts[concept_id] = concept

        # Load glosses
        gloss_layer = root.find('GlossLayer')
        if gloss_layer is not None:
            for gloss in gloss_layer.findall('Gloss'):
                definiendum = gloss.get('definiendum')
                # Get text content (mixed content with possible AnnotatedToken elements)
                gloss_text = self._extract_text_content(gloss)
                self.cili_glosses[definiendum] = gloss_text

        logger.info(f"Loaded {len(self.cili_concepts)} CILI concepts and {len(self.cili_glosses)} glosses")

    def _extract_text_content(self, element: etree.Element) -> str:
        """Extract all text content from an element (including mixed content)."""
        parts = []
        if element.text:
            parts.append(element.text)
        for child in element:
            if child.text:
                parts.append(child.text)
            if child.tail:
                parts.append(child.tail)
        return ''.join(parts)

    def load_relations_file(self):
        """Load and parse the relations file to identify existing concept relations."""
        if not self.relations_path:
            return

        logger.info(f"Loading existing relations from {self.relations_path}...")
        with self._open_file(self.relations_path) as f:
            tree = etree.parse(f)
        root = tree.getroot()

        # Load concept relations
        concept_relation_layer = root.find('ConceptRelationLayer')
        if concept_relation_layer is not None:
            for relation in concept_relation_layer.findall('ConceptRelation'):
                source = relation.get('source')
                target = relation.get('target')
                relation_type = relation.get('relation_type')
                if source and target and relation_type:
                    self.existing_concept_relations.add((source, target, relation_type))

        logger.info(f"Loaded {len(self.existing_concept_relations)} existing concept relations")

    def pass1_synsets_to_concepts_and_glosses(self, root: etree.Element):
        """
        Pass 1: Convert Synsets to Concepts and create Glosses.
        """
        logger.info("\nPass 1: Converting Synsets to Concepts and Glosses...")

        # Process all lexicons (including extensions)
        for lexicon in root.findall('Lexicon') + root.findall('LexiconExtension'):
            if self.lexicon_id is None:
                self.lexicon_id = lexicon.get('id')
                self.lexicon_language = lexicon.get('language')
                self.lexicon_version = lexicon.get('version')
                logger.info(f"  Using lexicon ID: {self.lexicon_id}, language: {self.lexicon_language}")

            for synset in lexicon.findall('Synset'):
                synset_id = synset.get('id')
                pos = synset.get('partOfSpeech', '')
                ili = synset.get('ili', '')

                # Normalize POS (s -> r) before any comparisons or storage
                pos = self._normalize_pos(pos)

                # Check if ILI is valid
                is_valid_ili = ili and ili not in ['', 'in']

                if is_valid_ili:
                    # Map to CILI concept
                    concept_id = f"cili.{ili}"

                    # Verify it exists in CILI
                    if concept_id not in self.cili_concepts:
                        self.log['missing_cili_concepts']['count'] += 1
                        # Create new concept instead
                        concept_id = f"{self.lexicon_id}.i{self.concept_counter}"
                        self.concept_counter += 1
                        concept = etree.Element('Concept',
                                                id=concept_id,
                                                ontological_category=pos,
                                                status='1')
                        # Add Provenance element
                        from_elem = etree.SubElement(concept, 'Provenance',
                                                     resource=self.lexicon_id,
                                                     version=self.lexicon_version)
                        from_elem.set('original_id', synset_id)
                        self.concepts.append(concept)
                        self.created_concepts.add(concept_id)
                        self.log['statistics']['concepts']['newly_created'] += 1
                        self.log['statistics']['concepts']['total'] += 1
                    else:
                        # Check POS match
                        cili_concept = self.cili_concepts[concept_id]
                        cili_ontological_category = cili_concept.get('ontological_category', '')

                        if pos != cili_ontological_category:
                            # Update summary statistics
                            self.log['synset_concept_pos_mismatches']['total_count'] += 1

                            # Count by POS pair (synset_pos-cili_pos)
                            pos_pair = f"synset_{pos}-cili_{cili_ontological_category}"
                            if pos_pair not in self.log['synset_concept_pos_mismatches']['by_pos_pair']:
                                self.log['synset_concept_pos_mismatches']['by_pos_pair'][pos_pair] = 0
                            self.log['synset_concept_pos_mismatches']['by_pos_pair'][pos_pair] += 1

                        self.log['statistics']['concepts']['from_cili'] += 1
                        self.log['statistics']['concepts']['total'] += 1

                        self.created_concepts.add(concept_id)
                        self.concept_categories[concept_id] = cili_ontological_category


                else:
                    # Create new concept
                    concept_id = f"{self.lexicon_id}.i{self.concept_counter}"
                    self.concept_counter += 1

                    concept = etree.Element('Concept',
                                            id=concept_id,
                                            ontological_category=pos,
                                            status='1')
                    # Add Provenance element
                    from_elem = etree.SubElement(concept, 'Provenance',
                                                 resource=self.lexicon_id,
                                                 version=self.lexicon_version)
                    from_elem.set('original_id', synset_id)
                    self.concepts.append(concept)
                    self.created_concepts.add(concept_id)
                    self.log['statistics']['concepts']['newly_created'] += 1
                    self.log['statistics']['concepts']['total'] += 1
                # Store mapping
                self.synset_to_concept[synset_id] = concept_id

                # Process Definition to create Gloss
                definition_elem = synset.find('Definition')
                if definition_elem is not None:
                    definition_text = self._extract_text_content(definition_elem)

                    should_add_gloss = not (self.skip_cili_defns and concept_id.startswith('cili.'))

                    if should_add_gloss:
                        gloss = etree.Element('Gloss',
                                              definiendum=concept_id,
                                              language=self.lexicon_language)

                        # Create AnnotatedSentence container for the definition text
                        annotated_sentence = etree.SubElement(gloss, 'AnnotatedSentence')
                        annotated_sentence.text = definition_text

                        # Add Provenance element
                        from_elem = etree.SubElement(gloss, 'Provenance',
                                                  resource=self.lexicon_id,
                                                  version=self.lexicon_version)

                        self.glosses.append(gloss)
                        self.log['statistics']['glosses']['created'] += 1

        logger.info(f"  Created {self.log['statistics']['concepts']['newly_created']} new concepts")
        logger.info(f"  Mapped to {self.log['statistics']['concepts']['from_cili']} CILI concepts")
        logger.info(f"  Total concepts: {self.log['statistics']['concepts']['total']}")
        logger.info(f"  Created {self.log['statistics']['glosses']['created']} glosses")
        logger.warning(f"  Found {self.log['synset_concept_pos_mismatches']['total_count']} synset-concept POS mismatches")

    def pass2_lexical_entries_to_lexemes_and_senses(self, root: etree.Element):
        """
        Pass 2: Convert LexicalEntries to Lexemes and create Senses.
        """
        logger.info("\nPass 2: Converting LexicalEntries to Lexemes and Senses...")

        # First, build a map of concept IDs to their ontological categories
        concept_pos_map = {}
        for concept in self.concepts:
            concept_pos_map[concept.get('id')] = concept.get('ontological_category')
        # Also include CILI concepts
        for concept_id, concept_elem in self.cili_concepts.items():
            concept_pos_map[concept_id] = concept_elem.get('ontological_category')

        for lexicon in root.findall('Lexicon') + root.findall('LexiconExtension'):
            # Also check for ExternalLexicalEntry in extensions
            lexical_entries = lexicon.findall('LexicalEntry') + lexicon.findall('ExternalLexicalEntry')

            for lex_entry in lexical_entries:
                old_entry_id = lex_entry.get('id')

                # Get Lemma
                lemma = lex_entry.find('Lemma')
                if lemma is None:
                    # Check for ExternalLemma
                    lemma = lex_entry.find('ExternalLemma')

                if lemma is None:
                    self.log['missing_lemmas']['count'] += 1
                    continue

                lemma_written_form = lemma.get('writtenForm', '')
                pos = lemma.get('partOfSpeech', '')

                # Normalize POS (s -> r) for grammatical category
                grammatical_category = self._normalize_pos(pos)

                # Collect ALL written wordforms (pronunciations are not part of lexeme identity)
                all_wordforms = []
                all_wordforms.append(lemma_written_form)  # Lemma written form is first

                # Add additional written forms only
                forms = lex_entry.findall('Form') + lex_entry.findall('ExternalForm')
                for form in forms:
                    form_text = form.get('writtenForm', '')
                    if form_text:
                        all_wordforms.append(form_text)

                # Deduplicate and sort: lemma first, rest alphabetically
                all_wordforms_set = set(all_wordforms)
                all_wordforms_sorted = [lemma_written_form]  # Lemma stays first
                remaining = sorted(all_wordforms_set - {lemma_written_form})
                all_wordforms_sorted.extend(remaining)

                # Build lexeme ID
                encoded_forms = [self._encode_for_xml_id(f) for f in all_wordforms_sorted]
                lexeme_id = f"{self.lexicon_language}.{grammatical_category}.{'-'.join(encoded_forms)}"

                # Create deduplication key
                dedup_key = (tuple(sorted(all_wordforms_set)), grammatical_category, self.lexicon_language)

                if dedup_key in self.lexeme_dedup_lookup:
                    # Lexeme already exists - merge SpokenWordforms
                    existing_lexeme_id = self.lexeme_dedup_lookup[dedup_key]
                    existing_lexeme = self.lexeme_lookup[existing_lexeme_id]

                    # Merge wordform data (pronunciations and scripts)
                    added_count = self._merge_wordform_data(existing_lexeme, lemma, forms)

                    # Log the merge
                    self.log['lexeme_merging']['total_merges'] += 1

                    # Add Provenance for this merged entry if different from existing
                    # Check if this provenance already exists
                    provenance_exists = False
                    for existing_prov in existing_lexeme.findall('Provenance'):
                        if (existing_prov.get('resource') == self.lexicon_id and
                                existing_prov.get('version') == self.lexicon_version and
                                existing_prov.get('original_id') == old_entry_id):
                            provenance_exists = True
                            break

                    if not provenance_exists:
                        from_elem = etree.SubElement(existing_lexeme, 'Provenance',
                                                     resource=self.lexicon_id,
                                                     version=self.lexicon_version)
                        from_elem.set('original_id', old_entry_id)

                    # Use existing lexeme_id for sense creation
                    lexeme_id = existing_lexeme_id
                else:
                    # Create new Lexeme element
                    lexeme = etree.Element('Lexeme',
                                           id=lexeme_id,
                                           language=self.lexicon_language,
                                           grammatical_category=grammatical_category)

                    # Add Wordform for lemma
                    wordform = etree.SubElement(lexeme, 'Wordform', form=lemma_written_form)
                    # Add Script if present
                    script = lemma.get('script')
                    if script:
                        script_elem = etree.SubElement(wordform, 'Script')
                        script_elem.text = script

                    # Add pronunciations for lemma
                    for pronunciation in lemma.findall('Pronunciation'):
                        pron_elem = etree.SubElement(wordform, 'Pronunciation')
                        pron_elem.text = pronunciation.text or ''
                        variety = pronunciation.get('variety')
                        if variety:
                            pron_elem.set('variety', variety)

                    # Add Wordforms for additional forms
                    for i, form in enumerate(forms):
                        form_text = form.get('writtenForm', '')
                        if form_text:
                            wordform = etree.SubElement(lexeme, 'Wordform', form=form_text)
                            # Add Script if present
                            script = form.get('script')
                            if script:
                                script_elem = etree.SubElement(wordform, 'Script')
                                script_elem.text = script

                            # Add pronunciations for this form
                            for pronunciation in form.findall('Pronunciation'):
                                pron_elem = etree.SubElement(wordform, 'Pronunciation')
                                pron_elem.text = pronunciation.text or ''
                                variety = pronunciation.get('variety')
                                if variety:
                                    pron_elem.set('variety', variety)

                    # Add Provenance element
                    from_elem = etree.SubElement(lexeme, 'Provenance',
                                                 resource=self.lexicon_id,
                                                 version=self.lexicon_version)
                    from_elem.set('original_id', old_entry_id)

                    self.lexemes.append(lexeme)
                    self.lexeme_lookup[lexeme_id] = lexeme
                    self.lexeme_dedup_lookup[dedup_key] = lexeme_id
                    self.log['statistics']['lexemes']['created'] += 1

                # Store mapping (whether new or merged)
                self.lexentry_to_lexeme[old_entry_id] = lexeme_id

                # Process Senses
                senses = lex_entry.findall('Sense') + lex_entry.findall('ExternalSense')
                for sense in senses:
                    old_sense_id = sense.get('id')
                    synset_ref = sense.get('synset')

                    if not synset_ref:
                        self.log['sense_missing_synset']['count'] += 1
                        continue

                    # Get concept ID from synset mapping
                    if synset_ref not in self.synset_to_concept:
                        self.log['synset_not_found']['count'] += 1
                        continue

                    concept_id = self.synset_to_concept[synset_ref]

                    # Check for lexeme-concept POS mismatch
                    if concept_id in concept_pos_map:
                        concept_ontological_category = concept_pos_map[concept_id]

                        if grammatical_category != concept_ontological_category:
                            # Update summary statistics
                            self.log['lexeme_concept_pos_mismatches']['total_count'] += 1

                            # Count by POS pair (lexeme_pos-concept_pos)
                            pos_pair = f"lexeme_{grammatical_category}-concept_{concept_ontological_category}"
                            if pos_pair not in self.log['lexeme_concept_pos_mismatches']['by_pos_pair']:
                                self.log['lexeme_concept_pos_mismatches']['by_pos_pair'][pos_pair] = 0
                            self.log['lexeme_concept_pos_mismatches']['by_pos_pair'][pos_pair] += 1

                    # Create new sense ID
                    new_sense_id = f"{lexeme_id}.{concept_id}"

                    # Check for duplicate sense
                    sense_key = (lexeme_id, concept_id)
                    if sense_key in self.created_senses:
                        # Duplicate sense - just store mapping and skip
                        self.old_sense_to_new_sense[old_sense_id] = new_sense_id
                        continue

                    # Store mapping
                    self.old_sense_to_new_sense[old_sense_id] = new_sense_id

                    # Create Sense element
                    sense_elem = etree.Element('Sense',
                                               id=new_sense_id,
                                               signifier=lexeme_id,
                                               signified=concept_id)
                    # Add Provenance element
                    from_elem = etree.SubElement(sense_elem, 'Provenance',
                                                 resource=self.lexicon_id,
                                                 version=self.lexicon_version)
                    from_elem.set('original_id', old_sense_id)
                    self.senses.append(sense_elem)
                    self.sense_lookup[new_sense_id] = sense_elem
                    self.created_senses.add(sense_key)
                    self.log['statistics']['senses']['created'] += 1

        logger.info(f"  Created {self.log['statistics']['lexemes']['created']} lexemes")
        logger.info(f"  Merged {self.log['lexeme_merging']['total_merges']} duplicate lexemes")
        logger.info(f"  Created {self.log['statistics']['senses']['created']} senses")
        logger.warning(f"  Found {self.log['lexeme_concept_pos_mismatches']['total_count']} lexeme-concept POS mismatches")

    def _get_concept_ontological_category(self, concept_id: str) -> str | None:
        """Get the ontological category for a concept (O(1) lookup)."""
        return self.concept_categories.get(concept_id)


    def _normalize_relation_tuple(self, source: str, target: str, rel_type: str,
                                  is_concept: bool) -> tuple[str, str, str]:
        """
        Normalize relation tuple for consistent ordering.
        For symmetric relations, order by source/target lexicographically.
        For asymmetric relations, keep as-is.
        Returns (source, target, relation_type) in canonical form.
        """
        pairs_dict = CONCEPT_REVERSE_RELATIONS if is_concept else SENSE_REVERSE_RELATIONS
        inverse = pairs_dict.get(rel_type)

        # If symmetric (maps to itself), use lexicographic ordering
        if inverse == rel_type:
            if source > target:
                return (target, source, rel_type)

        return (source, target, rel_type)

    def _get_relation_type_from_inverse(self, inverse_type: str, is_concept: bool) -> str | None:
        """
        Given an inverse relation type, find the corresponding forward type.
        E.g., given 'hyponym', return 'hypernym'
        Returns None if not found.
        """
        pairs_dict = CONCEPT_REVERSE_RELATIONS if is_concept else SENSE_REVERSE_RELATIONS

        for forward_type, inv in pairs_dict.items():
            if inv == inverse_type:
                return forward_type

        return None

    def pass3_relations(self, root: etree.Element):
        """
        Pass 3: Convert SynsetRelations to ConceptRelations and SenseRelations.

        Process:
        1. Load all relations from XML
        2. Filter unknown relation types
        3. Deduplicate
        4. Add missing inverses
        5. Keep only forward direction (keys in NAME_MAPPING)
        6. Remap to new names
        7. Filter out existing relations
        8. Check ontological categories
        9. Create final XML elements
        """
        logger.info("\nPass 3: Converting Relations...")

        def build_bidirectional_dict(pairs_dict):
            """Expand a forward-only pairs dict to include both directions."""
            bidirectional = {}
            for forward, inverse in pairs_dict.items():
                bidirectional[forward] = inverse
                if inverse is not None and inverse != forward:
                    bidirectional[inverse] = forward
            return bidirectional

        bidirectional_concept = build_bidirectional_dict(CONCEPT_REVERSE_RELATIONS)
        bidirectional_sense = build_bidirectional_dict(SENSE_REVERSE_RELATIONS)

        def process_relations(raw_relations, pairs_dict, bidirectional_dict,
                              is_concept, existing_relations=None):
            """Process a list of raw relations through all stages."""
            log_key = 'concept_relations' if is_concept else 'sense_relations'

            # Stage 1: Log unknown types (pass all through)
            unknown_count = sum(1 for _, _, r in raw_relations if r not in bidirectional_dict)
            self.log['relation_processing']['unknown_relation_types'][log_key]['count'] = unknown_count

            # Stage 2: Deduplicate
            relation_set = set(raw_relations)
            dup_count = len(raw_relations) - len(relation_set)
            self.log['relation_processing']['duplicates_removed'][log_key]['count'] = dup_count

            # Stage 3: Add missing inverses for known types
            with_inverses = set(relation_set)
            inverses_added = 0
            for source, target, rel_type_val in sorted(relation_set):
                inverse_type = bidirectional_dict.get(rel_type_val)
                if inverse_type is not None:
                    inverse_tuple = (target, source, inverse_type)
                    if inverse_tuple not in with_inverses:
                        with_inverses.add(inverse_tuple)
                        inverses_added += 1
                        self.log['relation_processing']['missing_inverses_added'][log_key]['count'] += 1

            # Stage 4: Keep only canonical direction
            #   - Symmetric: deduplicate to lexicographically ordered pair
            #   - Known asymmetric forward (key in pairs_dict): keep
            #   - Unknown type (not in bidirectional_dict): keep as-is
            #   - Known inverse direction (in bidirectional but not a key in pairs_dict): skip
            forward_only = []
            seen_symmetric = set()
            for source, target, rel_type_val in sorted(with_inverses):
                inverse_type = pairs_dict.get(rel_type_val)
                if inverse_type == rel_type_val:
                    # Symmetric: one canonical entry per pair
                    pair_key = (min(source, target), max(source, target), rel_type_val)
                    if pair_key not in seen_symmetric:
                        seen_symmetric.add(pair_key)
                        if source > target:
                            source, target = target, source
                        forward_only.append((source, target, rel_type_val))
                elif rel_type_val in pairs_dict:
                    # Known asymmetric forward direction (or no-inverse type)
                    forward_only.append((source, target, rel_type_val))
                elif rel_type_val not in bidirectional_dict:
                    # Unknown type — pass through
                    forward_only.append((source, target, rel_type_val))
                # else: known inverse direction — skip (MergeBuilder generates it)

            # Stage 5: Filter out existing relations
            if existing_relations is not None:
                result = []
                for s, t, r in forward_only:
                    if (s, t, r) in existing_relations:
                        self.log['relation_processing']['skipped_existing_relations'][log_key]['count'] += 1
                    else:
                        result.append((s, t, r))
                forward_only = result

            return forward_only, unknown_count, dup_count, inverses_added

        # Load all relations from XML
        logger.info("  Loading all relations...")
        raw_concept_relations = []
        raw_sense_relations = []

        for lexicon in root.findall('Lexicon') + root.findall('LexiconExtension'):
            # Load Concept Relations (from Synsets)
            synsets = lexicon.findall('Synset') + lexicon.findall('ExternalSynset')
            for synset in synsets:
                synset_id = synset.get('id')
                if synset_id not in self.synset_to_concept:
                    continue

                source_concept = self.synset_to_concept[synset_id]
                for rel in synset.findall('SynsetRelation'):
                    target_synset = rel.get('target')
                    rel_type = rel.get('relType')
                    if target_synset in self.synset_to_concept:
                        target_concept = self.synset_to_concept[target_synset]
                        raw_concept_relations.append((source_concept, target_concept, rel_type))

            # Load Sense Relations
            lexical_entries = lexicon.findall('LexicalEntry') + lexicon.findall('ExternalLexicalEntry')
            for lex_entry in lexical_entries:
                senses = lex_entry.findall('Sense') + lex_entry.findall('ExternalSense')
                for sense in senses:
                    old_sense_id = sense.get('id')
                    if old_sense_id not in self.old_sense_to_new_sense:
                        continue

                    source_sense = self.old_sense_to_new_sense[old_sense_id]
                    for rel in sense.findall('SenseRelation'):
                        target_old_sense = rel.get('target')
                        rel_type = rel.get('relType')
                        if target_old_sense in self.old_sense_to_new_sense:
                            target_sense = self.old_sense_to_new_sense[target_old_sense]
                            raw_sense_relations.append((source_sense, target_sense, rel_type))

        logger.info(f"    Loaded {len(raw_concept_relations)} concept relations")
        logger.info(f"    Loaded {len(raw_sense_relations)} sense relations")

        # Process concept relations
        logger.info("  Processing concept relations...")
        final_concept_relations, c_unknown, c_dup, c_inv = process_relations(
            raw_concept_relations,
            CONCEPT_REVERSE_RELATIONS,
            bidirectional_concept,
            is_concept=True,
            existing_relations=self.existing_concept_relations
        )

        logger.warning(f"    Unknown types: {c_unknown}")
        logger.info(f"    Removed {c_dup} duplicates")
        logger.info(f"    Added {c_inv} missing inverses")
        logger.info(f"    Final: {len(final_concept_relations)} concept relations")

        # Process sense relations
        logger.info("  Processing sense relations...")
        final_sense_relations, s_unknown, s_dup, s_inv = process_relations(
            raw_sense_relations,
            SENSE_REVERSE_RELATIONS,
            bidirectional_sense,
            is_concept=False,
            existing_relations=None
        )

        logger.warning(f"    Unknown types: {s_unknown}")
        logger.info(f"    Removed {s_dup} duplicates")
        logger.info(f"    Added {s_inv} missing inverses")
        logger.info(f"    Final: {len(final_sense_relations)} sense relations")

        # Check ontological categories for concept relations
        logger.info("  Checking ontological categories...")
        for source, target, rel_type in final_concept_relations:
            if rel_type in CONCEPT_RELATIONS_REQUIRING_SAME_CATEGORY:
                source_cat = self._get_concept_ontological_category(source)
                target_cat = self._get_concept_ontological_category(target)

                if source_cat and target_cat and source_cat != target_cat:
                    self.log['relation_processing']['ontological_category_mismatches']['count'] += 1

        mismatch_count = self.log['relation_processing']['ontological_category_mismatches']['count']
        logger.warning(f"    Found {mismatch_count} ontological category mismatches")

        # Create XML elements
        logger.info("  Creating XML elements...")

        for source, target, rel_type in final_concept_relations:
            concept_rel = etree.Element('ConceptRelation',
                                        relation_type=rel_type,
                                        source=source,
                                        target=target)
            from_elem = etree.SubElement(concept_rel, 'Provenance',
                                         resource=self.lexicon_id,
                                         version=self.lexicon_version)
            self.concept_relations.append(concept_rel)
            self.log['statistics']['relations']['concept_relations_created'] += 1

        for source, target, rel_type in final_sense_relations:
            sense_rel = etree.Element('SenseRelation',
                                      relation_type=rel_type,
                                      source=source,
                                      target=target)
            from_elem = etree.SubElement(sense_rel, 'Provenance',
                                         resource=self.lexicon_id,
                                         version=self.lexicon_version)
            self.sense_relations.append(sense_rel)
            self.log['statistics']['relations']['sense_relations_created'] += 1

        logger.info(f"  Created {self.log['statistics']['relations']['concept_relations_created']} concept relations")
        logger.info(f"  Created {self.log['statistics']['relations']['sense_relations_created']} sense relations")

        skipped_count = self.log['relation_processing']['skipped_existing_relations']['concept_relations']['count']
        if skipped_count > 0:
            logger.warning(f"  Skipped {skipped_count} concept relations that already exist in relations file")

    def pass4_examples(self, root: etree.Element):
        """
        Pass 4: Process examples from Synsets and Senses, tagging with sense annotations.
        """
        logger.info("\nPass 4: Processing examples...")

        self._initialize_nlp_tools()

        # Build concept_to_senses mapping (for synset examples)
        for sense_elem in self.senses:
            sense_id = sense_elem.get('id')
            concept_id = sense_elem.get('signified')
            self.concept_to_senses[concept_id].append(sense_id)

        example_counter = 1
        total_examples = 0

        # Count total examples first
        for lexicon in root.findall('Lexicon') + root.findall('LexiconExtension'):
            synsets = lexicon.findall('Synset') + lexicon.findall('ExternalSynset')
            for synset in synsets:
                total_examples += len(synset.findall('Example'))

            lexical_entries = lexicon.findall('LexicalEntry') + lexicon.findall('ExternalLexicalEntry')
            for entry in lexical_entries:
                senses = entry.findall('Sense') + entry.findall('ExternalSense')
                for sense in senses:
                    total_examples += len(sense.findall('Example'))

        self.log['statistics']['examples']['total_found'] = total_examples
        logger.info(f"  Total examples to process: {total_examples}")

        # Process synset examples
        for lexicon in root.findall('Lexicon') + root.findall('LexiconExtension'):
            synsets = lexicon.findall('Synset') + lexicon.findall('ExternalSynset')
            for synset in synsets:
                synset_id = synset.get('id')

                # Get concept for this synset
                if synset_id not in self.synset_to_concept:
                    continue

                concept_id = self.synset_to_concept[synset_id]

                for example in synset.findall('Example'):
                    if example.text:
                        self._process_single_example(
                            example.text.strip(),
                            f"ex_{example_counter}",
                            is_synset_example=True,
                            concept_id=concept_id
                        )
                        example_counter += 1

                        # Progress logging
                        if example_counter % 10000 == 0:
                            logger.info(
                                f"    Processed {example_counter}/{total_examples} examples "
                                f"(skipped: {self.log['statistics']['examples']['skipped']})")

        # Process sense examples
        for lexicon in root.findall('Lexicon') + root.findall('LexiconExtension'):
            lexical_entries = lexicon.findall('LexicalEntry') + lexicon.findall('ExternalLexicalEntry')
            for entry in lexical_entries:
                senses = entry.findall('Sense') + entry.findall('ExternalSense')
                for sense in senses:
                    old_sense_id = sense.get('id')

                    for example in sense.findall('Example'):
                        if example.text:
                            self._process_single_example(
                                example.text.strip(),
                                f"ex_{example_counter}",
                                is_synset_example=False,
                                old_sense_id=old_sense_id
                            )
                            example_counter += 1

                            # Progress logging
                            if example_counter % 100 == 0:
                                logger.info(
                                    f"    Processed {example_counter}/{total_examples} examples "
                                    f"(skipped: {self.log['statistics']['examples']['skipped']})")

        logger.info(f"  Examples processed: {self.log['statistics']['examples']['processed']}")
        logger.warning(f"  Examples skipped: {self.log['statistics']['examples']['skipped']}")
        if total_examples > 0:
            success_rate = (self.log['statistics']['examples']['processed'] / total_examples) * 100
            self.log['statistics']['examples']['success_rate_pct'] = round(success_rate, 1)
            logger.info(f"  Success rate: {success_rate:.1f}%")

    def _process_single_example(self, text: str, example_id: str,
                                is_synset_example: bool = False,
                                concept_id: str = None,
                                old_sense_id: str = None):
        """Process a single example and create annotated token."""
        candidates = []

        if is_synset_example:
            # Get all senses for this concept
            sense_ids = self.concept_to_senses.get(concept_id, [])

            # For each sense, get all written wordforms from its lexeme
            for sense_id in sense_ids:
                sense_elem = self.sense_lookup.get(sense_id)
                if sense_elem is None:
                    continue

                lexeme_id = sense_elem.get('signifier')
                written_forms = self._get_lexeme_written_forms(lexeme_id)

                for form in written_forms:
                    candidates.append((sense_id, form))

        else:
            # Sense example - get the new sense ID
            if old_sense_id not in self.old_sense_to_new_sense:
                self.log['examples']['skipped'] += 1
                return

            sense_id = self.old_sense_to_new_sense[old_sense_id]
            sense_elem = self.sense_lookup.get(sense_id)

            if sense_elem is None:
                self.log['examples']['skipped'] += 1
                return

            lexeme_id = sense_elem.get('signifier')
            written_forms = self._get_lexeme_written_forms(lexeme_id)

            for form in written_forms:
                candidates.append((sense_id, form))

        if not candidates:
            self.log['statistics']['examples']['skipped'] += 1
            self.log['statistics']['examples']['failed_matches'].append({
                'text': text,
                'candidate_wordforms': [],
            })
            return

        # Find best match
        target_sense_id, target_wordform = self._find_best_match(text, candidates)

        if not target_sense_id:
            self.log['statistics']['examples']['skipped'] += 1
            self.log['statistics']['examples']['failed_matches'].append({
                'text': text,
                'candidate_wordforms': [form for _, form in candidates],
            })
            return

        # Create annotated example
        self._create_example_with_annotation(text, target_wordform, target_sense_id, example_id)
        self.log['statistics']['examples']['processed'] += 1

    def _create_example_with_annotation(self, text: str, wordform: str, sense_id: str, example_id: str):
        """Create an Example element with AnnotatedToken."""
        text_lower = text.lower()
        wordform_lower = wordform.lower()

        # Find position in lowercased text
        pos = text_lower.find(wordform_lower)
        if pos == -1:
            return

        # Expand to token boundaries using lowercased text
        match_end = pos + len(wordform)
        expanded_start, expanded_end = self._expand_to_token_boundaries(text_lower, pos, match_end)

        # Create Example element using ORIGINAL text (not lowercased)
        example_elem = etree.Element('Example')

        # Create AnnotatedSentence container
        annotated_sentence = etree.SubElement(example_elem, 'AnnotatedSentence')

        # Add text before token (original capitalization)
        if expanded_start > 0:
            annotated_sentence.text = text[:expanded_start]

        # Create AnnotatedToken (original capitalization)
        annotated_token = etree.SubElement(annotated_sentence, 'AnnotatedToken', sense=sense_id)
        annotated_token.text = text[expanded_start:expanded_end]

        # Add text after token (original capitalization)
        if expanded_end < len(text):
            annotated_token.tail = text[expanded_end:]

        # Add Provenance element with original_id
        from_elem = etree.SubElement(example_elem, 'Provenance',
                                     resource=self.lexicon_id,
                                     version=self.lexicon_version)
        from_elem.set('original_id', example_id)

        self.examples.append(example_elem)

    def build_output_xml(self) -> etree.Element:
        """Build the output CygnetResource XML tree."""
        logger.info("\nBuilding output XML...")

        # Create root element with all source attributes preserved
        cygnet_root = etree.Element('CygnetResource', **self.lexicon_attrs)

        # Add layers only if they have content
        if self.concepts:
            concept_layer = etree.SubElement(cygnet_root, 'ConceptLayer')
            for concept in self.concepts:
                concept_layer.append(concept)

        if self.lexemes:
            lexeme_layer = etree.SubElement(cygnet_root, 'LexemeLayer')
            for lexeme in self.lexemes:
                lexeme_layer.append(lexeme)

        if self.senses:
            sense_layer = etree.SubElement(cygnet_root, 'SenseLayer')
            for sense in self.senses:
                sense_layer.append(sense)

        if self.glosses:
            gloss_layer = etree.SubElement(cygnet_root, 'GlossLayer')
            for gloss in self.glosses:
                gloss_layer.append(gloss)

        if self.examples:
            example_layer = etree.SubElement(cygnet_root, 'ExampleLayer')
            for example in self.examples:
                example_layer.append(example)

        if self.sense_relations:
            sense_rel_layer = etree.SubElement(cygnet_root, 'SenseRelationLayer')
            for rel in self.sense_relations:
                sense_rel_layer.append(rel)

        if self.concept_relations:
            concept_rel_layer = etree.SubElement(cygnet_root, 'ConceptRelationLayer')
            for rel in self.concept_relations:
                concept_rel_layer.append(rel)

        return cygnet_root

    def convert_from_tree(self, root: etree.Element):
        """Convert from an already-parsed XML tree."""
        # Load CILI
        self.load_cili()

        # Load existing relations (if provided)
        self.load_relations_file()

        # Run passes
        self.pass1_synsets_to_concepts_and_glosses(root)
        self.pass2_lexical_entries_to_lexemes_and_senses(root)
        self.pass3_relations(root)
        self.pass4_examples(root)


    def convert(self, input_path: str):
        """Parse input file, convert, and return the output XML tree."""
        root, _ = self.read_metadata(input_path)
        self.convert_from_tree(root)
        return etree.ElementTree(self.build_output_xml())


    def save(self, output_path: str):
        """Build output XML and save to file with log."""
        # Build output
        output_root = self.build_output_xml()
        output_tree = etree.ElementTree(output_root)

        # Derive log filename from output
        log_path = output_path[:-4] + '_log.json'

        # Write output XML
        logger.info(f"Writing output to {output_path}...")
        output_tree.write(output_path,
                          pretty_print=True,
                          xml_declaration=True,
                          encoding='UTF-8')

        # Write log
        logger.info(f"Writing log to {log_path}...")
        with open(log_path, 'w', encoding='utf-8') as f:
            json.dump(self.log, f, indent=2, ensure_ascii=False)



def convert_wordnet_to_cygnet(lmf_xml, cili_xml=None, relations_xml=None, skip_cili_defns=False):
    """Convert WordNet LMF XML to Cygnet XML structure.

    Args:
        lmf_xml: LMF XML data (can be file path, etree.Element, or string)
        cili_xml: Optional CILI XML path
        relations_xml: Optional relations XML path
        skip_cili_defns: If True, skip CILI definitions for concepts

    Returns:
        etree.Element: Root element of Cygnet XML structure
    """
    converter = WordNetToCygnetConverter(cili_xml, relations_xml, skip_cili_defns=skip_cili_defns)
    return converter.convert(lmf_xml)

def save_wordnet_to_cygnet(lmf_xml, output, cili_xml=None, relations_xml=None, skip_cili_defns=False):

    converter = WordNetToCygnetConverter(cili_xml, relations_xml, skip_cili_defns=skip_cili_defns)
    converter.convert_from_tree(lmf_xml)
    converter.save(output)

    logger.info("\nConversion complete!")

def main():
    parser = argparse.ArgumentParser(
        description='Convert WordNet LMF format to Cygnet format'
    )
    parser.add_argument('--input', required=True,
                        help='Input WordNet LMF XML file (.xml, .xml.gz, or .xml.xz)')
    parser.add_argument('--output', required=True,
                        help='Output Cygnet XML file (must end in .xml)')
    parser.add_argument('--cili', required=True,
                        help='CILI file path (e.g., bin/cyg-cili-1.0.xml)')
    parser.add_argument('--relations', required=False,
                        help='Optional relations file path for deduplication (supports .xml, .xml.gz, .xml.xz)')
    parser.add_argument('--skip-cili-defns', action='store_true',
                        help='Skip definitions for CILI concepts')

    args = parser.parse_args()

    # Validate output filename
    if not args.output.endswith('.xml'):
        parser.error("Output file must end with .xml")

    save_wordnet_to_cygnet(args.input, args.output, cili_xml=args.cili, relations_xml=args.relations,
                           skip_cili_defns=args.skip_cili_defns)

if __name__ == '__main__':
    main()