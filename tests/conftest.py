"""Shared pytest fixtures and test-data helpers for the Cygnet test suite."""

import json
import shutil
import subprocess
import textwrap
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pytest

from cyg.merge import MergeBuilder

_WORDNETS_DIR = Path(__file__).parent / 'wordnets'


# ---------------------------------------------------------------------------
# Shared XML helpers
# ---------------------------------------------------------------------------

def wn_xml(wn_id: str, body: str, language: str = 'en', version: str = '1.0') -> str:
    """Wrap *body* XML in a CygnetResource root element."""
    inner = textwrap.indent(textwrap.dedent(body).strip(), '  ')
    return (
        f"<?xml version='1.0' encoding='UTF-8'?>\n"
        f'<CygnetResource id="{wn_id}" label="Test {wn_id}"'
        f' language="{language}" version="{version}">\n'
        f'{inner}\n'
        f'</CygnetResource>\n'
    )


# ---------------------------------------------------------------------------
# XML snippets shared by conflict-detection tests
# ---------------------------------------------------------------------------

BASE_CONCEPTS = """\
<Concept id="cili.i1" ontological_category="NOUN" status="1">
  <Provenance resource="wn-a" version="1.0" original_id="t-1"/>
</Concept>
<Concept id="cili.i2" ontological_category="NOUN" status="1">
  <Provenance resource="wn-a" version="1.0" original_id="t-2"/>
</Concept>
<Gloss definiendum="cili.i1" language="en">
  <AnnotatedSentence>dog</AnnotatedSentence>
  <Provenance resource="wn-a" version="1.0"/>
</Gloss>
<Gloss definiendum="cili.i2" language="en">
  <AnnotatedSentence>animal</AnnotatedSentence>
  <Provenance resource="wn-a" version="1.0"/>
</Gloss>
"""

BASE_LEXEMES_AND_SENSES = """\
<Lexeme id="en.NOUN.dog" language="en" grammatical_category="NOUN">
  <Wordform form="dog"/>
  <Provenance resource="wn-a" version="1.0" original_id="dog-n"/>
</Lexeme>
<Lexeme id="en.NOUN.animal" language="en" grammatical_category="NOUN">
  <Wordform form="animal"/>
  <Provenance resource="wn-a" version="1.0" original_id="animal-n"/>
</Lexeme>
<Sense id="sense.dog" signifier="en.NOUN.dog" signified="cili.i1">
  <Provenance resource="wn-a" version="1.0"/>
</Sense>
<Sense id="sense.animal" signifier="en.NOUN.animal" signified="cili.i2">
  <Provenance resource="wn-a" version="1.0"/>
</Sense>
"""

BASE_CONCEPTS_SENSE_REL = """\
<Concept id="cili.i3" ontological_category="VERB" status="1">
  <Provenance resource="wn-a" version="1.0" original_id="t-3"/>
</Concept>
<Concept id="cili.i4" ontological_category="NOUN" status="1">
  <Provenance resource="wn-a" version="1.0" original_id="t-4"/>
</Concept>
<Gloss definiendum="cili.i3" language="en">
  <AnnotatedSentence>run</AnnotatedSentence>
  <Provenance resource="wn-a" version="1.0"/>
</Gloss>
<Gloss definiendum="cili.i4" language="en">
  <AnnotatedSentence>running</AnnotatedSentence>
  <Provenance resource="wn-a" version="1.0"/>
</Gloss>
<Lexeme id="en.VERB.run" language="en" grammatical_category="VERB">
  <Wordform form="run"/>
  <Provenance resource="wn-a" version="1.0" original_id="run-v"/>
</Lexeme>
<Lexeme id="en.NOUN.running" language="en" grammatical_category="NOUN">
  <Wordform form="running"/>
  <Provenance resource="wn-a" version="1.0" original_id="running-n"/>
</Lexeme>
<Sense id="sense.run" signifier="en.VERB.run" signified="cili.i3">
  <Provenance resource="wn-a" version="1.0"/>
</Sense>
<Sense id="sense.running" signifier="en.NOUN.running" signified="cili.i4">
  <Provenance resource="wn-a" version="1.0"/>
</Sense>
"""


# ---------------------------------------------------------------------------
# Per-test MergeBuilder (used by conflict-detection tests)
# ---------------------------------------------------------------------------

@pytest.fixture()
def builder(tmp_path):
    """MergeBuilder backed by temporary SQLite files."""
    return MergeBuilder(tmp_path / 'cygnet.db', tmp_path / 'provenance.db')


# ---------------------------------------------------------------------------
# Test corpus helpers
# ---------------------------------------------------------------------------
# Test wordnets live in tests/wordnets/ as readable XML files:
#   wn-en.xml  — English wordnet: all concepts, English senses, relations
#   wn-fr.xml  — French wordnet:  adds French senses for ILIs in wn-en
#   wn-bad.xml — Erroneous wordnet: reversed hypernym for conflict tests
#
# Expected search results after merging wn-en + wn-fr:
#   "dog"        exact  → 1 result  (en:dog)                       1 language
#   "dog*"       glob   → 2 results (en:dog, en:dogfish)            1 language
#   "*ness"      glob   → 1 result  (en:brightness)                 1 language
#   "i3"         ILI    → 2 results (en:dog, fr:chien)              2 languages
#   "def:animal" def    → 3 results (en:animal, en:dog, fr:chien)   2 languages
#   "def:animal" + English filter → 2 results                       1 language


