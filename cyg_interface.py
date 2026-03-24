from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List, Tuple, Literal, Optional

POS = Literal["noun", "verb", "adj", "adv", "adp", "unk", "conj", "nref"]

class Cygnet(ABC):

    @abstractmethod
    def concepts(self,
                form: Optional[str] = None,
                langs: List[str] | str | None = None,
                pos : Optional[POS] = None
                ) -> List[Concept]:
        pass

    @abstractmethod
    def concept(self,
                ili: str
                ) -> Optional[Concept]:
        pass

    @abstractmethod
    def senses(self,
               form: Optional[str] = None,
               langs: List[str] | str | None = None
               ) -> List[Sense]:
        pass

    @abstractmethod
    def lexemes(self,
                form: Optional[str] = None,
                langs: List[str] | str | None = None
                ) -> List[Lexeme]:
        pass

    @abstractmethod
    def langs(self) -> List[str]:
        pass

class Concept(ABC):

    @abstractmethod
    def definition(self,
                   lang: str = "en"
                   ) -> Optional[AnnotatedString]:
        pass

    @abstractmethod
    def pos(self) -> POS:
        pass

    @abstractmethod
    def index(self) -> str:
        pass

    @abstractmethod
    def senses(self,
               lang: Optional[str] = None
               ) -> List[Sense]:
        pass

    @abstractmethod
    def lexemes(self,
                lang: Optional[str] = None
                ) -> List[Lexeme]:
        pass

    @abstractmethod
    def hypernyms(self) -> List[Concept]:
        pass

    @abstractmethod
    def hyponyms(self) -> List[Concept]:
        pass

    @abstractmethod
    def meronyms(self) -> List[Concept]:
        pass

    @abstractmethod
    def holonyms(self) -> List[Concept]:
        pass

class Sense(ABC):

    @abstractmethod
    def index(self) -> str:
        pass

    @abstractmethod
    def examples(self) -> List[AnnotatedString]:
        pass

    @abstractmethod
    def concept(self) -> Concept:
        pass

    @abstractmethod
    def lexeme(self) -> Lexeme:
        pass

    @abstractmethod
    def lang(self) -> str:
        pass

class Lexeme(ABC):

    @abstractmethod
    def index(self) -> str:
        pass

    @abstractmethod
    def lang(self) -> str:
        pass

    @abstractmethod
    def lemma(self) -> str:
        pass

    @abstractmethod
    def all_forms(self) -> List[str]:
        pass

    @abstractmethod
    def senses(self) -> List[Sense]:
        pass

    @abstractmethod
    def concepts(self) -> List[Concept]:
        pass

class AnnotatedString(ABC):

    @abstractmethod
    def text(self) -> str:
        pass

    @abstractmethod
    def lang(self) -> str:
        pass

    @abstractmethod
    def sense_offsets(self) -> List[Tuple[Sense, int, int]]:
        pass
