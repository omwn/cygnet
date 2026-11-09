"""Locating a sense's word in an example sentence.

The matcher the converter grew for annotating examples: cheap string
matching first, then spaCy (lemmas, inflections via pyinflect, NLTK's
WordNet lemmatizer for English) for the rest, with the docs of a batch of
examples parsed in one nlp.pipe() call.  Used by the per-source cleaner to
decide which Sense a synset-level Example belongs to and where its word
sits (note="offsets=...").
"""

from __future__ import annotations

import logging
import sys
from collections import OrderedDict

import nltk
import pyinflect
import spacy
from nltk.stem import WordNetLemmatizer

logger = logging.getLogger(__name__)

# spaCy docs kept by WordNetToCygnetConverter._get_doc.
DOC_CACHE_SIZE = 4096
# Examples whose spaCy docs are parsed together in one nlp.pipe() batch:
# batching gives the same docs as one nlp() call each, 4-12 times faster.
EXAMPLE_BATCH_SIZE = 1000
# Characters that end a token, for deciding whether a match needs spaCy.
TOKEN_BOUNDARIES = frozenset(' .,!?;:"\'()[]{}-\u2014\u2013\n\t/\\')
# Words of a multi-word form that need not match the example.
FUNCTION_WORDS = frozenset({'a', 'an', 'the', 'of', 'in', 'on', 'at', 'to', 'for',
                            'with', 'by', 'from'})


def at_token_boundaries(text: str, start: int, end: int) -> bool:
    """True if text[start:end] begins and ends at a token boundary.

    Args:
        text: The text
        start: Start offset of the span
        end: End offset of the span

    Returns:
        Whether the span needs no expansion to whole tokens
    """
    return ((start == 0 or text[start - 1] in TOKEN_BOUNDARIES)
            and (end >= len(text) or text[end] in TOKEN_BOUNDARIES))


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


