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
#   --translate-only   Run only through Step 5 (translate definitions) and
#                      stop — implies --with-translate. Used by
#                      scripts/upload_translations.sh to produce
#                      bin/cygnets_presynth/mtg-1.0.xml for local, manual
#                      publishing (translation takes hours, so it isn't run
#                      in CI — see .github/workflows/check-wordnets.yml).
#   --skip-tests       Skip the test suite
#   --all-languages    Keep every language of the multi-lexicon releases
#                      listed under 'mul' in wordnets.toml.  By default only
#                      languages that already have another wordnet in the
#                      build are kept.  For local, per-project builds.
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
TRANSLATE_ONLY=false
ALL_LANGUAGES=false
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
        --translate-only) TRANSLATE_ONLY=true; WITH_TRANSLATE=true; shift ;;
        --skip-tests)     DO_TESTS=false;       shift ;;
        --all-languages)  ALL_LANGUAGES=true;   shift ;;
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
    # 7_validate_and_export.py reads cygnet.xsd from cwd; copy it into DATA_DIR.
    cp "$PROJECT_DIR/cyg/cygnet.xsd" "$DATA_DIR/cygnet.xsd"
    # 11_add_arasaac.py uses the pre-built ILI mapping from the cygnet repo
    # rather than re-downloading and re-building it from scratch.
    if [[ -f "$PROJECT_DIR/data/araasac-ili.json" ]]; then
        mkdir -p "$DATA_DIR/data"
        cp "$PROJECT_DIR/data/araasac-ili.json" "$DATA_DIR/data/araasac-ili.json"
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

# The name an archive member is saved as in bin/raw_wns/, so that Step 2
# finds it by the URL's stem.  (build.sh must run in bash 3.2, the version
# macOS ships, which has no associative arrays.)
raw_rename() {
    case "$1" in
        deWordNet.xml) echo odenet.xml ;;  # the ODEnet archive
        *) echo "$1" ;;
    esac
}

# Print the index of $1 among the remaining arguments; fail if it is absent.
index_of() {
    local needle="$1" i=0 x
    shift
    for x in "$@"; do
        if [[ "$x" == "$needle" ]]; then
            echo "$i"
            return 0
        fi
        i=$((i + 1))
    done
    return 1
}

