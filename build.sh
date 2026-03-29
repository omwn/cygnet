#!/usr/bin/env bash
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
#   --work-dir DIR     Use DIR as the data/output root instead of this
#                      project directory. bin/, web/ etc. are created there.
#                      wordnets.toml is read from DIR if present, otherwise
#                      copied from the project directory. Tests are skipped
#                      automatically when --work-dir is given.
#   --with-glosstag    Add Princeton GlossTag sense annotations to definitions
#                      (requires WordNet 3.0 GlossTag corpus in bin/WordNet-3.0/)
#   --with-translate   Machine-translate non-English glosses to English
#                      (requires argostranslate; very slow)
#   --with-xml         Also generate and validate cygnet.xml and cygnet_small.xml
#                      (requires xmlstarlet; slow — 678 MB output)
#   --download-only    Download data without running the build
#   --build-only       Run the build without downloading (assumes data exists)
#   --skip-tests       Skip the test suite
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
DO_TESTS=true
WORK_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --work-dir)
            if [[ $# -lt 2 || -z "${2:-}" ]]; then
                echo "Error: --work-dir requires a directory argument." >&2
                echo "Run with --help for usage." >&2
                exit 1
            fi
            WORK_DIR="$2"
            shift 2
            ;;
        --with-glosstag)  WITH_GLOSSTAG=true;  shift ;;
        --with-translate) WITH_TRANSLATE=true; shift ;;
        --with-xml)       WITH_XML=true;        shift ;;
        --download-only)  DO_BUILD=false;       shift ;;
        --build-only)     DO_DOWNLOAD=false;    shift ;;
        --skip-tests)     DO_TESTS=false;       shift ;;
        --help|-h)
            sed -n '3,/^$/{ s/^# \?//; p }' "$0"
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            echo "Run with --help for usage." >&2
            exit 1
            ;;
    esac
done

# --- Setup ---
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

if [[ -n "$WORK_DIR" ]]; then
    mkdir -p "$WORK_DIR"
    DATA_DIR="$(cd "$WORK_DIR" && pwd)"
    # Tests test the cygnet database, not an external work dir.
    DO_TESTS=false
    # Use the caller's wordnets.toml if present; otherwise seed from project.
    if [[ ! -f "$DATA_DIR/wordnets.toml" ]]; then
        cp "$PROJECT_DIR/wordnets.toml" "$DATA_DIR/wordnets.toml"
    fi
else
    DATA_DIR="$PROJECT_DIR"
fi

mkdir -p "$DATA_DIR/bin/raw_wns" "$DATA_DIR/bin/cygnets_presynth" "$DATA_DIR/web"

# Run a conversion script from DATA_DIR using cygnet's Python environment.
run_pipeline() {
    (cd "$DATA_DIR" && uv run --project "$PROJECT_DIR" \
        python "$PROJECT_DIR/conversion_scripts/$1" "${@:2}")
}

# Download a wordnet archive and extract its XML files into bin/raw_wns/ (flat).
download_standalone() {
    local name="$1" url="$2"
    echo "  Downloading $name..."
    (
        local tmpdir
        tmpdir=$(mktemp -d)
        trap 'rm -rf "$tmpdir"' EXIT
        curl -fSL -o "$tmpdir/archive" "$url"
        tar xf "$tmpdir/archive" -C "$tmpdir/"
        find "$tmpdir" \( -name '*.xml' -o -name '*.xml.gz' -o -name '*.xml.xz' \) \
            -exec cp -n {} "$DATA_DIR/bin/raw_wns/" \;
    )
}

