# Cygnet Web UI — Developer Guide

## Architecture

The entire UI is a single file: **`web/index.html`**.  It uses React 18 (UMD build, no build step) via Babel standalone, Tailwind CSS CDN, D3 v7 for the concept graph, and sql.js to run SQLite in the browser.

At runtime the browser fetches two gzipped databases from the same directory as `index.html`:

| File | Contents |
|---|---|
| `cygnet.db.gz` | Main lexical data (synsets, senses, forms, relations, …) |
| `provenance.db.gz` | Per-row source attribution |
| `relations.json` | Relation display names and colours (see below) |

For local development, all three files must be in `web/` and the page must be served over HTTP (not `file://`) so that `fetch()` works.  Use `bash run.sh` which starts a simple Python HTTP server.

---

## Key sections inside `index.html`

All code lives in the single `<script type="text/babel">` block.

### Module-level constants / helpers (lines ~22–540)

| Symbol | Purpose |
|---|---|
| `DB_BASE` | Empty string on localhost; GitHub release URL otherwise |
| `_relConfig`, `_relLang` | Relation config loaded from `relations.json`; current display language |
| `RELATION_TO_SHORT` | Map DB relation name → short code, rebuilt by `_buildRelLookups()` |
| `_relColors` | Map short code → hex colour, rebuilt by `_buildRelLookups()` |
| `getRelLabel(s)` | Returns display label for a DB name or short code in `_relLang`, falling back to `en` |
| `window._relTestHook` | Playwright test hook: `setLang(l)`, `getLabel(s)`, `getConfig()` |
| `querySense(rowid)` | Runs SQL; returns sense data object |
| `queryConcept(rowid)` | Runs SQL; returns synset data including `cr` (concept relations) |
| `queryResources()` | Returns all wordnet resources for the Wordnets tab |
| `queryStats()` | Returns aggregate counts for the About tab |
| `queryProvenance(table, rowid)` | Returns provenance rows for a given item |
| `detectSearchMode(term)` | Classifies a search string as `exact`, `glob`, `ili`, or `def` |

### React component (lines ~540–end)

The whole UI is a single `App` component.  Important state:

| State | Default | Purpose |
|---|---|---|
| `dbReady` | `false` | True once sql.js + DB are initialised |
| `relConfigLoaded` | `false` | True once `relations.json` has loaded; triggers label re-render |
| `siteConfig` | `{}` | Loaded from `local.json`; controls branding, DB filenames, language defaults |
| `activeTab` | `'browser'` | Top-level tab: `browser`, `about`, `publications`, `wordnets` |
| `view` | `'search'` | Within the browser tab: `search`, `concept`, or `sense` |
| `relationsView` | `localStorage` / `'graph'` | How concept relations are displayed: `graph`, `text`, `none` |
| `selectedConcept` | `null` | Synset rowid of currently open concept |
| `selectedSense` | `null` | Sense rowid of currently open sense |
| `languageFilter` | `new Set()` | Active search language filter (BCP 47 codes); seeded from `local.json` `searchLanguage` |
| `definitionLanguage` | `localStorage` / `'en'` | Display language for definitions and synset labels; seeded from `local.json` `displayLanguage` if no localStorage value |
| `synonymsCollapsed` | `{}` | Per-sense expansion state: `false` = expanded, `true` = explicitly collapsed, absent = default |

Key `useEffect` hooks (in order):
1. **`relations.json` fetch** — runs on mount; populates `_relConfig`, calls `_buildRelLookups()`, sets `relConfigLoaded`
2. **`local.json` / site config** — runs on mount; awaits `_configPromise`, sets `siteConfig` and applies `searchLanguage`/`displayLanguage` defaults
3. **DB init** — runs on mount; awaits `_configPromise`, loads sql.js, fetches/decompresses the DB (with IndexedDB caching keyed by filename + mtime), sets up the DB
4. **Provenance DB** — loads provenance DB on demand when user clicks "Load provenance data"
5. **URL sync** — keeps `window.location.hash` in sync with current view/search/concept/filters

---

## Relation display names (`web/relations.json`)

Each entry maps a **DB relation name** to its display metadata:

```json
{
  "hypernym": { "short": "hyp", "color": "#3b82f6", "en": "class hypernym", "ja": "上位語" },
  ...
}
```

| Field | Purpose |
|---|---|
| `short` | Short code used internally (stored in `synset_relations.t`); also used for graph edge labels |
| `color` | Hex colour for graph edges and UI badges |
| `en` | English display label |
| `ja` | Japanese display label (add more ISO 639-1 codes as needed) |

**To add a new relation**: add an entry in `relations.json` matching the DB relation name exactly.

**To add a new display language**: add a field with the ISO 639-1 code to each entry that has a translation, then set `_relLang` to that code.  The `getRelLabel()` function falls back to `en` when a translation is missing.

**To change a display name without changing DB data**: edit only `relations.json` — no Python or DB changes needed.

---

## Tabs

| `activeTab` value | Label shown | Contents |
|---|---|---|
| `browser` | Browser | Search + concept/sense view |
| `about` | About | Download links, citation guidance, language data, ARASAAC info |
| `publications` | Publications | Key papers + wordnet citations from DB |
| `wordnets` | Wordnets | Per-resource table with concept/sense counts, licences, citations |

---

## ARASAAC images

The `arasaac` table in `cygnet.db` maps synset rowids to ARASAAC pictogram IDs.  The UI looks up the ILI of the current concept, then checks `arasaac` for a direct match; if none is found it walks up the hypernym chain looking for a fallback image (shown with a dashed border).

Image URL pattern: `https://static.arasaac.org/pictograms/{id}/{id}_500.png`
Attribution link: `https://arasaac.org/en/pictograms/{id}/`

---

## Testing

UI tests live in `tests/test_ui.py` and use Playwright (Chromium).  The test fixture in `tests/conftest.py`:

1. Builds a small in-memory DB from `tests/wordnets/wn-en.xml` + `wn-fr.xml`
2. Gzips both DBs
3. Copies `web/index.html`, `web/relations.json`, and the gzipped DBs into a temp directory
4. Starts a local HTTP server on port 9877 serving that directory

Run all UI tests:

```bash
uv run pytest tests/test_ui.py -v
```

**When adding a new UI feature**, add a corresponding test class in `test_ui.py`.  Use `window._relTestHook` (or similar `window.*` test hooks) to test JS logic without relying on fragile DOM text matching when the feature involves asynchronously-loaded config.

---

## Common tasks

### Add a new top-level tab

1. Add the tab ID to the array in the `{['browser', 'about', 'publications', 'wordnets'].map(tab => ...)}` render block.
2. Add a `{activeTab === 'newtab' && ...}` block in the tab content section below.

### Add a new relation type

1. Run the pipeline with the new relation present in source XMLs — it will appear in the DB automatically.
2. Add an entry to `web/relations.json` with `short`, `color`, `en` (and any language codes needed).

### Change how a DB name maps to its label

Edit `web/relations.json`.  No code changes needed.

### Add a new language's relation labels

Add the ISO 639-1 key to each relevant entry in `web/relations.json`, then set `_relLang = 'xx'` wherever language selection is handled.
