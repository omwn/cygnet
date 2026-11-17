"""Tests for conversion_scripts/4_add_glosstag_to_cili.py, the GlossTag sidecar."""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent / 'conversion_scripts' / '4_add_glosstag_to_cili.py'
_spec = importlib.util.spec_from_file_location('glosstag_sidecar', _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def test_sense_key_to_oewn_id():
    assert _mod.sense_key_to_oewn_id('necessary%3:00:00::') == 'oewn-necessary__3.00.00..'
    assert _mod.sense_key_to_oewn_id('dog%1:05:00::') == 'oewn-dog__1.05.00..'


def test_overlapping():
    assert _mod.overlapping([[0, 3, 'a'], [2, 5, 'b']])
    assert not _mod.overlapping([[0, 3, 'a'], [3, 5, 'b']])


def test_build_sidecar(tmp_path, monkeypatch):
    tsv = tmp_path / 'cili.tsv'
    tsv.write_text('ili_id\tstatus\tsuperseded_by\torigin\tdefinition\n'
                   'i3\t1\t\tpwn-3.0:02084071-n\ta member of the genus Canis\n'
                   'i4\t1\t\tpwn-3.0:00000001-n\tanother\n', encoding='utf-8')
    monkeypatch.setattr(_mod, 'synset_name_to_pwn_id',
                        {'dog.n.01': '02084071-n', 'other.n.01': '00000001-n',
                         'gone.n.01': '99999999-n'}.__getitem__)
    definitions = {
        'dog.n.01': {'string': 'a member of the  genus Canis',
                     'annotations': [[14, 19, 'genus%1:14:00::'], [0, 1, 'a%1:00:00::']]},
        'other.n.01': {'string': 'another', 'annotations': [[0, 3, 'x%1:00:00::'], [2, 5, 'y%1:00:00::']]},
        'gone.n.01': {'string': 'x', 'annotations': [[0, 1, 'z%1:00:00::']]},
    }
    sidecar, stats = _mod.build_sidecar(definitions, tsv)
    assert sidecar == {'i3': [[0, 1, 'oewn-a__1.00.00..'], [14, 19, 'oewn-genus__1.14.00..']]}
    assert stats['definitions'] == 1 and stats['annotations'] == 2
    assert stats['overlapping'] == 1 and stats['not in cili'] == 1 and stats['gloss differs'] == 0
