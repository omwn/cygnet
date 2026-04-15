"""Tests for file:// URL handling in build.sh."""
import lzma
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

_WN_DIR = Path(__file__).parent / "wordnets"


# ---------------------------------------------------------------------------
# URL stem parsing (mirrors the Python snippet embedded in build.sh)
# ---------------------------------------------------------------------------

def stem(url: str) -> str:
    """Extracted from build.sh get_wordnet_urls for unit testing."""
    name = url.rstrip("/").split("/")[-1]
    for ext in [".tar.xz", ".tar.gz", ".tar.bz2", ".xz", ".gz"]:
        if name.endswith(ext):
            name = name[:-len(ext)]
            break
    if name.endswith(".xml"):
        name = name[:-4]
    return re.sub(r"-\d[\d.]*$", "", name)


@pytest.mark.parametrize("url,expected", [
    ("file://wn-fr.xml",                             "wn-fr"),
    ("file://open_slovene_wordnet_1.1.xml.xz",       "open_slovene_wordnet_1.1"),
    ("file:///abs/path/to/my-wn-1.0.xml.xz",        "my-wn"),
    ("https://example.com/omw-en-2.0.tar.xz",        "omw-en"),
    ("https://en-word.net/static/english-wordnet-2025.xml.gz", "english-wordnet"),
])
def test_stem_file_url(url, expected):
    assert stem(url) == expected


# ---------------------------------------------------------------------------
# English-base detection in 2_batch_convert_lmfs.py
# ---------------------------------------------------------------------------

def test_en_base_ids_includes_omw_en():
    """omw-en is accepted as a valid English base alongside oewn."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "batch_convert",
        Path(__file__).parent.parent / "conversion_scripts" / "2_batch_convert_lmfs.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "oewn" in mod._EN_BASE_IDS
    assert "omw-en" in mod._EN_BASE_IDS


# ---------------------------------------------------------------------------
# Actual bash copy logic — run the relevant fragment of build.sh in isolation
# ---------------------------------------------------------------------------

def _copy_script(data_dir: Path, url: str) -> str:
    """Bash fragment mirroring the direct-XML branch in build.sh."""
    return textwrap.dedent(f"""\
        set -euo pipefail
        DATA_DIR={data_dir}
        url="{url}"
        fname="$DATA_DIR/bin/raw_wns/$(basename "$url")"
        if [ ! -f "$fname" ]; then
            if [[ "$url" == file://* ]]; then
                local_path="${{url#file://}}"
                [[ "$local_path" != /* ]] && local_path="$DATA_DIR/$local_path"
                cp "$local_path" "$fname"
            else
                curl -fSL -o "$fname" "$url"
            fi
        fi
    """)


@pytest.fixture()
def work(tmp_path):
    d = tmp_path / "work"
    (d / "bin" / "raw_wns").mkdir(parents=True)
    return d


def test_file_url_relative_plain_xml(work):
    """file://wn-fr.xml (relative, plain XML) is resolved against DATA_DIR."""
    import shutil
    shutil.copy(_WN_DIR / "wn-fr.xml", work / "wn-fr.xml")

    result = subprocess.run(
        ["bash", "-c", _copy_script(work, "file://wn-fr.xml")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    dest = work / "bin" / "raw_wns" / "wn-fr.xml"
    assert dest.exists()
    assert dest.read_bytes() == (work / "wn-fr.xml").read_bytes()


def test_file_url_absolute_plain_xml(work):
    """file:///abs/path/to/wn-en.xml (absolute) is copied from the given path."""
    src = _WN_DIR / "wn-en.xml"
    result = subprocess.run(
        ["bash", "-c", _copy_script(work, f"file://{src}")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    dest = work / "bin" / "raw_wns" / "wn-en.xml"
    assert dest.exists()
    assert dest.read_bytes() == src.read_bytes()


def test_file_url_relative_compressed_package(work):
    """file://wn-fr.xml.xz (relative, compressed) is resolved and copied."""
    compressed = lzma.compress((_WN_DIR / "wn-fr.xml").read_bytes())
    (work / "wn-fr.xml.xz").write_bytes(compressed)

    result = subprocess.run(
        ["bash", "-c", _copy_script(work, "file://wn-fr.xml.xz")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    dest = work / "bin" / "raw_wns" / "wn-fr.xml.xz"
    assert dest.exists()
    assert dest.read_bytes() == compressed


def test_file_url_skips_if_already_present(work):
    """file:// copy is skipped when the destination already exists."""
    import shutil
    shutil.copy(_WN_DIR / "wn-fr.xml", work / "wn-fr.xml")
    dest = work / "bin" / "raw_wns" / "wn-fr.xml"
    sentinel = b"<already-present/>"
    dest.write_bytes(sentinel)

    result = subprocess.run(
        ["bash", "-c", _copy_script(work, "file://wn-fr.xml")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert dest.read_bytes() == sentinel
