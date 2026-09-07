"""Value object for the Bedrock binary encoding metasyntax."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from engine.syntax.metasyntax import Metasyntax, MetasyntaxError

_PATTERN_RE = re.compile(r"[01a-z]+")
_FIELD_RE = re.compile(r"[a-z]")


class EncodingMetasyntaxError(MetasyntaxError):
    """Raised when an encoding pattern or encoded value is invalid."""


@dataclass(frozen=True, order=True, slots=True)
class EncodingMetasyntax(Metasyntax):
    """A normalized MSB-first pattern of fixed bits and field markers."""

    def _validate(self) -> None:
        if not isinstance(self.code, str) or not _PATTERN_RE.fullmatch(self.code):
            raise EncodingMetasyntaxError(
                "encoding pattern must contain only 0, 1, and lowercase field characters"
            )

    @classmethod
    def _normalize(cls, value: object) -> str:
        if isinstance(value, str):
            return value
        if not isinstance(value, Sequence) or not value:
            raise EncodingMetasyntaxError(
                "encoding pattern must be a string or a non-empty sequence of strings"
            )
        if any(not isinstance(chunk, str) or not chunk for chunk in value):
            raise EncodingMetasyntaxError(
                "encoding pattern chunks must be non-empty strings"
            )
        return "".join(value)

    @property
    def bit_width(self) -> int:
        return len(self.code)

    @property
    def fields(self) -> frozenset[str]:
        return frozenset(
            character for character in self.code if _FIELD_RE.fullmatch(character)
        )

    def field_width(self, field: str) -> int:
        """Return the number of bits occupied by ``field``."""

        if not isinstance(field, str) or not _FIELD_RE.fullmatch(field):
            raise EncodingMetasyntaxError("field must be one lowercase character")
        return self.code.count(field)

    def field_positions(self, field: str) -> tuple[int, ...]:
        """Return opcode bit positions in field occurrence order, MSB first."""

        self.field_width(field)
        return tuple(
            len(self.code) - index - 1
            for index, character in enumerate(self.code)
            if character == field
        )

    @property
    def fixed_mask(self) -> int:
        mask = 0
        for character in self.code:
            mask = (mask << 1) | int(character in "01")
        return mask

    @property
    def fixed_value(self) -> int:
        value = 0
        for character in self.code:
            value = (value << 1) | int(character == "1")
        return value

    def matches(self, value: int) -> bool:
        """Return whether an integer encoding satisfies all fixed bits."""

        self._validate_value(value)
        return value & self.fixed_mask == self.fixed_value

    def overlaps(self, other: "EncodingMetasyntax") -> bool:
        """Return whether two patterns can match one common encoding."""

        other = self.parse(other)
        if self.bit_width != other.bit_width:
            return False
        common_mask = self.fixed_mask & other.fixed_mask
        return (self.fixed_value ^ other.fixed_value) & common_mask == 0

    def extract(self, value: int, field: str) -> int:
        """Extract a possibly non-contiguous field in pattern occurrence order."""

        self._validate_value(value)
        if self.field_width(field) == 0:
            raise EncodingMetasyntaxError(f"pattern has no field {field!r}")
        result = 0
        for index, character in enumerate(self.code):
            if character == field:
                result = (result << 1) | ((value >> (self.bit_width - index - 1)) & 1)
        return result

    def _validate_value(self, value: int) -> None:
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            or value >= 1 << self.bit_width
        ):
            raise EncodingMetasyntaxError(
                f"encoded value must be an unsigned {self.bit_width}-bit integer"
            )
