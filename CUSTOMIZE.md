# Using Cygnet for your own project

This guide walks through everything needed to deploy Cygnet's build pipeline
and web UI for your own wordnet project — from an initial `wordnets.toml` to a
live GitHub Pages site with downloadable databases.

**Working example:** [davidmoeljadi/OJW](https://github.com/davidmoeljadi/OJW)
uses exactly this workflow for the Old Javanese Wordnet.

---

## Overview

The end result is:

- `docs/index.html` — the Cygnet web UI, branded for your project
- `docs/local.json` — your site config
- `docs/relations.json`, `docs/omw-logo.svg` — supporting UI files
- Two gzipped SQLite databases attached to a GitHub release:
  - `myproject.db.gz` — main lexical database
  - `myproject-provenance.db.gz` — per-row source attribution

The databases are large — they are attached to a GitHub release rather than
committed to the repo.  The Pages deployment step (or a local build) places
them in `docs/` alongside `index.html`, where the browser loads them.

---

## Prerequisites

- [uv](https://github.com/astral-sh/uv) — Python package manager
- `curl`, `tar`, `xz`, `wget` (for archive downloads)
- `xmlstarlet` (for XML manipulation)
- `libxml2`, `libxslt` (for XML validation)

```bash
# Debian/Ubuntu
sudo apt-get install -y curl tar xz-utils wget xmlstarlet libxml2-dev libxslt-dev

# macOS
brew install curl wget xmlstarlet libxml2 libxslt
```

---

## Step 1 — Set up your project directory

Create a directory for your project alongside the cygnet checkout:

```
myproject/          ← your project
cygnet/             ← this repo (same parent)
```

Inside `myproject/`, you need at minimum:

```
myproject/
├── etc/
│   ├── wordnets.toml   ← which wordnets to include
│   └── local.json      ← UI branding and config
├── build.sh            ← builds XML → DBs → docs/
├── run.sh              ← serves docs/ locally for testing
└── docs/               ← populated by build.sh; served by GitHub Pages
```

---

## Step 2 — Configure `etc/wordnets.toml`

List every wordnet to include, keyed by BCP 47 language code.  Each entry is a
list of archive URLs in
[Global WordNet LMF](https://globalwordnet.github.io/schemas/) format.  The
first English entry must be OEWN (Open English WordNet), which provides the
base synset structure.

```toml
# etc/wordnets.toml

en  = ["https://en-word.net/static/english-wordnet-2025.xml.gz"]

# Add other languages:
id  = ["https://github.com/omwn/omw-data/releases/download/v2.0/omw-id-2.0.tar.xz"]

# Your own wordnet (pre-built XML will be placed here by build.sh):
kaw = ["https://github.com/yourorg/yourproject/releases/latest/download/yourwn.tar.xz"]
```

Archives may be `.xml.gz`, `.tar.gz`, or `.tar.xz`; cygnet's downloader handles
all three.

---

## Step 3 — Configure `etc/local.json`

All fields are optional — omit anything you don't need to override.

```json
{
  "_comment": "Cygnet UI config for My Wordnet.",

  "title":   "My Wordnet",
  "name":    "MWN",
  "tagline": "A lexical resource for Language X",
  "icon":    "📚",

  "databases": {
    "main": {
      "filename":    "mywn.db.gz",
      "url":         "https://github.com/yourorg/yourproject/releases/latest/download/mywn.db.gz",
      "description": "Main lexical database — synsets, senses, forms, definitions, examples."
    },
    "provenance": {
      "filename":    "mywn-provenance.db.gz",
      "url":         "https://github.com/yourorg/yourproject/releases/latest/download/mywn-provenance.db.gz",
      "description": "Provenance records tracing every data point back to its source."
    }
  },

  "logo": {
    "src": "mylogo.svg",
    "url": "https://github.com/yourorg/yourproject",
    "alt": "My Wordnet",
    "name": "MWN"
  },

  "header": "<strong>Preview build</strong> — data updated 2025-01-01.",

  "footer": "Built by the <a href='https://yourlab.org' style='text-decoration:underline'>Your Lab</a> team.",

  "about": {
    "intro": "<p>My Wordnet covers Language X.</p><p>Developed by Jane Doe. Source code on <a href='https://github.com/yourorg/yourproject' style='text-decoration:underline'>GitHub</a>.</p>",
    "citation": "If you use My Wordnet, please cite Doe (2025).",
    "languageData": "My Wordnet is a standalone resource covering Language X, linked to English via the Princeton WordNet synset hierarchy."
  },

  "searchLanguage": "kaw",
  "displayLanguage": "kaw",

  "publications": [
    "Jane Doe (2025). My Wordnet. In <em>Proceedings of GWC 2025</em>."
  ]
}
```

### Header layout

The header has two sides that are independently customisable:

```
[ icon  title ]                          [ logo.name  logo.src ]
  (left side)                               (right side)
```

- **Left side**: controlled by `icon`, `title`, and optionally `url`.
  By default clicking the title resets the search.  Set `url` to link
  the title to an external page instead (opens in a new tab).
- **Right side**: controlled by the `logo` object.  Set `logo.name`
  to show your project's short name to the left of your logo image,
  mirroring the layout of the left side.  Set `logo` to `null` to hide
  the right side entirely.

### Field reference

| Field | Default | Effect |
|---|---|---|
| `title` | `"Cygnet"` | Page `<title>` and header heading (left side) |
| `name` | value of `title` | Short name used in prose (e.g. "The MWN databases…") |
| `tagline` | — | Appended to `<title>` as `— tagline` |
| `icon` | `"🦢"` | Emoji shown to the left of the title in the header |
| `url` | — | URL for the title link (left side); omit to use click-to-reset-search behaviour |
| `databases.main.filename` | `"cygnet.db.gz"` | Main DB filename; fetched from the same directory as `index.html` |
| `databases.main.url` | — | Download URL for the main DB (shown in About tab) |
| `databases.main.description` | — | One-line description shown in About tab |
| `databases.provenance.filename` | `"provenance.db.gz"` | Provenance DB filename; same directory as `index.html` |
| `databases.provenance.url` | — | Download URL for the provenance DB (shown in About tab) |
| `databases.provenance.description` | — | One-line description shown in About tab |
| `logo` | OMW logo | Header logo object `{src, url, alt, name}`; `null` to hide right side |
| `logo.src` | — | Image path (relative to `index.html`) or URL |
| `logo.url` | — | Link URL; omit to render image without a hyperlink |
| `logo.alt` | `""` | Alt text for the logo image |
| `logo.name` | — | Short text shown to the left of the logo image (same bold style as `title`) |
| `header` | — | HTML banner shown below the nav bar |
| `footer` | Cygnet credit | HTML footer |
| `about.intro` | — | HTML replacing the About intro paragraphs |
| `about.citation` | — | HTML replacing the citation guidance |
| `about.languageData` | — | HTML replacing the Language Data paragraph |
| `publications` | — | HTML strings prepended to the Publications tab list |
| `searchLanguage` | — | BCP 47 code (or array of codes) to pre-select in the search language filter on load; URL params override this |
| `displayLanguage` | `"en"` | BCP 47 code for default display language (definitions and synset labels); overridden by `localStorage` if the user has previously changed it manually |

A full template is at [`notes/local.json.example`](notes/local.json.example).

---

## Step 4 — Write `build.sh`

Your `build.sh` should:

1. Build your wordnet XML (project-specific)
2. Call `cygnet/build.sh --work-dir` to run the pipeline
3. Copy the UI and databases to `docs/`

### Starting from a `.tab` file (OMW TSV format)

Many Open Multilingual Wordnet resources are distributed as `.tab` files rather
than WN-LMF XML.  To convert, use the `tsv2lmf.py` script from the
[omw-data](https://github.com/omwn/omw-data) repository and the ILI map from
[cili](https://github.com/globalwordnet/cili):

```bash
git clone https://github.com/omwn/omw-data.git external/omw-data
git clone https://github.com/globalwordnet/cili.git external/cili

pushd external/omw-data/scripts
python3 tsv2lmf.py \
    "$OLDPWD/my-wordnet.tab" \
    "$OLDPWD/build/mywn-VERSION/mywn-VERSION.xml" \
    --id='mywn' \
    --label='My Wordnet' \
    --language='xyz' \
    --version="$VERSION" \
    --email='contact@example.org' \
    --license='https://creativecommons.org/licenses/by/4.0/' \
    --url='https://github.com/yourorg/yourproject' \
    --citation="$(cat etc/citation.rst)" \
    --requires=omw-en:2.0 \
    --ili-map="$OLDPWD/external/cili/ili-map-pwn30.tab"
popd
```

The `.tab` header line must have **four** tab-separated fields (after `##`):
`label`, `language`, `url`, `license`.  If your file only has three (no URL),
prepend a fixed header before passing it to `tsv2lmf.py`:

```bash
{ printf '## My Wordnet\txyz\thttps://github.com/yourorg/yourproject\tCC BY 4.0\n'
  tail -n +2 my-wordnet.tab
} > build/my-wordnet-fixed.tab
```

### Minimal `build.sh` example (adapt to your own XML build):

```bash
#!/usr/bin/env bash
#
# build.sh — builds myproject databases and deploys the web UI to docs/.
#
# Usage: bash build.sh
# Outputs:
#   docs/            — web UI ready for GitHub Pages
#   build/mywn-*.tar.xz — packaged WordNet LMF archive (for release)

set -euo pipefail

VERSION="2026.01.01"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
CYGNET_DIR="$(cd "$PROJECT_DIR/../cygnet" && pwd)"
CYGNET_WORK="$PROJECT_DIR/build/cygnet-work"

# ── 1. Build your wordnet XML ────────────────────────────────────────────────
# (your own steps here — produce build/mywn-VERSION/mywn-VERSION.xml)

# ── 2. Run the Cygnet pipeline ───────────────────────────────────────────────
mkdir -p "$CYGNET_WORK/bin/raw_wns"
cp "$PROJECT_DIR/etc/wordnets.toml" "$CYGNET_WORK/wordnets.toml"
cp "$PROJECT_DIR/build/mywn-$VERSION/mywn-$VERSION.xml" \
   "$CYGNET_WORK/bin/raw_wns/mywn-$VERSION.xml"

bash "$CYGNET_DIR/build.sh" --work-dir "$CYGNET_WORK"

# ── 3. Deploy to docs/ ───────────────────────────────────────────────────────
mkdir -p "$PROJECT_DIR/docs"
cp "$CYGNET_DIR/web/index.html"          "$PROJECT_DIR/docs/"
cp "$CYGNET_DIR/web/relations.json"      "$PROJECT_DIR/docs/"
cp "$CYGNET_DIR/web/omw-logo.svg"        "$PROJECT_DIR/docs/" 2>/dev/null || true
cp "$PROJECT_DIR/etc/local.json"         "$PROJECT_DIR/docs/"
cp "$CYGNET_WORK/web/cygnet.db.gz"       "$PROJECT_DIR/docs/mywn.db.gz"
cp "$CYGNET_WORK/web/provenance.db.gz"   "$PROJECT_DIR/docs/mywn-provenance.db.gz"
```

The `--work-dir` flag tells cygnet's pipeline to use that directory for
intermediate files instead of cygnet's own `bin/` and `web/`.  Cygnet also
seeds the work directory's `wordnets.toml` from the provided one and skips
running the test suite (tests are cygnet-internal).

---

## Step 5 — Add `run.sh` for local testing

```bash
#!/usr/bin/env bash
# Serve docs/ locally for testing.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec bash "$SCRIPT_DIR/../cygnet/run.sh" "$SCRIPT_DIR/docs"
```

After running `bash build.sh`, start the server with:

```bash
bash run.sh
```

The terminal prints a URL like `http://localhost:8801`.  Open it in a browser —
the UI reads `docs/local.json` and loads `docs/mywn.db.gz` directly from the
local server.

---

## Step 6 — Set up `.gitignore`

The database files are large binaries that belong in a GitHub release, not in
git history.  Add them to `.gitignore`:

```
*~
.venv/
build/
external/
docs/*.db.gz
```

Commit `docs/index.html`, `docs/relations.json`, `docs/omw-logo.svg`, and
`docs/local.json` — these are small and needed for GitHub Pages to serve the
UI.

---

## Step 7 — Enable GitHub Pages

1. Go to your repository on GitHub → **Settings → Pages**.
2. Under *Source*, choose **Deploy from a branch**.
3. Select branch `main` (or `master`) and folder **`/docs`**.
4. Click **Save**.

GitHub will publish `https://<org>.github.io/<repo>/` within a minute.  The UI
will load but show an error fetching the database until you complete the next
step.

---

## Step 8 — Create a GitHub release and attach the databases

The UI reads `databases.main.filename` and `databases.provenance.filename` to
decide which database files to load (from the same directory as `index.html`).
These same entries drive the About-tab download links.

### Option A — release manually via the CLI

```bash
# Tag the release
git tag 2026.01.01
git push origin 2026.01.01

# Create the release and upload the databases
gh release create 2026.01.01 \
  --title "My Wordnet 2026.01.01" \
  --notes "Initial release." \
  docs/mywn.db.gz \
  docs/mywn-provenance.db.gz
```

If you also package your wordnet as a WordNet LMF archive (recommended, for
interoperability), attach that too:

```bash
gh release create 2026.01.01 \
  --title "My Wordnet 2026.01.01" \
  --notes "Initial release." \
  docs/mywn.db.gz \
  docs/mywn-provenance.db.gz \
  build/mywn-2026.01.01.tar.xz
```

### Option B — release via the GitHub web UI

1. Go to **Releases → Draft a new release**.
2. Create a new tag (e.g. `2026.01.01`).
3. Drag `mywn.db.gz`, `mywn-provenance.db.gz` (and optionally the `.tar.xz`)
   into the asset upload box.
4. Click **Publish release**.

### Recommended: automate with GitHub Actions

For repeatable releases, add a workflow that builds and uploads the databases
automatically.  See cygnet's own
[`.github/workflows/release.yml`](../.github/workflows/release.yml) for a
complete example — it caches the downloaded source archives so re-builds are
fast.

---

## Step 9 — Verify the live site

Open `https://<org>.github.io/<repo>/` in a browser.

- The page should show your project name and branding.
- The About tab should list your databases with the correct filenames and URLs.
- Clicking the download links should reach the release assets.
- The browser will fetch and decompress the database automatically on first
  load, then cache it in IndexedDB for subsequent visits.

If the old Cygnet databases appear instead of yours, the browser has cached an
older version.  The cache is invalidated automatically when the DB filename or
server modification time changes.  You can also force a reset via
**About → Cache → Clear cached database**.

---

## ARASAAC pictogram images

Cygnet displays [ARASAAC](https://arasaac.org) pictograms alongside synsets
when a matching image exists.  The mapping from synset ILIs to pictogram IDs
is pre-built and committed to the cygnet repo at `data/araasac-ili.json`.

When you build with `--work-dir`, cygnet automatically copies this file into
your work directory so the ARASAAC step works without any extra configuration.

**To regenerate the mapping** (e.g. after ARASAAC releases new pictograms):

```bash
cd /path/to/cygnet
uv run conversion_scripts/11_add_arasaac.py --rebuild
git add data/araasac-ili.json
git commit -m "Refresh ARASAAC ILI mapping"
```

**To patch an existing external database** without a full rebuild:

```bash
cp /path/to/cygnet/data/araasac-ili.json myproject/build/cygnet-work/data/
cd myproject/build/cygnet-work
uv run --project /path/to/cygnet \
    python3 /path/to/cygnet/conversion_scripts/11_add_arasaac.py
gzip -k -9 -f web/cygnet.db
cp web/cygnet.db.gz myproject/docs/mywn.db.gz
```

---

## Updating to a new Cygnet release

When a new version of cygnet is released:

1. Pull the latest cygnet code: `git -C ../cygnet pull`
2. Re-run `bash build.sh` in your project directory to regenerate `docs/` with
   the updated `index.html`, `relations.json`, and databases.
3. Commit the updated `docs/` static files and publish a new release with the
   new databases attached.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| UI shows `myproject.db.gz` names | Running `cygnet/run.sh` directly instead of your project's `run.sh` | Run `bash run.sh` from your project directory |
| No wordnets / empty browser tab | DB not loaded; check browser console | Confirm the DB file is in `docs/` (for local testing) or published as a release asset (for production) |
| DB filename appears but no data | `wordnets.toml` URLs unreachable during build | Check for download errors in build output |
| "Expected OEWN first" error | `wordnets.toml` lists a non-OEWN wordnet first for `en` | Put the OEWN URL first under `en` |
| Pages shows old content | GitHub Pages cache | Wait a minute; hard-reload with Ctrl+Shift+R |
