"""Unit tests for helper functions in conversion_scripts/1_extract_cili.py."""

import importlib.util
import sys
from pathlib import Path

import pytest

# Load module from numerically-prefixed filename
_script = Path(__file__).parent.parent / 'conversion_scripts' / '1_extract_cili.py'
_spec = importlib.util.spec_from_file_location('extract_cili', _script)
_mod = importlib.util.module_from_spec(_spec)
sys.modules['extract_cili'] = _mod
_spec.loader.exec_module(_mod)

normalize_whitespace = _mod.normalize_whitespace
get_ontological_category = _mod.get_ontological_category
get_from = _mod.get_from


class TestNormalizeWhitespace:
    def test_collapses_multiple_spaces(self):
        assert normalize_whitespace("a  b   c") == "a b c"

    def test_strips_leading_trailing(self):
        assert normalize_whitespace("  hello  ") == "hello"

    def test_empty_string(self):
        assert normalize_whitespace("") == ""

    def test_none_passthrough(self):
        assert normalize_whitespace(None) is None

    def test_tabs_and_newlines(self):
        assert normalize_whitespace("a\t\nb") == "a b"


class TestGetOntologicalCategory:
    @pytest.mark.parametrize("origin,expected", [
        ("pwn-3.0:foo-n", "NOUN"),
        ("pwn-3.0:foo-v", "VERB"),
        ("pwn-3.0:foo-a", "ADJ"),
        ("pwn-3.0:foo-r", "ADV"),
        ("pwn-3.0:foo-s", "ADJ"),
        ("pwn-3.0:foo-u", "UNK"),
    ])
    def test_known_pos_chars(self, origin, expected):
        assert get_ontological_category(origin) == expected

    def test_empty_origin_raises(self):
        with pytest.raises(ValueError, match="Empty origin"):
            get_ontological_category("")

    def test_unknown_pos_char_raises(self):
        with pytest.raises(ValueError, match="Unrecognised POS"):
            get_ontological_category("pwn-3.0:foo-z")


class TestGetFrom:
    def test_valid_origin(self):
        wn_name, version, orig_id = get_from("pwn-3.0:eng-30-02084071-n")
        assert wn_name == "pwn"
        assert version == "3.0"
        assert orig_id == "eng-30-02084071-n"

    def test_wrong_wn_name_raises(self):
        with pytest.raises(ValueError, match="Expected 'pwn'"):
            get_from("omw-3.0:foo-n")

    def test_wrong_version_raises(self):
        with pytest.raises(ValueError, match="Expected version '3.0'"):
            get_from("pwn-2.0:foo-n")
