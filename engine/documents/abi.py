"""Explicit ABI and interface selections for document projections."""
from __future__ import annotations
from dataclasses import dataclass
from abi.c.model.project import ResolvedValueClass
from abi.c.model.projection import CAbiProjection
from abi.elf.model.projection import ElfAbiProjection
from interfaces.c.model.projection import CInterfaceProjection
from engine.reference import Reference
from engine.isa.registers import Register

ABI_DIRECTIVES = frozenset({"c-return-rules", "c-memory-orders", "c-atomic-lowerings", "elf-relocations", "elf-debug-registers", "elf-entry-state", "intrinsic-groups", "intrinsic-group", "intrinsic-types"})

@dataclass(frozen=True, slots=True)
class CReturnRulesProjection:
    rows: tuple[ResolvedValueClass, ...]
    sret_register: Register

    def __post_init__(self) -> None:
        object.__setattr__(self, "rows", tuple(self.rows))

@dataclass(frozen=True, slots=True)
class CMemoryOrdersProjection:
    rows: tuple

@dataclass(frozen=True, slots=True)
class CAtomicLoweringsProjection:
    rows: tuple

@dataclass(frozen=True, slots=True)
class ElfRelocationsProjection:
    relocations: tuple

@dataclass(frozen=True, slots=True)
class ElfDebugRegistersProjection:
    assignments: tuple

@dataclass(frozen=True, slots=True)
class ElfEntryStateProjection:
    state: object

@dataclass(frozen=True, slots=True)
class IntrinsicGroupsProjection:
    rows: tuple

@dataclass(frozen=True, slots=True)
class IntrinsicGroupProjection:
    group: object
    rows: tuple

@dataclass(frozen=True, slots=True)
class IntrinsicTypesProjection:
    rows: tuple


def _select(values, keys, identity):
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate ABI document selection")
    by_key = {identity(value): value for value in values}
    unknown = set(keys) - set(by_key)
    if unknown:
        raise ValueError(f"unknown ABI document selection: {unknown}")
    return tuple(by_key[key] for key in keys)


def project_c_return_rules(project: CAbiProjection, selected) -> CReturnRulesProjection:
    return CReturnRulesProjection(
        _select(project.value_classes, selected, lambda value: value.definition.reference),
        project.calling_convention.sret_register,
    )


def project_c_memory_orders(project: CAbiProjection, selected) -> CMemoryOrdersProjection:
    return CMemoryOrdersProjection(_select(project.memory_orders, selected, lambda row: row[0].reference))


def project_c_atomic_lowerings(project: CAbiProjection, selected) -> CAtomicLoweringsProjection:
    return CAtomicLoweringsProjection(_select(project.atomic_lowerings, selected, lambda row: row[0].reference))


def project_elf_relocations(project: ElfAbiProjection, selected) -> ElfRelocationsProjection:
    return ElfRelocationsProjection(_select(project.relocations, selected, lambda value: value.reference))


def project_elf_debug_registers(project: ElfAbiProjection) -> ElfDebugRegistersProjection:
    return ElfDebugRegistersProjection(project.debug_assignments)


def project_elf_entry_state(project: ElfAbiProjection) -> ElfEntryStateProjection:
    return ElfEntryStateProjection(project.process_entry)


def project_intrinsic_groups(project: CInterfaceProjection, selected) -> IntrinsicGroupsProjection:
    rows = []
    for group in _select(project.intrinsic_groups, selected, lambda group: group.id):
        collections = tuple(collection for collection in project.collections if any(member is group for member in project.collection_groups[collection.id]))
        if not collections or group.reference not in project.group_exposure:
            raise ValueError(f"{group.source}: selected intrinsic group lacks public header family data")
        rows.append((group, collections, project.group_exposure[group.reference]))
    return IntrinsicGroupsProjection(tuple(rows))


def project_intrinsic_group(project: CInterfaceProjection, group_id) -> IntrinsicGroupProjection:
    group, = _select(project.intrinsic_groups, (group_id,), lambda value: value.id)
    rows = tuple((intrinsic, project.signatures[intrinsic.reference], project.operations[intrinsic.reference], project.lowering_operands[intrinsic.reference], project.descriptions[intrinsic.reference]) for intrinsic in sorted(project.intrinsics_by_group[group.reference], key=lambda value: value.id))
    return IntrinsicGroupProjection(group, rows)


def project_intrinsic_types(project: CInterfaceProjection, selected) -> IntrinsicTypesProjection:
    return IntrinsicTypesProjection(tuple((definition, project.type_descriptions[definition.reference]) for definition in _select(project.types, selected, lambda value: value.reference)))


def project_abi_fragment(directive, catalogs):
    try:
        name = directive.name
        if len(directive.arguments) != 1:
            raise ValueError(f"{name} requires one selection")
        selection = directive.arguments[0]
        if name in {"elf-entry-state", "elf-debug-registers"}:
            if selection:
                raise ValueError(f"{name} does not accept member arguments")
            project = catalogs["abi.elf"]
            return project_elf_entry_state(project) if name == "elf-entry-state" else project_elf_debug_registers(project)
        if not selection:
            raise ValueError(f"{name} requires explicit members")
        if name == "intrinsic-group":
            return project_intrinsic_group(catalogs["interfaces.c"], selection)
        if name == "intrinsic-groups":
            return project_intrinsic_groups(catalogs["interfaces.c"], tuple(selection.split(",")))
        references = tuple(Reference.parse(key) for key in selection.split(","))
        if name == "intrinsic-types":
            return project_intrinsic_types(catalogs["interfaces.c"], references)
        if name == "elf-relocations":
            return project_elf_relocations(catalogs["abi.elf"], references)
        if name == "c-return-rules":
            return project_c_return_rules(catalogs["abi.c"], references)
        if name == "c-memory-orders":
            return project_c_memory_orders(catalogs["abi.c"], references)
        if name == "c-atomic-lowerings":
            return project_c_atomic_lowerings(catalogs["abi.c"], references)
        raise ValueError(f"unknown ABI document directive {name!r}")
    except (ValueError, KeyError) as error:
        raise ValueError(f"{directive.span}: {error}") from error
