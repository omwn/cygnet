"""Tests for dropping untranslated English definitions in MergeBuilder.

Some wordnets (e.g. FinnWordNet) give the PWN English gloss as the definition
in their own language.  MergeBuilder drops a non-English definition that is
identical to an English definition of the same concept.
"""

from conftest import BASE_CONCEPTS, wn_xml

from cyg.merge import fold_gloss, is_english

# BASE_CONCEPTS gives cili.i1 the English gloss 'dog' and cili.i2 'animal';
# these add longer English glosses to compare against.
EN_GLOSSES = """\
<Gloss definiendum="cili.i1" language="en">
  <AnnotatedSentence>a domesticated member of the genus Canis</AnnotatedSentence>
  <Provenance resource="wn-a" version="1.0"/>
</Gloss>
"""


def _gloss(definiendum: str, language: str, text: str, resource: str = 'wn-fi') -> str:
    return f"""\
<Gloss definiendum="{definiendum}" language="{language}">
  <AnnotatedSentence>{text}</AnnotatedSentence>
  <Provenance resource="{resource}" version="1.0"/>
</Gloss>
"""


def _merge(builder, tmp_path, second: str, language: str = 'fi'):
    """Merge wn-a (English) then a second wordnet; return {lang: [texts]} for i1."""
    first = tmp_path / 'wn-a.xml'
    first.write_text(wn_xml('wn-a', BASE_CONCEPTS + EN_GLOSSES))
    other = tmp_path / 'wn-b.xml'
    other.write_text(wn_xml('wn-fi', second, language=language))
    builder.process_file(first)
    builder.process_file(other)
    rows = builder.cur.execute("""
        SELECT l.code, d.definition
        FROM definitions d
        JOIN languages l ON l.rowid = d.language_rowid
        JOIN synsets s ON s.rowid = d.synset_rowid
        WHERE s.ili = 'i1'
    """).fetchall()
    found: dict[str, list[str]] = {}
    for code, text in rows:
        found.setdefault(code, []).append(text)
    return found


class TestHelpers:
    def test_is_english(self):
        assert is_english('en')
        assert is_english('en-GB')
        assert not is_english('fi')
        assert not is_english('eno')
        assert not is_english(None)

    def test_fold_gloss(self):
        assert fold_gloss('  A pet\tDog ') == 'a pet dog'


class TestEnglishCopies:
    def test_identical_copy_dropped(self, builder, tmp_path):
        found = _merge(builder, tmp_path, _gloss(
            'cili.i1', 'fi', 'a domesticated member of the genus Canis'))
        assert 'fi' not in found
        assert builder.english_glosses_dropped == {'wn-fi': 1}

    def test_copy_differing_in_case_and_space_dropped(self, builder, tmp_path):
        found = _merge(builder, tmp_path, _gloss(
            'cili.i1', 'fi', 'A  domesticated member of the genus  Canis'))
        assert 'fi' not in found

    def test_translation_kept(self, builder, tmp_path):
        found = _merge(builder, tmp_path, _gloss(
            'cili.i1', 'fi', 'kesytetty Canis-suvun jäsen'))
        assert found['fi'] == ['kesytetty Canis-suvun jäsen']
        assert builder.english_glosses_dropped == {}

    def test_one_word_copy_kept(self, builder, tmp_path):
        """'dog' is i1's English gloss in BASE_CONCEPTS, but one word is kept."""
        found = _merge(builder, tmp_path, _gloss('cili.i1', 'fi', 'dog'))
        assert found['fi'] == ['dog']

    def test_english_gloss_of_other_concept_kept(self, builder, tmp_path):
        """Only the English definitions of the same concept count."""
        found = _merge(builder, tmp_path, _gloss(
            'cili.i2', 'fi', 'a domesticated member of the genus Canis'))
        assert 'fi' not in found  # i1 has no Finnish definition
        assert builder.english_glosses_dropped == {}

    def test_regional_english_kept(self, builder, tmp_path):
        found = _merge(builder, tmp_path, _gloss(
            'cili.i1', 'en-GB', 'a domesticated member of the genus Canis'),
            language='en-GB')
        assert found['en-GB'] == ['a domesticated member of the genus Canis']

    def test_copy_does_not_add_provenance(self, builder, tmp_path):
        """A dropped copy must not credit its wordnet with another definition."""
        _merge(builder, tmp_path,
               _gloss('cili.i1', 'fi', 'kesytetty Canis-suvun jäsen')
               + _gloss('cili.i1', 'fi', 'a domesticated member of the genus Canis'))
        builder._flush_all()
        n = builder.prov_cur.execute("""
            SELECT COUNT(*) FROM provenance p
            JOIN prov_tables t ON t.rowid = p.table_rowid
            JOIN prov_resources r ON r.rowid = p.resource_rowid
            WHERE t.name = 'definitions' AND r.code = 'wn-fi'
        """).fetchone()[0]
        assert n == 1