class ExampleLinker:
    """Finds the word of a sense in an example sentence.

    Attributes:
        language: The language code the spaCy model is chosen for.
        nlp: The spaCy pipeline, once load() has run.
        nltk_lemmatizer: NLTK's WordNet lemmatizer (English data), or None.
    """

    def __init__(self, language: str) -> None:
        self.language = language
        self.nlp = None
        self.nltk_lemmatizer = None
        # spaCy documents, most recently used last.  Bounded: a doc holds its
        # tensors, and an unbounded cache of every example text took about
        # 1 GB for plWordNet, though each text is only reused within one call.
        self.doc_cache: OrderedDict[str, spacy.tokens.Doc] = OrderedDict()
        # Docs parsed in advance for the current batch of examples
        self.prefetched_docs: dict[str, spacy.tokens.Doc] = {}
        self.form_cache: dict[str, set[str]] = {}  # morphological forms cache
        self.nltk_cache: dict[str, frozenset[str]] = {}  # NLTK lemmas per token

    def locate(self, text: str, candidates: list[tuple[str, str]],
               ) -> tuple[str, int, int] | None:
        """The sense whose word *text* contains, and the word's span.

        Args:
            text: The example sentence, as it will be written.
            candidates: (sense id, written form) pairs to look for.

        Returns:
            (sense id, start, end) with the span widened to whole tokens, or
            None if no candidate is found in the text.
        """
        sense_id, wordform = self.find_best_match(text, candidates)
        if not sense_id:
            return None
        text_lower = text.lower()
        start = text_lower.find(wordform.lower())
        if start == -1:
            return None
        start, end = self.expand_to_token_boundaries(text_lower, start, start + len(wordform))
        return sense_id, start, end

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
                    f"`uv pip install \"cygnet[{self.language}]\"`."
                )
            except OSError:
                logger.warning(f"  Could not load '{model_name}', trying next candidate.")

        raise RuntimeError(f"No usable spaCy model found for language '{self.language}'.")

    def load(self):
        """Initialize spaCy and NLTK tools for example processing."""
        logger.info(f"\nInitializing NLP tools for language '{self.language}'...")

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
        self.nlp = self._load_spacy_model(_spacy_candidates(self.language), disable)

        # Initialize NLTK lemmatizer
        logger.info("  Loading NLTK WordNet lemmatizer...")
        self.nltk_lemmatizer = WordNetLemmatizer()
        try:
            nltk.data.find('corpora/wordnet')
        except LookupError:
            logger.info("  Downloading NLTK wordnet data...")
            nltk.download('wordnet')

        logger.info("  NLP tools initialized successfully")

    def get_doc(self, text: str):
        """Get spaCy doc with caching."""
        doc = self.prefetched_docs.get(text)
        if doc is not None:
            return doc
        doc = self.doc_cache.get(text)
        if doc is None:
            doc = self.doc_cache[text] = self.nlp(text)
            if len(self.doc_cache) > DOC_CACHE_SIZE:
                self.doc_cache.popitem(last=False)
        else:
            self.doc_cache.move_to_end(text)
        return doc


    def get_all_forms(self, word: str) -> set[str]:
        """
        Get all possible morphological forms/lemmas of a word.
        Uses spaCy, NLTK, and pyinflect for comprehensive coverage.
        """
        word_lower = word.lower()
        if word_lower in self.form_cache:
            return self.form_cache[word_lower]

        forms = set()
        forms.add(word_lower)

        # Strip leading/trailing hyphens (e.g. Turkish verb stems like "yaz-",
        # Arabic broken plurals like "-kātib").  Add the stripped form and recurse
        # so it also gets spaCy/NLTK expansion, but only one level deep.
        stripped = word_lower.strip('-')
        if stripped and stripped != word_lower:
            forms.add(stripped)
            if stripped not in self.form_cache:
                # Temporarily mark to avoid infinite recursion
                self.form_cache[stripped] = set()
                self.form_cache[stripped] = self.get_all_forms(stripped)
            forms |= self.form_cache[stripped]

        # Get spaCy doc (cached)
        doc = self.get_doc(word_lower)
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

    def token_candidates(self, token_lower: str, token_lemma: str) -> set[str]:
        """Build the set of normalised forms for a single spaCy token.

        Handles empty lemmas (multilingual fallback model), Korean morpheme-split
        lemmas, and NLTK lemmatisation as a fallback for morphologically rich forms.
        """
        candidates = {token_lower}
        if token_lemma:
            candidates.add(token_lemma)
            if '+' in token_lemma:
                candidates.add(token_lemma.replace('+', ''))
                candidates.add(token_lemma.split('+')[0])
        if self.nltk_lemmatizer is not None:
            if token_lower not in self.nltk_cache:
                self.nltk_cache[token_lower] = frozenset(
                    self.nltk_lemmatizer.lemmatize(token_lower, pos=p)
                    for p in ['n', 'v', 'a', 'r']
                )
            candidates |= self.nltk_cache[token_lower]
        return candidates

    def match_single_word(self, wordform: str, text: str) -> str | None:
        """Match a single word using morphological form matching."""
        wordform_forms = self.get_all_forms(wordform)
        doc = self.get_doc(text)
        for token in doc:
            token_lower = token.text.lower()
            if self.token_candidates(token_lower, token.lemma_.lower()) & wordform_forms:
                return token_lower
        return None

    def match_multi_word(self, form_words: list[str], text: str) -> str | None:
        """Match a multi-word expression using morphological form matching."""
        # Get all forms for content words in the MWE
        content_word_forms = []
        for word in form_words:
            word_lower = word.lower()
            if word_lower not in FUNCTION_WORDS:
                forms = self.get_all_forms(word_lower)
                content_word_forms.append(forms)

        # Look for matching sequences in the document
        doc = self.get_doc(text)
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
                if self.token_candidates(token_lower, token.lemma_.lower()) & target_forms:
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

    def find_best_match(self, text: str, candidates: list[tuple[str, str]]) -> tuple[str | None, str | None]:
        """
        Find the best matching wordform in the text.

        Args:
            text: The example text to search in
            candidates: List of (sense_id, wordform) tuples

        Returns:
            Tuple of (sense_id, matched_wordform) or (None, None) if no match
        """
        # Sort candidates by length (longest first) to prefer longer matches
        candidates_sorted = sorted(candidates, key=lambda x: len(x[1]), reverse=True)
        match = self.cheap_match(text.lower(), candidates_sorted)
        if match is not None:
            return match

        # Strategy 3: Try morphological matching with spaCy (EXPENSIVE)
        for sense_id, form in candidates_sorted:
            form_words = form.split()

            if len(form_words) == 1:
                # Single word matching
                result = self.match_single_word(form, text)
                if result:
                    return sense_id, result
            else:
                # Multi-word expression matching
                result = self.match_multi_word(form_words, text)
                if result:
                    return sense_id, result

        return None, None

    def cheap_match(self, text_lower: str, candidates_sorted: list[tuple[str, str]]
                     ) -> tuple[str, str] | None:
        """Match a candidate by string comparison alone, without spaCy.

        Args:
            text_lower: The lowercased example text
            candidates_sorted: (sense_id, wordform) pairs, longest form first

        Returns:
            (sense_id, wordform) of the first match, or None
        """
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
        return None

    def expand_to_token_boundaries(self, text: str, match_start: int, match_end: int) -> tuple[int, int]:
        """Expand match to complete token boundaries."""
        # Stage 1: Check if already at boundaries (CHEAP)
        if at_token_boundaries(text, match_start, match_end):
            return match_start, match_end

        # Stage 2: Use spaCy to find actual token boundaries (EXPENSIVE)
        doc = self.get_doc(text)

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

    def prefetch_docs(self,
                       batch: list[tuple[str, list[tuple[str, str]] | None]]) -> None:
        """Parse in one nlp.pipe() batch the docs these examples will need.

        Mirrors _find_best_match and _expand_to_token_boundaries: if a cheap
        match lies on token boundaries no doc is needed; if it does not, the
        doc of the lowercased text is; if there is no cheap match, the docs
        of the text and the candidate words are. (A spaCy match is a whole
        token, so it rarely needs the lowercased text too.) Any doc missed
        here is parsed on demand, so this only affects speed.

        Args:
            batch: (example text, its candidates) pairs
        """
        needed: dict[str, None] = {}  # insertion-ordered set
        for text, candidates in batch:
            if not candidates:
                continue
            candidates_sorted = sorted(candidates, key=lambda x: len(x[1]),
                                       reverse=True)
            text_lower = text.lower()
            match = self.cheap_match(text_lower, candidates_sorted)
            if match is not None:
                start = text_lower.find(match[1].lower())
                end = start + len(match[1])
                if start != -1 and not at_token_boundaries(text_lower, start, end):
                    needed[text_lower] = None
                continue
            needed[text] = None
            for _sense_id, form in candidates_sorted:
                form_words = form.split()
                if len(form_words) == 1:
                    words = [form.lower()]
                else:
                    words = [w.lower() for w in form_words
                             if w.lower() not in FUNCTION_WORDS]
                for word in words:
                    for w in (word, word.strip('-')):
                        if w and w not in self.form_cache:
                            needed[w] = None
        texts = [t for t in needed if t not in self.doc_cache]
        self.prefetched_docs = dict(zip(texts, self.nlp.pipe(texts, batch_size=256)))
