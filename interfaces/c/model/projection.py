"""Resolved C interface declarations shared by public projectors."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TypeAlias

from interfaces.c.model.project import (
    CInterfaceProject, InterfaceGroup, InterfaceCollection, InterfaceType, _PRIMITIVES,
    InterfaceIntrinsic, InterfaceUtility, resolve_type_layouts, order_types,
)
from engine.isa.catalog import InstructionBundle
from engine.isa.registers import Register, RegisterGroup, register_selector_members
from engine.isa.control_registers import ControlRegister, ControlRegisterNamespace, control_register_selector_members
from engine.reference import Reference, QualifiedReference
from engine.source.yaml import freeze_source

SemanticType: TypeAlias = str | InterfaceType
ResolvedField: TypeAlias = tuple[str, SemanticType, int | None, bool, int, int, int]
ResolvedParameter: TypeAlias = tuple[str, SemanticType, bool]


@dataclass(frozen=True, slots=True)
class CInterfaceProjection:
    type_groups: tuple[InterfaceGroup, ...]
    intrinsic_groups: tuple[InterfaceGroup, ...]
    utility_groups: tuple[InterfaceGroup, ...]
    types_by_group: Mapping[Reference[InterfaceGroup], tuple[InterfaceType, ...]]
    intrinsics_by_group: Mapping[Reference[InterfaceGroup], tuple[InterfaceIntrinsic, ...]]
    utilities_by_group: Mapping[Reference[InterfaceGroup], tuple[InterfaceUtility, ...]]
    collections: tuple[InterfaceCollection, ...]
    collection_groups: Mapping[str, tuple[InterfaceGroup, ...]]
    types: tuple[InterfaceType, ...]
    type_descriptions: Mapping[Reference[InterfaceType], str]
    enum_members: Mapping[Reference[InterfaceType], tuple[tuple[Register | ControlRegister, int], ...]]
    enum_prefixes: Mapping[Reference[InterfaceType], str]
    record_fields: Mapping[Reference[InterfaceType], tuple[ResolvedField, ...]]
    layouts: Mapping[Reference[InterfaceType], tuple[int, int]]
    type_dependencies: tuple[tuple[InterfaceType, InterfaceType], ...]
    intrinsics: tuple[InterfaceIntrinsic, ...]
    signatures: Mapping[Reference[InterfaceIntrinsic], tuple[SemanticType, tuple[ResolvedParameter, ...]]]
    operations: Mapping[Reference[InterfaceIntrinsic], InstructionBundle]
    lowering_operands: Mapping[Reference[InterfaceIntrinsic], Mapping[str, object]]
    wrappers: Mapping[Reference[InterfaceIntrinsic], str]
    descriptions: Mapping[Reference[InterfaceIntrinsic], str]
    utilities: tuple[InterfaceUtility, ...]
    utility_definitions: Mapping[Reference[InterfaceUtility], tuple[tuple[str, ...], str]]
    group_includes: Mapping[Reference[InterfaceGroup], tuple[str, ...]]
    group_exposure: Mapping[Reference[InterfaceGroup], str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "type_groups", tuple(self.type_groups))
        object.__setattr__(self, "intrinsic_groups", tuple(self.intrinsic_groups))
        object.__setattr__(self, "utility_groups", tuple(self.utility_groups))
        object.__setattr__(self, "types_by_group", MappingProxyType({key0: tuple(value0) for key0, value0 in self.types_by_group.items()}))
        object.__setattr__(self, "intrinsics_by_group", MappingProxyType({key0: tuple(value0) for key0, value0 in self.intrinsics_by_group.items()}))
        object.__setattr__(self, "utilities_by_group", MappingProxyType({key0: tuple(value0) for key0, value0 in self.utilities_by_group.items()}))
        object.__setattr__(self, "collections", tuple(self.collections))
        object.__setattr__(self, "collection_groups", MappingProxyType({key0: tuple(value0) for key0, value0 in self.collection_groups.items()}))
        object.__setattr__(self, "types", tuple(self.types))
        object.__setattr__(self, "type_descriptions", MappingProxyType(dict(self.type_descriptions)))
        object.__setattr__(self, "enum_members", MappingProxyType({key0: tuple(tuple(item1) for item1 in value0) for key0, value0 in self.enum_members.items()}))
        object.__setattr__(self, "enum_prefixes", MappingProxyType(dict(self.enum_prefixes)))
        object.__setattr__(self, "record_fields", MappingProxyType({key: tuple(tuple(field) for field in fields) for key, fields in self.record_fields.items()}))
        object.__setattr__(self, "layouts", MappingProxyType({key0: tuple(value0) for key0, value0 in self.layouts.items()}))
        object.__setattr__(self, "type_dependencies", tuple(tuple(item0) for item0 in self.type_dependencies))
        object.__setattr__(self, "intrinsics", tuple(self.intrinsics))
        object.__setattr__(self, "signatures", MappingProxyType({key: (result, tuple(tuple(parameter) for parameter in parameters)) for key, (result, parameters) in self.signatures.items()}))
        object.__setattr__(self, "operations", MappingProxyType(dict(self.operations)))
        object.__setattr__(self, "lowering_operands", MappingProxyType({key: freeze_source(value) for key, value in self.lowering_operands.items()}))
        object.__setattr__(self, "wrappers", MappingProxyType(dict(self.wrappers)))
        object.__setattr__(self, "descriptions", MappingProxyType(dict(self.descriptions)))
        object.__setattr__(self, "utilities", tuple(self.utilities))
        object.__setattr__(self, "utility_definitions", MappingProxyType({key0: (tuple(value0[0]), value0[1]) for key0, value0 in self.utility_definitions.items()}))
        object.__setattr__(self, "group_includes", MappingProxyType({key0: tuple(value0) for key0, value0 in self.group_includes.items()}))
        object.__setattr__(self, "group_exposure", MappingProxyType(dict(self.group_exposure)))



def project_c_interface(project: CInterfaceProject, isa, c_abi) -> CInterfaceProjection:
    def semantic_type(value):
        if isinstance(value, QualifiedReference):
            if value.domain != "interfaces.c":
                raise ValueError(f"C interface type must belong to interfaces.c: {value!r}")
            return project.types.resolve(value.local)
        if value not in _PRIMITIVES:
            raise ValueError(f"unknown primitive C interface type {value!r}")
        return value
    ordered = order_types(project, tuple(project.types.values()))
    layouts, fields = resolve_type_layouts(project, isa, c_abi)
    enums, prefixes = {}, {}
    for definition in ordered:
        if definition.kind == "enum":
            reference = definition.enum_source
            if reference is None or reference.domain != "isa":
                raise ValueError(f"{definition.source}: enum must name an ISA selector collection")
            source = isa.resolve(reference.local)
            if isinstance(source, RegisterGroup):
                members = register_selector_members(source)
            elif isinstance(source, ControlRegisterNamespace):
                members = control_register_selector_members(source)
            else:
                raise ValueError(f"{definition.source}: enum source is not a register selector collection")
            enums[definition.reference] = members
            prefixes[definition.reference] = str(definition.data["values"]["member-prefix"])
    signatures, operations, operands, wrappers, descriptions = {}, {}, {}, {}, {}
    for intrinsic in project.intrinsics.values():
        parameters = intrinsic.data["signature"]["parameters"]
        signatures[intrinsic.reference] = (
            semantic_type(intrinsic.result_type),
            tuple((str(parameter["id"]), semantic_type(value), bool(parameter.get("constant", False))) for parameter, value in zip(parameters, intrinsic.parameter_types, strict=True)),
        )
        if intrinsic.operation.domain != "isa":
            raise ValueError(f"{intrinsic.source}: lowering operation must belong to ISA")
        operations[intrinsic.reference] = isa.catalog.instructions.resolve(intrinsic.operation.local)
        operands[intrinsic.reference] = intrinsic.data["lowering"].get("operands", MappingProxyType({}))
        wrapper = intrinsic.data.get("wrapper", {}).get("kind", "inline")
        if wrapper not in {"inline", "macro", "aggregate-result", "aggregate-result-macro"}:
            raise ValueError(f"{intrinsic.source}: unknown C wrapper {wrapper!r}")
        wrappers[intrinsic.reference] = wrapper
        descriptions[intrinsic.reference] = str(intrinsic.data["description"])
    def membership(groups, members, scope):
        selected = {group.reference: [] for group in groups.values()}
        for member in members:
            reference = Reference("base", (scope,), member.group)
            group = groups.resolve(reference)
            selected[group.reference].append(member)
        return MappingProxyType({reference: tuple(values) for reference, values in selected.items()})
    collections = {}
    for collection in project.collections.values():
        if len(set(collection.groups)) != len(collection.groups):
            raise ValueError(f"duplicate intrinsic group in collection {collection.id!r}")
        collections[collection.id] = tuple(project.intrinsic_groups.resolve(Reference("base", ("intrinsics",), identifier)) for identifier in collection.groups)
    groups = (*project.type_groups.values(), *project.intrinsic_groups.values(), *project.utility_groups.values())
    return CInterfaceProjection(
        tuple(project.type_groups.values()), tuple(project.intrinsic_groups.values()), tuple(project.utility_groups.values()),
        membership(project.type_groups, ordered, "types"), membership(project.intrinsic_groups, project.intrinsics.values(), "intrinsics"), membership(project.utility_groups, project.utilities.values(), "utilities"),
        tuple(project.collections.values()), MappingProxyType(collections), ordered, MappingProxyType({definition.reference: str(definition.data["summary"]) if "summary" in definition.data else definition.kind for definition in ordered}), MappingProxyType(enums), MappingProxyType(prefixes), fields, layouts,
        tuple((definition, project.types.resolve(value.local)) for definition in ordered for value in definition.field_types if isinstance(value, QualifiedReference)),
        tuple(project.intrinsics.values()), MappingProxyType(signatures), MappingProxyType(operations), MappingProxyType(operands), MappingProxyType(wrappers), MappingProxyType(descriptions),
        tuple(project.utilities.values()),
        MappingProxyType({utility.reference: (tuple(utility.data.get("parameters", ())), str(utility.data["body"])) for utility in project.utilities.values()}),
        MappingProxyType({group.reference: tuple(group.data.get("includes", ())) for group in groups}),
        MappingProxyType({group.reference: str(group.data["exposure"]) for group in groups if "exposure" in group.data}),
    )
