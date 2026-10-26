"""Tests for MergeBuilder.write_resource_provenance().

Covers the precomputed per-resource provenance split that lets a
#/search?id=<original_id>[&wn=<resource>] link (see
findSynsetInResourceProvenance() in web/index.html) fetch only one
resource's small provenance database instead of the whole
provenance.db.
"""

import gzip
import json
import sqlite3
from pathlib import Path

import pytest

from conftest import wn_xml

_WN_A_BODY = """\
<Concept id="cili.i1" ontological_category="NOUN" status="1">
  <Provenance resource="wn-a" version="1.0" original_id="wn-a-dog-n"/>
</Concept>
<Gloss definiendum="cili.i1" language="en">
  <AnnotatedSentence>a domesticated canine</AnnotatedSentence>
  <Provenance resource="wn-a" version="1.0"/>
</Gloss>
<Lexeme id="lex.en.dog" language="en" grammatical_category="NOUN">
  <Wordform form="dog"/>
  <Provenance resource="wn-a" version="1.0" original_id="wn-a-dog-n"/>
</Lexeme>
<Sense id="sense.dog" signifier="lex.en.dog" signified="cili.i1">
  <Provenance resource="wn-a" version="1.0"/>
</Sense>
"""

_WN_B_BODY = """\
<Concept id="cili.i2" ontological_category="NOUN" status="1">
  <Provenance resource="wn-b" version="2.0" original_id="wn-b-cat-n"/>
</Concept>
<Gloss definiendum="cili.i2" language="en">
  <AnnotatedSentence>a domesticated feline</AnnotatedSentence>
  <Provenance resource="wn-b" version="2.0"/>
</Gloss>
<Lexeme id="lex.en.cat" language="en" grammatical_category="NOUN">
  <Wordform form="cat"/>
  <Provenance resource="wn-b" version="2.0" original_id="wn-b-cat-n"/>
</Lexeme>
<Sense id="sense.cat" signifier="lex.en.cat" signified="cili.i2">
  <Provenance resource="wn-b" version="2.0"/>
</Sense>
"""

_BUILD_VERSION = '20260101000000'


@pytest.fixture()
def builder_split(builder, tmp_path):
    (tmp_path / 'wn-a.xml').write_text(wn_xml('wn-a', _WN_A_BODY))
    (tmp_path / 'wn-b.xml').write_text(wn_xml('wn-b', _WN_B_BODY))
    builder.process_file(tmp_path / 'wn-a.xml')
    builder.process_file(tmp_path / 'wn-b.xml')
    builder.create_indexes()
    builder.merge_case_variants()
    builder.cascade_delete()
    builder.remove_orphans()
    builder.compute_sense_indices()
    builder.insert_resources()
    builder.load_core_synsets()
    return builder


def _open_gz(path: Path) -> sqlite3.Connection:
    """Decompress a .db.gz split file next to itself and open it."""
    raw = path.with_suffix('')
    raw.write_bytes(gzip.decompress(path.read_bytes()))
    return sqlite3.connect(raw)


class TestWriteResourceProvenance:
    def test_writes_one_file_per_resource(self, builder_split, tmp_path):
        out_dir = tmp_path / 'provenance'
        n = builder_split.write_resource_provenance(out_dir, _BUILD_VERSION)
        assert n == 2
        assert (out_dir / 'wn-a.db.gz').exists()
        assert (out_dir / 'wn-b.db.gz').exists()

    def test_index_json_has_version_and_row_counts(self, builder_split, tmp_path):
        out_dir = tmp_path / 'provenance'
        builder_split.write_resource_provenance(out_dir, _BUILD_VERSION)
        index = json.loads((out_dir / 'index.json').read_text())
        assert index['version'] == _BUILD_VERSION
        assert index['resources']['wn-a']['file'] == 'wn-a.db.gz'
        assert index['resources']['wn-a']['rows'] > 0
        assert index['resources']['wn-b']['rows'] > 0

    def test_split_db_keeps_rowids_and_original_ids(self, builder_split, tmp_path):
        out_dir = tmp_path / 'provenance'
        builder_split.write_resource_provenance(out_dir, _BUILD_VERSION)
        conn = _open_gz(out_dir / 'wn-a.db.gz')
        row = conn.execute(
            "SELECT p.item_rowid FROM provenance p "
            "JOIN prov_tables pt ON p.table_rowid = pt.rowid "
            "WHERE pt.name = 'synsets' AND p.original_id = ?",
            ('wn-a-dog-n',),
        ).fetchone()
        assert row is not None
        # The other resource's rows must not leak into this file.
        other = conn.execute(
            "SELECT 1 FROM provenance WHERE original_id = ?", ('wn-b-cat-n',),
        ).fetchone()
        assert other is None

    def test_split_db_stamped_with_build_version(self, builder_split, tmp_path):
        out_dir = tmp_path / 'provenance'
        builder_split.write_resource_provenance(out_dir, _BUILD_VERSION)
        conn = _open_gz(out_dir / 'wn-a.db.gz')
        assert conn.execute('SELECT version FROM build_info').fetchone() == (_BUILD_VERSION,)

    def test_rerun_replaces_stale_files(self, builder_split, tmp_path):
        out_dir = tmp_path / 'provenance'
        out_dir.mkdir()
        (out_dir / 'stale.db.gz').write_bytes(b'')
        builder_split.write_resource_provenance(out_dir, _BUILD_VERSION)
        assert not (out_dir / 'stale.db.gz').exists()
