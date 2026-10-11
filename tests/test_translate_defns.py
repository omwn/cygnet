"""Unit tests for conversion_scripts/5_translate_defns.py."""

import importlib.util
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

# 5_translate_defns.py imports argostranslate at module level, which is an
# optional dependency (the "translate" extra — see pyproject.toml) only
# installed when a build explicitly requests it. Skip this whole module
# rather than erroring out the entire test session's collection when it's
# absent, which is the default for a plain `build.sh` run.
pytest.importorskip('argostranslate')

# Load module from numerically-prefixed filename
_script = Path(__file__).parent.parent / 'conversion_scripts' / '5_translate_defns.py'
_spec = importlib.util.spec_from_file_location('translate_defns', _script)
_mod = importlib.util.module_from_spec(_spec)
sys.modules['translate_defns'] = _mod
_spec.loader.exec_module(_mod)

main = _mod.main
create_xml_from_translations = _mod.create_xml_from_translations


def _stub_pipeline(monkeypatch, glosses, by_language):
    """Mock every main() dependency except translate_language_batch."""
    monkeypatch.setattr(_mod, 'extract_glosses', lambda: glosses)
    monkeypatch.setattr(_mod, 'get_already_translated', lambda: set())
    monkeypatch.setattr(_mod, 'filter_pending_glosses', lambda g, t: g)
    monkeypatch.setattr(_mod, 'group_by_language', lambda g: by_language)
    monkeypatch.setattr(_mod, 'create_xml_from_translations', lambda: None)


class TestMainLanguageIsolation:
    """A single language's translate_language_batch failure must not abort
    the rest of what's otherwise a multi-hour run — see the try/except
    added around that call in main()'s loop.
    """

    def test_continues_past_a_failing_language(self, monkeypatch, capsys):
        glosses = [
            {'definiendum_id': 'cili.i1', 'language': 'bn', 'definition': 'x'},
            {'definiendum_id': 'cili.i2', 'language': 'fr', 'definition': 'y'},
        ]
        _stub_pipeline(monkeypatch, glosses, {
            'bn': [glosses[0]],
            'fr': [glosses[1]],
        })

        calls = []

        def fake_translate(lang_code, lang_glosses, output_file):
            calls.append(lang_code)
            if lang_code == 'bn':
                raise KeyError('packages')

        monkeypatch.setattr(_mod, 'translate_language_batch', fake_translate)

        main()  # must not raise

        assert calls == ['bn', 'fr']
        assert 'bn failed unexpectedly' in capsys.readouterr().out

    def test_multiple_failures_still_processes_all_languages(self, monkeypatch):
        glosses = [{'definiendum_id': f'cili.i{i}', 'language': lang, 'definition': 'x'}
                   for i, lang in enumerate(['a', 'b', 'c'])]
        _stub_pipeline(monkeypatch, glosses, {
            'a': [glosses[0]], 'b': [glosses[1]], 'c': [glosses[2]],
        })

        calls = []

        def fake_translate(lang_code, lang_glosses, output_file):
            calls.append(lang_code)
            raise RuntimeError('simulated MT pipeline failure')

        monkeypatch.setattr(_mod, 'translate_language_batch', fake_translate)

        main()  # must not raise even if every language fails

        assert calls == ['a', 'b', 'c']

    def test_no_pending_glosses_skips_loop_cleanly(self, monkeypatch):
        _stub_pipeline(monkeypatch, [], {})
        called = []
        monkeypatch.setattr(
            _mod, 'translate_language_batch', lambda *a: called.append(a)
        )

        main()

        assert called == []


class TestCreateXmlFromTranslations:
    """The output Gloss must record which language it was translated from,
    so cyg.merge can carry that through to the definitions table — see
    tests/test_pipeline.py::TestTranslatedFromGloss for the merge side.
    """

    def _write_jsonl(self, tmp_path, monkeypatch, records):
        monkeypatch.chdir(tmp_path)
        (tmp_path / 'bin' / 'cygnets_presynth').mkdir(parents=True)
        jsonl = tmp_path / 'bin' / 'translated_glosses.jsonl'
        jsonl.write_text('\n'.join(json.dumps(r) for r in records) + '\n')

    def test_gloss_has_translated_from_and_provenance(self, tmp_path, monkeypatch):
        self._write_jsonl(tmp_path, monkeypatch, [{
            'translated_definition': 'a dog',
            'definiendum_id': 'cili.i1',
            'source_language': 'hu',
            'source_text': 'kutya',
        }])

        create_xml_from_translations()

        root = ET.parse(tmp_path / 'bin' / 'cygnets_presynth' / 'mtg-1.0.xml').getroot()
        gloss = root.find('.//Gloss')
        assert gloss.get('definiendum') == 'cili.i1'
        assert gloss.get('language') == 'en'
        assert gloss.get('translated_from') == 'hu'

        prov = gloss.find('Provenance')
        assert prov is not None
        assert prov.get('resource') == 'mtg'
        assert prov.get('version') == '1.0'

    def test_different_source_languages_recorded_independently(self, tmp_path, monkeypatch):
        self._write_jsonl(tmp_path, monkeypatch, [
            {'translated_definition': 'a dog', 'definiendum_id': 'cili.i1',
             'source_language': 'hu', 'source_text': 'kutya'},
            {'translated_definition': 'a cat', 'definiendum_id': 'cili.i2',
             'source_language': 'ru', 'source_text': 'kot'},
        ])

        create_xml_from_translations()

        root = ET.parse(tmp_path / 'bin' / 'cygnets_presynth' / 'mtg-1.0.xml').getroot()
        by_definiendum = {g.get('definiendum'): g.get('translated_from')
                           for g in root.findall('.//Gloss')}
        assert by_definiendum == {'cili.i1': 'hu', 'cili.i2': 'ru'}
