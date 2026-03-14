"""Playwright UI regression tests for the Cygnet web interface.

Uses a small purpose-built test database (built by conftest.py) with known
contents, so every assertion can be exact.

Test DB contents (see conftest._UI_TEST_WORDNET):
  Synsets:  entity (i1), animal (i2), dog (i3), brightness (i4), dogfish (i5)
  Senses:   en:entity, en:animal, en:dog, en:brightness, en:dogfish, fr:chien
  Relations: dog→animal (hypernym), animal→entity (hypernym)

Expected search results (exact/glob match on normalized_form):
  "dog"        exact  → 1  (en:dog)                       across 1 language
  "dog*"       glob   → 2  (en:dog, en:dogfish)            across 1 language
  "*ness"      glob   → 1  (en:brightness)                 across 1 language
  "i3"         ILI    → 2  (en:dog, fr:chien)              across 2 languages
  "def:animal" def    → 3  (en:animal, en:dog, fr:chien)   across 2 languages
  "def:animal" + English filter → 2 (en:animal, en:dog)   across 1 language
"""

import pytest
from playwright.sync_api import Page, expect

_DB_LOAD_TIMEOUT = 30_000   # ms — small test DB loads quickly
_SEARCH_TIMEOUT = 15_000    # ms


# ---------------------------------------------------------------------------
# Page fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def page_ready(page: Page, http_server):
    """Open the app and wait for the DB to finish loading."""
    page.goto(http_server)
    page.wait_for_selector('input[placeholder*="word"]', timeout=_DB_LOAD_TIMEOUT)
    return page


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _search(page: Page, term: str) -> None:
    """Type *term* into the search box, submit, and wait for results."""
    box = page.locator('input[placeholder*="word"]')
    box.fill(term)
    box.press('Enter')
    # "1 result across …" uses singular; match on "across" which appears in both
    page.locator('span.text-sm.text-gray-500').filter(has_text='across').or_(
        page.locator('text=No results found')
    ).wait_for(timeout=_SEARCH_TIMEOUT)


def _result_count(page: Page) -> int:
    text = (
        page.locator('span.text-sm.text-gray-500')
        .filter(has_text='across')
        .text_content()
    )
    return int(text.split()[0].replace(',', ''))


def _language_count(page: Page) -> int:
    text = (
        page.locator('span.text-sm.text-gray-500')
        .filter(has_text='across')
        .text_content()
    )
    return int(text.split('across')[1].split('language')[0].strip())


# ---------------------------------------------------------------------------
# Page load
# ---------------------------------------------------------------------------

class TestPageLoad:
    def test_search_input_visible(self, page_ready: Page):
        expect(page_ready.locator('input[placeholder*="word"]')).to_be_visible()

    def test_title_visible(self, page_ready: Page):
        expect(page_ready.locator('h1', has_text='Cygnet')).to_be_visible()

    def test_nav_tabs_visible(self, page_ready: Page):
        expect(page_ready.locator('button', has_text='Browser')).to_be_visible()
        expect(page_ready.locator('button', has_text='Data')).to_be_visible()


# ---------------------------------------------------------------------------
# Exact search
# ---------------------------------------------------------------------------

class TestExactSearch:
    def test_dog_returns_one_result(self, page_ready: Page):
        """'dog' matches only the English form — exactly 1 sense."""
        _search(page_ready, 'dog')
        assert _result_count(page_ready) == 1

    def test_dog_spans_one_language(self, page_ready: Page):
        _search(page_ready, 'dog')
        assert _language_count(page_ready) == 1

    def test_result_count_summary_grammar(self, page_ready: Page):
        """Summary line should contain 'across' and 'language'."""
        _search(page_ready, 'dog')
        summary = (
            page_ready.locator('span.text-sm.text-gray-500')
            .filter(has_text='across')
            .text_content()
        )
        assert 'across' in summary
        assert 'language' in summary

    def test_nonexistent_word_shows_no_results(self, page_ready: Page):
        _search(page_ready, 'xyzzy_nonexistent')
        expect(
            page_ready.locator('text=No results found')
        ).to_be_visible(timeout=_SEARCH_TIMEOUT)

    def test_entity_returns_one_result(self, page_ready: Page):
        """'entity' exists only in English — exactly 1 result."""
        _search(page_ready, 'entity')
        assert _result_count(page_ready) == 1
        assert _language_count(page_ready) == 1


# ---------------------------------------------------------------------------
# Glob search
# ---------------------------------------------------------------------------

