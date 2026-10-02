"""Unit tests for conversion_scripts/5_translate_defns.py."""

import importlib.util
import sys
from pathlib import Path

# Load module from numerically-prefixed filename
_script = Path(__file__).parent.parent / 'conversion_scripts' / '5_translate_defns.py'
_spec = importlib.util.spec_from_file_location('translate_defns', _script)
_mod = importlib.util.module_from_spec(_spec)
sys.modules['translate_defns'] = _mod
_spec.loader.exec_module(_mod)

main = _mod.main


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
