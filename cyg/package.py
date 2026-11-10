"""Package each cleaned WN-LMF file for download (Step 8).

A wordnet's page in the web UI links to the release its project
published (the wordnets.toml URL) and to the file Cygnet actually merges:
``<id>-<version>.tar.gz`` holding, under ``<id>-<version>/``, the cleaned
LMF, its cleaning log, and the supporting files the source archive came
with (``citation.bib``, ``README``, ``LICENSE``).  A source without a
``citation.bib`` gets one made from the Lexicon header.  A member of a
multi-lexicon release also carries the concepts lexicon it Requires, so
the archive loads on its own.
"""

from __future__ import annotations

import gzip
import io
import tarfile
from pathlib import Path

from lxml import etree

from cyg.lmf import PARSER_OPTIONS
from cyg.multilex import open_xml
from cyg.presynth import log_path, read_sources

CITATION_NAME = 'citation.bib'


def package_name(cleaned_file: str) -> str:
    """The archive a cleaned file is packaged as: ``omw-arb-2.0.xml`` ->
    ``omw-arb-2.0.tar.gz``."""
    return f'{Path(cleaned_file).stem}.tar.gz'


def lexicon_attributes(path: Path) -> dict[str, str]:
    """The attributes of a file's first Lexicon (label, url, citation ...).

    Args:
        path: A WN-LMF file.

    Returns:
        The attributes, {} when there is no Lexicon.
    """
    with open_xml(path) as f:
        for _event, elem in etree.iterparse(f, events=('start',), **PARSER_OPTIONS):
            if etree.QName(elem).localname in ('Lexicon', 'LexiconExtension'):
                return {etree.QName(k).localname: v for k, v in elem.attrib.items()}
    return {}


def _bib_value(text: str) -> str:
    return ' '.join(text.split()).replace('{', '').replace('}', '')


def citation_bib(attrs: dict[str, str]) -> str:
    """A BibTeX entry for a wordnet from its Lexicon header, for a release
    that ships none.

    Args:
        attrs: The Lexicon's attributes (lexicon_attributes()).

    Returns:
        The file's text: an ``@misc`` entry whose ``note`` carries the
        header's free-text citation.
    """
    fields = [('title', attrs.get('label')), ('version', attrs.get('version')),
              ('howpublished', attrs.get('url')), ('note', attrs.get('citation'))]
    lines = [f'  {name} = {{{_bib_value(value)}}},' for name, value in fields if value]
    key = ''.join(c if c.isalnum() else '-' for c in attrs.get('id', 'wordnet'))
    return ('% Made by Cygnet from the Lexicon header: the release has no citation.bib.\n'
            f'@misc{{{key},\n' + '\n'.join(lines) + '\n}\n')


def package_one(presynth: Path, extras_root: Path, out_dir: Path, lexicon: str,
                entry: dict, sources: dict[str, dict]) -> Path | None:
    """Write one wordnet's archive, unless it is already up to date.

    Args:
        presynth: The cleaned files' directory.
        extras_root: ``bin/raw_wns/extras``, holding ``<stem>/`` per archive URL.
        out_dir: Where the archive goes.
        lexicon: The lexicon id.
        entry: Its sources.json record ({"file", "url", "extras", "requires"}).
        sources: Every record, for the concepts lexicon a member Requires.

    Returns:
        The archive written, or None when the existing one is newer than
        its inputs.
    """
    xml = presynth / entry['file']
    stem = xml.stem
    members: list[Path] = [xml]
    log = log_path(xml)
    if log.exists():
        members.append(log)
    extras = extras_root / entry['extras'] if entry.get('extras') else None
    if extras is not None and extras.is_dir():
        members.extend(sorted(p for p in extras.iterdir() if p.is_file()))
    concepts = sources.get(entry['requires'] or '')
    if concepts:
        members.append(presynth / concepts['file'])
    out = out_dir / package_name(entry['file'])
    if out.exists() and out.stat().st_mtime >= max(p.stat().st_mtime for p in members):
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out, 'wb') as f, gzip.GzipFile(filename='', mode='wb', fileobj=f, mtime=0) as gz, \
            tarfile.open(fileobj=gz, mode='w') as tar:
        for path in members:
            info = tar.gettarinfo(str(path), arcname=f'{stem}/{path.name}')
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            with open(path, 'rb') as src:
                tar.addfile(info, src)
        if not any(p.name.lower() == CITATION_NAME for p in members):
            data = citation_bib(lexicon_attributes(xml)).encode('utf-8')
            info = tarfile.TarInfo(f'{stem}/{CITATION_NAME}')
            info.size = len(data)
            info.mtime = int(xml.stat().st_mtime)
            tar.addfile(info, io.BytesIO(data))
    return out


def package_all(presynth: Path, extras_root: Path, out_dir: Path) -> tuple[int, int]:
    """Package every cleaned file sources.json lists.

    Args:
        presynth: The cleaned files' directory (with sources.json).
        extras_root: ``bin/raw_wns/extras``.
        out_dir: Where the archives go (``web/lmf``).

    Returns:
        (archives written, archives already up to date).
    """
    sources = read_sources(presynth)
    written = kept = 0
    for lexicon, entry in sorted(sources.items()):
        if not (presynth / entry['file']).exists():
            print(f'  {entry["file"]} missing, not packaged')
            continue
        if package_one(presynth, extras_root, out_dir, lexicon, entry, sources) is None:
            kept += 1
        else:
            written += 1
    return written, kept