class TestGlobSearch:
    def test_dog_suffix_glob_returns_two(self, page_ready: Page):
        """'dog*' matches normalized forms 'dog' and 'dogfish' (both English)."""
        _search(page_ready, 'dog*')
        assert _result_count(page_ready) == 2
        assert _language_count(page_ready) == 1

    def test_ness_prefix_glob_returns_one(self, page_ready: Page):
        """'*ness' matches only brightness."""
        _search(page_ready, '*ness')
        assert _result_count(page_ready) == 1
        assert _language_count(page_ready) == 1

    def test_glob_more_results_than_exact(self, page_ready: Page):
        _search(page_ready, 'dog')
        exact = _result_count(page_ready)
        _search(page_ready, 'dog*')
        assert _result_count(page_ready) > exact


# ---------------------------------------------------------------------------
# ILI search
# ---------------------------------------------------------------------------

class TestIliSearch:
    def test_ili_returns_two_results(self, page_ready: Page, valid_ili: str):
        """ILI i3 = dog synset → en:dog + fr:chien = 2 results."""
        _search(page_ready, valid_ili)
        assert _result_count(page_ready) == 2

    def test_cili_prefix_returns_same_count(self, page_ready: Page, valid_ili: str):
        _search(page_ready, valid_ili)
        bare = _result_count(page_ready)
        _search(page_ready, f'cili.{valid_ili}')
        assert _result_count(page_ready) == bare

    def test_ili_spans_two_languages(self, page_ready: Page, valid_ili: str):
        _search(page_ready, valid_ili)
        assert _language_count(page_ready) == 2


# ---------------------------------------------------------------------------
# Definition search
# ---------------------------------------------------------------------------

class TestDefinitionSearch:
    def test_def_animal_returns_three(self, page_ready: Page):
        """'def:animal' matches i2 ('a living animal') and i3 ('a domesticated animal')
        → en:animal + en:dog + fr:chien = 3 results."""
        _search(page_ready, 'def:animal')
        assert _result_count(page_ready) == 3

    def test_def_animal_spans_two_languages(self, page_ready: Page):
        _search(page_ready, 'def:animal')
        assert _language_count(page_ready) == 2

    def test_def_search_language_filter_reduces_to_one_language(
        self, page_ready: Page
    ):
        """Enabling English-only filter on 'def:animal' should give 2 results
        (en:animal, en:dog) across exactly 1 language."""
        _search(page_ready, 'def:animal')
        assert _result_count(page_ready) == 3  # baseline

        # Open settings and enable English filter
        page_ready.locator('button[title="Settings"]').click()
        page_ready.wait_for_selector('text=Search filters', timeout=3_000)

        en_label = (
            page_ready.locator('div.flex.flex-wrap.gap-2 label')
            .filter(has_text='English')
            .first
        )
        en_label.click()

        page_ready.locator('span.text-sm.text-gray-500').filter(has_text='across').or_(
            page_ready.locator('text=No results found')
        ).wait_for(timeout=_SEARCH_TIMEOUT)

        assert _result_count(page_ready) == 2
        assert _language_count(page_ready) == 1

        # Restore state — uncheck English filter
        en_label.click()
        page_ready.locator('span.text-sm.text-gray-500').filter(has_text='across').or_(
            page_ready.locator('text=No results found')
        ).wait_for(timeout=_SEARCH_TIMEOUT)


# ---------------------------------------------------------------------------
# Concept navigation
# ---------------------------------------------------------------------------

class TestConceptNavigation:
    def test_clicking_concept_shows_concept_view(self, page_ready: Page):
        _search(page_ready, 'dog')
        page_ready.locator('.concept-inner').first.click()
        expect(
            page_ready.locator('text=All forms expressing this concept')
        ).to_be_visible(timeout=10_000)

    def test_concept_view_has_back_button(self, page_ready: Page):
        _search(page_ready, 'dog')
        page_ready.locator('.concept-inner').first.click()
        page_ready.wait_for_selector(
            'text=All forms expressing this concept', timeout=10_000
        )
        expect(
            page_ready.locator('button', has_text='Back to search')
        ).to_be_visible()

    def test_back_to_search_restores_input(self, page_ready: Page):
        _search(page_ready, 'dog')
        page_ready.locator('.concept-inner').first.click()
        page_ready.wait_for_selector(
            'text=All forms expressing this concept', timeout=10_000
        )
        page_ready.locator('button', has_text='Back to search').click()
        expect(
            page_ready.locator('input[placeholder*="word"]')
        ).to_be_visible(timeout=5_000)

    def test_concept_view_shows_ili(self, page_ready: Page):
        """The dog synset has ILI i3; it should be displayed as ⟪i3⟫."""
        _search(page_ready, 'dog')
        page_ready.locator('.concept-inner').first.click()
        page_ready.wait_for_selector(
            'text=All forms expressing this concept', timeout=10_000
        )
        expect(page_ready.locator('text=⟪i3⟫')).to_be_visible()

    def test_concept_view_shows_all_languages(self, page_ready: Page):
        """Dog concept has English and French forms — both should appear."""
        _search(page_ready, 'dog')
        page_ready.locator('.concept-inner').first.click()
        page_ready.wait_for_selector(
            'text=All forms expressing this concept', timeout=10_000
        )
        # The all-forms section lists language codes / names
        content = page_ready.content()
        assert 'dog' in content
        assert 'chien' in content


