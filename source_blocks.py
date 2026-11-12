"""Source ranges returned by the fixed-format splitters."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceReference:
    keyword: str
    title: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class SourceBlock:
    keyword: str
    title: str
    kind: str
    start_line: int
    end_line: int
    statement_line: int | None = None
    parent_line: int = 0
    context: tuple[SourceReference, ...] = ()
