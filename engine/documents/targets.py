"""Canonical entities explicitly selected as public document targets."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable

from engine.entity import Entity, EntityCatalog
from engine.reference import Reference, ReferenceIndex, UnknownReferenceError


@dataclass(frozen=True, slots=True)
class PublicTargetCatalog:
    selected: ReferenceIndex[Entity]
    required_references: frozenset[Reference]
    _unselected_references: frozenset[Reference]

    def __post_init__(self) -> None:
        object.__setattr__(self, "selected", ReferenceIndex(self.selected))
        object.__setattr__(self, "required_references", frozenset(self.required_references))
        object.__setattr__(self, "_unselected_references", frozenset(self._unselected_references))
        if not self.required_references <= set(self.selected):
            raise ValueError("public target requirements include unselected references")
        if self._unselected_references & set(self.selected):
            raise ValueError("selected public targets also occur in the unselected set")
        for reference, entity in self.selected.items():
            if not isinstance(entity, Entity) or entity.reference != reference:
                raise ValueError("public target key does not match its entity identity")

    @classmethod
    def create(
        cls,
        entities: EntityCatalog,
        selected_blocks: Iterable[Iterable[Reference]],
        required_references: Iterable[Reference],
    ) -> PublicTargetCatalog:
        selected = {}
        for declarations in selected_blocks:
            for reference in declarations:
                entity = entities.resolve(reference)
                if reference in selected:
                    raise ValueError(f"duplicate public target declaration: {reference!r}")
                selected[reference] = entity
        required = frozenset(required_references)
        for reference in required:
            entities.resolve(reference)
            if reference not in selected:
                raise ValueError(f"entity {reference!r} is referenced but has no public target")
        return cls(
            ReferenceIndex(selected), required,
            frozenset(entities.references) - frozenset(selected),
        )

    def resolve(self, reference: Reference) -> Entity:
        if reference in self.selected:
            return self.selected.resolve(reference)
        if reference in self._unselected_references:
            raise ValueError(f"entity {reference!r} is not selected in this public projection")
        raise UnknownReferenceError(f"unknown public entity reference: {reference!r}")

    def contains(self, reference: Reference) -> bool:
        return reference in self.selected