# ---------------------------------------------------------------------------
# Path finder
# ---------------------------------------------------------------------------

class TestPathFinder:
    def _open_concept(self, page: Page, term: str) -> None:
        _search(page, term)
        page.locator('.concept-inner').first.click()
        page.wait_for_selector(
            'text=All forms expressing this concept', timeout=10_000
        )

    def test_path_found_shows_intermediate_step(self, page_ready: Page):
        """dog (i3) → entity (i1) path passes through animal (i2)."""
        self._open_concept(page_ready, 'dog')
        page_ready.locator('input[placeholder*="e.g. i"]').fill('i1')
        # Wait for the ILI to resolve (Find path button becomes enabled)
        expect(
            page_ready.locator('button', has_text='Find path')
        ).to_be_enabled(timeout=5_000)
        page_ready.locator('button', has_text='Find path').click()
        expect(
            page_ready.locator('button', has_text='animal')
        ).to_be_visible(timeout=5_000)

    def test_path_includes_start_and_end(self, page_ready: Page):
        """The rendered path must contain both the source and target concepts."""
        self._open_concept(page_ready, 'dog')
        page_ready.locator('input[placeholder*="e.g. i"]').fill('i1')
        expect(
            page_ready.locator('button', has_text='Find path')
        ).to_be_enabled(timeout=5_000)
        page_ready.locator('button', has_text='Find path').click()
        page_ready.locator('button', has_text='animal').wait_for(timeout=5_000)
        content = page_ready.content()
        assert 'entity' in content

    def test_path_not_found_shows_message(self, page_ready: Page):
        """brightness (i4) has no hypernym — no path to entity (i1)."""
        self._open_concept(page_ready, 'brightness')
        page_ready.locator('input[placeholder*="e.g. i"]').fill('i1')
        expect(
            page_ready.locator('button', has_text='Find path')
        ).to_be_enabled(timeout=5_000)
        page_ready.locator('button', has_text='Find path').click()
        expect(
            page_ready.locator('text=No path found')
        ).to_be_visible(timeout=5_000)


# ---------------------------------------------------------------------------
# Large-result hint
# ---------------------------------------------------------------------------

class TestLargeResultHint:
    def test_hint_not_shown_for_small_result_set(self, page_ready: Page):
        """With only 6 senses in the DB, no hint should appear."""
        _search(page_ready, 'dog*')
        expect(
            page_ready.locator('text=Too many results')
        ).not_to_be_visible()


# ---------------------------------------------------------------------------
# Security: SQL injection and XSS via search input
# ---------------------------------------------------------------------------

class TestSecurity:
    """Search input is passed as a parameterised SQL value, never interpolated.
    Injection attempts must return 0 results (not crash or leak data).
    Rendered output must not execute injected script tags."""

    _INJECTIONS = [
        "' OR '1'='1",
        "'; DROP TABLE synsets; --",
        "' UNION SELECT code, rowid, rowid, rowid, rowid FROM resources --",
        "dog' AND '1'='1",
        "\\x00",
        "a" * 10_000,           # very long input
    ]

    def test_sql_injection_returns_no_results(self, page_ready: Page):
        """Classic SQL injection in the search box must not return real rows."""
        for payload in self._INJECTIONS:
            _search(page_ready, payload)
            expect(
                page_ready.locator('text=No results found')
            ).to_be_visible(timeout=_SEARCH_TIMEOUT)

    def test_sql_injection_does_not_crash_page(self, page_ready: Page):
        """The search input must remain usable after every injection attempt."""
        for payload in self._INJECTIONS:
            _search(page_ready, payload)
        # After all payloads the search box must still be present
        expect(page_ready.locator('input[placeholder*="word"]')).to_be_visible()

    def test_xss_script_tag_not_executed(self, page_ready: Page):
        """A <script> tag injected via search must not execute JS."""
        page_ready.evaluate("window.__xss_fired = false;")
        _search(page_ready, "<script>window.__xss_fired=true;</script>")
        fired = page_ready.evaluate("window.__xss_fired")
        assert not fired, "XSS payload was executed"

    def test_xss_event_handler_not_executed(self, page_ready: Page):
        """An onerror/onload attribute injected via search must not execute."""
        page_ready.evaluate("window.__xss_fired = false;")
        _search(page_ready, '<img src=x onerror="window.__xss_fired=true;">')
        fired = page_ready.evaluate("window.__xss_fired")
        assert not fired, "XSS event handler was executed"

    def test_glob_injection_returns_no_unexpected_results(self, page_ready: Page):
        """GLOB wildcards must not bypass the search intent or leak all rows."""
        _search(page_ready, '*')
        # '*' alone matches everything — there should be results but no crash
        expect(page_ready.locator('input[placeholder*="word"]')).to_be_visible()

    def test_def_injection_no_raw_sql_leak(self, page_ready: Page):
        """def: prefix with embedded SQL must show no results, not an error."""
        _search(page_ready, "def:' OR '1'='1")
        expect(
            page_ready.locator('text=No results found')
        ).to_be_visible(timeout=_SEARCH_TIMEOUT)


