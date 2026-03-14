#!/bin/bash
#
# build.sh - Download wordnets and build Cygnet
#
# Downloads wordnets listed in wordnets.toml, then runs the full Cygnet
# build pipeline.  To add or remove a language, edit wordnets.toml.
#
# Prerequisites: uv, curl, tar, xz
#
# Optional data files (place in bin/ before building):
#   bin/araasac-ili.json  ARASAAC pictogram ILI mapping (from chainnet-viz)
#
# Optional flags:
#   --with-glosstag    Add Princeton GlossTag sense annotations to definitions
#                      (requires WordNet 3.0 GlossTag corpus in bin/WordNet-3.0/)
#   --with-translate   Machine-translate non-English glosses to English
#                      (requires argostranslate; very slow)
#   --with-xml         Also generate and validate cygnet.xml and cygnet_small.xml
#                      (requires xmlstarlet; slow — 678 MB output)
#   --download-only    Download data without running the build
#   --build-only       Run the build without downloading (assumes data exists)
#
set -euo pipefail

# --- Configuration ---
CILI_DEFS_URL="https://github.com/globalwordnet/cili/releases/download/v1.0/cili.tsv.xz"
CILI_PWN_MAP_URL="https://raw.githubusercontent.com/globalwordnet/cili/master/ili-map-pwn30.tab"

# --- Parse arguments ---
WITH_GLOSSTAG=false
WITH_TRANSLATE=false
WITH_XML=false
DO_DOWNLOAD=true
DO_BUILD=true

for arg in "$@"; do
    case "$arg" in
        --with-glosstag)  WITH_GLOSSTAG=true ;;
        --with-translate) WITH_TRANSLATE=true ;;
        --with-xml)       WITH_XML=true ;;
        --download-only)  DO_BUILD=false ;;
        --build-only)     DO_DOWNLOAD=false ;;
        --help|-h)
            sed -n '3,/^$/{ s/^# \?//; p }' "$0"
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg"
            echo "Run with --help for usage."
            exit 1
            ;;
    esac
done

# --- Setup ---
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

mkdir -p bin/raw_wns bin/cygnets_presynth

# Download a wordnet archive and extract its XML files into bin/raw_wns/ (flat).
download_standalone() {
    local name="$1" url="$2"
    echo "  Downloading $name..."
    local tmpdir
    tmpdir=$(mktemp -d)
    curl -fSL -o "$tmpdir/archive" "$url"
    tar xf "$tmpdir/archive" -C "$tmpdir/"
    # Copy all wordnet XML files (plain, gzipped, or xz-compressed)
    find "$tmpdir" \( -name '*.xml' -o -name '*.xml.gz' -o -name '*.xml.xz' \) \
        -exec cp --update=none {} bin/raw_wns/ \;
    rm -rf "$tmpdir"
}

# Parse wordnets.toml and emit "stem<TAB>url" lines (no tomllib dependency needed).
get_wordnet_urls() {
    python3 << 'PYEOF'
import re, sys

content = open("wordnets.toml").read()

def stem(url):
    name = url.rstrip("/").split("/")[-1]
    for ext in [".tar.xz", ".tar.gz", ".tar.bz2", ".xz", ".gz"]:
        if name.endswith(ext):
            name = name[:-len(ext)]
            break
    if name.endswith(".xml"):
        name = name[:-4]
    return re.sub(r"-\d[\d.]*$", "", name)

for m in re.finditer(r"^\s*[\w-]+\s*=\s*(\[.*?\])", content, re.MULTILINE | re.DOTALL):
    for url in re.findall(r'"([^"]*)"', m.group(1)):
        print(stem(url) + "\t" + url)
PYEOF
}

# ============================================================
# DOWNLOADS
# ============================================================
if $DO_DOWNLOAD; then
    echo "=== Downloading data ==="

    # CILI (Collaborative Interlingual Index)
    # Script 1 expects columns: ili_id, status, superseded_by, origin, definition
    # The release TSV only has ILI+Definition, so we merge it with the PWN 3.0 mapping.
    if [ ! -f bin/cili.tsv ]; then
        echo "  Downloading CILI..."
        curl -fSL -o bin/cili_defs.tsv.xz "$CILI_DEFS_URL"
        xz -d bin/cili_defs.tsv.xz
        curl -fSL -o bin/cili_pwn_map.tab "$CILI_PWN_MAP_URL"
        python3 -c "
import csv, sys
# Load PWN 3.0 mapping (ili_id -> synset_id)
pwn = {}
with open('bin/cili_pwn_map.tab') as f:
    for line in f:
        parts = line.strip().split('\t')
        if len(parts) == 2:
            pwn[parts[0]] = parts[1]
