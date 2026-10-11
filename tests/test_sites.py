"""Tests for scripts/sites.py — downstream site tracking."""

import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).parent.parent / "scripts" / "sites.py"
_spec = importlib.util.spec_from_file_location("sites", _PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["sites"] = _mod
_spec.loader.exec_module(_mod)

SITES_TOML = """
[policy]
shared_files = ["index.html"]
text = "Copy shared files after each release."

[[site]]
name = "Up"
title = "Upstream"
url = "https://example.org/"
repo = "omwn/cygnet"
branch = "main"
web_dir = "web"

[[site]]
name = "Down"
title = "Downstream Wordnet"
url = "https://down.example.org/"
repo = "omwn/down"
branch = "master"
web_dir = "docs"
clone = "CLONE"
languages = ["xx"]
maintainer = "Ann Owner"
web_maintainer = "Web Person"
"""


def _write(tmp_path: Path, clone: Path) -> Path:
    p = tmp_path / "sites.toml"
    p.write_text(SITES_TOML.replace("CLONE", str(clone)))
    return p


def test_render_markdown(tmp_path):
    data = _mod.load_sites(_write(tmp_path, tmp_path / "down"))
    md = _mod.render_markdown(data)
    assert md.startswith(_mod.HEADER)
    assert "[Downstream Wordnet](https://down.example.org/)" in md
    assert "[omwn/down](https://github.com/omwn/down) (`master`)" in md
    assert "Copy shared files after each release." in md
    assert "| Ann Owner | Web Person |" in md
    # Update PRs: branch, copy, then a PR against the site's repo and branch
    assert f"cd {tmp_path / 'down'}" in md
    assert "cp ~/git/cygnet/web/index.html docs/" in md
    assert "gh pr create -R omwn/down --base master --fill" in md
    assert "git push origin" not in md
    # Upstream has no clone, so no PR commands are generated for it
    assert md.count("gh pr create") == 1


def test_site_status(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("new")
    docs = tmp_path / "down" / "docs"
    docs.mkdir(parents=True)
    site = _mod.load_sites(_write(tmp_path, tmp_path / "down"))["site"][1]

    assert "missing" in _mod.site_status(site, ["index.html"], web)[1]
    (docs / "index.html").write_text("old")
    assert "differs" in _mod.site_status(site, ["index.html"], web)[1]
    (docs / "index.html").write_text("new")
    assert "in sync" in _mod.site_status(site, ["index.html"], web)[1]


def test_site_status_no_clone(tmp_path):
    site = {"name": "X", "web_dir": "docs", "clone": str(tmp_path / "nope")}
    assert "not found" in _mod.site_status(site, ["index.html"], tmp_path)[0]


def test_repo_sites_toml_renders():
    """The checked-in sites.toml parses and lists every site."""
    data = _mod.load_sites(Path(__file__).parent.parent / "sites.toml")
    md = _mod.render_markdown(data)
    for site in data["site"]:
        assert site["url"] in md
