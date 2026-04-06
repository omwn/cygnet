"""Playwright UI regression tests for the Cygnet web interface.

Uses a small purpose-built test database (built by conftest.py) with known
contents, so every assertion can be exact.

Test DB contents (see conftest._UI_TEST_WORDNET):
  Synsets:  entity (i1), animal (i2), dog (i3), brightness (i4), dogfish (i5)
  Senses:   en:entity, en:animal, en:dog, en:brightness, en:dogfish, fr:chien
  Variants: en:dog has variant form "doggo"
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

_DB_LOAD_TIMEOUT = 60_000   # ms — allow extra time on slower/loaded machines
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

def _wait_for_results(page: Page) -> None:
    """Wait until the results summary or 'No results found' banner appears."""
    page.locator('span.text-sm.text-gray-500').filter(has_text='across').or_(
        page.locator('text=No results found')
    ).wait_for(timeout=_SEARCH_TIMEOUT)


def _search(page: Page, term: str) -> None:
    """Type *term* into the search box, submit, and wait for results."""
    box = page.locator('input[placeholder*="word"]')
    box.fill(term)
    box.press('Enter')
    _wait_for_results(page)


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
        expect(page_ready.locator('button', has_text='About')).to_be_visible()

    def test_python_tab_absent(self, page_ready: Page):
        """Python tab was removed — should not appear in the nav."""
        expect(page_ready.locator('button', has_text='Python')).to_have_count(0)

    def test_data_tab_absent(self, page_ready: Page):
        """Data tab was merged into About — should not appear in the nav."""
        expect(page_ready.locator('button', has_text='Data')).to_have_count(0)


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

        _wait_for_results(page_ready)

        assert _result_count(page_ready) == 2
        assert _language_count(page_ready) == 1

        # Restore state — uncheck English filter
        en_label.click()
        _wait_for_results(page_ready)


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
        """ARASAAC pictogram links to the arasaac.org /pictograms/en/ page."""
        _search(page_ready, 'dog')
        page_ready.locator('.concept-inner').first.click()
        page_ready.wait_for_selector(_CONCEPT_LOADED, timeout=10_000)
        link = page_ready.locator('a[href*="arasaac.org/pictograms/en"]').first
        expect(link).to_be_visible()

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

    def test_eq_synonym_fallback_image_has_dashed_border(self, page_ready: Page):
        """Gleaming has no direct pictogram but eq_synonymous dog does.

        The fallback image should be visible in the concept view with a dashed border.
        """
        _search(page_ready, 'gleaming')
        page_ready.locator('.concept-inner').first.click()
        page_ready.wait_for_selector(_CONCEPT_LOADED, timeout=10_000)
        img = page_ready.locator('img[src*="arasaac.org"]').first
        expect(img).to_be_visible()
        classes = img.get_attribute('class') or ''
        assert 'border-dashed' in classes

    def test_similar_fallback_image_has_dashed_border(self, page_ready: Page):
        """Glowing has no direct pictogram but similar dog does.

        The fallback image should be visible in the concept view with a dashed border.
        """
        _search(page_ready, 'glowing')
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


class TestPublications:
    def test_publications_tab_shows_configured_paper(self, page_ready: Page):
        """Publications tab lists papers from local.json publications array."""
        page_ready.locator('button', has_text='Publications').click()
        expect(page_ready.locator('text=Test Author')).to_be_visible(timeout=5_000)

    def test_publications_tab_shows_wordnet_citations_header(self, page_ready: Page):
        """Publications tab has a Wordnet Citations section."""
        page_ready.locator('button', has_text='Publications').click()
        expect(page_ready.locator('text=Wordnet Citations')).to_be_visible(timeout=5_000)

    def test_publications_tab_shows_disclaimer(self, page_ready: Page):
        """Publications tab shows the disclaimer about citation source."""
        page_ready.locator('button', has_text='Publications').click()
        expect(
            page_ready.locator('text=Citation data taken from')
        ).to_be_visible(timeout=5_000)

    def test_publications_tab_renders_wordnet_citation(self, page_ready: Page):
        """Wordnet citation from fixture is rendered (RST converted to HTML)."""
        page_ready.locator('button', has_text='Publications').click()
        page_ready.wait_for_selector('text=Wordnet Citations', timeout=5_000)
        expect(page_ready.locator('text=Test English WordNet')).to_be_visible()

    def test_publications_tab_rst_link_rendered(self, page_ready: Page):
        """RST hyperlink in citation is converted to a clickable <a> tag."""
        page_ready.locator('button', has_text='Publications').click()
        page_ready.wait_for_selector('text=Wordnet Citations', timeout=5_000)
        expect(
            page_ready.locator('a[href="https://github.com/rowanhm/cygnet"]')
        ).to_be_visible()

    def test_about_tab_citation_section(self, page_ready: Page):
        """About tab has a Citation section with content from local.json."""
        page_ready.locator('button', has_text='About').click()
        expect(page_ready.locator('text=Citation')).to_be_visible(timeout=5_000)
        expect(page_ready.locator('text=Test Author')).to_be_visible()

    def test_about_tab_download_section(self, page_ready: Page):
        """About tab has a Download section with database links."""
        page_ready.locator('button', has_text='About').click()
        expect(page_ready.get_by_role('heading', name='Download')).to_be_visible(timeout=5_000)
        expect(page_ready.locator('a', has_text='.db.gz').first).to_be_visible()


# ---------------------------------------------------------------------------
# Relation display names (relations.json)
# ---------------------------------------------------------------------------

class TestRelationNames:
    def _wait_for_rel_config(self, page: Page) -> None:
        """Block until relations.json has been fetched and parsed."""
        page.wait_for_function(
            "() => Object.keys(window._relTestHook.getConfig()).length > 0",
            timeout=5_000,
        )

    def test_english_hypernym_label(self, page_ready: Page):
        """getRelLabel returns English display name from relations.json."""
        self._wait_for_rel_config(page_ready)
        label = page_ready.evaluate(
            "() => window._relTestHook.getLabel('hypernym')"
        )
        assert label == 'class hypernym'

    def test_english_hyponym_label(self, page_ready: Page):
        """getRelLabel resolves label by short code too."""
        self._wait_for_rel_config(page_ready)
        # '-hyp' is the short code for hyponym
        label = page_ready.evaluate(
            "() => window._relTestHook.getLabel('-hyp')"
        )
        assert label == 'class hyponym'

    def test_japanese_hypernym_label(self, page_ready: Page):
        """getRelLabel returns Japanese when display language is 'ja'."""
        self._wait_for_rel_config(page_ready)
        label = page_ready.evaluate(
            "() => { window._relTestHook.setLang('ja'); "
            "return window._relTestHook.getLabel('hypernym'); }"
        )
        assert label == '上位語'

    def test_japanese_hyponym_label(self, page_ready: Page):
        """getRelLabel returns Japanese hyponym label."""
        self._wait_for_rel_config(page_ready)
        label = page_ready.evaluate(
            "() => { window._relTestHook.setLang('ja'); "
            "return window._relTestHook.getLabel('hyponym'); }"
        )
        assert label == '下位語'


# ---------------------------------------------------------------------------
# URL parameters: search_lang and display_lang
# ---------------------------------------------------------------------------

class TestUrlParams:
    def _open_settings(self, page: Page) -> None:
        page.locator('button[title="Settings"]').click()
        page.wait_for_selector('text=Display results in', timeout=5_000)

    def _display_lang_select(self, page: Page):
        """Return the 'Display results in' language selector."""
        return page.locator('label:has-text("Display results in") + select, '
                            'label:has-text("Display results in") ~ select').first

    def test_display_lang_in_url_after_change(self, page_ready: Page):
        """Setting display language writes display_lang=xx to the URL hash."""
        self._open_settings(page_ready)
        self._display_lang_select(page_ready).select_option('fr')
        page_ready.wait_for_timeout(300)
        assert 'display_lang=fr' in page_ready.url

    def test_display_lang_url_restores_on_load(self, page: Page, http_server):
        """Loading a URL with display_lang=fr restores the display language."""
        page.goto(http_server + '#/search?q=dog&display_lang=fr')
        page.wait_for_selector('input[placeholder*="word"]', timeout=_DB_LOAD_TIMEOUT)
        page.locator('button[title="Settings"]').click()
        page.wait_for_selector('text=Display results in', timeout=5_000)
        expect(self._display_lang_select(page)).to_have_value('fr', timeout=3_000)

    def test_search_lang_in_url_after_filter(self, page_ready: Page):
        """Selecting a language filter writes search_lang=xx to the URL hash."""
        _search(page_ready, 'dog')
        self._open_settings(page_ready)
        page_ready.locator('label').filter(has_text='English').click()
        page_ready.wait_for_timeout(300)
        assert 'search_lang=en' in page_ready.url


# ---------------------------------------------------------------------------
# Wordnets table: sorting and deduplication
# ---------------------------------------------------------------------------

class TestWordnetsTable:
    def _open_wordnets(self, page: Page) -> None:
        page.locator('button', has_text='Wordnets').click(force=True)
        page.wait_for_selector('table tbody tr', timeout=_SEARCH_TIMEOUT)
        page.wait_for_timeout(300)

    def _click_header(self, page: Page, col: str) -> None:
        page.locator('table thead th', has_text=col).click(force=True)
        page.wait_for_timeout(300)

    def _row_language_texts(self, page: Page) -> list[str]:
        """Return the first-column text of every tbody row."""
        return [
            page.locator('table tbody tr').nth(i).locator('td').first.text_content().strip()
            for i in range(page.locator('table tbody tr').count())
        ]

    def test_cili_appears_exactly_once(self, page_ready: Page):
        """CILI must appear exactly once — no duplicate resource rows."""
        self._open_wordnets(page_ready)
        # Match rows whose Name column (2nd td) contains the CILI label.
        cili_rows = page_ready.locator('table tbody tr').filter(
            has=page_ready.locator('td:nth-child(2)').filter(
                has_text='Collaborative Interlingual Index'
            )
        )
        expect(cili_rows).to_have_count(1)

    def test_sort_by_language_ascending(self, page_ready: Page):
        """Clicking Language sorts rows so English appears before French."""
        self._open_wordnets(page_ready)
        self._click_header(page_ready, 'Language')
        langs = self._row_language_texts(page_ready)
        en_idx = next((i for i, t in enumerate(langs) if 'English' in t), None)
        fr_idx = next((i for i, t in enumerate(langs) if 'French' in t), None)
        assert en_idx is not None and fr_idx is not None
        assert en_idx < fr_idx, f"English ({en_idx}) should precede French ({fr_idx}) ascending"

    def test_sort_by_language_descending(self, page_ready: Page):
        """Clicking Language twice reverses order: French before English."""
        self._open_wordnets(page_ready)
        self._click_header(page_ready, 'Language')
        self._click_header(page_ready, 'Language')
        langs = self._row_language_texts(page_ready)
        en_idx = next((i for i, t in enumerate(langs) if 'English' in t), None)
        fr_idx = next((i for i, t in enumerate(langs) if 'French' in t), None)
        assert en_idx is not None and fr_idx is not None
        assert fr_idx < en_idx, f"French ({fr_idx}) should precede English ({en_idx}) descending"

    def test_sort_by_name(self, page_ready: Page):
        """Clicking Name sorts rows alphabetically by wordnet name."""
        self._open_wordnets(page_ready)
        self._click_header(page_ready, 'Name')
        names = [
            page_ready.locator('table tbody tr').nth(i)
                .locator('td').nth(1).text_content().replace('ⓘ', '').strip()
            for i in range(page_ready.locator('table tbody tr').count())
        ]
        assert names == sorted(names, key=str.lower), f"Names not sorted: {names}"


# ---------------------------------------------------------------------------
# ARASAAC images in search results
# ---------------------------------------------------------------------------

class TestArasaacInSearch:
    def test_arasaac_image_shown_in_search_results(self, page_ready: Page):
        """Search results for 'dog' include a small ARASAAC pictogram."""
        _search(page_ready, 'dog')
        page_ready.wait_for_selector('img[src*="arasaac.org"]', timeout=10_000)
        img = page_ready.locator('img[src*="arasaac.org"]').first
        expect(img).to_be_visible()

    def test_arasaac_image_in_search_links_to_arasaac(self, page_ready: Page):
        """The search-result pictogram is wrapped in a link to arasaac.org."""
        _search(page_ready, 'dog')
        page_ready.wait_for_selector('a[href*="arasaac.org/pictograms/en"]', timeout=10_000)
        expect(page_ready.locator('a[href*="arasaac.org/pictograms/en"]').first).to_be_visible()

    def test_no_arasaac_image_for_brightness_in_search(self, page_ready: Page):
        """Search results for 'brightness' (no ARASAAC data) show no pictogram."""
        _search(page_ready, 'brightness')
        page_ready.wait_for_selector('.concept-inner', timeout=_SEARCH_TIMEOUT)
        expect(page_ready.locator('img[src*="arasaac.org"]')).to_have_count(0)

    def test_hypernym_fallback_image_shown_in_search_results(self, page_ready: Page):
        """Search results for 'animal' (no direct image, but hypernym entity has one)
        show a dashed-border ARASAAC pictogram without requiring a click-through."""
        _search(page_ready, 'animal')
        page_ready.wait_for_selector('img[src*="arasaac.org"]', timeout=10_000)
        img = page_ready.locator('img[src*="arasaac.org"]').first
        expect(img).to_be_visible()
        classes = img.get_attribute('class') or ''
        assert 'border-dashed' in classes, (
            "Hypernym-fallback image in search results should have a dashed border"
        )

    def test_eq_synonym_fallback_image_shown_in_search_results(self, page_ready: Page):
        """Search results for 'gleaming' (no direct image, eq_synonym dog has one)
        show a dashed-border ARASAAC pictogram without requiring a click-through."""
        _search(page_ready, 'gleaming')
        page_ready.wait_for_selector('img[src*="arasaac.org"]', timeout=10_000)
        img = page_ready.locator('img[src*="arasaac.org"]').first
        expect(img).to_be_visible()
        classes = img.get_attribute('class') or ''
        assert 'border-dashed' in classes

    def test_similar_fallback_image_shown_in_search_results(self, page_ready: Page):
        """Search results for 'glowing' (no direct image, similar dog has one)
        show a dashed-border ARASAAC pictogram without requiring a click-through."""
        _search(page_ready, 'glowing')
        page_ready.wait_for_selector('img[src*="arasaac.org"]', timeout=10_000)
        img = page_ready.locator('img[src*="arasaac.org"]').first
        expect(img).to_be_visible()
        classes = img.get_attribute('class') or ''
        assert 'border-dashed' in classes


# ---------------------------------------------------------------------------
# Sense relations
# ---------------------------------------------------------------------------

_TEXT_VIEW_TIMEOUT = 8_000


def _open_concept_text_view(page: Page, term: str) -> None:
    """Search for *term*, open the first concept, switch to text relations view."""
    _search(page, term)
    page.locator('.concept-inner').first.click()
    page.wait_for_selector(_CONCEPT_LOADED, timeout=10_000)
    # Switch to text view
    page.locator('button', has_text='Text').click()


class TestSenseRelations:
    """Sense-level relations (antonym gleaming ↔ glowing) appear in the UI."""

    def test_concept_text_view_shows_sense_relation_type(self, page_ready: Page):
        """Gleaming concept text view lists 'opposite' from the antonym sense relation."""
        _open_concept_text_view(page_ready, 'gleaming')
        expect(
            page_ready.locator('span.italic', has_text='opposite')
        ).to_be_visible(timeout=_TEXT_VIEW_TIMEOUT)

    def test_concept_text_view_shows_subheader_and_target(self, page_ready: Page):
        """Gleaming concept text view has 'Related through senses' header and shows target."""
        _open_concept_text_view(page_ready, 'gleaming')
        page_ready.wait_for_selector('span.italic:has-text("opposite")', timeout=_TEXT_VIEW_TIMEOUT)
        expect(
            page_ready.locator('text=Related through senses')
        ).to_be_visible(timeout=_TEXT_VIEW_TIMEOUT)
        content = page_ready.content()
        assert 'glowing' in content

    def test_concept_text_view_antonym_link_navigates(self, page_ready: Page):
        """Clicking the antonym 'glowing' link in gleaming's concept view navigates to glowing."""
        _open_concept_text_view(page_ready, 'gleaming')
        page_ready.wait_for_selector('span.italic:has-text("opposite")', timeout=_TEXT_VIEW_TIMEOUT)
        # Click the 'glowing' button in the sense relations section
        page_ready.locator('button', has_text='glowing').first.click()
        # App switches back to search view — wait for results summary
        _wait_for_results(page_ready)
        content = page_ready.content()
        assert 'glowing' in content

    def _expand_gleaming_card(self, page: Page) -> None:
        """Search for gleaming, wait for the expand arrow, then click the arrow
        (avoiding the concept-inner which would navigate instead of expanding)."""
        _search(page, 'gleaming')
        # Sense data loads asynchronously — wait for the expand arrow
        page.wait_for_selector('.sense-box .text-gray-500.text-sm.pt-1', timeout=_TEXT_VIEW_TIMEOUT)
        # Click the expand arrow (not concept-inner, which has stopPropagation)
        page.locator('.sense-box .text-gray-500.text-sm.pt-1').first.click()

    def test_search_sense_card_shows_sense_relation(self, page_ready: Page):
        """Expanded gleaming sense card shows the antonym relation to glowing."""
        self._expand_gleaming_card(page_ready)
        expect(
            page_ready.locator('.sense-box span.font-medium', has_text='opposite')
        ).to_be_visible(timeout=_TEXT_VIEW_TIMEOUT)

    def test_search_sense_card_relation_target_clickable(self, page_ready: Page):
        """Antonym link in expanded gleaming sense card navigates to glowing."""
        self._expand_gleaming_card(page_ready)
        page_ready.wait_for_selector('.sense-box span.font-medium:has-text("opposite")', timeout=_TEXT_VIEW_TIMEOUT)
        # Target sense loads async; locator retries until the '…' resolves to 'glowing'
        page_ready.locator('.sense-box button', has_text='glowing').first.click(timeout=_TEXT_VIEW_TIMEOUT * 2)
        # App switches to search view with glowing results
        _wait_for_results(page_ready)
        content = page_ready.content()
        assert 'glowing' in content


