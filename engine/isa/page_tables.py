"""Structured page-table-entry layouts and field lookup."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from engine.entity import Entity
from engine.reference import Reference, ReferenceIndex, register_reference
from engine.source.yaml import load_schema_yaml


class PageTableEntryLayoutId(StrEnum):
    LEAF = "leaf"
    TABLE_POINTER = "table_pointer"


@dataclass(frozen=True, slots=True)
class PageTableEntryField(Entity):
    """One named field in a page-table-entry layout."""

    reference: Reference["PageTableEntryField"]
    source: Path
    id: str
    lsb: int
    bits: int

    @property
    def msb(self) -> int:
        return self.lsb + self.bits - 1

    def overlaps(self, other: "PageTableEntryField") -> bool:
        return self.lsb <= other.msb and other.lsb <= self.msb


@dataclass(frozen=True, slots=True)
class PageTableEntryLayout:
    """Fields active in one value-selected PTE layout branch."""

    id: PageTableEntryLayoutId
    fields: tuple[PageTableEntryField, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", tuple(self.fields))



@dataclass(frozen=True, slots=True)
class PageTableEntry(Entity):
    """The base ISA page-table-entry format."""

    reference: Reference["PageTableEntry"]
    source: Path
    id: str
    bits: int
    common_fields: tuple[PageTableEntryField, ...]
    layouts: Mapping[PageTableEntryLayoutId, PageTableEntryLayout]


    def __post_init__(self) -> None:
        object.__setattr__(self, "common_fields", tuple(self.common_fields))
        object.__setattr__(self, "layouts", MappingProxyType(dict(self.layouts)))

    @property
    def fields(self) -> tuple[PageTableEntryField, ...]:
        return (
            *self.common_fields,
            *(field for layout in self.layouts.values() for field in layout.fields),
        )


@dataclass(frozen=True, slots=True)
class PageTableEntryCatalog:
    """The base ISA page-table-entry definition and typed references."""

    entry: PageTableEntry
    entries: ReferenceIndex[PageTableEntry]
    fields: ReferenceIndex[PageTableEntryField]

    def __post_init__(self) -> None:
        if any(key != layout.id for key, layout in self.entry.layouts.items()):
            raise ValueError(f"{self.entry.source}: PTE layout key differs from its identity")
        fields = {}
        parent = self.entry.reference
        for member in self.entry.fields:
            if member.reference != Reference(parent.owner, (*parent.path, parent.element), member.id):
                raise ValueError(f"{member.source}: PTE field identity differs from its owning entry")
            register_reference(fields, member.reference, member)
        for name, expected in (("entries", {parent: self.entry}), ("fields", fields)):
            supplied = getattr(self, name)
            index = supplied if isinstance(supplied, ReferenceIndex) else ReferenceIndex(supplied)
            if set(index) != set(expected) or any(index[reference] is not member for reference, member in expected.items()):
                raise ValueError(f"{self.entry.source}: PTE {name} index differs from its canonical tree")
            object.__setattr__(self, name, index)




def _validate_layouts(entry: PageTableEntry) -> None:
    for layout in entry.layouts.values():
        active = (*entry.common_fields, *layout.fields)
        for index, field in enumerate(active):
            if field.msb >= entry.bits:
                raise ValueError(
                    f"{entry.source}: PTE field {field.id!r} exceeds "
                    f"the {entry.bits}-bit entry"
                )
            conflict = next(
                (other for other in active[index + 1 :] if field.overlaps(other)),
                None,
            )
            if conflict is not None:
                raise ValueError(
                    f"{entry.source}: PTE fields {field.id!r} and "
                    f"{conflict.id!r} overlap in {layout.id.value} layout"
                )


def load_page_table_entry( isa_root: str | Path) -> "PageTableEntryCatalog":
    root = Path(isa_root).resolve()
    source = root / "memory/translation/definitions/page_table_entry.yaml"
    schema = root / "schemas/page-table-entry.yaml"
    raw = load_schema_yaml(source, schema)
    entry_reference: Reference[PageTableEntry] = Reference(
        "base", ("memory", "translation"), raw["id"]
    )

    fields = {}

    def load_fields(values) -> tuple[PageTableEntryField, ...]:
        result: list[PageTableEntryField] = []
        for value in values:
            field = PageTableEntryField(
                Reference(
                    entry_reference.owner,
                    (*entry_reference.path, entry_reference.element),
                    value["id"],
                ),
                source,
                value["id"],
                value["lsb"],
                value["bits"],
            )
            register_reference(fields, field.reference, field)
            result.append(field)
        return tuple(result)

    common_fields = load_fields(raw["common_fields"])
    layouts = {
        PageTableEntryLayoutId(layout_id): PageTableEntryLayout(
            PageTableEntryLayoutId(layout_id), load_fields(layout["fields"])
        )
        for layout_id, layout in raw["layouts"].items()
    }
    entry = PageTableEntry(
        entry_reference,
        source,
        raw["id"],
        raw["bits"],
        common_fields,
        MappingProxyType(layouts),
    )
    _validate_layouts(entry)
    entries = {}
    register_reference(entries, entry.reference, entry)
    return PageTableEntryCatalog(
        entry=entry,
        entries=ReferenceIndex(entries),
        fields=ReferenceIndex(fields),
    )