# Parse wordnets.toml and emit "stem<TAB>url" lines (no tomllib dependency needed).
get_wordnet_urls() {
    python3 - "$DATA_DIR/wordnets.toml" << 'PYEOF'
import re, sys

content = open(sys.argv[1]).read()

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
    if [ ! -f "$DATA_DIR/bin/cili.tsv" ]; then
        echo "  Downloading CILI..."
        curl -fSL -o "$DATA_DIR/bin/cili_defs.tsv.xz" "$CILI_DEFS_URL"
        xz -d "$DATA_DIR/bin/cili_defs.tsv.xz"
        curl -fSL -o "$DATA_DIR/bin/cili_pwn_map.tab" "$CILI_PWN_MAP_URL"
        python3 -c "
import csv, sys
d = sys.argv[1]
pwn = {}
with open(f'{d}/cili_pwn_map.tab') as f:
    for line in f:
        parts = line.strip().split('\t')
        if len(parts) == 2:
            pwn[parts[0]] = parts[1]
with open(f'{d}/cili_defs.tsv') as fin, open(f'{d}/cili.tsv', 'w', newline='') as fout:
    reader = csv.DictReader(fin, delimiter='\t')
    writer = csv.writer(fout, delimiter='\t')
    writer.writerow(['ili_id', 'status', 'superseded_by', 'origin', 'definition'])
    for row in reader:
        ili = row['ILI']
        if ili in pwn:
            writer.writerow([ili, '1', '', f'pwn-3.0:{pwn[ili]}', row['Definition']])
" "$DATA_DIR/bin"
        rm -f "$DATA_DIR/bin/cili_defs.tsv" "$DATA_DIR/bin/cili_pwn_map.tab"
    else
        echo "  CILI already present, skipping."
    fi

    # Wordnets (from wordnets.toml)
    echo "  Downloading wordnets from wordnets.toml..."
    while IFS=$'\t' read -r stem url; do
        if [[ "$url" == *.xml.gz ]] || [[ "$url" == *.xml.xz ]] || [[ "$url" == *.xml ]]; then
            # Direct XML (possibly compressed) — download straight to bin/raw_wns/
            fname="$DATA_DIR/bin/raw_wns/$(basename "$url")"
            if [ ! -f "$fname" ]; then
                echo "  Downloading $(basename "$url")..."
                curl -fSL -o "$fname" "$url"
            else
                echo "  $(basename "$url") already present, skipping."
            fi
        else
            # Archive — extract and copy XMLs flat into bin/raw_wns/
            if ! compgen -G "$DATA_DIR/bin/raw_wns/${stem}*.xml" > /dev/null 2>&1 && \
               ! compgen -G "$DATA_DIR/bin/raw_wns/*/${stem}*/*.xml" > /dev/null 2>&1; then
                download_standalone "$stem" "$url"
            else
                echo "  $stem already present, skipping."
            fi
        fi
    done < <(get_wordnet_urls)

    # ODEnet archive uses 'deWordNet.xml' internally; rename to match stem.
    [[ -f "$DATA_DIR/bin/raw_wns/deWordNet.xml" ]] && \
        [[ ! -f "$DATA_DIR/bin/raw_wns/odenet.xml" ]] && \
        mv "$DATA_DIR/bin/raw_wns/deWordNet.xml" "$DATA_DIR/bin/raw_wns/odenet.xml" || true

    echo "  Downloads complete."
    echo
fi

# ============================================================
# PYTHON ENVIRONMENT
# ============================================================
if $DO_BUILD; then
    echo "=== Setting up Python environment ==="

    EXTRAS=()
    if $WITH_GLOSSTAG; then EXTRAS+=(--extra glosstag); fi
    if $WITH_TRANSLATE; then EXTRAS+=(--extra translate); fi

    uv sync ${EXTRAS[@]+"${EXTRAS[@]}"}

    uv run python -c "import nltk; nltk.download('wordnet', quiet=True); nltk.download('omw-1.4', quiet=True)"
    uv run playwright install chromium

    echo
fi

# ============================================================
# BUILD PIPELINE
# ============================================================
if $DO_BUILD; then

    echo "=== Step 1: Extract CILI ==="
    run_pipeline 1_extract_cili.py
    echo

    echo "=== Step 2: Convert wordnets ==="
    run_pipeline 2_batch_convert_lmfs.py
    echo

    if $WITH_GLOSSTAG; then
        echo "=== Step 3: Extract GlossTag ==="
        run_pipeline 3_extract_glosstag.py
        echo

        echo "=== Step 4: Add GlossTag to CILI ==="
        run_pipeline 4_add_glosstag_to_cili.py
        echo
    fi

    if $WITH_TRANSLATE; then
        echo "=== Step 5: Translate definitions ==="
        run_pipeline 5_translate_defns.py
        echo
    fi

    echo "=== Step 6: Synthesise ==="
    run_pipeline 6_synthesise.py
    echo

    echo "=== Step 9: Populate language names ==="
    run_pipeline 9_lang_codes.py
    echo

    echo "=== Step 11: Add ARASAAC pictogram IDs ==="
    run_pipeline 11_add_arasaac.py
    echo

    if $WITH_XML; then
        echo "=== Step 7: Generate and validate XML ==="
        run_pipeline 7_validate_and_export.py
        echo
    fi

    if $DO_TESTS; then
        echo "=== Tests ==="
        uv run pytest tests/ -v
        echo
    fi

    echo "=== Step 12: Compress databases ==="
    gzip -k -9 -f "$DATA_DIR/web/cygnet.db"
    gzip -k -9 -f "$DATA_DIR/web/provenance.db"
    echo

    echo "=== Build complete! ==="
    echo "Output:"
    echo "  $DATA_DIR/web/cygnet.db         - SQLite database for web interface"
    echo "  $DATA_DIR/web/cygnet.db.gz      - compressed"
    echo "  $DATA_DIR/web/provenance.db     - provenance database"
    echo "  $DATA_DIR/web/provenance.db.gz  - compressed"
    if $WITH_XML; then
        echo "  $DATA_DIR/cygnet.xml            - full merged resource (with provenance)"
        echo "  $DATA_DIR/cygnet_small.xml      - without provenance metadata"
    fi
    echo
    if [[ "$DATA_DIR" == "$PROJECT_DIR" ]]; then
        echo "You can test with: bash run.sh"
    fi
fi
