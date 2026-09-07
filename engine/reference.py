"""Logical references shared by ISA definition formats."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Generic, TypeVar

_OWNER_RE = re.compile(r"base|[A-Z][A-Z0-9_]*")
_SEGMENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_DOMAIN_RE = re.compile(r"[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)*")
_T_co = TypeVar("_T_co", covariant=True)
_T = TypeVar("_T")


class ReferenceError(ValueError):
    """Base class for invalid logical reference operations."""


class DuplicateReferenceError(ReferenceError):
    """Raised when an index receives the same logical reference twice."""


class UnknownReferenceError(ReferenceError):
    """Raised when an index cannot resolve a logical reference."""


@dataclass(frozen=True, order=True, slots=True)
class Reference(Generic[_T_co]):
    """A ``<owner>(.<path>)*.<element>`` logical reference."""

    owner: str
    path: tuple[str, ...]
    element: str

    def __post_init__(self) -> None:
        if not isinstance(self.owner, str) or not _OWNER_RE.fullmatch(self.owner):
            raise ReferenceError(
                "reference owner must be 'base' or an uppercase extension ID"
            )
        if not isinstance(self.path, tuple):
            object.__setattr__(self, "path", tuple(self.path))
        for segment in (*self.path, self.element):
            if not isinstance(segment, str) or not _SEGMENT_RE.fullmatch(segment):
                raise ReferenceError(f"invalid reference segment {segment!r}")

    @classmethod
    def parse(cls, value: str | "Reference[_T_co]") -> "Reference[_T_co]":
        """Parse a dotted reference, returning existing references unchanged."""

        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise ReferenceError("reference must be a string")
        parts = value.split(".")
        if len(parts) < 2:
            raise ReferenceError("reference must contain an owner and an element")
        return cls(owner=parts[0], path=tuple(parts[1:-1]), element=parts[-1])

    def __str__(self) -> str:
        raise TypeError("Reference does not provide a string representation")


@dataclass(frozen=True, order=True, slots=True)
class QualifiedReference(Generic[_T_co]):
    """A workspace domain paired with one provider-local reference."""

    domain: str
    local: Reference[_T_co]

    def __post_init__(self) -> None:
        if not isinstance(self.domain, str) or not _DOMAIN_RE.fullmatch(self.domain):
            raise ReferenceError(
                "reference domain must be a lowercase dotted identifier"
            )
        if not isinstance(self.local, Reference):
            raise ReferenceError("qualified reference local value must be a Reference")

    @classmethod
    def parse(
        cls,
        value: str | "QualifiedReference[_T_co]",
        *,
        current_domain: str | None = None,
    ) -> "QualifiedReference[_T_co]":
        """Parse ``<domain>:<local>`` or qualify a local reference in context."""

        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise ReferenceError("qualified reference must be a string")
        if ":" in value:
            domain, local = value.split(":", 1)
        else:
            if current_domain is None:
                raise ReferenceError(
                    "local reference requires an explicit current domain"
                )
            domain, local = current_domain, value
        if not domain or not local or ":" in local:
            raise ReferenceError(f"invalid qualified reference {value!r}")
        return cls(domain, Reference.parse(local))

    def __str__(self) -> str:
        raise TypeError("QualifiedReference does not provide a string representation")


@dataclass(frozen=True, slots=True, eq=False)
class ReferenceIndex(Mapping[Reference[_T], _T], Generic[_T]):
    """A filesystem-independent mapping from logical references to values."""

    _entries: Mapping[Reference[_T], _T]


    def __init__(
        self, entries: Mapping[Reference[_T], _T] = MappingProxyType({})
    ) -> None:
        values = dict(entries)
        if any(not isinstance(reference, Reference) for reference in values):
            raise ReferenceError("reference index keys must be Reference values")
        object.__setattr__(self, "_entries", MappingProxyType(values))

    def resolve(self, reference: Reference[_T]) -> _T:
        """Resolve a logical reference or raise ``UnknownReferenceError``."""

        return resolve_reference(self._entries, reference)

    def __getitem__(self, reference: Reference[_T]) -> _T:
        """Look up a mapping key, raising ``KeyError`` if it is absent."""

        if not isinstance(reference, Reference):
            raise ReferenceError("reference index lookup requires a Reference")
        return self._entries[reference]

    def __contains__(self, reference: object) -> bool:
        if not isinstance(reference, Reference):
            raise ReferenceError("reference index membership requires a Reference")
        return reference in self._entries

    def __iter__(self) -> Iterator[Reference[_T]]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)


def register_reference(
    entries: dict[Reference[_T], _T], reference: Reference[_T], value: _T
) -> Reference[_T]:
    """Add a unique reference while constructing an owner-local index."""
    if not isinstance(reference, Reference):
        raise ReferenceError("reference index registration requires a Reference")
    if reference in entries:
        raise DuplicateReferenceError("duplicate reference")
    entries[reference] = value
    return reference


def resolve_reference(
    entries: Mapping[Reference[_T], _T], reference: Reference[_T]
) -> _T:
    if not isinstance(reference, Reference):
        raise ReferenceError("reference index resolution requires a Reference")
    try:
        return entries[reference]
    except KeyError as error:
        raise UnknownReferenceError("unknown reference") from error
