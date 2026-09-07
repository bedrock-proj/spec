"""Common entity contract and unified logical-reference catalogs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping, cast

from engine.reference import (
    QualifiedReference,
    Reference,
    ReferenceIndex,
    register_reference,
)


class EntityDisplayStyle(StrEnum):
    TEXT = "text"
    CODE = "code"


class Entity:
    """Nominal parent of every provider-local referencable domain object."""

    __slots__ = ()

    reference: Reference["Entity"]
    source: Path


@dataclass(frozen=True, slots=True)
class EntityPresentation:
    """Provider-owned default spelling used by explicit public projections."""

    display: str
    display_style: EntityDisplayStyle = EntityDisplayStyle.TEXT


@dataclass(frozen=True, slots=True)
class EntityCatalog:
    references: ReferenceIndex[Entity]
    presentations: Mapping[Reference[Entity], EntityPresentation]


    def __post_init__(self) -> None:
        if not isinstance(self.references, ReferenceIndex):
            object.__setattr__(self, "references", ReferenceIndex(self.references))
        object.__setattr__(self, "presentations", MappingProxyType(dict(self.presentations)))
        if set(self.references) != set(self.presentations):
            raise ValueError("entity presentations and references must have identical membership")
        for reference, entity in self.references.items():
            if not isinstance(entity, Entity) or not isinstance(entity.source, Path):
                raise TypeError("entity catalog members require Entity identity and source provenance")
            if entity.reference != reference:
                raise ValueError("entity index key differs from member identity")
            if not isinstance(self.presentations[reference], EntityPresentation):
                raise TypeError("entity presentation must be an EntityPresentation")

    def resolve(self, reference: Reference[Entity]) -> Entity:
        return self.references.resolve(reference)

    def presentation(self, reference: Reference[object]) -> EntityPresentation:
        normalized = cast(Reference[Entity], reference)
        self.resolve(normalized)
        return self.presentations[normalized]




@dataclass(frozen=True, slots=True)
class EntityDependency:
    """One provider-owned authored or structured relationship."""

    source: Reference[object]
    target: QualifiedReference[object]
    kind: str


def create_entity_catalog(
    entries: Iterable[tuple[Entity, str, EntityDisplayStyle] | tuple[Entity, str]],
) -> "EntityCatalog":
    references = {}
    presentations: dict[Reference[Entity], EntityPresentation] = {}
    for entry in entries:
        entity, display = entry[:2]
        style = entry[2] if len(entry) == 3 else EntityDisplayStyle.TEXT
        if not isinstance(entity, Entity):
            raise TypeError("entity catalog entries must inherit Entity")
        if not isinstance(entity.reference, Reference):
            raise TypeError("entity reference must be a Reference")
        if not isinstance(entity.source, Path):
            raise TypeError("entity source must be a Path")
        reference = cast(Reference[Entity], entity.reference)
        register_reference(references, reference, entity)
        presentations[reference] = EntityPresentation(display, style)
    return EntityCatalog(
        references=ReferenceIndex(references),
        presentations=MappingProxyType(presentations),
    )
