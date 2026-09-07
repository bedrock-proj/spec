"""Resolved ELF relationships consumed by document and compiler projections."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from abi.elf.model.project import (
    ElfAbiProject, Relocation, CodeModel, TlsModel, LinkageProtocol,
    DebugRegisterAssignment, EntryState, check_elf_abi, resolve_debug_registers,
)
from engine.isa.catalog import InstructionBundle
from engine.isa.registers import Register
from engine.isa.types import FieldType, PayloadType
from engine.reference import Reference


@dataclass(frozen=True, slots=True)
class ElfAbiProjection:
    relocations: tuple[Relocation, ...]
    relocation_fields: Mapping[Reference[Relocation], FieldType | PayloadType]
    relaxations: tuple[tuple[Relocation, Relocation], ...]
    code_models: tuple[CodeModel, ...]
    code_model_relocations: tuple[tuple[CodeModel, Relocation], ...]
    tls_models: tuple[TlsModel, ...]
    tls_protocols: tuple[tuple[TlsModel, LinkageProtocol], ...]
    tls_registers: tuple[tuple[TlsModel, Register], ...]
    tls_relocations: tuple[tuple[TlsModel, Relocation], ...]
    linkage_protocols: tuple[LinkageProtocol, ...]
    linkage_steps: tuple[tuple[LinkageProtocol, tuple[tuple[InstructionBundle, str | None, Relocation | None], ...]], ...]
    linkage_state: tuple[tuple[LinkageProtocol, str, tuple[Register, ...]], ...]
    debug_assignments: tuple[DebugRegisterAssignment, ...]
    process_entry: EntryState
    properties: Mapping[Reference, tuple[tuple[tuple[str | int, ...], str | int | bool], ...]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "relocations", tuple(self.relocations))
        object.__setattr__(self, "relocation_fields", MappingProxyType(dict(self.relocation_fields)))
        object.__setattr__(self, "relaxations", tuple(tuple(item0) for item0 in self.relaxations))
        object.__setattr__(self, "code_models", tuple(self.code_models))
        object.__setattr__(self, "code_model_relocations", tuple(tuple(item0) for item0 in self.code_model_relocations))
        object.__setattr__(self, "tls_models", tuple(self.tls_models))
        object.__setattr__(self, "tls_protocols", tuple(tuple(item0) for item0 in self.tls_protocols))
        object.__setattr__(self, "tls_registers", tuple(tuple(item0) for item0 in self.tls_registers))
        object.__setattr__(self, "tls_relocations", tuple(tuple(item0) for item0 in self.tls_relocations))
        object.__setattr__(self, "linkage_protocols", tuple(self.linkage_protocols))
        object.__setattr__(self, "linkage_steps", tuple((item0[0], tuple(tuple(item2) for item2 in item0[1])) for item0 in self.linkage_steps))
        object.__setattr__(self, "linkage_state", tuple((item0[0], item0[1], tuple(item0[2])) for item0 in self.linkage_state))
        object.__setattr__(self, "debug_assignments", tuple(self.debug_assignments))
        object.__setattr__(self, "properties", MappingProxyType({key0: tuple((tuple(item1[0]), item1[1]) for item1 in value0) for key0, value0 in self.properties.items()}))
        members = {}
        for collection in (self.relocations, self.code_models, self.tls_models, self.linkage_protocols):
            for member in collection:
                if member.reference in members:
                    raise ValueError(f"{member.source}: duplicate ELF projection member {member.reference!r}")
                members[member.reference] = member

        def canonical(member):
            if members.get(member.reference) is not member:
                raise ValueError(f"{member.source}: ELF projection relation must use a canonical selected member")

        for relation in (self.relaxations, self.code_model_relocations, self.tls_protocols, self.tls_relocations):
            for source, target in relation:
                canonical(source)
                canonical(target)
        isa_members = {}

        def canonical_isa(member):
            if isa_members.setdefault(member.reference, member) is not member:
                raise ValueError(f"{member.source}: ELF projection has multiple instances of one ISA entity")

        for model, register in self.tls_registers:
            canonical(model)
            canonical_isa(register)
        for protocol, steps in self.linkage_steps:
            canonical(protocol)
            for instruction, _, relocation in steps:
                canonical_isa(instruction)
                if relocation is not None:
                    canonical(relocation)
        for protocol, _, registers in self.linkage_state:
            canonical(protocol)
            for register in registers:
                canonical_isa(register)
        expected_fields = {item.reference for item in self.relocations if item.field is not None}
        if set(self.relocation_fields) != expected_fields:
            raise ValueError("ELF relocation fields must match the selected relocations with field references")
        for field in self.relocation_fields.values():
            canonical_isa(field)
        expected_properties = {item.reference for collection in (self.code_models, self.tls_models, self.linkage_protocols) for item in collection}
        if set(self.properties) != expected_properties:
            raise ValueError("ELF property owners must match the selected code, TLS and linkage models")
        for assignment in self.debug_assignments:
            for register in assignment.registers:
                canonical_isa(register)
        entry = self.process_entry
        for register in (
            entry.entry_point, entry.stack, *entry.segment_contexts.values(), *entry.cleared,
            *((entry.tls_base,) if entry.tls_base is not None else ()),
        ):
            canonical_isa(register)



def project_elf_abi(project: ElfAbiProject, isa) -> ElfAbiProjection:
    check_elf_abi(project, isa)
    def register(reference):
        return isa.registers.registers.resolve(reference.local)
    def instruction(reference):
        return isa.catalog.instructions.resolve(reference.local)
    fields = {}
    for relocation in project.relocations.values():
        if relocation.field is not None:
            definition = isa.resolve(relocation.field.local)
            fields[relocation.reference] = definition
    properties = {definition.reference: _properties(definition) for collection in (project.code_models, project.tls_models, project.linkage_protocols) for definition in collection.values()}
    return ElfAbiProjection(
        tuple(project.relocations.values()), MappingProxyType(fields),
        tuple((relocation, project.relocations.resolve(target)) for relocation in project.relocations.values() for target in relocation.relaxations),
        tuple(project.code_models.values()),
        tuple((model, project.relocations.resolve(target)) for model in project.code_models.values() for target in model.default_relocations),
        tuple(project.tls_models.values()),
        tuple((model, project.linkage_protocols.resolve(model.protocol)) for model in project.tls_models.values() if model.protocol is not None),
        tuple((model, register(model.base_register)) for model in project.tls_models.values() if model.base_register is not None),
        tuple((model, project.relocations.resolve(target)) for model in project.tls_models.values() for target in model.relocations),
        tuple(project.linkage_protocols.values()),
        tuple((protocol, tuple((instruction(step.instruction), step.form, project.relocations.resolve(step.relocation.local) if step.relocation is not None else None) for step in protocol.steps)) for protocol in project.linkage_protocols.values()),
        tuple((protocol, state.disposition, tuple(register(target) for target in state.registers)) for protocol in project.linkage_protocols.values() for state in protocol.state),
        resolve_debug_registers(project, isa.registers), project.process_entry,
        MappingProxyType(properties),
    )


def _properties(definition):
    """Project the declared model properties, independent of extra source keys."""
    paths = ()
    if isinstance(definition, CodeModel):
        paths = (("placement",), ("strategy",))
    elif isinstance(definition, TlsModel):
        paths = (("selection",), ("descriptor", "size_bytes"), ("descriptor", "alignment_bytes"))
    elif isinstance(definition, LinkageProtocol):
        paths = (
            ("entry", "size_bytes"), ("entry", "alignment_bytes"),
            ("layout", "instruction_offset_bytes"), ("layout", "relocation_field_offset_bytes"),
            ("layout", "relocation_addend"), ("layout", "padding_offset_bytes"),
            ("layout", "padding_size_bytes"), ("layout", "padding_byte"),
            ("got_slot", "size_bytes"), ("got_slot", "alignment_bytes"),
            ("got_slot", "publication"), ("input",), ("output",),
        )
    result = []
    for path in paths:
        value = definition.data
        for key in path:
            if not isinstance(value, Mapping) or key not in value:
                break
            value = value[key]
        else:
            if type(value) not in (str, int, bool):
                raise ValueError(f"{definition.source}: property {path!r} must be scalar")
            result.append((path, value))
    if isinstance(definition, TlsModel) and "descriptor" in definition.data:
        descriptor = definition.data["descriptor"]
        for index, field in enumerate(descriptor.get("fields", ())):
            for key in ("id", "offset_bytes", "type"):
                value = field[key]
                if type(value) not in (str, int):
                    raise ValueError(f"{definition.source}: descriptor field {key!r} must be a string or integer")
                result.append((("descriptor", "fields", index, key), value))
    return tuple(result)
