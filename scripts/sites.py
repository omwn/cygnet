#!/usr/bin/env python3
"""Track the sites that deploy the Cygnet web UI (sites.toml).

    render  write SITES.md from sites.toml
    status  compare each site's local clone against cygnet's web/ files
"""
import argparse
import filecmp
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADER = "<!-- Generated from sites.toml by scripts/sites.py; do not edit. -->"


def load_sites(path: Path) -> dict:
    """Load a sites.toml file.

    Args:
        path: Path to the TOML file

    Returns:
        Dict with a 'policy' table and a 'site' list (both always present)
    """
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    data.setdefault("policy", {})
    data.setdefault("site", [])
    return data


def render_markdown(data: dict) -> str:
    """Render the sites table, update policy and sync commands as Markdown.

    Args:
        data: Parsed sites.toml, as returned by load_sites

    Returns:
        The Markdown text for SITES.md
    """
    policy = data["policy"]
    shared = policy.get("shared_files", [])
    lines = [HEADER, "", "# Sites using Cygnet", ""]
    lines += [
        "| Site | Repository | Languages | Maintainer | Web maintainer | Notes |",
        "|---|---|---|---|---|---|",
    ]
    for s in data["site"]:
        repo = f"[{s['repo']}](https://github.com/{s['repo']}) (`{s['branch']}`)"
        lines.append(
            f"| [{s['title']}]({s['url']}) | {repo} "
            f"| {', '.join(s.get('languages', []))} "
            f"| {s.get('maintainer', '')} | {s.get('web_maintainer', '')} "
            f"| {s.get('notes', '')} |"
        )
    lines += ["", "## Update policy", ""]
    lines += [policy.get("text", "").strip(), ""]
    if shared:
        lines += [
            "Shared files: " + ", ".join(f"`{f}`" for f in shared) + ".",
            "",
        ]
    downstream = [s for s in data["site"] if s.get("web_dir") and s.get("clone")]
    if downstream and shared:
        lines += [
            "## Opening update PRs",
            "",
            "Set `C` to the Cygnet commit being propagated "
            "(`C=$(git -C ~/git/cygnet rev-parse --short HEAD)`), then for each site:",
            "",
        ]
        for s in downstream:
            lines += pr_commands(s, shared)
    return "\n".join(lines)


def pr_commands(site: dict, shared: list[str]) -> list[str]:
    """Shell commands that open a PR updating one site's shared files.

    Args:
        site: One [[site]] table from sites.toml (needs clone, web_dir,
            branch and repo)
        shared: Names of the shared files

    Returns:
        Markdown lines: a heading and a fenced bash block
    """
    files = " ".join(f"~/git/cygnet/web/{f}" for f in shared)
    return [
        f"### {site['title']}",
        "",
        "```bash",
        f"cd {site['clone']}",
        f"git switch {site['branch']} && git pull",
        'git switch -c "cygnet-$C"',
        f"cp {files} {site['web_dir']}/",
        f"git add {' '.join(site['web_dir'] + '/' + f for f in shared)}",
        'git commit -m "Update web UI from cygnet ($C)"',
        "git push -u origin HEAD",
        f"gh pr create -R {site['repo']} --base {site['branch']} --fill",
        "```",
        "",
    ]


def last_commit(clone: Path, path: str) -> str:
    """Describe the last commit in a clone that touched a file.

    Args:
        clone: Path to the git working tree
        path: File path relative to the clone root

    Returns:
        'hash date subject', or '' if there is no such commit
    """
    result = subprocess.run(
        ["git", "-C", str(clone), "log", "-1", "--format=%h %ad %s",
         "--date=short", "--", path],
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip()


def site_status(site: dict, shared: list[str], web: Path) -> list[str]:
    """Report whether each shared file in a site's clone matches cygnet's.

    Args:
        site: One [[site]] table from sites.toml
        shared: Names of the shared files
        web: Cygnet's web/ directory

    Returns:
        Report lines: a heading, then one line per file giving
        'in sync', 'differs' or 'missing' and the file's last commit
    """
    if "clone" not in site:
        return [f"{site['name']}: no local clone listed"]
    clone = Path(site["clone"]).expanduser()
    if not clone.is_dir():
        return [f"{site['name']}: clone {clone} not found"]
    out = [f"{site['name']} ({clone}):"]
    for f in shared:
        theirs = clone / site["web_dir"] / f
        if not theirs.exists():
            state = "missing"
        elif filecmp.cmp(web / f, theirs, shallow=False):
            state = "in sync"
        else:
            state = "differs"
        commit = last_commit(clone, f"{site['web_dir']}/{f}")
        out.append(f"  {f:16} {state:8} {commit}")
    return out


def main() -> None:
    """Run the render or status command."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["render", "status"])
    parser.add_argument("--sites", type=Path, default=ROOT / "sites.toml")
    parser.add_argument("--out", type=Path, default=ROOT / "SITES.md")
    args = parser.parse_args()

    data = load_sites(args.sites)
    if args.command == "render":
        args.out.write_text(render_markdown(data), encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        shared = data["policy"].get("shared_files", [])
        for site in data["site"]:
            if site.get("repo") == "omwn/cygnet":
                continue
            print("\n".join(site_status(site, shared, ROOT / "web")))


if __name__ == "__main__":
    main()