def build_test_db(db_path: Path, prov_path: Path,
                  xml_files: list[Path]) -> None:
    """Run MergeBuilder on *xml_files* in order and write gzipped DBs."""
    b = MergeBuilder(db_path, prov_path)
    for f in xml_files:
        b.process_file(f)

    b.cur.execute("UPDATE languages SET name = 'English' WHERE code = 'en'")
    b.cur.execute("UPDATE languages SET name = 'French' WHERE code = 'fr'")

    b.create_indexes()
    b.merge_case_variants()
    b.cascade_delete()
    b.remove_orphans()
    b.compute_sense_indices()
    b.insert_resources()

    # Insert known ARASAAC pictogram IDs for UI tests:
    #   dog (i3)    → id 2253  (direct image test)
    #   entity (i1) → id 2254  (hypernym-fallback test: animal has entity as hypernym)
    #   bright (i6) → id 2255  (eq_synonym-fallback test: gleaming; similar-fallback: glowing)
    for ili, arasaac_id in [('i3', 2253), ('i1', 2254), ('i6', 2255)]:
        row = b.cur.execute("SELECT rowid FROM synsets WHERE ili = ?", (ili,)).fetchone()
        if row:
            b.cur.execute(
                "INSERT INTO arasaac (synset_rowid, arasaac_id) VALUES (?, ?)",
                (row[0], arasaac_id),
            )
    b.conn.commit()

    b.finalize(db_path, prov_path)
    for path in (db_path, prov_path):
        subprocess.run(['gzip', '-k', '-9', '-f', str(path)], check=True)


@pytest.fixture(scope='session')
def test_db_dir(tmp_path_factory):
    """Merge wn-en + wn-fr into a test DB; return a directory for HTTP serving."""
    build_dir = tmp_path_factory.mktemp('db_build')
    db_path = build_dir / 'cygnet.db'
    prov_path = build_dir / 'provenance.db'
    build_test_db(db_path, prov_path, [
        _WORDNETS_DIR / 'wn-en.xml',
        _WORDNETS_DIR / 'wn-fr.xml',
    ])

    # Assemble the server root: index.html + compressed DBs
    serve_dir = tmp_path_factory.mktemp('serve')
    web_dir = Path(__file__).parent.parent / 'web'
    shutil.copy(web_dir / 'index.html', serve_dir / 'index.html')
    shutil.copy(web_dir / 'relations.json', serve_dir / 'relations.json')
    shutil.copy(db_path.with_suffix('.db.gz'), serve_dir / 'cygnet.db.gz')
    shutil.copy(prov_path.with_suffix('.db.gz'), serve_dir / 'provenance.db.gz')
    return serve_dir


@pytest.fixture(scope='session')
def valid_ili() -> str:
    """ILI of the dog synset in the test DB."""
    return 'i3'


# ---------------------------------------------------------------------------
# HTTP server fixture (used by UI tests)
# ---------------------------------------------------------------------------

def _make_handler(directory: str):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

        def log_message(self, *_):
            pass

        def guess_type(self, path):
            if str(path).endswith('.gz'):
                return 'application/octet-stream'
            return super().guess_type(path)

    return Handler


def _start_server(directory: str) -> tuple[HTTPServer, str]:
    """Bind an HTTPServer to an ephemeral port; return (server, base_url)."""
    server = HTTPServer(('localhost', 0), _make_handler(directory))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f'http://localhost:{port}'


@pytest.fixture(scope='session')
def http_server(test_db_dir):
    """Session-scoped HTTP server serving the test DB directory."""
    server, url = _start_server(str(test_db_dir))
    yield url
    server.shutdown()


@pytest.fixture(scope='session')
def http_server_with_config(test_db_dir, tmp_path_factory):
    """HTTP server like http_server but also serves a local.json config file.

    The config sets searchLanguage='fr' and displayLanguage='fr' so tests
    can verify that local.json seeds the correct UI defaults on page load.
    """
    config_dir = tmp_path_factory.mktemp('serve_config')
    for f in test_db_dir.iterdir():
        shutil.copy(f, config_dir / f.name)
    (config_dir / 'local.json').write_text(
        json.dumps({'searchLanguage': 'fr', 'displayLanguage': 'fr'})
    )
    server, url = _start_server(str(config_dir))
    yield url
    server.shutdown()


@pytest.fixture(scope='session')
def http_server_with_branding(test_db_dir, tmp_path_factory):
    """HTTP server serving a local.json with full header branding.

    Sets title='TestWN', icon='🧪', name='TWN', and a custom logo with
    name='TWN' so tests can verify both header sides are customisable.
    """
    branding_dir = tmp_path_factory.mktemp('serve_branding')
    for f in test_db_dir.iterdir():
        shutil.copy(f, branding_dir / f.name)
    (branding_dir / 'local.json').write_text(json.dumps({
        'title': 'TestWN',
        'icon': '🧪',
        'name': 'TWN',
        'logo': {
            'src': 'omw-logo.svg',
            'url': 'https://omwn.org',
            'alt': 'Test Wordnet',
            'name': 'TWN',
        },
    }))
    server, url = _start_server(str(branding_dir))
    yield url
    server.shutdown()
