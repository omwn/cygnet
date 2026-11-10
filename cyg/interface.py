from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

POS = Literal["noun", "verb", "adj", "adv", "adp", "unk", "conj", "nref"]

class Cygnet(ABC):
    """Top-level access point for querying the Cygnet database."""

    @abstractmethod
    def concepts(self,
                form: str | None = None,
                langs: list[str] | str | None = None,
                pos : POS | None = None
                ) -> list[Concept]:
        """Returns all concepts, optionally filtered by wordform, language(s), and/or part of speech."""

    @abstractmethod
    def concept(self,
                ili: str
                ) -> Concept | None:
        """Returns the concept identified by the given ILI (Interlingual Index) string, or None if not found."""

    @abstractmethod
    def senses(self,
               form: str | None = None,
               langs: list[str] | str | None = None
               ) -> list[Sense]:
        """Returns all senses, optionally filtered by wordform and/or language(s)."""

    @abstractmethod
    def lexemes(self,
                form: str | None = None,
                langs: list[str] | str | None = None
                ) -> list[Lexeme]:
        """Returns all lexemes, optionally filtered by wordform and/or language(s)."""

    @abstractmethod
    def langs(self) -> list[str]:
        """Returns the list of language codes available in the database."""

class Concept(ABC):
    """A language-independent concept, corresponding to a synset in traditional Wordnet releases."""

    @abstractmethod
    def definition(self,
                   lang: str = "en"
                   ) -> AnnotatedString | None:
        """Returns the definition of this concept in the given language, or None if unavailable."""

    @abstractmethod
    def pos(self) -> POS:
        """Returns the part of speech (ontological category) of this concept."""

    @abstractmethod
    def index(self) -> str:
        """Returns the unique identifier (ILI) for this concept."""

    @abstractmethod
    def senses(self,
               lang: str | None = None
               ) -> list[Sense]:
        """Returns all senses linked to this concept, optionally filtered by language."""

    @abstractmethod
    def lexemes(self,
                lang: str | None = None
                ) -> list[Lexeme]:
        """Returns all lexemes linked to senses of this concept, optionally filtered by language."""

    @abstractmethod
    def hypernyms(self) -> list[Concept]:
        """Returns concepts that are connected to this concept by a hypernym relation."""

    @abstractmethod
    def hyponyms(self) -> list[Concept]:
        """Returns concepts that are connected to this concept by a hyponymy relation."""

    @abstractmethod
    def meronyms(self) -> list[Concept]:
        """Returns concepts that are connected to this concept by a meronymy relation."""

    @abstractmethod
    def holonyms(self) -> list[Concept]:
        """Returns concepts that are connected to this concept by a holonymy relation."""

class Sense(ABC):
    """A pairing of a lexeme with a concept, representing one meaning of a word."""

    @abstractmethod
    def index(self) -> str:
        """Returns the unique identifier for this sense."""

    @abstractmethod
    def examples(self) -> list[AnnotatedString]:
        """Returns usage examples illustrating this sense."""

    @abstractmethod
    def concept(self) -> Concept:
        """Returns the concept associated with this sense (the signified)."""

    @abstractmethod
    def lexeme(self) -> Lexeme:
        """Returns the lexeme associated with this sense (the signifier)."""

    @abstractmethod
    def lang(self) -> str:
        """Returns the language code of this sense's lexeme."""

class Lexeme(ABC):
    """A wordform in a specific language, with all possible inflections."""

    @abstractmethod
    def index(self) -> str:
        """Returns the unique identifier for this lexeme."""

    @abstractmethod
    def lang(self) -> str:
        """Returns the language code of this lexeme."""

    @abstractmethod
    def lemma(self) -> str:
        """Returns the canonical form of this lexeme."""

    @abstractmethod
    def all_forms(self) -> list[str]:
        """Returns all inflected forms of this lexeme (including the lemma)."""

    @abstractmethod
    def senses(self) -> list[Sense]:
        """Returns every sense that references this lexeme."""

    @abstractmethod
    def concepts(self) -> list[Concept]:
        """Returns every concept linked to this lexeme via a sense."""

class AnnotatedString(ABC):
    """A string of text with sense annotations."""

    @abstractmethod
    def text(self) -> str:
        """Return the plain text content of this string."""

    @abstractmethod
    def lang(self) -> str:
        """Return the language code of the language this string is in."""

    @abstractmethod
    def sense_offsets(self) -> list[tuple[Sense, int, int]]:
        """Return a list of (sense, start, end) tuples marking annotated spans within the text."""
