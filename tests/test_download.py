"""Tests for the wordnet download step of build.sh (download_wordnets).

The functions are taken from build.sh itself and run on file:// sources, so
nothing is fetched from the network.
"""
import re
import subprocess
import tarfile
from pathlib import Path

import pytest

_BUILD_SH = Path(__file__).parent.parent / "build.sh"
_FUNCTIONS = ("get_wordnet_urls", "download_standalone", "direct_xml_name",
              "fetch_wordnet", "all_present", "download_wordnets")


def _function_source(name: str) -> str:
    """Return the definition of a shell function in build.sh."""
    m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", _BUILD_SH.read_text(),
                  re.MULTILINE | re.DOTALL)
    assert m, f"{name} not found in build.sh"
    return m.group(0)


def _renames_source() -> str:
    """Return the RAW_RENAMES declaration from build.sh."""
    m = re.search(r"^declare -A RAW_RENAMES=\(\n.*?^\)\n", _BUILD_SH.read_text(),
                  re.MULTILINE | re.DOTALL)
    assert m, "RAW_RENAMES not found in build.sh"
    return m.group(0)


def _lexicon(version: str) -> str:
    """A minimal WN-LMF document whose content records its version."""
    return f'<LexicalResource><Lexicon version="{version}"/></LexicalResource>\n'


def _make_archive(work: Path, stem: str, version: str) -> str:
    """Write a {stem}-{version}.tar.xz holding {stem}-{version}.xml.

    Returns:
        A file:// URL for the archive, relative to the work directory.
    """
    src = work / "src" / f"{stem}-{version}.xml"
    src.parent.mkdir(exist_ok=True)
    src.write_text(_lexicon(version))
    name = f"{stem}-{version}.tar.xz"
    with tarfile.open(work / "src" / name, "w:xz") as tar:
        tar.add(src, arcname=f"{stem}/{src.name}")
    return f"file://src/{name}"


def _run(work: Path, urls: list[str]) -> subprocess.CompletedProcess:
    """Write wordnets.toml listing urls and run download_wordnets in work."""
    listed = ", ".join(f'"{u}"' for u in urls)
    (work / "wordnets.toml").write_text(f"xx = [{listed}]\n")
    script = (f"set -euo pipefail\nDATA_DIR={work}\n"
              + _renames_source()
              + "".join(_function_source(f) for f in _FUNCTIONS)
              + "download_wordnets\n"
              + 'printf "failed: %s\\n" "${FAILED_DOWNLOADS[@]}"\n')
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result


def _raw_names(work: Path) -> list[str]:
    """Wordnet files in bin/raw_wns (not the record)."""
    return sorted(p.name for p in (work / "bin" / "raw_wns").iterdir()
                  if p.name != "sources.tsv")


@pytest.fixture()
def work(tmp_path):
    d = tmp_path / "work"
    (d / "bin" / "raw_wns").mkdir(parents=True)
    return d


def test_archive_fetched_and_recorded(work):
    """A new URL is fetched, and its files are recorded against it."""
    url = _make_archive(work, "wnb-xx", "1.4")

    _run(work, [url])

    assert _raw_names(work) == ["wnb-xx-1.4.xml"]
    record = (work / "bin" / "raw_wns" / "sources.tsv").read_text()
    assert record == f"{url}\twnb-xx-1.4.xml\n"


def test_same_url_not_fetched_again(work):
    """A recorded URL whose files are present is skipped."""
    url = _make_archive(work, "wnb-xx", "1.4")
    _run(work, [url])
    raw = work / "bin" / "raw_wns" / "wnb-xx-1.4.xml"
    raw.write_text("<kept/>")

    result = _run(work, [url])

    assert "already present, skipping" in result.stdout
    assert raw.read_text() == "<kept/>"


def test_missing_file_fetched_again(work):
    """A recorded URL is fetched again if one of its files was deleted."""
    url = _make_archive(work, "wnb-xx", "1.4")
    _run(work, [url])
    (work / "bin" / "raw_wns" / "wnb-xx-1.4.xml").unlink()

    _run(work, [url])

    assert _raw_names(work) == ["wnb-xx-1.4.xml"]


