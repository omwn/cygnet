#!/usr/bin/env python3
"""Print every resource URL in wordnets.toml, one per line, sorted.

Used by scripts/upload_translations.sh (to snapshot what a translation run
covered) and .github/workflows/check-wordnets.yml (to detect new resources
that haven't been translated yet) — kept in one place so both stay in sync.
"""
import tomllib
from pathlib import Path


def main() -> None:
    data = tomllib.loads(Path("wordnets.toml").read_bytes().decode())
    urls = sorted({u for v in data.values() for u in v})
    print("\n".join(urls))


if __name__ == "__main__":
    main()
