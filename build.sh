#!/bin/bash
#
# build.sh - Download wordnets and build Cygnet
#
# Downloads OEWN, OMW, OdeNet, Portuguese WN (own-pt), and KurdNet,
# then runs the full Cygnet build pipeline.
#
# Prerequisites: uv, curl, tar, xz
#
# Optional flags:
#   --with-glosstag    Add Princeton GlossTag sense annotations to definitions
#                      (requires WordNet 3.0 GlossTag corpus in bin/WordNet-3.0/)
#   --with-translate   Machine-translate non-English glosses to English
#                      (requires argostranslate; very slow)
#   --download-only    Download data without running the build
#   --build-only       Run the build without downloading (assumes data exists)
#
set -euo pipefail

# --- Configuration ---
OEWN_URL="https://en-word.net/static/english-wordnet-2025.xml.gz"
OMW_URL="https://github.com/omwn/omw-data/releases/download/v2.0/omw-2.0.tar.xz"
ODENET_URL="https://github.com/hdaSprachtechnologie/odenet/releases/download/v1.4/odenet-1.4.tar.xz"
OWNPT_URL="https://github.com/own-pt/openWordnet-PT/releases/download/v1.0.0/own-pt.tar.gz"
KURDNET_URL="https://github.com/sinaahmadi/kurdnet/releases/download/kurdnet-1.0.tar.xz/kurdnet-1.0.tar.xz"
CILI_DEFS_URL="https://github.com/globalwordnet/cili/releases/download/v1.0/cili.tsv.xz"
CILI_PWN_MAP_URL="https://raw.githubusercontent.com/globalwordnet/cili/master/ili-map-pwn30.tab"

# --- Parse arguments ---
WITH_GLOSSTAG=false
WITH_TRANSLATE=false
DO_DOWNLOAD=true
DO_BUILD=true

for arg in "$@"; do
    case "$arg" in
        --with-glosstag)  WITH_GLOSSTAG=true ;;
        --with-translate) WITH_TRANSLATE=true ;;
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

mkdir -p bin/raw_wns bin/cygnets_presynth website_data

# Download a standalone wordnet archive and extract its XML into bin/raw_wns/
download_standalone() {
    local name="$1" url="$2"
    echo "  Downloading $name..."
    local tmpdir
    tmpdir=$(mktemp -d)
    curl -fSL -o "$tmpdir/archive" "$url"
    tar xf "$tmpdir/archive" -C "$tmpdir/"
    # Copy all wordnet XML files (plain, gzipped, or xz-compressed)
    find "$tmpdir" \( -name '*.xml' -o -name '*.xml.gz' -o -name '*.xml.xz' \) \
        -exec cp -n {} bin/raw_wns/ \;
    rm -rf "$tmpdir"
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

    # OEWN (Open English WordNet)
    if ! ls bin/raw_wns/english-wordnet-*.xml.gz &>/dev/null; then
        echo "  Downloading OEWN..."
        curl -fSL -o bin/raw_wns/english-wordnet-2025.xml.gz "$OEWN_URL"
    else
        echo "  OEWN already present, skipping."
    fi

    # OMW (Open Multilingual Wordnet)
    if ! ls -d bin/raw_wns/omw-* &>/dev/null; then
        echo "  Downloading OMW..."
        tmpdir=$(mktemp -d)
        curl -fSL -o "$tmpdir/omw.tar.xz" "$OMW_URL"
        tar xf "$tmpdir/omw.tar.xz" -C bin/raw_wns/
        rm -rf "$tmpdir"
    else
        echo "  OMW already present, skipping."
    fi

    # OdeNet (German)
    if ! ls bin/raw_wns/odenet* &>/dev/null; then
        download_standalone "OdeNet" "$ODENET_URL"
    else
        echo "  OdeNet already present, skipping."
    fi

    # Portuguese WordNet (own-pt)
    if ! ls bin/raw_wns/own-pt* &>/dev/null; then
        download_standalone "Portuguese WN (own-pt)" "$OWNPT_URL"
    else
        echo "  Portuguese WN already present, skipping."
    fi

    # KurdNet (Kurdish)
    if ! ls bin/raw_wns/kurdnet* &>/dev/null; then
        download_standalone "KurdNet" "$KURDNET_URL"
    else
        echo "  KurdNet already present, skipping."
    fi

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

    uv sync "${EXTRAS[@]}"

    # Download NLTK data (wordnet lemmatizer needs this)
    uv run python -c "import nltk; nltk.download('wordnet', quiet=True); nltk.download('omw-1.4', quiet=True)"

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

    echo "=== Step 7: Validate ==="
    uv run python conversion_scripts/7_validity_checker.py
    echo

    echo "=== Step 8a: Compress ==="
    uv run python conversion_scripts/8_compress.py
    echo

    echo "=== Step 8b: Website conversion ==="
    uv run python conversion_scripts/8_website_conversion.py
    echo

    echo "=== Step 9: Language codes ==="
    uv run python conversion_scripts/9_lang_codes.py
    echo
    
    echo "=== Step 10: Make sql for web ==="
    uv run python conversion_scripts/10_sqlite_conversion.py
    echo

    echo "=== Build complete! ==="
    echo "Output:"
    echo "  cygnet.xml        - Full merged resource"
    echo "  cygnet_small.xml  - Without provenance metadata"
    echo "  web/cygnet.db     - sql db for web interface"
    echo "  web/cygnet.db.gz  - compressed"
fi