def test_version_bump_replaces_old_files(work):
    """When a URL changes, the new release is fetched and the old one removed."""
    old = _make_archive(work, "wnb-xx", "1.4")
    new = _make_archive(work, "wnb-xx", "1.5")
    _run(work, [old])

    _run(work, [new])

    assert _raw_names(work) == ["wnb-xx-1.5.xml"]
    record = (work / "bin" / "raw_wns" / "sources.tsv").read_text()
    assert record == f"{new}\twnb-xx-1.5.xml\n"


def test_unversioned_name_overwritten(work):
    """A new release with the same file name replaces the old file."""
    for version in ("2.0", "2.1"):
        src = work / "src" / f"omw-xx-{version}" / "omw-xx.xml"
        src.parent.mkdir(parents=True)
        src.write_text(_lexicon(version))
        with tarfile.open(work / "src" / f"omw-xx-{version}.tar.xz", "w:xz") as tar:
            tar.add(src, arcname="omw-xx/omw-xx.xml")
    _run(work, ["file://src/omw-xx-2.0.tar.xz"])

    _run(work, ["file://src/omw-xx-2.1.tar.xz"])

    raw = work / "bin" / "raw_wns" / "omw-xx.xml"
    assert raw.read_text() == _lexicon("2.1")


def test_unrecorded_old_release_removed(work):
    """Files from before the record existed are replaced by a newer release."""
    (work / "bin" / "raw_wns" / "wnb-xx-1.4.xml").write_text(_lexicon("1.4"))
    new = _make_archive(work, "wnb-xx", "1.5")

    _run(work, [new])

    assert _raw_names(work) == ["wnb-xx-1.5.xml"]


def test_unrecorded_direct_xml_kept(work):
    """A direct-XML file already present from before the record is kept."""
    raw = work / "bin" / "raw_wns" / "kenet.xml"
    raw.write_text("<kept/>")

    result = _run(work, ["file://src/kenet.xml"])  # source does not exist

    assert raw.read_text() == "<kept/>"
    assert "failed: kenet" not in result.stdout
    record = (work / "bin" / "raw_wns" / "sources.tsv").read_text()
    assert record == "file://src/kenet.xml\tkenet.xml\n"


def test_failed_download_keeps_old_release(work):
    """If the new release cannot be fetched, the old one is kept."""
    old = _make_archive(work, "wnb-xx", "1.4")
    _run(work, [old])

    result = _run(work, ["file://src/wnb-xx-1.5.tar.xz"])  # does not exist

    assert "failed: wnb-xx: file://src/wnb-xx-1.5.tar.xz" in result.stdout
    assert _raw_names(work) == ["wnb-xx-1.4.xml"]
    record = (work / "bin" / "raw_wns" / "sources.tsv").read_text()
    assert record == f"{old}\twnb-xx-1.4.xml\n"


def test_other_wordnets_untouched(work):
    """Bumping one wordnet leaves the files of another alone."""
    a = _make_archive(work, "wnb-xx", "1.4")
    b = _make_archive(work, "wnb-yy", "1.0")
    _run(work, [a, b])

    _run(work, [_make_archive(work, "wnb-xx", "1.5"), b])

    assert _raw_names(work) == ["wnb-xx-1.5.xml", "wnb-yy-1.0.xml"]


def test_renamed_member_recorded_under_new_name(work):
    """ODEnet's deWordNet.xml is saved as odenet.xml and not fetched again."""
    src = work / "src" / "deWordNet.xml"
    src.parent.mkdir()
    src.write_text(_lexicon("1.4"))
    with tarfile.open(work / "src" / "odenet-1.4.tar.xz", "w:xz") as tar:
        tar.add(src, arcname="odenet-1.4/deWordNet.xml")
    _run(work, ["file://src/odenet-1.4.tar.xz"])

    result = _run(work, ["file://src/odenet-1.4.tar.xz"])

    assert _raw_names(work) == ["odenet.xml"]
    assert "odenet already present, skipping" in result.stdout