# Read definitions and merge
with open('bin/cili_defs.tsv') as fin, open('bin/cili.tsv', 'w', newline='') as fout:
    reader = csv.DictReader(fin, delimiter='\t')
    writer = csv.writer(fout, delimiter='\t')
    writer.writerow(['ili_id', 'status', 'superseded_by', 'origin', 'definition'])
    for row in reader:
        ili = row['ILI']
        if ili in pwn:
            origin = f'pwn-3.0:{pwn[ili]}'
            writer.writerow([ili, '1', '', origin, row['Definition']])
"
        rm -f bin/cili_defs.tsv bin/cili_pwn_map.tab
    else
        echo "  CILI already present, skipping."
    fi

    # Wordnets (from wordnets.toml)
    echo "  Downloading wordnets from wordnets.toml..."
    while IFS=$'\t' read -r stem url; do
        if [[ "$url" == *.xml.gz ]] || [[ "$url" == *.xml.xz ]] || [[ "$url" == *.xml ]]; then
            # Direct XML (possibly compressed) — download straight to bin/raw_wns/
            fname="bin/raw_wns/$(basename "$url")"
            if [ ! -f "$fname" ]; then
                echo "  Downloading $(basename "$url")..."
                curl -fSL -o "$fname" "$url"
            else
                echo "  $(basename "$url") already present, skipping."
            fi
        else
            # Archive — extract and copy XMLs flat into bin/raw_wns/
            if ! compgen -G "bin/raw_wns/${stem}*.xml" > /dev/null 2>&1 && \
               ! compgen -G "bin/raw_wns/*/${stem}*/*.xml" > /dev/null 2>&1; then
                download_standalone "$stem" "$url"
            else
                echo "  $stem already present, skipping."
            fi
        fi
    done < <(get_wordnet_urls)

    echo "  Downloads complete."
    echo
fi

# ============================================================
# PYTHON ENVIRONMENT
# ============================================================
if $DO_BUILD; then
    echo "=== Setting up Python environment ==="

    # Build the extras list for uv sync
    EXTRAS=()
    if $WITH_GLOSSTAG; then EXTRAS+=(--extra glosstag); fi
    if $WITH_TRANSLATE; then EXTRAS+=(--extra translate); fi

    uv sync ${EXTRAS[@]+"${EXTRAS[@]}"}

    # Download NLTK data (wordnet lemmatizer needs this)
    uv run python -c "import nltk; nltk.download('wordnet', quiet=True); nltk.download('omw-1.4', quiet=True)"

    # Install Playwright browser binaries if not already present
    uv run playwright install chromium

    echo
fi

# ============================================================
# BUILD PIPELINE
# ============================================================
if $DO_BUILD; then

    echo "=== Step 1: Extract CILI ==="
    uv run python conversion_scripts/1_extract_cili.py
    echo

    echo "=== Step 2: Convert wordnets ==="
    uv run python conversion_scripts/2_batch_convert_lmfs.py
    echo

    if $WITH_GLOSSTAG; then
        echo "=== Step 3: Extract GlossTag ==="
        uv run python conversion_scripts/3_extract_glosstag.py
        echo

        echo "=== Step 4: Add GlossTag to CILI ==="
        uv run python conversion_scripts/4_add_glosstag_to_cili.py
        echo
    fi

    if $WITH_TRANSLATE; then
        echo "=== Step 5: Translate definitions ==="
        uv run python conversion_scripts/5_translate_defns.py
        echo
    fi

    echo "=== Step 6: Synthesise ==="
    uv run python conversion_scripts/6_synthesise.py
    echo

    echo "=== Step 9: Populate language names ==="
    uv run python conversion_scripts/9_lang_codes.py
    echo

    echo "=== Step 11: Add ARASAAC pictogram IDs ==="
    uv run python conversion_scripts/11_add_arasaac.py
    echo

    if $WITH_XML; then
        echo "=== Step 7: Generate and validate XML ==="
        uv run python conversion_scripts/7_validate_and_export.py
        echo
    fi

    echo "=== Tests ==="
    uv run pytest tests/ -v
    echo

    echo "=== Build complete! ==="
    echo "Output:"
    echo "  web/cygnet.db         - SQLite database for web interface"
    echo "  web/cygnet.db.gz      - compressed"
    echo "  web/provenance.db     - provenance database"
    echo "  web/provenance.db.gz  - compressed"
    if $WITH_XML; then
        echo "  cygnet.xml            - full merged resource (with provenance)"
        echo "  cygnet_small.xml      - without provenance metadata"
    fi
fi
