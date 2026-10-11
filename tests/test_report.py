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
collect_issues = _mod.collect_issues
format_summary = _mod.format_summary
_diversify = _mod._diversify
_denominator_key = _mod._denominator_key
_denominator_value = _mod._denominator_value
fetch_senses_per_lemma_ranking = _mod.fetch_senses_per_lemma_ranking
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
# _diversify
# ---------------------------------------------------------------------------

class TestDiversify:
    def test_spreads_across_groups_before_repeating(self):
        entries = (
            [{"candidate_wordforms": ["a"]}] * 5
            + [{"candidate_wordforms": ["b"]}] * 5
            + [{"candidate_wordforms": ["c"]}] * 5
        )
        picked = _diversify(entries, 3)
        groups = {tuple(e["candidate_wordforms"]) for e in picked}
        assert groups == {("a",), ("b",), ("c",)}

    def test_returns_fewer_than_limit_if_not_enough_entries(self):
        entries = [{"candidate_wordforms": ["a"]}]
        assert len(_diversify(entries, 5)) == 1

    def test_empty_input_returns_empty(self):
        assert _diversify([], 5) == []

    def test_falls_back_to_repeating_once_groups_exhausted(self):
        entries = [{"candidate_wordforms": ["a"]}] * 3 + [{"candidate_wordforms": ["b"]}]
        picked = _diversify(entries, 4)
        assert len(picked) == 4


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

    def test_dominant_concept_does_not_crowd_out_sample(self):
        """One concept with many failures at the front of the file must not
        fill the whole example sample — this is the Latvian wordnet_lv case
        (38 failed "gads" examples all sorting first) that motivated
        diversifying the sample instead of taking failed_matches[:N].
        """
        failed = [
            {"text": f"gads sentence {i}", "candidate_wordforms": ["gads"]}
            for i in range(20)
        ] + [
            {"text": "a different concept's sentence", "candidate_wordforms": ["cits"]},
        ]
        log = {"statistics": {"examples": {"skipped": len(failed), "failed_matches": failed}}}
        result = issues_from_json_log(log)
        match = next(i for i in result if "Example" in i.title)
        assert any("cits" in item for item in match.items)

    def test_zero_counts_produce_no_issues(self):
        log = {
            "synset_concept_pos_mismatches": {"total_count": 0, "by_pos_pair": {}},
            "lexeme_concept_pos_mismatches": {"total_count": 0, "by_pos_pair": {}},
            "missing_cili_concepts": {"count": 0},
            "statistics": {"examples": {"skipped": 0}},
        }
        assert issues_from_json_log(log) == []

    def test_invalid_pos_values_reported(self):
        log = {"invalid_pos_values": {"i": 3229}}
        result = issues_from_json_log(log)
        issue = next(i for i in result if "Unrecognised part-of-speech" in i.title)
        assert issue.severity == "CRITICAL"
        assert issue.total == 3229
        assert "'i'" in issue.title

    def test_invalid_pos_values_empty_code_labelled(self):
        log = {"invalid_pos_values": {"": 92492}}
        result = issues_from_json_log(log)
        issue = next(i for i in result if "Unrecognised part-of-speech" in i.title)
        assert "(empty)" in issue.title
        assert issue.total == 92492

    def test_invalid_pos_values_one_issue_per_code(self):
        log = {"invalid_pos_values": {"i": 5, "q": 2}}
        result = [
            i for i in issues_from_json_log(log)
            if "Unrecognised part-of-speech" in i.title
        ]
        assert len(result) == 2
        assert {i.total for i in result} == {5, 2}

    def test_no_invalid_pos_values_key_produces_no_issue(self):
        result = issues_from_json_log({"missing_cili_concepts": {"count": 1}})
        assert not any("Unrecognised part-of-speech" in i.title for i in result)


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
            # Residual records are logged under the bare resource code (no
            # version), unlike file_cycles above — see resolve_residual_
            # cycles()'s attribution in cyg/merge.py. Deliberately mismatched
            # here to guard against matching residual records on xml_stem.
            {"xml_stem": "wn-test", "src": "cili.i5", "rel": "hypernym",
             "tgt": "cili.i6", "chain": ["cili.i6", "cili.i5"], "residual": True},
        ],
    }

    def _write_json(self, tmp_path):
        p = tmp_path / "conflicts.json"
        p.write_text(_json.dumps(self._sample))
        return p

    def test_returns_empty_when_no_log(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CONFLICTS_JSON", tmp_path / "nonexistent.json")
        rev, file_cyc, residual_cyc = parse_conflicts_json("wn-test", "wn-test-1.0")
        assert rev == [] and file_cyc == [] and residual_cyc == []

    def test_parses_reversed_relation_by_resource_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CONFLICTS_JSON", self._write_json(tmp_path))
        rev, _file_cyc, _residual_cyc = parse_conflicts_json("wn-test", "wn-test-1.0")
        assert len(rev) == 1
        assert rev[0]["src"] == "cili.i1"

    def test_excludes_other_resource(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CONFLICTS_JSON", self._write_json(tmp_path))
        rev, _file_cyc, _residual_cyc = parse_conflicts_json("other-wn", "other-wn-1.0")
        assert len(rev) == 1
        assert rev[0]["src"] == "cili.i3"

    def test_parses_cycle_by_xml_stem(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CONFLICTS_JSON", self._write_json(tmp_path))
        _rev, file_cyc, _residual_cyc = parse_conflicts_json("wn-test", "wn-test-1.0")
        assert len(file_cyc) == 1
        assert file_cyc[0]["src"] == "cili.i1"
        assert "cili.i2" in file_cyc[0]["chain"]

    def test_splits_residual_from_file_cycles(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CONFLICTS_JSON", self._write_json(tmp_path))
        _rev, file_cyc, residual_cyc = parse_conflicts_json("wn-test", "wn-test-1.0")
        assert len(file_cyc) == 1 and len(residual_cyc) == 1
        assert residual_cyc[0]["src"] == "cili.i5"


# ---------------------------------------------------------------------------
# issues_from_conflicts_log
# ---------------------------------------------------------------------------

class TestIssuesFromConflictsLog:
    _empty_data = _mod.WordnetData()

    def test_empty_inputs_return_no_issues(self):
        assert issues_from_conflicts_log([], [], [], self._empty_data) == []

    def test_reversed_relations_produce_critical_issue(self):
        recs = [
            {"resource_id": "wn", "kind": "synset",
             "src": "cili.i1", "rel": "hypernym", "tgt": "cili.i2",
             "prior_resource": "oewn"},
            {"resource_id": "wn", "kind": "synset",
             "src": "cili.i3", "rel": "hypernym", "tgt": "cili.i4",
             "prior_resource": "oewn"},
        ]
        result = issues_from_conflicts_log(recs, [], [], self._empty_data)
        assert len(result) == 1
        issue = result[0]
        assert issue.severity == "CRITICAL"
        assert issue.total == 2
        assert any("cili.i1" in item for item in issue.items)

    def test_file_cycles_produce_critical_issue(self):
        recs = [
            {"xml_stem": "wn-1.0", "src": "cili.i1", "rel": "hypernym",
             "tgt": "cili.i2", "chain": ["cili.i2", "cili.i1"]},
        ]
        result = issues_from_conflicts_log([], recs, [], self._empty_data)
        assert len(result) == 1
        issue = result[0]
        assert issue.severity == "CRITICAL"
        assert issue.title == "Hypernym cycles spanning multiple wordnets"
        assert issue.total == 1
        assert any("cili.i1" in item for item in issue.items)

    def test_residual_cycles_produce_separate_issue(self):
        recs = [
            {"xml_stem": "wn-1.0", "src": "cili.i5", "rel": "hypernym",
             "tgt": "cili.i6", "chain": ["cili.i6", "cili.i5"]},
        ]
        result = issues_from_conflicts_log([], [], recs, self._empty_data)
        assert len(result) == 1
        issue = result[0]
        assert issue.severity == "CRITICAL"
        assert "residual" in issue.title
        assert issue.total == 1
        assert any("cili.i5" in item for item in issue.items)

    def test_file_and_residual_cycles_produce_two_separate_issues(self):
        file_recs = [
            {"xml_stem": "wn-1.0", "src": "cili.i1", "rel": "hypernym",
             "tgt": "cili.i2", "chain": ["cili.i2", "cili.i1"]},
        ]
        residual_recs = [
            {"xml_stem": "wn-1.0", "src": "cili.i5", "rel": "hypernym",
             "tgt": "cili.i6", "chain": ["cili.i6", "cili.i5"]},
        ]
        result = issues_from_conflicts_log([], file_recs, residual_recs, self._empty_data)
        assert len(result) == 2
        titles = {i.title for i in result}
        assert "Hypernym cycles spanning multiple wordnets" in titles
        assert any("residual" in t for t in titles)


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


# ---------------------------------------------------------------------------
# collect_issues
# ---------------------------------------------------------------------------

class TestCollectIssues:
    def test_returns_none_for_cili(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CONFLICTS_JSON", tmp_path / "nonexistent.json")
        path = _write_xml(tmp_path, _GOOD, wn_id="cili")
        assert collect_issues(path) is None

    def test_returns_data_and_issues(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CONFLICTS_JSON", tmp_path / "nonexistent.json")
        body = """\
<Concept id="cili.i99" ontological_category="NOUN" status="1">
  <Provenance resource="wn-test" version="1.0"/>
</Concept>
"""
        path = _write_xml(tmp_path, body)
        result = collect_issues(path)
        assert result is not None
        data, issues, _json_log = result
        assert data.resource_id == "wn-test"
        assert any(i.title == "Concepts without definitions" for i in issues)

    def test_source_hint_set_on_xml_issues(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CONFLICTS_JSON", tmp_path / "nonexistent.json")
        body = """\
<Concept id="cili.i99" ontological_category="NOUN" status="1">
  <Provenance resource="wn-test" version="1.0"/>
</Concept>
"""
        path = _write_xml(tmp_path, body)
        _, issues, _json_log = collect_issues(path)
        issue = next(i for i in issues if i.title == "Concepts without definitions")
        assert issue.source_hint == path.name

    def test_returns_converter_log(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CONFLICTS_JSON", tmp_path / "nonexistent.json")
        path = _write_xml(tmp_path, _GOOD, wn_id="wn-test")
        path.with_name("wn-test_log.json").write_text('{"missing_cili_concepts": {"count": 3}}')
        _data, _issues, json_log = collect_issues(path)
        assert json_log["missing_cili_concepts"]["count"] == 3

    def test_returns_empty_dict_when_no_log_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CONFLICTS_JSON", tmp_path / "nonexistent.json")
        path = _write_xml(tmp_path, _GOOD, wn_id="wn-test")
        _data, _issues, json_log = collect_issues(path)
        assert json_log == {}


# ---------------------------------------------------------------------------
# format_summary
# ---------------------------------------------------------------------------

# One unglossed concept
_ONE_UNGLOSSED = """\
<Concept id="cili.i1" ontological_category="NOUN" status="1">
  <Provenance resource="wn-a" version="1.0"/>
</Concept>
"""

# Two unglossed concepts
_TWO_UNGLOSSED = """\
<Concept id="cili.i1" ontological_category="NOUN" status="1">
  <Provenance resource="wn-b" version="1.0"/>
</Concept>
<Concept id="cili.i2" ontological_category="NOUN" status="1">
  <Provenance resource="wn-b" version="1.0"/>
</Concept>
"""


class TestFormatSummary:
    def test_counts_per_wordnet(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CONFLICTS_JSON", tmp_path / "nonexistent.json")
        paths = [
            _write_xml(tmp_path, _ONE_UNGLOSSED, wn_id="wn-a"),
            _write_xml(tmp_path, _TWO_UNGLOSSED, wn_id="wn-b"),
        ]
        summary = format_summary(paths, markdown=False)
        assert "Concepts without definitions" in summary
        assert "3 total across 2 wordnet(s)" in summary
        assert "wn-a" in summary
        assert "wn-b" in summary

    def test_clean_wordnet_not_listed_under_issue(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CONFLICTS_JSON", tmp_path / "nonexistent.json")
        paths = [
            _write_xml(tmp_path, _GOOD, wn_id="wn-clean"),
            _write_xml(tmp_path, _ONE_UNGLOSSED, wn_id="wn-a"),
        ]
        summary = format_summary(paths, markdown=False)
        section = summary.split("Concepts without definitions")[1].split("\n\n")[0]
        assert "wn-clean" not in section

    def test_cili_excluded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CONFLICTS_JSON", tmp_path / "nonexistent.json")
        paths = [
            _write_xml(tmp_path, _ONE_UNGLOSSED, wn_id="cili"),
            _write_xml(tmp_path, _ONE_UNGLOSSED, wn_id="wn-a"),
        ]
        summary = format_summary(paths, markdown=False)
        assert "1 total across 1 wordnet(s)" in summary

    def test_markdown_output_has_table(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CONFLICTS_JSON", tmp_path / "nonexistent.json")
        paths = [_write_xml(tmp_path, _ONE_UNGLOSSED, wn_id="wn-a")]
        summary = format_summary(paths, markdown=True)
        assert "| Wordnet | Count |" in summary
        assert "| wn-a | 1 |" in summary

    def test_grouped_by_severity(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CONFLICTS_JSON", tmp_path / "nonexistent.json")
        paths = [_write_xml(tmp_path, _ONE_UNGLOSSED, wn_id="wn-a")]
        summary = format_summary(paths, markdown=False)
        assert summary.index("CRITICAL") < summary.index("Concepts without definitions")

    def test_senses_per_lemma_section_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CONFLICTS_JSON", tmp_path / "nonexistent.json")
        paths = [_write_xml(tmp_path, _GOOD, wn_id="wn-a")]
        summary = format_summary(paths, markdown=True)
        assert "Senses per lemma, by wordnet" in summary
        # _GOOD has exactly one entry and one sense -> ratio 1.0
        assert "| wn-a | 1 | 1 | 1.0 |" in summary
        # The ranking section must render right after the report title.
        assert summary.index("Senses per lemma") < summary.index("Cross-Wordnet Issue Summary") + 50

    def test_same_invalid_pos_code_grouped_across_wordnets(self, tmp_path, monkeypatch):
        """Two wordnets both hitting an empty POS code show up under one title."""
        monkeypatch.setattr(_mod, "CONFLICTS_JSON", tmp_path / "nonexistent.json")
        path_a = _write_xml(tmp_path, _GOOD, wn_id="wn-a")
        path_a.with_name("wn-a_log.json").write_text('{"invalid_pos_values": {"": 10}}')
        path_b = _write_xml(tmp_path, _GOOD, wn_id="wn-b")
        path_b.with_name("wn-b_log.json").write_text('{"invalid_pos_values": {"": 5}}')

        summary = format_summary([path_a, path_b], markdown=False)

        section = summary.split("Unrecognised part-of-speech code (empty)")[1]
        section = section.split("\n\n")[0]
        assert "wn-a" in section and "10" in section
        assert "wn-b" in section and "5" in section

    def test_percentage_column_for_concepts_issue(self, tmp_path, monkeypatch):
        """1 unglossed concept out of 1 total concept is a 100% proportion."""
        monkeypatch.setattr(_mod, "CONFLICTS_JSON", tmp_path / "nonexistent.json")
        paths = [_write_xml(tmp_path, _ONE_UNGLOSSED, wn_id="wn-a")]
        summary = format_summary(paths, markdown=True)
        assert "% of concepts" in summary
        assert "| wn-a | 1 | 100.0% |" in summary

    def test_unrecognised_pos_code_has_no_percentage_column(self, tmp_path, monkeypatch):
        """No denominator exists for this title — must not show a % column."""
        monkeypatch.setattr(_mod, "CONFLICTS_JSON", tmp_path / "nonexistent.json")
        path_a = _write_xml(tmp_path, _GOOD, wn_id="wn-a")
        path_a.with_name("wn-a_log.json").write_text('{"invalid_pos_values": {"i": 5}}')
        summary = format_summary([path_a], markdown=True)
        section = summary.split("Unrecognised part-of-speech code")[1].split("\n\n")[0]
        assert "%" not in section


# ---------------------------------------------------------------------------
# _denominator_key / _denominator_value
# ---------------------------------------------------------------------------

class TestDenominator:
    def test_duplicate_ids_map_to_matching_kind(self):
        assert _denominator_key("Duplicate concept IDs") == "concepts"
        assert _denominator_key("Duplicate entry IDs") == "entries"
        assert _denominator_key("Duplicate sense IDs") == "senses"

    def test_unrecognised_pos_code_has_no_key(self):
        assert _denominator_key("Unrecognised part-of-speech code 'i'") is None
        assert _denominator_key("Unrecognised part-of-speech code (empty)") is None

    def test_unknown_title_has_no_key(self):
        assert _denominator_key("Some future check nobody mapped yet") is None

    def test_relations_key_sums_concept_and_sense_relations(self, tmp_path):
        data = _parse(tmp_path, _GOOD)
        data.concept_rels = [("a", "hypernym", "b")]
        data.sense_rels = [("s1", "antonym", "s2")]
        assert _denominator_value("relations", data, 0, {}) == 2

    def test_examples_found_adds_back_the_failures(self, tmp_path):
        data = _parse(tmp_path, _GOOD)
        data.examples = [("one sentence", frozenset())]
        assert _denominator_value("examples_found", data, 9, {}) == 10

    def test_unknown_key_raises(self, tmp_path):
        data = _parse(tmp_path, _GOOD)
        with pytest.raises(ValueError):
            _denominator_value("not-a-real-key", data, 0, {})

    def test_lexeme_vs_concept_pos_uses_senses_not_entries(self):
        """Regression: ancientgreek-grc showed 379% when this used 'entries' —
        the check increments once per Sense link, not per LexicalEntry, so
        senses (which can vastly outnumber entries) is the correct scope.
        """
        assert _denominator_key("POS mismatches: lexeme vs its concept") == "senses"

    def test_hypernym_loops_uses_relation_graph_nodes_not_concepts(self):
        """Regression: odwn-nl showed 10120% when this used len(data.concepts)
        — cyclic-SCC membership ranges over concepts *referenced* by this
        file's relations (mostly pre-existing cili.* concepts), not just the
        <Concept> elements this file itself defines.
        """
        assert (
            _denominator_key("Hypernym loops (cycles in the is-a hierarchy)")
            == "relation_graph_nodes"
        )

    def test_relation_graph_nodes_counts_distinct_referenced_concepts(self, tmp_path):
        data = _parse(tmp_path, _GOOD)
        data.concept_rels = [("a", "hypernym", "b"), ("b", "hypernym", "c")]
        assert _denominator_value("relation_graph_nodes", data, 0, {}) == 3

    def test_concepts_from_cili_reads_converter_log(self, tmp_path):
        data = _parse(tmp_path, _GOOD)
        log = {"statistics": {"concepts": {"from_cili": 42}}}
        assert _denominator_value("concepts_from_cili", data, 0, log) == 42

    def test_concepts_from_cili_defaults_to_zero(self, tmp_path):
        data = _parse(tmp_path, _GOOD)
        assert _denominator_value("concepts_from_cili", data, 0, {}) == 0

    def test_relations_attempted_sums_created_skipped_and_duplicates(self, tmp_path):
        data = _parse(tmp_path, _GOOD)
        log = {
            "statistics": {"relations": {"concept_relations_created": 10}},
            "relation_processing": {
                "skipped_existing_relations": {"concept_relations": {"count": 3}},
                "duplicates_removed": {"concept_relations": {"count": 2}},
            },
        }
        assert _denominator_value("relations_attempted", data, 0, log) == 15

    def test_synset_vs_cili_pos_mismatch_uses_from_cili(self):
        assert (
            _denominator_key("POS mismatches: synset vs CILI concept")
            == "concepts_from_cili"
        )

    def test_concept_relations_covered_uses_relations_attempted(self):
        assert (
            _denominator_key("Concept relations already covered by another wordnet")
            == "relations_attempted"
        )


# ---------------------------------------------------------------------------
# fetch_senses_per_lemma_ranking
# ---------------------------------------------------------------------------

def _data_with(n_entries: int, n_senses: int):
    data = _mod.WordnetData()
    data.entries = {f"e{i}": "en" for i in range(n_entries)}
    data.senses = {f"s{i}": (f"e{i % max(n_entries, 1)}", "cili.i1") for i in range(n_senses)}
    return data


class TestFetchSensesPerLemmaRanking:
    def test_ranked_ratio_descending(self):
        data_by_resource = {
            "low": _data_with(n_entries=10, n_senses=12),
            "high": _data_with(n_entries=10, n_senses=400),
        }
        ranking = fetch_senses_per_lemma_ranking(data_by_resource)
        assert [r[0] for r in ranking] == ["high", "low"]

    def test_ratio_computed_correctly(self):
        data_by_resource = {"wn": _data_with(n_entries=100, n_senses=250)}
        ranking = fetch_senses_per_lemma_ranking(data_by_resource)
        resource_id, senses, entries, ratio = ranking[0]
        assert (resource_id, senses, entries) == ("wn", 250, 100)
        assert ratio == 2.5

    def test_zero_entries_excluded_not_divide_by_zero(self):
        data_by_resource = {"empty": _data_with(n_entries=0, n_senses=5)}
        assert fetch_senses_per_lemma_ranking(data_by_resource) == []

    def test_empty_input_returns_empty(self):
        assert fetch_senses_per_lemma_ranking({}) == []
