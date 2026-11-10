"""Tests for cyg/package.py (Step 8): the download archive of a cleaned file."""

import json
import tarfile
from pathlib import Path

from cyg.package import citation_bib, lexicon_attributes, package_all, package_name
from cyg.presynth import write_sources

_XML = ('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<LexicalResource xmlns:dc="https://globalwordnet.github.io/schemas/dc/">\n'
        '<Lexicon id="{id}" label="{label}" language="{lang}" email="e" license="https://l" '
        'version="{version}" url="https://example.org/{id}" citation="Someone (2020). {label}."/>\n'
        '</LexicalResource>\n')


def _presynth(tmp_path: Path) -> tuple[Path, Path, Path]:
    presynth = tmp_path / 'presynth'
    presynth.mkdir()
    (presynth / 'kin-0.2.xml').write_text(_XML.format(id='kin', label='Kin', lang='mul', version='0.2.🦢'))
    (presynth / 'kin-fi-0.2.xml').write_text(_XML.format(id='kin-fi', label='Kin Finnish', lang='fi', version='0.2'))
    (presynth / 'kin-fi-0.2_log.json').write_text('{"changes": {}}')
    (presynth / 'wnb-xx-1.4.xml').write_text(_XML.format(id='wnb-xx', label='Wordnet XX', lang='xx', version='1.4.🦢'))
    write_sources(presynth, {
        'kin': {'file': 'kin-0.2.xml', 'url': 'https://x/kin-0.2.tar.xz', 'extras': 'kin', 'requires': None},
        'kin-fi': {'file': 'kin-fi-0.2.xml', 'url': 'https://x/kin-0.2.tar.xz', 'extras': 'kin', 'requires': 'kin'},
        'wnb-xx': {'file': 'wnb-xx-1.4.xml', 'url': 'https://x/wnb-xx-1.4.xml.gz', 'extras': 'wnb-xx', 'requires': None},
    })
    extras = tmp_path / 'extras'
    (extras / 'kin').mkdir(parents=True)
    (extras / 'kin' / 'citation.bib').write_text('@misc{kin, title={Kin}}\n')
    (extras / 'kin' / 'README.md').write_text('# Kin\n')
    (extras / 'kin' / 'LICENSE').write_text('CC BY\n')
    return presynth, extras, tmp_path / 'lmf'


def _names(archive: Path) -> list[str]:
    with tarfile.open(archive) as tar:
        return sorted(m.name for m in tar.getmembers())


def test_package_name():
    assert package_name('omw-arb-2.0.xml') == 'omw-arb-2.0.tar.gz'


def test_archive_holds_file_log_and_the_releases_supporting_files(tmp_path):
    presynth, extras, out = _presynth(tmp_path)

    assert package_all(presynth, extras, out) == (3, 0)

    assert _names(out / 'kin-fi-0.2.tar.gz') == [
        'kin-fi-0.2/LICENSE', 'kin-fi-0.2/README.md', 'kin-fi-0.2/citation.bib',
        'kin-fi-0.2/kin-0.2.xml',        # the concepts lexicon the member Requires
        'kin-fi-0.2/kin-fi-0.2.xml', 'kin-fi-0.2/kin-fi-0.2_log.json']
    with tarfile.open(out / 'kin-fi-0.2.tar.gz') as tar:
        assert tar.extractfile('kin-fi-0.2/citation.bib').read() == b'@misc{kin, title={Kin}}\n'
        assert all(m.uid == 0 and m.uname == '' for m in tar.getmembers())


def test_missing_citation_is_made_from_the_header(tmp_path):
    presynth, extras, out = _presynth(tmp_path)
    package_all(presynth, extras, out)

    assert _names(out / 'wnb-xx-1.4.tar.gz') == ['wnb-xx-1.4/citation.bib', 'wnb-xx-1.4/wnb-xx-1.4.xml']
    with tarfile.open(out / 'wnb-xx-1.4.tar.gz') as tar:
        bib = tar.extractfile('wnb-xx-1.4/citation.bib').read().decode()
    assert bib.startswith('% Made by Cygnet')
    assert '@misc{wnb-xx,' in bib
    assert '  title = {Wordnet XX},' in bib
    assert '  version = {1.4.🦢},' in bib
    assert '  note = {Someone (2020). Wordnet XX.},' in bib


def test_citation_bib_strips_braces_and_whitespace():
    bib = citation_bib({'id': 'a b', 'label': 'X {Y}\n  Z', 'url': 'https://u'})
    assert '@misc{a-b,' in bib
    assert '  title = {X Y Z},' in bib
    assert 'note' not in bib


def test_lexicon_attributes_reads_the_header(tmp_path):
    presynth, _extras, _out = _presynth(tmp_path)
    attrs = lexicon_attributes(presynth / 'kin-0.2.xml')
    assert attrs['label'] == 'Kin' and attrs['version'] == '0.2.🦢'


def test_up_to_date_archives_are_kept_and_reproducible(tmp_path):
    presynth, extras, out = _presynth(tmp_path)
    package_all(presynth, extras, out)
    first = (out / 'wnb-xx-1.4.tar.gz').read_bytes()

    assert package_all(presynth, extras, out) == (0, 3)
    (out / 'wnb-xx-1.4.tar.gz').unlink()
    assert package_all(presynth, extras, out) == (1, 2)
    assert (out / 'wnb-xx-1.4.tar.gz').read_bytes() == first


def test_file_listed_but_missing_is_skipped(tmp_path, capsys):
    presynth, extras, out = _presynth(tmp_path)
    sources = json.loads((presynth / 'sources.json').read_text())
    sources['gone'] = {'file': 'gone-1.xml', 'url': None, 'extras': None, 'requires': None}
    write_sources(presynth, sources)

    assert package_all(presynth, extras, out) == (3, 0)
    assert 'gone-1.xml missing' in capsys.readouterr().out
