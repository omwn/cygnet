"""Tests for cyg/linker.py beyond the matching ones in test_example_matching."""

from cyg import linker as linker_module
from cyg.linker import ExampleLinker


def test_doc_cache_bounded(monkeypatch):
    """The spaCy document cache drops the least recently used document."""
    monkeypatch.setattr(linker_module, "DOC_CACHE_SIZE", 2)
    linker = ExampleLinker("en")
    linker.nlp = lambda text: object()
    first = linker.get_doc("a")
    linker.get_doc("b")
    assert linker.get_doc("a") is first  # cache hit; "b" is now the oldest

    linker.get_doc("c")

    assert list(linker.doc_cache) == ["a", "c"]
