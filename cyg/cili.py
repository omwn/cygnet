"""The Collaborative Interlingual Index as the pipeline needs it: which ILIs
exist, the part of speech of each, and (for the merge) the rows of the
``cili.tsv`` release file -- status, origin in PWN 3.0 and definition."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from cyg.lmf import collapse_whitespace, pos_label


@dataclass(frozen=True)
class CiliRow:
    """One ILI of the ``cili.tsv`` release (bin/cili.tsv)."""

    ili: str
    status: str
    superseded_by: str
    resource: str      # 'pwn'
    version: str       # '3.0'
    original_id: str   # PWN synset id, e.g. '00001740-a'
    pos: str           # POS label, from the id's trailing letter
    definition: str


def parse_origin(origin: str) -> tuple[str, str, str, str]:
    """Split an origin such as 'pwn-3.0:00001740-a'.

    Args:
        origin: The origin column of cili.tsv.

    Returns:
        (resource, version, original id, POS label).

    Raises:
        ValueError: If the origin is not of that form.
    """
    try:
        code, original_id = origin.strip().split(':')
        resource, version = code.split('-')
    except ValueError as exc:
        raise ValueError(f'unexpected CILI origin {origin!r}') from exc
    return resource, version, original_id, pos_label(original_id[-1].lower())


def read_cili_tsv(path: Path) -> Iterator[CiliRow]:
    """The rows of cili.tsv (columns ili_id, status, superseded_by, origin,
    definition), definitions with their whitespace collapsed.

    Args:
        path: The TSV file.

    Yields:
        One CiliRow per line.
    """
    with open(path, encoding='utf-8', newline='') as f:
        for row in csv.DictReader(f, delimiter='\t'):
            resource, version, original_id, pos = parse_origin(row['origin'])
            yield CiliRow(row['ili_id'], row.get('status', ''), row.get('superseded_by', ''),
                          resource, version, original_id, pos,
                          collapse_whitespace(row.get('definition')))


class CiliIndex:
    """ILI id ('i123') -> part-of-speech label ('NOUN'), or None when unknown,
    read from the cili.tsv release file."""

    def __init__(self, pos_by_ili: dict[str, str | None]) -> None:
        self._pos = pos_by_ili

    @classmethod
    def from_tsv(cls, path: Path) -> CiliIndex:
        """Load the index from the cili.tsv release file.

        Args:
            path: bin/cili.tsv.

        Returns:
            The index.
        """
        return cls({row.ili: row.pos for row in read_cili_tsv(path)})

    def __contains__(self, ili: str) -> bool:
        return ili in self._pos

    def __len__(self) -> int:
        return len(self._pos)

    def pos(self, ili: str) -> str | None:
        """The part of speech recorded for *ili*, or None."""
        return self._pos.get(ili)