# Download a wordnet archive and extract its XML files into bin/raw_wns/ (flat),
# writing the names of the extracted files, one per line, to $3.
download_standalone() {
    local name="$1" url="$2" list="$3"
    (
        # This subshell is invoked as `if ! download_standalone ...; then`,
        # which suspends `set -e` for everything inside it (a well-known
        # bash gotcha: errexit is disabled for the whole compound command
        # under test, including nested subshells). Each step below must
        # therefore check its own exit status explicitly with `|| exit 1`
        # rather than relying on inherited errexit.
        local tmpdir dest
        tmpdir=$(mktemp -d)
        trap 'rm -rf "$tmpdir"' EXIT
        if [[ "$url" == file://* ]]; then
            local local_path="${url#file://}"
            [[ "$local_path" != /* ]] && local_path="$DATA_DIR/$local_path"
            echo "  Copying $name..."
            cp "$local_path" "$tmpdir/archive" || exit 1
        else
            echo "  Downloading $name..."
            curl -fSL -o "$tmpdir/archive" "$url" || exit 1
        fi
        # Detect archive type from file content — some hosts (e.g. DSpace
        # repositories) serve zip files behind extensionless /download URLs.
        if file -b "$tmpdir/archive" | grep -qi "Zip archive"; then
            unzip -q "$tmpdir/archive" -d "$tmpdir/" || exit 1
        else
            tar xf "$tmpdir/archive" -C "$tmpdir/" || exit 1
        fi
        # Skip macOS AppleDouble metadata (._*) that some archives carry.
        # Overwrite: a file of the same name may be left from an older release.
        find "$tmpdir" \( -name '*.xml' -o -name '*.xml.gz' -o -name '*.xml.xz' \) \
            ! -name '._*' -print0 |
            while IFS= read -r -d '' f; do
                dest=$(basename "$f")
                dest=$(raw_rename "$dest")
                cp -f "$f" "$DATA_DIR/bin/raw_wns/$dest" || exit 1
                echo "$dest"
            done > "$list" || exit 1
        if [[ ! -s "$list" ]]; then
            echo "  No XML files found in $url" >&2
            exit 1
        fi
    )
}

# The file name a direct-XML URL (possibly compressed) is saved as; fails for
# archives.
direct_xml_name() {
    local url_path="${1%%\?*}"  # strip query string for extension/filename checks
    [[ "$url_path" == *.xml.gz || "$url_path" == *.xml.xz || "$url_path" == *.xml ]] ||
        return 1
    basename "$url_path"
}

# Fetch one wordnets.toml URL into bin/raw_wns/, writing the names of the files
# it produced, one per line, to $3.
fetch_wordnet() {
    local stem="$1" url="$2" list="$3" fname
    if ! fname=$(direct_xml_name "$url"); then
        download_standalone "$stem" "$url" "$list"
        return
    fi
    if [[ "$url" == file://* ]]; then
        local local_path="${url#file://}"
        [[ "$local_path" != /* ]] && local_path="$DATA_DIR/$local_path"
        echo "  Copying $fname..."
        cp "$local_path" "$DATA_DIR/bin/raw_wns/$fname" || return 1
    else
        echo "  Downloading $fname..."
        if ! curl -fSL -o "$DATA_DIR/bin/raw_wns/$fname" "$url"; then
            rm -f "$DATA_DIR/bin/raw_wns/$fname"
            return 1
        fi
    fi
    echo "$fname" > "$list"
}

# True if every named file ($2...) exists in directory $1 (and there is one).
all_present() {
    local dir="$1" f
    shift
    [[ $# -gt 0 ]] || return 1
    for f in "$@"; do
        [[ -f "$dir/$f" ]] || return 1
    done
}

# Download the wordnets in wordnets.toml into bin/raw_wns/.
#
# bin/raw_wns/sources.tsv records the files each URL produced
# ("url<TAB>file file ..."). A URL whose files are all present is skipped;
# a new or changed URL (e.g. a version bump) is fetched, and the files of a
# URL no longer listed are deleted, so that Step 2, which finds files by
# their unversioned stem, never picks up an old release.
#
# Files from before this record existed are not listed in it: a direct-XML
# file of the right name is kept, an archive is fetched again, and any other
# file matching its stem is deleted.
#
# Sets FAILED_DOWNLOADS to "stem: url" for each URL that could not be fetched.
download_wordnets() {
    local raw="$DATA_DIR/bin/raw_wns"
    local record="$raw/sources.tsv"
    # URL -> "file file ..." maps, as pairs of indexed arrays (bash 3.2 has no
    # associative arrays): rec_* from sources.tsv, cur_* for this run.
    local -a rec_urls=() rec_files=() cur_urls=() cur_files=()
    local -a order=() fresh=() names=() keep=()
    local stem url files f fname list i
    if [[ -f "$record" ]]; then
        while IFS=$'\t' read -r url files; do
            rec_urls+=("$url")
            rec_files+=("$files")
        done < "$record"
    fi
    list=$(mktemp)
    FAILED_DOWNLOADS=()
    while IFS=$'\t' read -r stem url; do
        # listed twice
        index_of "$url" ${cur_urls[@]+"${cur_urls[@]}"} > /dev/null && continue
        order+=("$url")
        if i=$(index_of "$url" ${rec_urls[@]+"${rec_urls[@]}"}); then
            read -ra names <<< "${rec_files[i]}"
            if all_present "$raw" ${names[@]+"${names[@]}"}; then
                echo "  $stem already present, skipping."
                cur_urls+=("$url")
                cur_files+=("${rec_files[i]}")
                continue
            fi
        elif fname=$(direct_xml_name "$url") && [[ -f "$raw/$fname" ]]; then
            echo "  $fname already present, skipping."
            cur_urls+=("$url")
            cur_files+=("$fname")
            continue
        fi
        if fetch_wordnet "$stem" "$url" "$list"; then
            files=$(tr '\n' ' ' < "$list")
            cur_urls+=("$url")
            cur_files+=("${files% }")
            index_of "$url" ${rec_urls[@]+"${rec_urls[@]}"} > /dev/null ||
                fresh+=("$stem")
        else
            echo "  WARNING: failed to fetch $stem from $url (skipping)" >&2
            FAILED_DOWNLOADS+=("$stem: $url")
            # Keep whatever this URL left before.
            if i=$(index_of "$url" ${rec_urls[@]+"${rec_urls[@]}"}); then
                cur_urls+=("$url")
                cur_files+=("${rec_files[i]}")
            fi
        fi
    done < <(get_wordnet_urls)
    rm -f "$list"

    for ((i = 0; i < ${#cur_files[@]}; i++)); do
        read -ra names <<< "${cur_files[i]}"
        keep+=(${names[@]+"${names[@]}"})
    done
    # Files of URLs no longer listed. If a download failed, they may be the
    # only copy of that wordnet, so keep them (and their record) until it works.
    for ((i = 0; i < ${#rec_urls[@]}; i++)); do
        url="${rec_urls[i]}"
        index_of "$url" ${cur_urls[@]+"${cur_urls[@]}"} > /dev/null && continue
        if [[ ${#FAILED_DOWNLOADS[@]} -gt 0 ]]; then
            cur_urls+=("$url")
            cur_files+=("${rec_files[i]}")
            order+=("$url")
            continue
        fi
        read -ra names <<< "${rec_files[i]}"
        for f in ${names[@]+"${names[@]}"}; do
            if ! index_of "$f" ${keep[@]+"${keep[@]}"} > /dev/null &&
                    [[ -f "$raw/$f" ]]; then
                echo "  Removing $f (its URL is no longer in wordnets.toml)"
                rm -f "$raw/$f"
            fi
        done
    done
    # Unrecorded files left by an earlier release of a newly recorded archive.
    for stem in ${fresh[@]+"${fresh[@]}"}; do
        for f in "$raw/$stem"*.xml "$raw/$stem"*.xml.gz "$raw/$stem"*.xml.xz; do
            [[ -f "$f" ]] || continue
            index_of "$(basename "$f")" ${keep[@]+"${keep[@]}"} > /dev/null && continue
            echo "  Removing $(basename "$f") (superseded by the $stem download)"
            rm -f "$f"
        done
    done

    for url in ${order[@]+"${order[@]}"}; do
        files=""
        if i=$(index_of "$url" ${cur_urls[@]+"${cur_urls[@]}"}); then
            files="${cur_files[i]}"
        fi
        printf '%s\t%s\n' "$url" "$files"
    done | grep -v $'\t$' > "$record.tmp" || true
    mv "$record.tmp" "$record"
}

# Parse wordnets.toml and emit "stem<TAB>url" lines (no tomllib dependency needed).
get_wordnet_urls() {
    python3 - "$DATA_DIR/wordnets.toml" << 'PYEOF'
import re, sys

content = open(sys.argv[1]).read()

def stem(url):
    name = url.split("?")[0].rstrip("/").split("/")[-1]
    for ext in [".tar.xz", ".tar.gz", ".tar.bz2", ".zip", ".xz", ".gz"]:
        if name.endswith(ext):
            name = name[:-len(ext)]
            break
    if name.endswith(".xml"):
        name = name[:-4]
    return re.sub(r"[-_]\d[\d.]*$", "", name)

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
    download_wordnets

    echo "  Downloads complete."
    if [[ ${#FAILED_DOWNLOADS[@]} -gt 0 ]]; then
        echo "  WARNING: ${#FAILED_DOWNLOADS[@]} wordnet source(s) could not be fetched and were skipped:"
        printf '    - %s\n' "${FAILED_DOWNLOADS[@]}"
    fi
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

    # --inexact keeps hand-installed packages (e.g. spaCy language models)
    uv sync --inexact ${EXTRAS[@]+"${EXTRAS[@]}"}

    uv run python -c "import nltk; nltk.download('wordnet', quiet=True); nltk.download('omw-1.4', quiet=True)"
    # Browsers are only needed for the UI tests; a platform Playwright does
    # not yet support (or an offline machine) should not abort the build.
    uv run playwright install chromium \
        || echo "WARNING: playwright browser install failed; UI tests may not run."

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
    STEP2_ARGS=()
    if $ALL_LANGUAGES; then STEP2_ARGS+=(--all-languages); fi
    run_pipeline 2_batch_convert_lmfs.py ${STEP2_ARGS[@]+"${STEP2_ARGS[@]}"}
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

    if $TRANSLATE_ONLY; then
        echo "=== Translate-only mode: stopping after Step 5 ==="
        echo "Output: $DATA_DIR/bin/cygnets_presynth/mtg-1.0.xml"
        exit 0
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
