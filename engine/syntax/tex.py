"""Offset-preserving lexical view of authored TeX."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SourceSpan:
    source: Path
    start: int
    end: int

    def __post_init__(self):
        if self.start < 0 or self.end < self.start:
            raise ValueError("source span must be a nonnegative ordered interval")


@dataclass(frozen=True, slots=True)
class SourceDirective:
    span: SourceSpan
    name: str
    arguments: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, "arguments", tuple(self.arguments))


def mask_tex_code(text: str) -> str:
    result = list(text)
    index = 0
    while index < len(text):
        if text[index] == "\\":
            if index + 1 < len(text) and not text[index + 1].isalpha():
                result[index] = " "
                if text[index + 1] != "\n":
                    result[index + 1] = " "
            index += min(2, len(text) - index)
        elif text[index] == "%":
            while index < len(text) and text[index] != "\n":
                result[index] = " "
                index += 1
        else:
            index += 1
    return "".join(result)
