# Cygnet

Cygnet is a merged multilingual wordnet covering 30+ languages and ~121,000
concepts.  It combines [Open English WordNet](https://en-word.net/),
[OMW Data](https://github.com/omwn/omw-data), [TUFS](https://github.com/omwn/tufs),
[OdeNet](https://github.com/hdaSprachtechnologie/odenet),
[DanNet](https://github.com/kuhumcst/DanNet), and several other wordnets into a
single SQLite database, indexed by the
[Collaborative Interlingual Index (CILI)](https://github.com/globalwordnet/cili).

| Metric | Count |
|---|---|
| Concepts (synsets) | ~180,000 |
| Lexemes (entries) | ~1,400,000 |
| Senses | ~2,100,000 |
| Languages | 40+ |

The database is browsable at  **[cygnet.maudslay.eu](http://cygnet.maudslay.eu)** or **[fcbond.github.io/cygnet](https://fcbond.github.io/cygnet)**
and queryable directly as a SQLite file.

It is made and maintained by Rowan Hall Maudslay and Francis Bond.

---

## Languages

Wordnet sources are configured in `wordnets.toml`.  Current coverage includes:
Arabic, Assamese, Basque, Bulgarian, Burmese, Cantonese, Catalan, Chinese
(Mandarin), Croatian, Danish, Dutch, Finnish, Filipino, French, Galician,
German, Greek, Hebrew, Icelandic, Indonesian, Italian, Japanese, Khmer, Korean,
Kurdish (Kurmanji), Lao, Latvian, Lithuanian, Malay, Mongolian, Norwegian
(Bokmål & Nynorsk), Polish, Portuguese, Romanian, Russian, Slovak, Slovenian,
Albanian, Spanish, Swedish, Thai, Turkish, Urdu, Vietnamese, and
[Abui](https://en.wikipedia.org/wiki/Abui_language).

To add a language, append an entry to `wordnets.toml` and re-run `build.sh`.

---

## Building

### Prerequisites

- [uv](https://github.com/astral-sh/uv) (Python package manager)
- `curl`, `tar`, `xz`
- `libxml2-dev`, `libxslt-dev` (system packages)

```bash
sudo apt-get install -y libxml2-dev libxslt-dev   # Debian/Ubuntu
brew install libxml2 libxslt                        # macOS
```

### Full build

```bash
bash build.sh
```

This downloads all source wordnet archives (~700 MB), builds the databases, and
runs the test suite.  Intermediate download files are cached in `bin/` so
re-running is fast if the sources haven't changed.

### Build options

| Flag | Effect |
|---|---|
| `--download-only` | Download sources without building |
| `--build-only` | Build from existing sources without downloading |
| `--skip-tests` | Skip the test suite |
| `--with-glosstag` | Add Princeton GlossTag sense annotations (requires WordNet 3.0 GlossTag corpus in `bin/WordNet-3.0/`) |
| `--with-translate` | Machine-translate non-English glosses to English (requires argostranslate; very slow) |
| `--with-xml` | Also generate and validate `cygnet.xml` / `cygnet_small.xml` (~678 MB) |

### Outputs

| File | Size | Description |
|---|---|---|
| `web/cygnet.db` | ~219 MB | Main SQLite database |
| `web/cygnet.db.gz` | ~89 MB | Compressed (released asset) |
| `web/provenance.db` | ~201 MB | Per-row source attribution |
| `web/provenance.db.gz` | ~70 MB | Compressed (released asset) |

### Local web UI

```bash
bash run.sh
```

Opens a local HTTP server at `http://localhost:8000` serving `web/index.html`.
The page must be served over HTTP (not `file://`) because it uses `fetch()` to
load the databases.

---

## Querying the database

The database is a plain SQLite file.  Any SQLite client can query it:

```python
import sqlite3
con = sqlite3.connect('web/cygnet.db')

# Find all English senses of "dog"
rows = con.execute('''
    SELECT f.form, s.sense_index, substr(d.definition, 1, 60) AS def
    FROM forms f
    JOIN entries e   ON f.entry_rowid  = e.rowid
    JOIN languages l ON e.language_rowid = l.rowid
    JOIN senses s    ON s.entry_rowid  = e.rowid
    JOIN synsets sy  ON s.synset_rowid = sy.rowid
    LEFT JOIN definitions d ON d.synset_rowid = sy.rowid
        AND d.language_rowid = l.rowid
    WHERE f.normalized_form = 'dog' AND l.code = 'en'
    ORDER BY s.sense_index
''').fetchall()
for form, idx, defn in rows:
    print(f'{form}#{idx}  {defn}')
```

For detailed schema documentation and more query examples see
[`conversion_scripts/SCHEMA.md`](conversion_scripts/SCHEMA.md) and
[`notes/api-demo.md`](notes/api-demo.md).

---

## Web UI

The web interface (`web/index.html`) is a single-file React app that runs
entirely in the browser using [sql.js](https://sql.js.org/) (SQLite compiled to
WebAssembly).  No server-side component is needed after the initial file loads.

**Search modes** (detected automatically):

| Input | Mode |
|---|---|
| `dog` | Exact lemma match |
| `dog*`, `*ness` | Glob pattern |
| `i46360`, `cili.i46360` | ILI lookup |
| `def:domesticated animal` | Definition search |

See [`WEB_UI.md`](WEB_UI.md) for developer documentation.

### Customising the web UI

For a full guide to deploying Cygnet for your own project — including
`wordnets.toml`, `local.json`, `build.sh`, releases, and GitHub Pages — see
**[`CUSTOMIZE.md`](CUSTOMIZE.md)**.  The
[Old Javanese Wordnet](https://github.com/davidmoeljadi/OJW) is a working
example.

To deploy `web/index.html` for a different project, create a `web/local.json`
file alongside it.  All fields are optional — omit any you don't need:

```json
{
  "title":       "My Wordnet",
  "tagline":     "A multilingual lexical resource",
  "icon":        "📚",

  "databases": {
    "main":       { "filename": "myproject.db.gz",            "url": "https://github.com/myorg/myproject/releases/latest/download/myproject.db.gz" },
    "provenance": { "filename": "myproject-provenance.db.gz", "url": "https://github.com/myorg/myproject/releases/latest/download/myproject-provenance.db.gz" }
  },

  "logo": {
    "src": "mylogo.svg",
    "url": "https://myproject.org",
    "alt": "My Project"
  },

  "header": "<strong>Preview build</strong> — data updated 2025-01-01.",
  "footer": "Built by the <a href='https://mylab.org'>My Lab</a> team.",

  "about": {
    "intro":    "<p>My Wordnet covers X languages…</p>",
    "citation": "If you use this resource, please cite Doe (2025)."
  },

  "publications": [
    "Jane Doe (2025). My Wordnet. In <em>Proceedings of GWC 2025</em>."
  ]
}
```

| Field | Effect |
|---|---|
| `title` | Header h1 and browser `<title>` |
| `tagline` | Appended to `<title>` as `— tagline` |
| `icon` | Emoji shown in the header and as the favicon |
| `databases.main` | Main DB: `filename` (loaded by the UI) and `url` + `description` (shown in About tab) |
| `databases.provenance` | Provenance DB: same fields |
| `logo` | Header logo (`{src, url, alt}`); set to `null` to hide it |
| `header` | HTML banner below the nav bar |
| `footer` | HTML replacing the footer |
| `about.intro` | HTML replacing the About tab intro paragraphs |
| `about.citation` | HTML replacing the citation guidance in About |
| `publications` | HTML strings prepended to the Publications list |

This table lists the most commonly used fields. For the full reference (including `name`, `databases`, `about.languageData`, `searchLanguage`, `displayLanguage`, and more) see [`CUSTOMIZE.md`](CUSTOMIZE.md) or the annotated template at [`notes/local.json.example`](notes/local.json.example).

---

## Tests

```bash
uv run pytest tests/ -v
```

- **Unit tests** (`tests/test_*.py`) — Python pipeline logic
- **UI tests** (`tests/test_ui.py`) — Playwright/Chromium end-to-end tests
  against a small test database built from `tests/wordnets/`

Tests run automatically on every push to `main` and on pull requests via
GitHub Actions (`.github/workflows/tests.yml`).

---

## Releases

Releases are published automatically by GitHub Actions when a tag is pushed.

### Tag format

Tags use a **date-based** scheme: `YYYY.MM.DD` (e.g. `2026.03.14`).
If more than one release is needed on the same day, append a counter:
`2026.03.14.1`.

### How to make a release

**Option A — tag locally and push:**
```bash
git tag 2026.03.14
git push origin 2026.03.14
```

**Option B — trigger manually via GitHub Actions:**
```bash
gh workflow run release.yml -f tag=2026.03.14
```

Or go to *Actions → Build and release databases → Run workflow* in the GitHub UI.

### What the release workflow does

1. Checks out the repository.
2. Restores cached wordnet source archives from GitHub's cache (keyed on
   `build.sh`); downloads any missing archives on a cache miss.
3. Builds `web/cygnet.db` and `web/provenance.db` from scratch.
4. Compresses both databases with gzip.
5. Creates a GitHub release named `Cygnet <tag>`, attaching:
   - `cygnet.db.gz` (~89 MB)
   - `provenance.db.gz` (~70 MB)

Tests are not run during a release build; they run separately via
`.github/workflows/tests.yml`.

### GitHub Pages

The web interface is automatically deployed to GitHub Pages when a release is
published. Forks that mirror the site need to trigger the Pages workflow
manually after each upstream release:

```bash
gh workflow run pages.yml
```

---

## Contributing

To add a wordnet source, append its archive URL to `wordnets.toml` under the
appropriate BCP 47 language code, then rebuild.  The source must be in
[Global WordNet LMF](https://globalwordnet.github.io/schemas/) format.

Bug reports and pull requests are welcome on
[GitHub](https://github.com/rowanhm/cygnet).
