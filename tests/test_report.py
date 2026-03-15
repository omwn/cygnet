"""Tests for scripts/report.py — wordnet data-quality checker."""

from pathlib import Path

import pytest

from conftest import wn_xml

# Import the module under test via its file path so we don't need to install it.
import importlib.util, sys

_REPORT_PATH = Path(__file__).parent.parent / "scripts" / "report.py"
_spec = importlib.util.spec_from_file_location("report", _REPORT_PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["report"] = _mod
_spec.loader.exec_module(_mod)

parse_xml = _mod.parse_xml
run_checks = _mod.run_checks
format_report = _mod.format_report
check_empty_entries = _mod.check_empty_entries
check_unglossed_concepts = _mod.check_unglossed_concepts
check_hypernym_cycles = _mod.check_hypernym_cycles
check_self_loops = _mod.check_self_loops
check_internal_reversed_relations = _mod.check_internal_reversed_relations
check_dangling_senses = _mod.check_dangling_senses
check_unmatched_examples = _mod.check_unmatched_examples
check_non_standard_relations = _mod.check_non_standard_relations
check_duplicate_ids = _mod.check_duplicate_ids
check_glossed_concepts_without_senses = _mod.check_glossed_concepts_without_senses
load_json_log = _mod.load_json_log
parse_conflicts_json = _mod.parse_conflicts_json
issues_from_json_log = _mod.issues_from_json_log
issues_from_conflicts_log = _mod.issues_from_conflicts_log
label_concept = _mod.label_concept


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_xml(tmp_path: Path, body: str, wn_id: str = "wn-test") -> Path:
    p = tmp_path / f"{wn_id}.xml"
    p.write_text(wn_xml(wn_id, body))
    return p


def _parse(tmp_path: Path, body: str, wn_id: str = "wn-test"):
    return parse_xml(_write_xml(tmp_path, body, wn_id))


# Minimal well-formed body: one concept + gloss + entry + sense
_GOOD = """\
<Concept id="cili.i1" ontological_category="NOUN" status="1">
  <Provenance resource="wn-test" version="1.0"/>
</Concept>
<Gloss definiendum="cili.i1" language="en">
  <AnnotatedSentence>a test concept</AnnotatedSentence>
  <Provenance resource="wn-test" version="1.0"/>
</Gloss>
<Lexeme id="en.NOUN.test" language="en" grammatical_category="NOUN">
  <Wordform form="test"/>
  <Provenance resource="wn-test" version="1.0"/>
</Lexeme>
<Sense id="sense.test" signifier="en.NOUN.test" signified="cili.i1">
  <Provenance resource="wn-test" version="1.0"/>
</Sense>
"""


# ---------------------------------------------------------------------------
# parse_xml
# ---------------------------------------------------------------------------

class TestParseXml:
    def test_root_attributes(self, tmp_path):
        data = _parse(tmp_path, _GOOD)
        assert data.resource_id == "wn-test"
        assert data.language == "en"
        assert data.version == "1.0"

    def test_counts_clean_file(self, tmp_path):
        data = _parse(tmp_path, _GOOD)
        assert len(data.concepts) == 1
        assert len(data.entries) == 1
        assert len(data.senses) == 1
        assert "cili.i1" in data.glossed

    def test_layer_wrappers_transparent(self, tmp_path):
        """Elements inside layer wrappers (ConceptLayer etc.) are parsed correctly."""
        body = """\
<ConceptLayer>
  <Concept id="cili.i2" ontological_category="NOUN" status="1">
    <Provenance resource="wn-test" version="1.0"/>
  </Concept>
</ConceptLayer>
<GlossLayer>
  <Gloss definiendum="cili.i2" language="en">
    <AnnotatedSentence>wrapped</AnnotatedSentence>
    <Provenance resource="wn-test" version="1.0"/>
  </Gloss>
</GlossLayer>
"""
        data = _parse(tmp_path, body)
        assert "cili.i2" in data.concepts
        assert "cili.i2" in data.glossed

    def test_example_sense_ids_extracted(self, tmp_path):
        body = """\
<Concept id="cili.i1" ontological_category="NOUN" status="1">
  <Provenance resource="wn-test" version="1.0"/>
</Concept>
<Gloss definiendum="cili.i1" language="en">
  <AnnotatedSentence>thing</AnnotatedSentence>
  <Provenance resource="wn-test" version="1.0"/>
</Gloss>
<Lexeme id="en.NOUN.foo" language="en" grammatical_category="NOUN">
  <Wordform form="foo"/>
  <Provenance resource="wn-test" version="1.0"/>
</Lexeme>
<Sense id="sense.foo" signifier="en.NOUN.foo" signified="cili.i1">
  <Provenance resource="wn-test" version="1.0"/>
</Sense>
<Example>
  <AnnotatedSentence>The <AnnotatedToken sense="sense.foo">foo</AnnotatedToken> bar.</AnnotatedSentence>
  <Provenance resource="wn-test" version="1.0"/>
</Example>
"""
        data = _parse(tmp_path, body)
        assert len(data.examples) == 1
        _text, sense_ids = data.examples[0]
        assert "sense.foo" in sense_ids

    def test_duplicate_concept_tracked(self, tmp_path):
        body = """\
<Concept id="cili.i1" ontological_category="NOUN" status="1">
  <Provenance resource="wn-test" version="1.0"/>
</Concept>
<Concept id="cili.i1" ontological_category="NOUN" status="1">
  <Provenance resource="wn-test" version="1.0"/>
</Concept>
"""
        data = _parse(tmp_path, body)
        assert "cili.i1" in data.duplicate_concept_ids


# ---------------------------------------------------------------------------
# check_empty_entries
# ---------------------------------------------------------------------------

class TestCheckEmptyEntries:
    def test_no_issue_when_all_have_forms(self, tmp_path):
        data = _parse(tmp_path, _GOOD)
        assert check_empty_entries(data) is None

    def test_flags_entry_without_wordform(self, tmp_path):
        body = """\
<Lexeme id="en.NOUN.empty" language="en" grammatical_category="NOUN">
  <Provenance resource="wn-test" version="1.0"/>
</Lexeme>
"""
        data = _parse(tmp_path, body)
        issue = check_empty_entries(data)
        assert issue is not None
        assert issue.severity == "CRITICAL"
        assert issue.total == 1
        assert "en.NOUN.empty" in issue.items

    def test_counts_lost_senses(self, tmp_path):
        body = """\
<Concept id="cili.i1" ontological_category="NOUN" status="1">
  <Provenance resource="wn-test" version="1.0"/>
</Concept>
<Lexeme id="en.NOUN.empty" language="en" grammatical_category="NOUN">
  <Provenance resource="wn-test" version="1.0"/>
</Lexeme>
<Sense id="sense.empty" signifier="en.NOUN.empty" signified="cili.i1">
  <Provenance resource="wn-test" version="1.0"/>
</Sense>
"""
        data = _parse(tmp_path, body)
        issue = check_empty_entries(data)
        assert issue is not None
        assert "1 sense" in issue.explanation


# ---------------------------------------------------------------------------
# check_unglossed_concepts
# ---------------------------------------------------------------------------

class TestCheckUnglosssedConcepts:
    def test_no_issue_when_all_glossed(self, tmp_path):
        data = _parse(tmp_path, _GOOD)
        assert check_unglossed_concepts(data) is None

    def test_flags_concept_without_gloss(self, tmp_path):
        body = """\
<Concept id="cili.i99" ontological_category="NOUN" status="1">
  <Provenance resource="wn-test" version="1.0"/>
</Concept>
"""
        data = _parse(tmp_path, body)
        issue = check_unglossed_concepts(data)
        assert issue is not None
        assert issue.severity == "CRITICAL"
        assert "cili.i99" in issue.items[0]

    def test_sense_count_in_item(self, tmp_path):
        body = """\
<Concept id="cili.i99" ontological_category="NOUN" status="1">
  <Provenance resource="wn-test" version="1.0"/>
</Concept>
<Lexeme id="en.NOUN.x" language="en" grammatical_category="NOUN">
  <Wordform form="x"/>
  <Provenance resource="wn-test" version="1.0"/>
</Lexeme>
<Sense id="sense.x" signifier="en.NOUN.x" signified="cili.i99">
  <Provenance resource="wn-test" version="1.0"/>
</Sense>
"""
        data = _parse(tmp_path, body)
        issue = check_unglossed_concepts(data)
        assert issue is not None
        assert "1 sense" in issue.items[0]


# ---------------------------------------------------------------------------
# check_hypernym_cycles
# ---------------------------------------------------------------------------

class TestCheckHypernymCycles:
    def test_no_issue_for_dag(self, tmp_path):
        body = _GOOD + """\
<Concept id="cili.i2" ontological_category="NOUN" status="1">
  <Provenance resource="wn-test" version="1.0"/>
</Concept>
<Gloss definiendum="cili.i2" language="en">
  <AnnotatedSentence>parent</AnnotatedSentence>
  <Provenance resource="wn-test" version="1.0"/>
</Gloss>
<ConceptRelation relation_type="hypernym" source="cili.i1" target="cili.i2">
  <Provenance resource="wn-test" version="1.0"/>
</ConceptRelation>
"""
        data = _parse(tmp_path, body)
        assert check_hypernym_cycles(data) is None

    def test_detects_two_node_cycle(self, tmp_path):
        body = """\
<ConceptRelation relation_type="hypernym" source="cili.i1" target="cili.i2">
  <Provenance resource="wn-test" version="1.0"/>
</ConceptRelation>
<ConceptRelation relation_type="hypernym" source="cili.i2" target="cili.i1">
  <Provenance resource="wn-test" version="1.0"/>
</ConceptRelation>
"""
        data = _parse(tmp_path, body)
        issue = check_hypernym_cycles(data)
        assert issue is not None
        assert issue.severity == "CRITICAL"
        assert issue.total >= 1
        assert any("cili.i1" in item and "cili.i2" in item for item in issue.items)

    def test_detects_three_node_cycle(self, tmp_path):
        body = """\
<ConceptRelation relation_type="hypernym" source="cili.i1" target="cili.i2">
  <Provenance resource="wn-test" version="1.0"/>
</ConceptRelation>
<ConceptRelation relation_type="hypernym" source="cili.i2" target="cili.i3">
  <Provenance resource="wn-test" version="1.0"/>
</ConceptRelation>
<ConceptRelation relation_type="hypernym" source="cili.i3" target="cili.i1">
  <Provenance resource="wn-test" version="1.0"/>
</ConceptRelation>
"""
        data = _parse(tmp_path, body)
        issue = check_hypernym_cycles(data)
        assert issue is not None
        assert issue.total >= 1


# ---------------------------------------------------------------------------
# check_self_loops
# ---------------------------------------------------------------------------

class TestCheckSelfLoops:
    def test_no_issue_for_clean_file(self, tmp_path):
        data = _parse(tmp_path, _GOOD)
        assert check_self_loops(data) is None

    def test_detects_concept_self_loop(self, tmp_path):
        body = """\
<ConceptRelation relation_type="hypernym" source="cili.i1" target="cili.i1">
  <Provenance resource="wn-test" version="1.0"/>
</ConceptRelation>
"""
        data = _parse(tmp_path, body)
        issue = check_self_loops(data)
        assert issue is not None
        assert issue.severity == "WARNING"
        assert "cili.i1" in issue.items

    def test_detects_sense_self_loop(self, tmp_path):
        body = """\
<SenseRelation relation_type="antonym" source="sense.x" target="sense.x">
  <Provenance resource="wn-test" version="1.0"/>
</SenseRelation>
"""
        data = _parse(tmp_path, body)
        issue = check_self_loops(data)
        assert issue is not None
        assert "sense.x" in issue.items


# ---------------------------------------------------------------------------
# check_internal_reversed_relations
# ---------------------------------------------------------------------------

class TestCheckInternalReversedRelations:
    def test_no_issue_for_dag(self, tmp_path):
        body = """\
<ConceptRelation relation_type="hypernym" source="cili.i1" target="cili.i2">
  <Provenance resource="wn-test" version="1.0"/>
</ConceptRelation>
"""
        data = _parse(tmp_path, body)
        assert check_internal_reversed_relations(data) is None

    def test_detects_reversed_hypernym(self, tmp_path):
        body = """\
<ConceptRelation relation_type="hypernym" source="cili.i1" target="cili.i2">
  <Provenance resource="wn-test" version="1.0"/>
</ConceptRelation>
<ConceptRelation relation_type="hypernym" source="cili.i2" target="cili.i1">
  <Provenance resource="wn-test" version="1.0"/>
</ConceptRelation>
"""
        data = _parse(tmp_path, body)
        issue = check_internal_reversed_relations(data)
        assert issue is not None
        assert issue.severity == "WARNING"
        assert issue.total == 1

    def test_items_include_labels(self, tmp_path):
        """Concept labels (wordforms) appear in the conflict string."""
        body = """\
<Concept id="cili.i1" ontological_category="NOUN" status="1">
  <Provenance resource="wn-test" version="1.0"/>
</Concept>
<Gloss definiendum="cili.i1" language="en">
  <AnnotatedSentence>dog</AnnotatedSentence>
  <Provenance resource="wn-test" version="1.0"/>
</Gloss>
<Concept id="cili.i2" ontological_category="NOUN" status="1">
  <Provenance resource="wn-test" version="1.0"/>
</Concept>
<Gloss definiendum="cili.i2" language="en">
  <AnnotatedSentence>animal</AnnotatedSentence>
  <Provenance resource="wn-test" version="1.0"/>
</Gloss>
<Lexeme id="en.NOUN.dog" language="en" grammatical_category="NOUN">
  <Wordform form="dog"/>
  <Provenance resource="wn-test" version="1.0"/>
</Lexeme>
<Lexeme id="en.NOUN.animal" language="en" grammatical_category="NOUN">
  <Wordform form="animal"/>
  <Provenance resource="wn-test" version="1.0"/>
</Lexeme>
<Sense id="sense.dog" signifier="en.NOUN.dog" signified="cili.i1">
  <Provenance resource="wn-test" version="1.0"/>
</Sense>
<Sense id="sense.animal" signifier="en.NOUN.animal" signified="cili.i2">
  <Provenance resource="wn-test" version="1.0"/>
</Sense>
<ConceptRelation relation_type="hypernym" source="cili.i1" target="cili.i2">
  <Provenance resource="wn-test" version="1.0"/>
</ConceptRelation>
<ConceptRelation relation_type="hypernym" source="cili.i2" target="cili.i1">
  <Provenance resource="wn-test" version="1.0"/>
</ConceptRelation>
"""
        data = _parse(tmp_path, body)
        issue = check_internal_reversed_relations(data)
        assert issue is not None
        assert any("dog" in item and "animal" in item for item in issue.items)

    def test_symmetric_antonym_not_flagged(self, tmp_path):
        """antonym is symmetric — both directions in the same file is not a conflict."""
        body = """\
<ConceptRelation relation_type="antonym" source="cili.i1" target="cili.i2">
  <Provenance resource="wn-test" version="1.0"/>
</ConceptRelation>
<ConceptRelation relation_type="antonym" source="cili.i2" target="cili.i1">
  <Provenance resource="wn-test" version="1.0"/>
</ConceptRelation>
"""
        data = _parse(tmp_path, body)
        assert check_internal_reversed_relations(data) is None


# ---------------------------------------------------------------------------
# check_dangling_senses
# ---------------------------------------------------------------------------

class TestCheckDanglingSenses:
    def test_no_issue_for_clean_file(self, tmp_path):
        data = _parse(tmp_path, _GOOD)
        assert check_dangling_senses(data) is None

    def test_flags_unknown_signifier(self, tmp_path):
        body = """\
<Sense id="sense.ghost" signifier="en.NOUN.ghost" signified="cili.i1">
  <Provenance resource="wn-test" version="1.0"/>
</Sense>
"""
        data = _parse(tmp_path, body)
        issue = check_dangling_senses(data)
        assert issue is not None
        assert issue.severity == "WARNING"
        assert any("en.NOUN.ghost" in item for item in issue.items)

    def test_cross_file_concept_ok(self, tmp_path):
        """signified references to cili. IDs that aren't in this file are expected — no issue."""
        data = _parse(tmp_path, _GOOD)
        # The sense in _GOOD references cili.i1 which IS in this file; ensure no dangling
        assert check_dangling_senses(data) is None


# ---------------------------------------------------------------------------
# check_unmatched_examples
# ---------------------------------------------------------------------------

class TestCheckUnmatchedExamples:
    def test_no_issue_when_example_matches(self, tmp_path):
        body = _GOOD + """\
<Example>
  <AnnotatedSentence>The <AnnotatedToken sense="sense.test">test</AnnotatedToken> works.</AnnotatedSentence>
  <Provenance resource="wn-test" version="1.0"/>
</Example>
"""
        data = _parse(tmp_path, body)
        assert check_unmatched_examples(data) is None

    def test_flags_unannotated_example(self, tmp_path):
        body = """\
<Example>
  <AnnotatedSentence>No annotations here at all.</AnnotatedSentence>
  <Provenance resource="wn-test" version="1.0"/>
</Example>
"""
        data = _parse(tmp_path, body)
        issue = check_unmatched_examples(data)
        assert issue is not None
        assert issue.severity == "WARNING"
        assert "no annotations" in issue.items[0]

    def test_flags_example_with_unknown_sense(self, tmp_path):
        body = """\
<Example>
  <AnnotatedSentence><AnnotatedToken sense="sense.nonexistent">word</AnnotatedToken></AnnotatedSentence>
  <Provenance resource="wn-test" version="1.0"/>
</Example>
"""
        data = _parse(tmp_path, body)
        issue = check_unmatched_examples(data)
        assert issue is not None
        assert "sense.nonexistent" in issue.items[0]


# ---------------------------------------------------------------------------
# check_non_standard_relations
# ---------------------------------------------------------------------------

class TestCheckNonStandardRelations:
    def test_no_issue_for_standard_relations(self, tmp_path):
        body = """\
<ConceptRelation relation_type="hypernym" source="cili.i1" target="cili.i2">
  <Provenance resource="wn-test" version="1.0"/>
</ConceptRelation>
<SenseRelation relation_type="derivation" source="sense.a" target="sense.b">
  <Provenance resource="wn-test" version="1.0"/>
</SenseRelation>
"""
        data = _parse(tmp_path, body)
        assert check_non_standard_relations(data) is None

    def test_flags_unknown_concept_relation(self, tmp_path):
        body = """\
<ConceptRelation relation_type="made_of_cheese" source="cili.i1" target="cili.i2">
  <Provenance resource="wn-test" version="1.0"/>
</ConceptRelation>
"""
        data = _parse(tmp_path, body)
        issue = check_non_standard_relations(data)
        assert issue is not None
        assert issue.severity == "INFO"
        assert any("made_of_cheese" in item for item in issue.items)

    def test_flags_unknown_sense_relation(self, tmp_path):
        body = """\
<SenseRelation relation_type="rhymes_with" source="sense.a" target="sense.b">
  <Provenance resource="wn-test" version="1.0"/>
</SenseRelation>
"""
        data = _parse(tmp_path, body)
        issue = check_non_standard_relations(data)
        assert issue is not None
        assert any("rhymes_with" in item for item in issue.items)


# ---------------------------------------------------------------------------
# check_duplicate_ids
# ---------------------------------------------------------------------------

class TestCheckDuplicateIds:
    def test_no_issue_for_clean_file(self, tmp_path):
        data = _parse(tmp_path, _GOOD)
        assert check_duplicate_ids(data) == []

    def test_flags_duplicate_concept(self, tmp_path):
        body = """\
<Concept id="cili.i1" ontological_category="NOUN" status="1">
  <Provenance resource="wn-test" version="1.0"/>
</Concept>
<Concept id="cili.i1" ontological_category="NOUN" status="1">
  <Provenance resource="wn-test" version="1.0"/>
</Concept>
"""
        data = _parse(tmp_path, body)
        issues = check_duplicate_ids(data)
        assert any(i.title.startswith("Duplicate concept") for i in issues)

    def test_flags_duplicate_sense(self, tmp_path):
        body = """\
<Sense id="sense.dup" signifier="en.NOUN.x" signified="cili.i1">
  <Provenance resource="wn-test" version="1.0"/>
</Sense>
<Sense id="sense.dup" signifier="en.NOUN.x" signified="cili.i1">
  <Provenance resource="wn-test" version="1.0"/>
</Sense>
"""
        data = _parse(tmp_path, body)
        issues = check_duplicate_ids(data)
        assert any(i.title.startswith("Duplicate sense") for i in issues)


# ---------------------------------------------------------------------------
# check_glossed_concepts_without_senses
# ---------------------------------------------------------------------------

class TestCheckGlossedConceptsWithoutSenses:
    def test_no_issue_when_concept_has_sense(self, tmp_path):
        data = _parse(tmp_path, _GOOD)
        assert check_glossed_concepts_without_senses(data) is None

    def test_flags_glossed_concept_with_no_senses(self, tmp_path):
        body = """\
<Concept id="cili.i99" ontological_category="NOUN" status="1">
  <Provenance resource="wn-test" version="1.0"/>
</Concept>
<Gloss definiendum="cili.i99" language="en">
  <AnnotatedSentence>orphan concept</AnnotatedSentence>
  <Provenance resource="wn-test" version="1.0"/>
</Gloss>
"""
        data = _parse(tmp_path, body)
        issue = check_glossed_concepts_without_senses(data)
        assert issue is not None
        assert issue.severity == "INFO"
        assert "cili.i99" in issue.items

    def test_unglossed_concept_not_double_counted(self, tmp_path):
        """A concept with NO gloss should NOT appear in the 'glossed but no senses' check."""
        body = """\
<Concept id="cili.i99" ontological_category="NOUN" status="1">
  <Provenance resource="wn-test" version="1.0"/>
</Concept>
"""
        data = _parse(tmp_path, body)
        # unglossed_concepts will catch this; glossed_concepts_without_senses should not
        assert check_glossed_concepts_without_senses(data) is None


# ---------------------------------------------------------------------------
# run_checks / format_report integration
# ---------------------------------------------------------------------------

class TestRunChecks:
    def test_clean_file_returns_no_issues(self, tmp_path):
        data = _parse(tmp_path, _GOOD)
        assert run_checks(data) == []

    def test_multiple_issues_detected(self, tmp_path):
        body = """\
<Concept id="cili.i1" ontological_category="NOUN" status="1">
  <Provenance resource="wn-test" version="1.0"/>
</Concept>
<Lexeme id="en.NOUN.empty" language="en" grammatical_category="NOUN">
  <Provenance resource="wn-test" version="1.0"/>
</Lexeme>
"""
        data = _parse(tmp_path, body)
        issues = run_checks(data)
        severities = {i.severity for i in issues}
        assert "CRITICAL" in severities

    def test_format_report_plain_no_issues(self, tmp_path):
        data = _parse(tmp_path, _GOOD)
        text = format_report(tmp_path / "wn-test.xml", data, [], markdown=False)
        assert "No issues found" in text

    def test_format_report_markdown_no_issues(self, tmp_path):
        data = _parse(tmp_path, _GOOD)
        text = format_report(tmp_path / "wn-test.xml", data, [], markdown=True)
        assert text.startswith("#")
        assert "No issues found" in text

    def test_format_report_shows_items(self, tmp_path):
        body = """\
<Concept id="cili.i99" ontological_category="NOUN" status="1">
  <Provenance resource="wn-test" version="1.0"/>
</Concept>
"""
        data = _parse(tmp_path, body)
        issues = run_checks(data)
        text = format_report(tmp_path / "wn-test.xml", data, issues, markdown=False)
        assert "CRITICAL" in text
        assert "cili.i99" in text

    def test_format_report_source_hint_on_overflow(self, tmp_path):
        """When items are truncated, the 'and N more' line includes the source hint."""
        data = _parse(tmp_path, _GOOD)
        # Build an issue with 12 items (> MAX_EXAMPLES=10) and a source_hint
        issue = _mod.Issue(
            severity="INFO",
            title="Dummy",
            total=12,
            explanation="x",
            recommendation="y",
            items=[f"item{i}" for i in range(10)],
            source_hint="myfile_log.json (some.key)",
        )
        text = format_report(tmp_path / "wn-test.xml", data, [issue], markdown=False)
        assert "… and 2 more" in text
        assert "myfile_log.json (some.key)" in text

    def test_format_report_no_hint_when_empty(self, tmp_path):
        """When source_hint is empty the overflow line has no extra text."""
        data = _parse(tmp_path, _GOOD)
        issue = _mod.Issue(
            severity="INFO",
            title="Dummy",
            total=12,
            explanation="x",
            recommendation="y",
            items=[f"item{i}" for i in range(10)],
            source_hint="",
        )
        text = format_report(tmp_path / "wn-test.xml", data, [issue], markdown=False)
        assert "… and 2 more" in text
        assert "see" not in text

    def test_real_wordnet_file_parses(self):
        """Smoke-test: the existing wn-en.xml test fixture parses without errors."""
        path = Path(__file__).parent / "wordnets" / "wn-en.xml"
        data = parse_xml(path)
        issues = run_checks(data)
        # wn-en is a well-formed test file — should be issue-free
        assert all(i.severity != "CRITICAL" for i in issues)


# ---------------------------------------------------------------------------
# load_json_log
# ---------------------------------------------------------------------------

class TestLoadJsonLog:
    def test_returns_empty_dict_when_no_log(self, tmp_path):
        xml = tmp_path / "wn-test.xml"
        xml.write_text("<CygnetResource/>")
        assert load_json_log(xml) == {}

    def test_loads_json_log_when_present(self, tmp_path):
        xml = tmp_path / "wn-test.xml"
        xml.write_text("<CygnetResource/>")
        log = tmp_path / "wn-test_log.json"
        log.write_text('{"missing_cili_concepts": {"count": 5}}')
        result = load_json_log(xml)
        assert result["missing_cili_concepts"]["count"] == 5


# ---------------------------------------------------------------------------
# issues_from_json_log
# ---------------------------------------------------------------------------

class TestIssuesFromJsonLog:
    def test_empty_log_returns_no_issues(self):
        assert issues_from_json_log({}) == []

    def test_pos_mismatch_synset_concept(self):
        log = {"synset_concept_pos_mismatches": {
            "total_count": 10,
            "by_pos_pair": {"synset_NOUN-cili_VERB": 10},
        }}
        result = issues_from_json_log(log)
        assert len(result) == 1
        assert result[0].severity == "WARNING"
        assert result[0].total == 10
        assert any("synset_NOUN-cili_VERB" in item for item in result[0].items)

    def test_pos_mismatch_lexeme_concept(self):
        log = {"lexeme_concept_pos_mismatches": {
            "total_count": 3,
            "by_pos_pair": {"lexeme_VERB-concept_NOUN": 3},
        }}
        result = issues_from_json_log(log)
        assert any(i.title.startswith("POS mismatches: lexeme") for i in result)

    def test_skipped_existing_relations(self):
        log = {"relation_processing": {
            "skipped_existing_relations": {"concept_relations": {"count": 42}},
        }}
        result = issues_from_json_log(log)
        assert any(i.severity == "INFO" and i.total == 42 for i in result)

    def test_missing_cili_concepts(self):
        log = {"missing_cili_concepts": {"count": 7}}
        result = issues_from_json_log(log)
        assert any(i.severity == "INFO" and i.total == 7 for i in result)

    def test_skipped_examples_with_failed_matches(self):
        log = {
            "statistics": {
                "examples": {
                    "skipped": 2,
                    "failed_matches": [
                        {"text": "The dog barked loudly.", "candidate_wordforms": ["bark"]},
                    ],
                }
            }
        }
        result = issues_from_json_log(log)
        match = next((i for i in result if "Example" in i.title), None)
        assert match is not None
        assert match.total == 2
        assert any("looked for" in item and "bark" in item for item in match.items)

    def test_empty_candidate_wordforms_distinct_message(self):
        log = {
            "statistics": {
                "examples": {
                    "skipped": 1,
                    "failed_matches": [
                        {"text": "ai e zëvendësoi briskun.", "candidate_wordforms": []},
                    ],
                }
            }
        }
        result = issues_from_json_log(log)
        match = next((i for i in result if "Example" in i.title), None)
        assert match is not None
        assert any("no senses/wordforms found" in item for item in match.items)
        assert not any("looked for:" in item for item in match.items)

    def test_zero_counts_produce_no_issues(self):
        log = {
            "synset_concept_pos_mismatches": {"total_count": 0, "by_pos_pair": {}},
            "lexeme_concept_pos_mismatches": {"total_count": 0, "by_pos_pair": {}},
            "missing_cili_concepts": {"count": 0},
            "statistics": {"examples": {"skipped": 0}},
        }
        assert issues_from_json_log(log) == []


# ---------------------------------------------------------------------------
# parse_conflicts_json
# ---------------------------------------------------------------------------

import json as _json


class TestParseConflictsJson:
    _sample = {
        "reversed_relations": [
            {"resource_id": "wn-test", "kind": "synset",
             "src": "cili.i1", "rel": "hypernym", "tgt": "cili.i2",
             "prior_resource": "oewn"},
            {"resource_id": "other-wn", "kind": "synset",
             "src": "cili.i3", "rel": "hypernym", "tgt": "cili.i4",
             "prior_resource": "oewn"},
        ],
        "cycles": [
            {"xml_stem": "wn-test-1.0", "src": "cili.i1", "rel": "hypernym",
             "tgt": "cili.i2", "chain": ["cili.i2", "cili.i1"]},
        ],
    }

    def _write_json(self, tmp_path):
        p = tmp_path / "conflicts.json"
        p.write_text(_json.dumps(self._sample))
        return p

    def test_returns_empty_when_no_log(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CONFLICTS_JSON", tmp_path / "nonexistent.json")
        rev, cyc = parse_conflicts_json("wn-test", "wn-test-1.0")
        assert rev == [] and cyc == []

    def test_parses_reversed_relation_by_resource_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CONFLICTS_JSON", self._write_json(tmp_path))
        rev, cyc = parse_conflicts_json("wn-test", "wn-test-1.0")
        assert len(rev) == 1
        assert rev[0]["src"] == "cili.i1"

    def test_excludes_other_resource(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CONFLICTS_JSON", self._write_json(tmp_path))
        rev, _cyc = parse_conflicts_json("other-wn", "other-wn-1.0")
        assert len(rev) == 1
        assert rev[0]["src"] == "cili.i3"

    def test_parses_cycle_by_xml_stem(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CONFLICTS_JSON", self._write_json(tmp_path))
        _rev, cyc = parse_conflicts_json("wn-test", "wn-test-1.0")
        assert len(cyc) == 1
        assert cyc[0]["src"] == "cili.i1"
        assert "cili.i2" in cyc[0]["chain"]


# ---------------------------------------------------------------------------
# issues_from_conflicts_log
# ---------------------------------------------------------------------------

class TestIssuesFromConflictsLog:
    _empty_data = _mod.WordnetData()

    def test_empty_inputs_return_no_issues(self):
        assert issues_from_conflicts_log([], [], self._empty_data) == []

    def test_reversed_relations_produce_critical_issue(self):
        recs = [
            {"resource_id": "wn", "kind": "synset",
             "src": "cili.i1", "rel": "hypernym", "tgt": "cili.i2",
             "prior_resource": "oewn"},
            {"resource_id": "wn", "kind": "synset",
             "src": "cili.i3", "rel": "hypernym", "tgt": "cili.i4",
             "prior_resource": "oewn"},
        ]
        result = issues_from_conflicts_log(recs, [], self._empty_data)
        assert len(result) == 1
        issue = result[0]
        assert issue.severity == "CRITICAL"
        assert issue.total == 2
        assert any("cili.i1" in item for item in issue.items)

    def test_cycles_produce_critical_issue(self):
        recs = [
            {"xml_stem": "wn-1.0", "src": "cili.i1", "rel": "hypernym",
             "tgt": "cili.i2", "chain": ["cili.i2", "cili.i1"]},
        ]
        result = issues_from_conflicts_log([], recs, self._empty_data)
        assert len(result) == 1
        issue = result[0]
        assert issue.severity == "CRITICAL"
        assert issue.total == 1
        assert any("cili.i1" in item for item in issue.items)


# ---------------------------------------------------------------------------
# label_concept
# ---------------------------------------------------------------------------

_LABELLED_BODY = """\
<Concept id="cili.i1" ontological_category="NOUN" status="1">
  <Provenance resource="wn-id" version="1.0"/>
</Concept>
<Gloss definiendum="cili.i1" language="id">
  <AnnotatedSentence>hewan berkaki empat</AnnotatedSentence>
  <Provenance resource="wn-id" version="1.0"/>
</Gloss>
<Lexeme id="id.NOUN.anjing" language="id" grammatical_category="NOUN">
  <Wordform form="anjing"/>
  <Provenance resource="wn-id" version="1.0"/>
</Lexeme>
<Lexeme id="en.NOUN.dog" language="en" grammatical_category="NOUN">
  <Wordform form="dog"/>
  <Provenance resource="wn-id" version="1.0"/>
</Lexeme>
<Sense id="sense.anjing" signifier="id.NOUN.anjing" signified="cili.i1">
  <Provenance resource="wn-id" version="1.0"/>
</Sense>
<Sense id="sense.dog" signifier="en.NOUN.dog" signified="cili.i1">
  <Provenance resource="wn-id" version="1.0"/>
</Sense>
"""


class TestLabelConcept:
    def _parse_id(self, tmp_path):
        p = tmp_path / "wn-id.xml"
        p.write_text(wn_xml("wn-id", _LABELLED_BODY, language="id"))
        return parse_xml(p)

    def test_local_and_english_label(self, tmp_path):
        data = self._parse_id(tmp_path)
        result = label_concept("cili.i1", data)
        assert result == "cili.i1 [anjing/dog]"

    def test_english_wordnet_shows_single_word(self, tmp_path):
        """When the wordnet language is 'en', no slash is needed."""
        data = _parse(tmp_path, _GOOD)  # language="en", wordform="test"
        result = label_concept("cili.i1", data)
        assert result == "cili.i1 [test]"

    def test_unknown_concept_returns_id_unchanged(self, tmp_path):
        data = _parse(tmp_path, _GOOD)
        result = label_concept("cili.i999", data)
        assert result == "cili.i999"

    def test_concept_without_senses_returns_id_unchanged(self, tmp_path):
        body = """\
<Concept id="cili.i42" ontological_category="NOUN" status="1">
  <Provenance resource="wn-test" version="1.0"/>
</Concept>
"""
        data = _parse(tmp_path, body)
        assert label_concept("cili.i42", data) == "cili.i42"

    def test_label_used_in_unglossed_concepts_items(self, tmp_path):
        """check_unglossed_concepts items include the human-readable label."""
        p = tmp_path / "wn-id.xml"
        p.write_text(wn_xml("wn-id", _LABELLED_BODY, language="id"))
        data = parse_xml(p)
        data.glossed.clear()
        issue = check_unglossed_concepts(data)
        assert issue is not None
        assert any("anjing/dog" in item for item in issue.items)

    def test_label_used_in_cycle_items(self, tmp_path):
        """Hypernym cycle items include the human-readable label."""
        body = _LABELLED_BODY + """\
<ConceptRelation relation_type="hypernym" source="cili.i1" target="cili.i1">
  <Provenance resource="wn-id" version="1.0"/>
</ConceptRelation>
"""
        p = tmp_path / "wn-id.xml"
        p.write_text(wn_xml("wn-id", body, language="id"))
        data = parse_xml(p)
        issue = check_hypernym_cycles(data)
        assert issue is not None
        assert any("anjing/dog" in item for item in issue.items)
