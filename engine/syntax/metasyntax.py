"""Common API for validated metasyntax value objects."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, TypeVar

_MetasyntaxT = TypeVar("_MetasyntaxT", bound="Metasyntax")


class MetasyntaxError(ValueError):
    """Base error for malformed metasyntax values."""


@dataclass(frozen=True, slots=True)
class Metasyntax(ABC):
    """Immutable source spelling with construction-time validation."""

    code: str

    def __post_init__(self) -> None:
        self.validate()

    @classmethod
    def parse(cls: type[_MetasyntaxT], value: Any) -> _MetasyntaxT:
        """Normalize ``value`` and return one validated instance."""

        if isinstance(value, cls):
            return value
        return cls(cls._normalize(value))

    @classmethod
    def _normalize(cls, value: Any) -> Any:
        return value

    def validate(self) -> None:
        """Validate the stored spelling according to the concrete grammar."""

        self._validate()

    @abstractmethod
    def _validate(self) -> None:
        """Implement grammar-specific validation and parsing."""

    def __str__(self) -> str:
        return self.code