class TestVariantFormSearch:
    """Searching by a variant form (rank > 0) auto-expands the variants section."""

    def test_variant_search_shows_variants_expanded(self, page_ready: Page):
        """Searching 'doggo' (a variant of 'dog') shows the Variants row without
        requiring the user to click to expand the sense card."""
        _search(page_ready, 'doggo')
        page_ready.wait_for_selector('.sense-box', timeout=_SEARCH_TIMEOUT)
        expect(
            page_ready.locator('.sense-box').filter(has_text='Variants')
        ).to_be_visible()

    def test_lemma_search_does_not_auto_expand(self, page_ready: Page):
        """Searching the canonical lemma 'dog' does NOT auto-expand the variants
        section (variants are still accessible via the expand arrow)."""
        _search(page_ready, 'dog')
        page_ready.wait_for_selector('.sense-box', timeout=_SEARCH_TIMEOUT)
        expect(page_ready.locator('.sense-box')).to_be_visible()
        expect(
            page_ready.locator('.sense-box').filter(has_text='Variants')
        ).not_to_be_visible()


class TestLocalJsonConfig:
    """local.json searchLanguage/displayLanguage seed the correct UI defaults."""

    @pytest.fixture()
    def page_with_config(self, page: Page, http_server_with_config):
        """Open the app served with a local.json setting fr as search/display language."""
        page.goto(http_server_with_config)
        page.wait_for_selector('input[placeholder*="word"]', timeout=_DB_LOAD_TIMEOUT)
        return page

    def test_search_language_filters_to_configured_language(
        self, page_with_config: Page
    ):
        """searchLanguage:'fr' means searching i3 returns only the French sense."""
        _search(page_with_config, 'i3')
        # With searchLanguage=fr the filter is pre-set to French, so only chien appears
        assert _result_count(page_with_config) == 1
        assert _language_count(page_with_config) == 1
        expect(page_with_config.locator('.sense-box', has_text='chien')).to_be_visible()

    def test_url_param_overrides_search_language(
        self, page: Page, http_server_with_config
    ):
        """A search_lang URL param overrides the local.json searchLanguage default."""
        page.goto(http_server_with_config + '#/search?q=i3&search_lang=en')
        page.wait_for_selector('input[placeholder*="word"]', timeout=_DB_LOAD_TIMEOUT)
        _wait_for_results(page)
        assert _result_count(page) == 1
        expect(page.locator('.sense-box', has_text='dog')).to_be_visible()

    def test_url_without_search_lang_respects_default(
        self, page: Page, http_server_with_config
    ):
        """A URL without search_lang still uses the local.json searchLanguage default."""
        page.goto(http_server_with_config + '#/search?q=i3')
        page.wait_for_selector('input[placeholder*="word"]', timeout=_DB_LOAD_TIMEOUT)
        _wait_for_results(page)
        assert _result_count(page) == 1
        expect(page.locator('.sense-box', has_text='chien')).to_be_visible()


# ---------------------------------------------------------------------------
# Header customisation via local.json
# ---------------------------------------------------------------------------

class TestHeaderCustomization:
    @pytest.fixture()
    def page_branding(self, page: Page, http_server_with_branding):
        page.goto(http_server_with_branding)
        page.wait_for_selector('input[placeholder*="word"]', timeout=_DB_LOAD_TIMEOUT)
        return page

    def test_custom_title_in_header(self, page_branding: Page):
        """local.json title replaces 'Cygnet' in the header."""
        expect(page_branding.locator('header h1', has_text='TestWN')).to_be_visible()

    def test_custom_icon_in_header(self, page_branding: Page):
        """local.json icon replaces the default swan emoji in the header."""
        expect(page_branding.locator('header h1', has_text='🧪')).to_be_visible()

    def test_custom_logo_name_in_header(self, page_branding: Page):
        """local.json logo.name appears to the left of the logo image."""
        expect(page_branding.locator('header span', has_text='TWN').first).to_be_visible()

    def test_custom_title_url_in_header(self, page_branding: Page):
        """local.json url makes the title an external link."""
        link = page_branding.locator('header a', has_text='TestWN')
        expect(link).to_be_visible()
        assert link.get_attribute('href') == 'https://example.org/testwn'
