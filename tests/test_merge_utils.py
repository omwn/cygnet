"""Unit tests for utility functions in cyg.merge."""

import pytest
from lxml import etree as ET

from cyg.merge import normalize_form, parse_annotated_sentence, remove_accents


class TestRemoveAccents:
    def test_plain_ascii_unchanged(self):
        assert remove_accents("hello world") == "hello world"

    def test_accented_characters_stripped(self):
        assert remove_accents("café") == "cafe"

    def test_combining_diacritics_stripped(self):
        # NFD: 'e' + combining acute accent
        assert remove_accents("e\u0301") == "e"

    def test_empty_string(self):
        assert remove_accents("") == ""

    def test_non_latin_script_unaffected(self):
        # CJK characters have no combining marks; should pass through
        result = remove_accents("日本語")
        assert result == "日本語"

    def test_multiple_accents(self):
        assert remove_accents("naïve résumé") == "naive resume"


class TestNormalizeForm:
    def test_plain_ascii_unchanged(self):
        assert normalize_form("hello") == "hello"

    def test_empty_string(self):
        assert normalize_form("") == ""

    def test_nfd_input_composed_to_nfc(self):
        # 'e' + combining acute accent (NFD) -> precomposed 'é' (NFC)
        nfd = "é"
        assert normalize_form(nfd) == "é"

    def test_nfc_input_unchanged(self):
        nfc = "é"
        assert normalize_form(nfc) == nfc

    def test_nfc_and_nfd_variants_become_identical(self):
        nfc = "café"
        nfd = "café"
        assert normalize_form(nfc) == normalize_form(nfd)


class TestParseAnnotatedSentence:
    def _elem(self, xml: str) -> ET.Element:
        return ET.fromstring(xml)

    def test_plain_text_no_annotations(self):
        elem = self._elem("<AnnotatedSentence>a dog</AnnotatedSentence>")
        text, annotations = parse_annotated_sentence(elem)
        assert text == "a dog"
        assert annotations == []

    def test_empty_element(self):
        elem = self._elem("<AnnotatedSentence/>")
        text, annotations = parse_annotated_sentence(elem)
        assert text == ""
        assert annotations == []

    def test_annotated_word(self):
        elem = self._elem(
            "<AnnotatedSentence>a "
            "<tok sense='s1'>dog</tok>"
            " ran</AnnotatedSentence>"
        )
        text, annotations = parse_annotated_sentence(elem)
        assert "dog" in text
        assert any(a["sense"] == "s1" for a in annotations)

    def test_multiple_annotations(self):
        elem = self._elem(
            "<AnnotatedSentence>"
            "<tok sense='s1'>cat</tok>"
            " and "
            "<tok sense='s2'>dog</tok>"
            "</AnnotatedSentence>"
        )
        text, annotations = parse_annotated_sentence(elem)
        assert len(annotations) == 2
        sense_ids = {a["sense"] for a in annotations}
        assert sense_ids == {"s1", "s2"}