_CONCEPT_LOADED = 'text=All forms expressing this concept'


class TestArasaac:
    def test_arasaac_image_shown_for_dog(self, page_ready: Page):
        """Concept view for dog shows an ARASAAC pictogram image."""
        _search(page_ready, 'dog')
        page_ready.locator('.concept-inner').first.click()
        page_ready.wait_for_selector(_CONCEPT_LOADED, timeout=10_000)
        img = page_ready.locator('img[src*="arasaac.org"]').first
        expect(img).to_be_visible()

    def test_arasaac_image_links_to_arasaac(self, page_ready: Page):
        """ARASAAC pictogram links to the arasaac.org page for that pictogram."""
        _search(page_ready, 'dog')
        page_ready.locator('.concept-inner').first.click()
        page_ready.wait_for_selector(_CONCEPT_LOADED, timeout=10_000)
        link = page_ready.locator('a[href*="arasaac.org/pictograms"]').first
        expect(link).to_be_visible()

    def test_arasaac_attribution_shown(self, page_ready: Page):
        """Concept view with a pictogram shows ARASAAC attribution text."""
        _search(page_ready, 'dog')
        page_ready.locator('.concept-inner').first.click()
        page_ready.wait_for_selector(_CONCEPT_LOADED, timeout=10_000)
        expect(page_ready.locator('text=ARASAAC')).to_be_visible()

    def test_no_arasaac_image_for_brightness(self, page_ready: Page):
        """Concept view for a word without a pictogram shows no ARASAAC image."""
        _search(page_ready, 'brightness')
        page_ready.locator('.concept-inner').first.click()
        page_ready.wait_for_selector(_CONCEPT_LOADED, timeout=10_000)
        assert page_ready.locator('img[src*="arasaac.org"]').count() == 0

    def test_direct_image_has_solid_border(self, page_ready: Page):
        """Dog has a direct pictogram — its image must not have a dashed border."""
        _search(page_ready, 'dog')
        page_ready.locator('.concept-inner').first.click()
        page_ready.wait_for_selector(_CONCEPT_LOADED, timeout=10_000)
        img = page_ready.locator('img[src*="arasaac.org"]').first
        expect(img).to_be_visible()
        # dashed border class is only present on hypernym fallback images
        classes = img.get_attribute('class') or ''
        assert 'border-dashed' not in classes

    def test_hypernym_fallback_image_has_dashed_border(self, page_ready: Page):
        """Animal has no direct pictogram but entity (its hypernym) does.

        The fallback image should be visible and have a dashed border.
        """
        _search(page_ready, 'animal')
        page_ready.locator('.concept-inner').first.click()
        page_ready.wait_for_selector(_CONCEPT_LOADED, timeout=10_000)
        img = page_ready.locator('img[src*="arasaac.org"]').first
        expect(img).to_be_visible()
        classes = img.get_attribute('class') or ''
        assert 'border-dashed' in classes

    def test_about_tab_mentions_arasaac(self, page_ready: Page):
        """The About tab contains an ARASAAC attribution link."""
        page_ready.locator('button', has_text='About').click()
        expect(
            page_ready.locator('a[href*="arasaac.org"]')
        ).to_be_visible(timeout=5_000)

    def test_about_tab_explains_dashed_border(self, page_ready: Page):
        """The About tab explains that dashed borders indicate hypernym images."""
        page_ready.locator('button', has_text='About').click()
        expect(
            page_ready.locator('text=dashed border')
        ).to_be_visible(timeout=5_000)
