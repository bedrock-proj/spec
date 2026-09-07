"""Structured diagnostics shared by ISA authoring commands."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    NOTE = "note"


@dataclass(frozen=True, slots=True)
class RelatedLocation:
    source: Path
    message: str
    path: tuple[str | int, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", tuple(self.path))


@dataclass(frozen=True, slots=True)
class Diagnostic:
    severity: Severity
    code: str
    source: Path
    message: str
    path: tuple[str | int, ...] = ()
    related: tuple[RelatedLocation, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", tuple(self.path))
        object.__setattr__(self, "related", tuple(self.related))

    def location(self) -> str:
        return _location(self.source, self.path)


@dataclass(frozen=True, slots=True)
class DiagnosticBag(Sequence[Diagnostic]):
    """An immutable ordered collection of completed diagnostics."""

    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))

    @property
    def has_errors(self) -> bool:
        return any(item.severity is Severity.ERROR for item in self.diagnostics)

    def __getitem__(self, index: int | slice) -> Diagnostic | tuple[Diagnostic, ...]:
        return self.diagnostics[index]

    def __iter__(self) -> Iterator[Diagnostic]:
        return iter(self.diagnostics)

    def __len__(self) -> int:
        return len(self.diagnostics)


def _location(source: Path, path: tuple[str | int, ...]) -> str:
    suffix = "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}" for part in path
    )
    return f"{source}{suffix}"


def render_diagnostics_text(bag: DiagnosticBag) -> str:
    blocks: list[str] = []
    for item in bag:
        lines = [
            f"{item.location()}: {item.severity.value}[{item.code}]: {item.message}"
        ]
        lines.extend(
            f"  related: {_location(related.source, related.path)}: {related.message}"
            for related in item.related
        )
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def render_diagnostics_json(bag: DiagnosticBag) -> str:
    return json.dumps(
        [
            {
                "severity": item.severity.value,
                "code": item.code,
                "source": str(item.source),
                "path": list(item.path),
                "message": item.message,
                "related": [
                    {
                        "source": str(related.source),
                        "path": list(related.path),
                        "message": related.message,
                    }
                    for related in item.related
                ],
            }
            for item in bag
        ],
        indent=2,
        sort_keys=True,
    )


def _error(
    code: str,
    source: Path,
    message: str,
    *path: str | int,
    related: tuple[RelatedLocation, ...] = (),
) -> Diagnostic:
    return Diagnostic(Severity.ERROR, code, source, message, tuple(path), related)
