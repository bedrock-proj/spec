"""Structured extended-instruction header fields."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from engine.entity import Entity
from engine.reference import Reference, ReferenceIndex, register_reference
from engine.source.yaml import load_schema_yaml


@dataclass(frozen=True, slots=True)
class InstructionHeaderField(Entity):
    reference: Reference["InstructionHeaderField"]
    source: Path
    id: str
    lsb: int
    bits: int

    @property
    def msb(self) -> int:
        return self.lsb + self.bits - 1


@dataclass(frozen=True, slots=True)
class InstructionHeader(Entity):
    reference: Reference["InstructionHeader"]
    source: Path
    id: str
    bits: int
    fields: tuple[InstructionHeaderField, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", tuple(self.fields))



@dataclass(frozen=True, slots=True)
class InstructionHeaderCatalog:
    header: InstructionHeader
    headers: ReferenceIndex[InstructionHeader]
    fields: ReferenceIndex[InstructionHeaderField]

    def __post_init__(self) -> None:
        fields = {}
        parent = self.header.reference
        for member in self.header.fields:
            if member.reference != Reference(parent.owner, (*parent.path, parent.element), member.id):
                raise ValueError(f"{member.source}: instruction field identity differs from its header")
            register_reference(fields, member.reference, member)
        for name, expected in (("headers", {parent: self.header}), ("fields", fields)):
            supplied = getattr(self, name)
            index = supplied if isinstance(supplied, ReferenceIndex) else ReferenceIndex(supplied)
            if set(index) != set(expected) or any(index[reference] is not member for reference, member in expected.items()):
                raise ValueError(f"{self.header.source}: instruction {name} index differs from its canonical tree")
            object.__setattr__(self, name, index)



def load_instruction_header( isa_root: str | Path) -> "InstructionHeaderCatalog":
    root = Path(isa_root).resolve()
    source = root / "encoding/definitions/extended_header.yaml"
    raw = load_schema_yaml(source, root / "schemas/instruction-header.yaml")
    header_reference: Reference[InstructionHeader] = Reference(
        "base", ("encoding", "instruction"), raw["id"]
    )
    fields = tuple(
        InstructionHeaderField(
            Reference(
                header_reference.owner,
                (*header_reference.path, header_reference.element),
                value["id"],
            ),
            source,
            value["id"],
            value["lsb"],
            value["bits"],
        )
        for value in raw["fields"]
    )
    header = InstructionHeader(
        header_reference, source, raw["id"], raw["bits"], fields
    )
    for index, field in enumerate(fields):
        if field.msb >= header.bits:
            raise ValueError(
                f"{source}: instruction-header field {field.id!r} exceeds "
                f"the {header.bits}-bit header"
            )
        for other in fields[index + 1 :]:
            if field.lsb <= other.msb and other.lsb <= field.msb:
                raise ValueError(
                    f"{source}: instruction-header fields {field.id!r} "
                    f"and {other.id!r} overlap"
                )
    headers = {}
    register_reference(headers, header.reference, header)
    field_index = {}
    for field in fields:
        register_reference(field_index, field.reference, field)
    return InstructionHeaderCatalog(
        header=header,
        headers=ReferenceIndex(headers),
        fields=ReferenceIndex(field_index),
    )
