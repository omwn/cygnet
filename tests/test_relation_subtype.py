"""Tests for storing a relation's finer-grained subtype/subtype_label
(e.g. plWordnet's dc:type="plwn:56" on a relType="diminutive" relation)
alongside its catalogued relation_type.
"""

from conftest import BASE_CONCEPTS, BASE_LEXEMES_AND_SENSES, wn_xml


class TestSynsetRelationSubtype:
    def test_subtype_and_label_stored(self, builder, tmp_path):
        wn_a = tmp_path / 'wn-a.xml'
        wn_a.write_text(wn_xml('wn-a', BASE_CONCEPTS + BASE_LEXEMES_AND_SENSES + """\
<ConceptRelation relation_type="diminutive" source="cili.i1" target="cili.i2"
    subtype="plwn:56" subtype_label="deminutywność">
  <Provenance resource="wn-a" version="1.0"/>
</ConceptRelation>
"""))
        builder.process_file(wn_a)

        row = builder.cur.execute("""
            SELECT sr.subtype, sr.subtype_label FROM synset_relations sr
            JOIN relation_types rt ON sr.type_rowid = rt.rowid
            WHERE rt.type = 'diminutive'
        """).fetchone()
        assert row == ('plwn:56', 'deminutywność')

    def test_generated_inverse_has_no_subtype(self, builder, tmp_path):
        """The auto-generated has_diminutive inverse wasn't in the source --
        it must not inherit the forward relation's subtype."""
        wn_a = tmp_path / 'wn-a.xml'
        wn_a.write_text(wn_xml('wn-a', BASE_CONCEPTS + """\
<ConceptRelation relation_type="diminutive" source="cili.i1" target="cili.i2"
    subtype="plwn:56" subtype_label="deminutywność">
  <Provenance resource="wn-a" version="1.0"/>
</ConceptRelation>
"""))
        builder.process_file(wn_a)

        row = builder.cur.execute("""
            SELECT sr.subtype, sr.subtype_label FROM synset_relations sr
            JOIN relation_types rt ON sr.type_rowid = rt.rowid
            WHERE rt.type = 'has_diminutive'
        """).fetchone()
        assert row == (None, None)

    def test_no_subtype_is_null(self, builder, tmp_path):
        wn_a = tmp_path / 'wn-a.xml'
        wn_a.write_text(wn_xml('wn-a', BASE_CONCEPTS + """\
<ConceptRelation relation_type="hypernym" source="cili.i2" target="cili.i1">
  <Provenance resource="wn-a" version="1.0"/>
</ConceptRelation>
"""))
        builder.process_file(wn_a)

        row = builder.cur.execute("""
            SELECT sr.subtype, sr.subtype_label FROM synset_relations sr
            JOIN relation_types rt ON sr.type_rowid = rt.rowid
            WHERE rt.type = 'hypernym'
        """).fetchone()
        assert row == (None, None)


class TestSenseRelationSubtype:
    def test_subtype_and_label_stored(self, builder, tmp_path):
        wn_a = tmp_path / 'wn-a.xml'
        wn_a.write_text(wn_xml('wn-a', BASE_CONCEPTS + BASE_LEXEMES_AND_SENSES + """\
<SenseRelation relation_type="antonym" source="sense.dog" target="sense.animal"
    subtype="plwn:99" subtype_label="przeciwstawność">
  <Provenance resource="wn-a" version="1.0"/>
</SenseRelation>
"""))
        builder.process_file(wn_a)

        row = builder.cur.execute("""
            SELECT sr.subtype, sr.subtype_label FROM sense_relations sr
            JOIN relation_types rt ON sr.type_rowid = rt.rowid
            WHERE rt.type = 'antonym'
        """).fetchone()
        assert row == ('plwn:99', 'przeciwstawność')
