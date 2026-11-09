"""The Collaborative Interlingual Index as the pipeline needs it: which ILIs
exist, and the part of speech of each."""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from cyg.multilex import open_xml


class CiliIndex:
    """ILI id ('i123') -> part-of-speech label ('NOUN'), or None when unknown.

    Read from the Cygnet XML CILI file (``cili-1.0.xml``, a ConceptLayer of
    ``cili.<ili>`` concepts) or from a two-column TSV (ili, pos).
    """

    def __init__(self, pos_by_ili: dict[str, str | None]) -> None:
        self._pos = pos_by_ili

    @classmethod
    def from_file(cls, path: Path) -> CiliIndex:
        """Load the index from whichever format *path* is in.

        Args:
            path: ``cili-1.0.xml`` (Cygnet XML) or ``cili.tsv``.

        Returns:
            The index.
        """
        if path.suffix == '.tsv':
            return cls.from_tsv(path)
        return cls.from_cygnet_xml(path)

    @classmethod
    def from_cygnet_xml(cls, path: Path) -> CiliIndex:
        """Load the index from the Cygnet XML CILI file."""
        pos_by_ili: dict[str, str | None] = {}
        with open_xml(path) as f:
            for _event, elem in etree.iterparse(f, events=('end',), tag='Concept'):
                cid = elem.get('id') or ''
                if cid.startswith('cili.'):
                    pos_by_ili[cid[5:]] = elem.get('ontological_category')
                elem.clear()
        return cls(pos_by_ili)

    @classmethod
    def from_tsv(cls, path: Path) -> CiliIndex:
        """Load the index from a TSV whose first two columns are ili and POS."""
        pos_by_ili: dict[str, str | None] = {}
        with open(path, encoding='utf-8') as f:
            for line in f:
                if not line.strip() or line.startswith('#'):
                    continue
                ili, pos, *_rest = line.rstrip('\n').split('\t')
                pos_by_ili[ili] = pos or None
        return cls(pos_by_ili)

    def __contains__(self, ili: str) -> bool:
        return ili in self._pos

    def __len__(self) -> int:
        return len(self._pos)

    def pos(self, ili: str) -> str | None:
        """The part of speech recorded for *ili*, or None."""
        return self._pos.get(ili)
