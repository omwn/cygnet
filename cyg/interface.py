from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List, Tuple, Literal, Optional

POS = Literal["noun", "verb", "adj", "adv", "adp", "unk", "conj", "nref"]

class Cygnet(ABC):
    """Top-level access point for querying the Cygnet database."""

    @abstractmethod
    def concepts(self,
                form: Optional[str] = None,
                langs: List[str] | str | None = None,
                pos : Optional[POS] = None
                ) -> List[Concept]:
        """Returns all concepts, optionally filtered by wordform, language(s), and/or part of speech."""
        pass

    @abstractmethod
    def concept(self,
                ili: str
                ) -> Optional[Concept]:
        """Returns the concept identified by the given ILI (Interlingual Index) string, or None if not found."""
        pass

    @abstractmethod
    def senses(self,
               form: Optional[str] = None,
               langs: List[str] | str | None = None
               ) -> List[Sense]:
        """Returns all senses, optionally filtered by wordform and/or language(s)."""
        pass

    @abstractmethod
    def lexemes(self,
                form: Optional[str] = None,
                langs: List[str] | str | None = None
                ) -> List[Lexeme]:
        """Returns all lexemes, optionally filtered by wordform and/or language(s)."""
        pass

    @abstractmethod
    def langs(self) -> List[str]:
        """Returns the list of language codes available in the database."""
        pass

class Concept(ABC):
    """A language-independent concept, corresponding to a synset in traditional Wordnet releases."""

    @abstractmethod
    def definition(self,
                   lang: str = "en"
                   ) -> Optional[AnnotatedString]:
        """Returns the definition of this concept in the given language, or None if unavailable."""
        pass

    @abstractmethod
    def pos(self) -> POS:
        """Returns the part of speech (ontological category) of this concept."""
        pass

    @abstractmethod
    def index(self) -> str:
        """Returns the unique identifier (ILI) for this concept."""
        pass

    @abstractmethod
    def senses(self,
               lang: Optional[str] = None
               ) -> List[Sense]:
        """Returns all senses linked to this concept, optionally filtered by language."""
        pass

    @abstractmethod
    def lexemes(self,
                lang: Optional[str] = None
                ) -> List[Lexeme]:
        """Returns all lexemes linked to senses of this concept, optionally filtered by language."""
        pass

    @abstractmethod
    def hypernyms(self) -> List[Concept]:
        """Returns concepts that are connected to this concept by a hypernym relation."""
        pass

    @abstractmethod
    def hyponyms(self) -> List[Concept]:
        """Returns concepts that are connected to this concept by a hyponymy relation."""
        pass

    @abstractmethod
    def meronyms(self) -> List[Concept]:
        """Returns concepts that are connected to this concept by a meronymy relation."""
        pass

    @abstractmethod
    def holonyms(self) -> List[Concept]:
        """Returns concepts that are connected to this concept by a holonymy relation."""
        pass

class Sense(ABC):
    """A pairing of a lexeme with a concept, representing one meaning of a word."""

    @abstractmethod
    def index(self) -> str:
        """Returns the unique identifier for this sense."""
        pass

    @abstractmethod
    def examples(self) -> List[AnnotatedString]:
        """Returns usage examples illustrating this sense."""
        pass

    @abstractmethod
    def concept(self) -> Concept:
        """Returns the concept associated with this sense (the signified)."""
        pass

    @abstractmethod
    def lexeme(self) -> Lexeme:
        """Returns the lexeme associated with this sense (the signifier)."""
        pass

    @abstractmethod
    def lang(self) -> str:
        """Returns the language code of this sense's lexeme."""
        pass

class Lexeme(ABC):
    """A wordform in a specific language, with all possible inflections."""

    @abstractmethod
    def index(self) -> str:
        """Returns the unique identifier for this lexeme."""
        pass

    @abstractmethod
    def lang(self) -> str:
        """Returns the language code of this lexeme."""
        pass

    @abstractmethod
    def lemma(self) -> str:
        """Returns the canonical form of this lexeme."""
        pass

    @abstractmethod
    def all_forms(self) -> List[str]:
        """Returns all inflected forms of this lexeme (including the lemma)."""
        pass

    @abstractmethod
    def senses(self) -> List[Sense]:
        """Returns every sense that references this lexeme."""
        pass

    @abstractmethod
    def concepts(self) -> List[Concept]:
        """Returns every concept linked to this lexeme via a sense."""
        pass

class AnnotatedString(ABC):
    """A string of text with sense annotations."""

    @abstractmethod
    def text(self) -> str:
        """Return the plain text content of this string."""
        pass

    @abstractmethod
    def lang(self) -> str:
        """Return the language code of the language this string is in."""
        pass

    @abstractmethod
    def sense_offsets(self) -> List[Tuple[Sense, int, int]]:
        """Return a list of (sense, start, end) tuples marking annotated spans within the text."""
        pass
