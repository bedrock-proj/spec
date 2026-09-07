"""Semantic dependency graph collected from authored strings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from engine.reference import Reference
from engine.syntax.semantic_text import (
    EntityReferenceText,
    SemanticText,
    TermReferenceText,
)


@dataclass(frozen=True, slots=True)
class DependencyEdge:
    source: Reference[object] | Path
    target: Reference[object]
    kind: str
    source_path: Path
    offset: int


@dataclass(frozen=True, slots=True)
class DependencyGraph:
    edges: tuple[DependencyEdge, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "edges", tuple(self.edges))








def dependency_edges(
    source: Reference[object] | Path, text: SemanticText
) -> tuple[DependencyEdge, ...]:
    return tuple(
        DependencyEdge(
            source,
            part.reference,
            "reference" if isinstance(part, EntityReferenceText) else "term",
            text.origin.source,
            part.start,
        )
        for part in text.parts
        if isinstance(part, (EntityReferenceText, TermReferenceText))
    )
