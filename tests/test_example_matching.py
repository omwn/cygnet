"""Tests for example-sentence matching in WordNetToCygnetConverter.

Covers _get_all_forms and _find_best_match across English and the
morphologically rich languages where tok2vec was previously disabled.
"""

import pytest
import spacy

from cyg.converters import WordNetToCygnetConverter, _spacy_candidates

_INSTALLED = set(spacy.util.get_installed_models())


def _lang_model_installed(lang: str) -> bool:
    """True if a language-specific (non-fallback) model is installed."""
    return any(m in _INSTALLED for m in _spacy_candidates(lang)[:-1])


# ---------------------------------------------------------------------------
# Helpers — build a minimal converter with NLP tools initialised
# ---------------------------------------------------------------------------

def _make_converter(lang: str) -> WordNetToCygnetConverter:
    """Return a converter with NLP tools loaded for *lang*, no DB needed."""
    conv = object.__new__(WordNetToCygnetConverter)
    conv.lexicon_language = lang
    conv.doc_cache = {}
    conv.form_cache = {}
    conv.nltk_lemmatizer = None
    try:
        import nltk
        from nltk.stem import WordNetLemmatizer
        nltk.data.find('corpora/wordnet')
        conv.nltk_lemmatizer = WordNetLemmatizer()
    except (ImportError, LookupError):
        pass
    conv.nlp = conv._load_spacy_model(_spacy_candidates(lang), disable=["parser", "ner"])
    return conv


# ---------------------------------------------------------------------------
# English — baseline
# ---------------------------------------------------------------------------

class TestEnglishMatching:
    @pytest.fixture(scope="class")
    def conv(self):
        return _make_converter('en')

    def test_plural_noun(self, conv):
        sid, _ = conv._find_best_match("The dogs barked.", [("s1", "dog")])
        assert sid == "s1"

    def test_past_tense_verb(self, conv):
        sid, _ = conv._find_best_match("She ran quickly.", [("s1", "run")])
        assert sid == "s1"

    def test_gerund(self, conv):
        sid, _ = conv._find_best_match("He is running.", [("s1", "run")])
        assert sid == "s1"

    def test_no_match(self, conv):
        assert conv._find_best_match("The cat sat.", [("s1", "dog")]) == (None, None)


# ---------------------------------------------------------------------------
# Russian — morphological case/gender inflection
# ---------------------------------------------------------------------------

class TestRussianMatching:
    @pytest.fixture(scope="class")
    def conv(self):
        pytest.importorskip("spacy")
        if not _lang_model_installed("ru"):
            pytest.skip("ru spaCy model not installed")
        return _make_converter('ru')

    def test_genitive_month(self, conv):
        """'январь' should match genitive 'января'."""
        sid, form = conv._find_best_match(
            "Сегодня двадцать третье января.", [("s1", "январь")]
        )
        assert sid == "s1"

    def test_neuter_adjective(self, conv):
        """'большой' should match neuter 'большое'."""
        sid, form = conv._find_best_match(
            "В её комнате большое зеркало.", [("s1", "большой")]
        )
        assert sid == "s1"

    def test_plural_noun(self, conv):
        """'девочка' should match plural nominative 'Девочки'."""
        sid, form = conv._find_best_match(
            "Девочки любят конфеты.", [("s1", "девочка")]
        )
        assert sid == "s1"


# ---------------------------------------------------------------------------
# Slovenian — morphological case inflection
# ---------------------------------------------------------------------------

class TestSlovenianMatching:
    @pytest.fixture(scope="class")
    def conv(self):
        pytest.importorskip("spacy")
        if not _lang_model_installed("sl"):
            pytest.skip("sl spaCy model not installed")
        return _make_converter('sl')

    def test_locative_plural(self, conv):
        """'življenje' should match locative plural 'življenjih'."""
        sid, form = conv._find_best_match(
            "Vojne terjajo velikansko ceno v človeških življenjih.",
            [("s1", "življenje")],
        )
        assert sid == "s1"


# ---------------------------------------------------------------------------
# Portuguese — verb inflection
# ---------------------------------------------------------------------------

class TestPortugueseMatching:
    @pytest.fixture(scope="class")
    def conv(self):
        pytest.importorskip("spacy")
        if not _lang_model_installed("pt"):
            pytest.skip("pt spaCy model not installed")
        return _make_converter('pt')

    def test_gerund(self, conv):
        """'respirar' should match gerund 'respirando'."""
        sid, form = conv._find_best_match(
            "O paciente está respirando.", [("s1", "respirar")]
        )
        assert sid == "s1"

    def test_feminine_plural_adjective(self, conv):
        """'último' should match feminine plural 'últimas'."""
        sid, form = conv._find_best_match(
            "As suas últimas palavras.", [("s1", "último")]
        )
        assert sid == "s1"


# ---------------------------------------------------------------------------
# Korean — stem-based matching via morpheme split
# ---------------------------------------------------------------------------

class TestKoreanMatching:
    @pytest.fixture(scope="class")
    def conv(self):
        pytest.importorskip("spacy")
        if not _lang_model_installed("ko"):
            pytest.skip("ko spaCy model not installed")
        return _make_converter('ko')

    def test_adnominal_adjective(self, conv):
        """'크다' (big) should match adnominal form '큰' via shared stem '크'."""
        sid, form = conv._find_best_match(
            "서울에서 제일 큰 서점이 어디에요?", [("s1", "크다")]
        )
        assert sid == "s1"

    def test_verb_sells(self, conv):
        """'팔다' (to sell) should match conjugated '파는'."""
        sid, form = conv._find_best_match(
            "그 백화점에서 파는 음식은 다 신선해요.", [("s1", "팔다")]
        )
        assert sid == "s1"
