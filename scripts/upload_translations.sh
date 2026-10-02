#!/usr/bin/env bash
# upload_translations.sh
#
# Runs the gloss-translation pipeline stage locally (Steps 1-5 of build.sh)
# and publishes the result to the rolling "translated-glosses" GitHub
# Release, so release.yml can pick it up without ever running translation
# in CI.
#
# Translation takes several hours and only needs rerunning when a wordnet
# is added, removed, or swapped for a source with different glosses.
# .github/workflows/check-wordnets.yml checks for that daily and opens a
# GitHub issue when it's the case, rather than attempting the translation
# itself.
#
# Usage: ./scripts/upload_translations.sh
# Requires: gh (GitHub CLI), authenticated with push access to this repo.

set -euo pipefail

command -v gh >/dev/null 2>&1 || { echo "Error: gh (GitHub CLI) is required" >&2; exit 1; }

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "=== Running translation pipeline (this takes a while) ==="
./build.sh --translate-only

MTG_FILE="bin/cygnets_presynth/mtg-1.0.xml"
if [[ ! -f "$MTG_FILE" ]]; then
    echo "Error: $MTG_FILE not found after build.sh --translate-only" >&2
    exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

# Snapshot of every resource URL this translation run covered, so
# check-wordnets.yml can tell whether a future wordnets.toml edit
# introduces anything new (see that workflow for the comparison logic).
BASELINE_FILE="$TMP_DIR/translated_wordnets_baseline.txt"
uv run python scripts/wordnet_urls.py > "$BASELINE_FILE"

echo "=== Publishing to the 'translated-glosses' release ==="
if gh release view translated-glosses &>/dev/null; then
    gh release upload translated-glosses "$MTG_FILE" "$BASELINE_FILE" --clobber
else
    gh release create translated-glosses \
        --title "Pre-translated glosses (rolling)" \
        --notes "Not a Cygnet release — holds the latest machine-translated non-English glosses, consumed by release.yml. Regenerate with scripts/upload_translations.sh after adding, removing, or changing a wordnet." \
        --prerelease \
        "$MTG_FILE" \
        "$BASELINE_FILE"
fi

echo "Done."
