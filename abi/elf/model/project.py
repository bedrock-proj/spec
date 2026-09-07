"""Hierarchical typed project for the Bedrock ELF ABI."""

from __future__ import annotations

from engine.source.inventory import inspect_inventory, require_exact

from engine.entity import create_entity_catalog

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import re
from types import MappingProxyType
from typing import TYPE_CHECKING, TypeVar, cast

from engine.entity import Entity, EntityCatalog, EntityDisplayStyle
from engine.entity import EntityDependency
from engine.source.inventory import DirectoryInventory
from engine.reference import (
    QualifiedReference,
    Reference,
    ReferenceIndex,
    register_reference,
)
from engine.source.yaml import freeze_source, load_schema_yaml

from .relocation_metasyntax import RelocationMetasyntax

if TYPE_CHECKING:
    from engine.isa.catalog import InstructionBundle
    from engine.isa.project import IsaProject
    from engine.isa.registers import Register, RegisterGroup
    from engine.isa.types import FieldType, PayloadType


_T = TypeVar("_T")


class RelocationResultKind(StrEnum):
    NONE = "none"
    INTEGER = "integer"
    BYTES = "bytes"
    PAIR = "pair"


class DebugRegisterRangeErrorReason(StrEnum):
    EMPTY = "empty"
    NONCONTIGUOUS = "noncontiguous"
    UNBOUNDED_NOT_LAST = "unbounded_not_last"
    UNBOUNDED_ASSIGNED = "unbounded_assigned"
    REVERSED = "reversed"
    ASSIGNMENT_WIDTH = "assignment_width"
    RESERVED_ASSIGNED = "reserved_assigned"
    MISSING_UNBOUNDED_TAIL = "missing_unbounded_tail"


class DebugRegisterRangeError(ValueError):
    """A DWARF register-number range violates the range topology."""

    def __init__(
        self, reason: DebugRegisterRangeErrorReason, message: str
    ) -> None:
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class RelocationResult:
    kind: RelocationResultKind
    width_bits: int | None
    signed: bool | None


@dataclass(frozen=True, slots=True)
class Relocation(Entity):
    reference: Reference["Relocation"]
    source: Path
    root: Path
    id: str
    value: int
    result: RelocationResult
    calculation: RelocationMetasyntax
    family: str | None
    field: QualifiedReference[FieldType] | QualifiedReference[PayloadType] | None
    relaxations: tuple[Reference["Relocation"], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "relaxations", tuple(self.relaxations))



@dataclass(frozen=True, slots=True)
class LinkageStep:
    instruction: QualifiedReference[InstructionBundle]
    form: str | None
    relocation: QualifiedReference["Relocation"] | None


@dataclass(frozen=True, slots=True)
class StateContract:
    registers: tuple[QualifiedReference[Register], ...]
    disposition: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "registers", tuple(self.registers))



@dataclass(frozen=True, slots=True)
class LinkageProtocol(Entity):
    reference: Reference["LinkageProtocol"]
    source: Path
    root: Path
    id: str
    steps: tuple[LinkageStep, ...]
    state: tuple[StateContract, ...]
    data: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "steps", tuple(self.steps))
        object.__setattr__(self, "state", tuple(self.state))
        object.__setattr__(self, "data", freeze_source(self.data))


@dataclass(frozen=True, slots=True)
class TlsModel(Entity):
    reference: Reference["TlsModel"]
    source: Path
    root: Path
    id: str
    base_register: QualifiedReference[Register] | None
    protocol: Reference["LinkageProtocol"] | None
    relocations: tuple[Reference[Relocation], ...]
    data: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "relocations", tuple(self.relocations))
        object.__setattr__(self, "data", freeze_source(self.data))


@dataclass(frozen=True, slots=True)
class CodeModel(Entity):
    reference: Reference["CodeModel"]
    source: Path
    root: Path
    id: str
    default_relocations: tuple[Reference[Relocation], ...]
    data: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "default_relocations", tuple(self.default_relocations))
        object.__setattr__(self, "data", freeze_source(self.data))


@dataclass(frozen=True, slots=True)
class DwarfRegisterRange:
    source: Path
    group: str | None
    register_group: QualifiedReference[RegisterGroup] | None
    first: int
    last: int | None
    status: str
    register_names: tuple[str, ...] | None
    condition: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "register_names", (None if self.register_names is None else tuple(self.register_names)))



@dataclass(frozen=True, slots=True)
class DebugRegisterAssignment:
    source: Path
    group: str
    first: int
    last: int | None
    status: str
    registers: tuple[Register, ...]
    condition: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "registers", tuple(self.registers))



@dataclass(frozen=True, slots=True)
class EntryState:
    source: Path
    root: Path
    entry_point: Register
    entry_point_source: str
    stack: Register
    stack_alignment_bytes: int
    stack_permissions: tuple[str, ...]
    segment_contexts: Mapping[str, Register]
    tls_base: Register | None
    readiness: tuple[str, ...]
    cleared: tuple[Register, ...]
    payload_owner: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "stack_permissions", tuple(self.stack_permissions))
        object.__setattr__(self, "segment_contexts", MappingProxyType(dict(self.segment_contexts)))
        object.__setattr__(self, "readiness", tuple(self.readiness))
        object.__setattr__(self, "cleared", tuple(self.cleared))



@dataclass(frozen=True, slots=True)
class ElfRegisterGroup(Entity):
    reference: Reference["ElfRegisterGroup"]
    source: Path
    root: Path
    id: str
    register_group: QualifiedReference[RegisterGroup]
    dwarf: tuple[DwarfRegisterRange, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "dwarf", tuple(self.dwarf))
        if self.register_group.domain != "isa":
            raise ValueError(f"{self.source}: ELF register group must reference an ISA register group")
        if any(item.group != self.id or item.register_group != self.register_group for item in self.dwarf):
            raise ValueError(f"{self.source}: DWARF ranges must belong to their ELF register group")



@dataclass(frozen=True, slots=True)
class ElfAbiNamespace:
    owner: str
    root: Path
    relocation_inventory: DirectoryInventory
    linkage_protocol_inventory: DirectoryInventory
    tls_model_inventory: DirectoryInventory
    code_model_inventory: DirectoryInventory
    register_group_inventory: DirectoryInventory
    relocations: Mapping[str, Relocation]
    linkage_protocols: Mapping[str, LinkageProtocol]
    tls_models: Mapping[str, TlsModel]
    code_models: Mapping[str, CodeModel]
    register_groups: Mapping[str, ElfRegisterGroup]
    reserved_dwarf_ranges: tuple[DwarfRegisterRange, ...]
    process_entry: EntryState

    def __post_init__(self) -> None:
        for name, kind, member_type, inventory, relative_root, manifest, reference_path, filename in (
            ("relocations", "relocation", Relocation, self.relocation_inventory, "relocations", "relocations.yaml", ("relocations",), "relocation.yaml"),
            ("linkage_protocols", "linkage-protocol", LinkageProtocol, self.linkage_protocol_inventory, "linkage_protocols", "linkage_protocols.yaml", ("linkage_protocols",), "protocol.yaml"),
            ("tls_models", "tls-model", TlsModel, self.tls_model_inventory, "tls_models", "tls_models.yaml", ("tls_models",), "model.yaml"),
            ("code_models", "code-model", CodeModel, self.code_model_inventory, "code_models", "code_models.yaml", ("code_models",), "model.yaml"),
            ("register_groups", "elf-register-group", ElfRegisterGroup, self.register_group_inventory, "registers/groups", "groups.yaml", ("registers",), "group.yaml"),
        ):
            members = dict(getattr(self, name))
            collection_root = self.root / relative_root
            if inventory.owner != self.owner or inventory.kind != kind or inventory.root != collection_root or inventory.source != collection_root / manifest:
                raise ValueError(f"{inventory.source}: ELF {kind} inventory differs from its owning collection")
            if inventory.duplicates or inventory.missing or inventory.undeclared or set(members) != set(inventory.declared):
                raise ValueError(f"{inventory.source}: declared, actual and loaded ELF {kind} membership must agree")
            pattern = r"R_BEDROCK_[A-Z0-9_]+" if kind == "relocation" else r"[A-Z][A-Z0-9_]*"
            for key, member in members.items():
                if not isinstance(key, str) or re.fullmatch(pattern, key) is None or not isinstance(member, member_type):
                    raise ValueError(f"{inventory.source}: invalid ELF {kind} member {key!r}")
                member_root = collection_root / key
                if member.id != key or member.reference != Reference(self.owner, reference_path, key) or member.root != member_root or member.source != member_root / filename:
                    raise ValueError(f"{inventory.source}: ELF {kind} identity or source differs from its collection")
            object.__setattr__(self, name, MappingProxyType(members))
        object.__setattr__(self, "reserved_dwarf_ranges", tuple(self.reserved_dwarf_ranges))
        if any(item.group is not None or item.register_group is not None or item.status != "reserved" or item.source != self.root / "registers/dwarf.yaml" for item in self.reserved_dwarf_ranges):
            raise ValueError(f"{self.root}: reserved DWARF ranges must belong to the namespace numbering manifest")
        if self.process_entry.root != self.root or self.process_entry.source != self.root / "process_entry.yaml":
            raise ValueError(f"{self.root}: process entry differs from its owning namespace")



@dataclass(frozen=True, slots=True)
class ElfAbiProject:
    root: Path
    namespaces: Mapping[str, ElfAbiNamespace]
    relocations: ReferenceIndex[Relocation]
    linkage_protocols: ReferenceIndex[LinkageProtocol]
    tls_models: ReferenceIndex[TlsModel]
    code_models: ReferenceIndex[CodeModel]
    register_groups: ReferenceIndex[ElfRegisterGroup]
    dwarf_ranges: tuple[DwarfRegisterRange, ...]
    process_entry: EntryState
    entities: EntityCatalog



    def __post_init__(self) -> None:
        object.__setattr__(self, "namespaces", MappingProxyType(dict(self.namespaces)))
        for key, namespace in self.namespaces.items():
            if key != namespace.owner:
                raise ValueError(f"{self.root}: ELF namespace key differs from its owner")
        entities: dict[Reference[Entity], Entity] = {}
        for name in ("relocations", "linkage_protocols", "tls_models", "code_models", "register_groups"):
            supplied = getattr(self, name)
            index = supplied if isinstance(supplied, ReferenceIndex) else ReferenceIndex(supplied)
            expected = {}
            for namespace in self.namespaces.values():
                for member in getattr(namespace, name).values():
                    register_reference(expected, member.reference, member)
                    register_reference(entities, member.reference, member)
            if set(index) != set(expected) or any(index[reference] is not member for reference, member in expected.items()):
                raise ValueError(f"{self.root}: ELF {name} index must contain its canonical namespace members")
            object.__setattr__(self, name, index)
        if set(self.entities.references) != set(entities) or any(self.entities.resolve(reference) is not member for reference, member in entities.items()):
            raise ValueError(f"{self.root}: entity catalog must contain the canonical ELF members")
        object.__setattr__(self, "dwarf_ranges", tuple(self.dwarf_ranges))
        ranges = tuple(
            item for namespace in self.namespaces.values()
            for item in (
                *(item for group_id in namespace.register_group_inventory.declared for item in namespace.register_groups[group_id].dwarf),
                *namespace.reserved_dwarf_ranges,
            )
        )
        if len(ranges) != len(self.dwarf_ranges) or any(left is not right for left, right in zip(ranges, self.dwarf_ranges)):
            raise ValueError(f"{self.root}: DWARF ranges must preserve canonical namespace members and declared order")
        if sum(self.process_entry is namespace.process_entry for namespace in self.namespaces.values()) != 1:
            raise ValueError(f"{self.root}: process entry must be owned by exactly one ELF namespace")

    def resolve(self, reference: Reference[_T]) -> _T:
        return cast(
            _T,
            self.entities.resolve(cast(Reference[Entity], reference)),
        )

    def entity_dependencies(self) -> tuple[EntityDependency, ...]:
        """Return the ELF ABI relationships intentionally exposed to tooling."""

        result: list[EntityDependency] = []

        def add(
            source: Reference[object],
            target: QualifiedReference[object],
            kind: str,
        ) -> None:
            result.append(EntityDependency(source, target, kind))

        def local(reference: Reference[object]) -> QualifiedReference[object]:
            return QualifiedReference("abi.elf", reference)

        for definition in self.relocations.values():
            source = cast(Reference[object], definition.reference)
            if definition.field is not None:
                add(
                    source,
                    cast(QualifiedReference[object], definition.field),
                    "relocation-field-type",
                )
            for target in definition.relaxations:
                add(
                    source,
                    local(cast(Reference[object], target)),
                    "relaxation",
                )
        for definition in self.linkage_protocols.values():
            source = cast(Reference[object], definition.reference)
            for step in definition.steps:
                add(
                    source,
                    cast(QualifiedReference[object], step.instruction),
                    "linkage-instruction",
                )
                if step.relocation is not None:
                    add(
                        source,
                        cast(QualifiedReference[object], step.relocation),
                        "linkage-relocation",
                    )
            for state in definition.state:
                for target in state.registers:
                    add(
                        source,
                        cast(QualifiedReference[object], target),
                        "linkage-register",
                    )
        for definition in self.tls_models.values():
            source = cast(Reference[object], definition.reference)
            if definition.base_register is not None:
                add(
                    source,
                    cast(QualifiedReference[object], definition.base_register),
                    "tls-base-register",
                )
            if definition.protocol is not None:
                add(
                    source,
                    local(cast(Reference[object], definition.protocol)),
                    "tls-protocol",
                )
            for target in definition.relocations:
                add(
                    source,
                    local(cast(Reference[object], target)),
                    "tls-relocation",
                )
        for definition in self.code_models.values():
            source = cast(Reference[object], definition.reference)
            for target in definition.default_relocations:
                add(
                    source,
                    local(cast(Reference[object], target)),
                    "code-model-relocation",
                )
        for definition in self.register_groups.values():
            source = cast(Reference[object], definition.reference)
            add(
                source,
                cast(QualifiedReference[object], definition.register_group),
                "dwarf-register-group",
            )
            for item in definition.dwarf:
                if item.register_group is not None:
                    add(
                        source,
                        cast(QualifiedReference[object], item.register_group),
                        "dwarf-register-group",
                    )
        return tuple(result)




def _load_namespace(
    owner: str,
    root: Path,
    schemas: Path,
    relocation_index: dict[Reference[Relocation], Relocation],
    protocol_index: dict[Reference[LinkageProtocol], LinkageProtocol],
    tls_index: dict[Reference[TlsModel], TlsModel],
    code_model_index: dict[Reference[CodeModel], CodeModel],
    register_group_index: dict[Reference[ElfRegisterGroup], ElfRegisterGroup],
    isa: "IsaProject",
) -> ElfAbiNamespace:
    relocation_inventory = _inventory(owner, root, "relocation", "relocations")
    protocol_inventory = _inventory(
        owner, root, "linkage-protocol", "linkage_protocols"
    )
    tls_inventory = _inventory(owner, root, "tls-model", "tls_models")
    code_inventory = _inventory(owner, root, "code-model", "code_models")
    register_group_inventory = require_exact(inspect_inventory(owner=owner, kind="elf-register-group", source=root / "registers/groups/groups.yaml", root=root / "registers/groups", key="groups", exact_keys=True, name_pattern=r"[A-Za-z][A-Za-z0-9_-]*"))
    invalid_register_groups = tuple(
        entity_id
        for entity_id in register_group_inventory.actual
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", entity_id) is None
    )
    if invalid_register_groups:
        raise ValueError(
            f"{register_group_inventory.source}: invalid register-group "
            f"directory names {invalid_register_groups}"
        )

    relocations: dict[str, Relocation] = {}
    for entity_id in relocation_inventory.declared:
        entity_root = relocation_inventory.root / entity_id
        source = entity_root / "relocation.yaml"
        raw = load_schema_yaml(source, schemas / "relocation.yaml")
        relocation_reference: Reference[Relocation] = Reference(
            owner, ("relocations",), entity_id
        )
        result = raw["result"]
        field = QualifiedReference.parse(raw["field"]) if "field" in raw else None
        relocation_definition = Relocation(
            relocation_reference,
            source,
            entity_root,
            entity_id,
            int(raw["value"]),
            RelocationResult(
                RelocationResultKind(result["kind"]),
                result.get("width_bits"),
                result.get("signed"),
            ),
            RelocationMetasyntax.parse(raw["calculation"]),
            raw.get("family"),
            field,
            tuple(Reference.parse(item) for item in raw.get("relaxations", ())),
        )
        register_reference(
            relocation_index, relocation_reference, relocation_definition
        )
        relocations[entity_id] = relocation_definition

    protocols: dict[str, LinkageProtocol] = {}
    for entity_id in protocol_inventory.declared:
        entity_root = protocol_inventory.root / entity_id
        source = entity_root / "protocol.yaml"
        raw = load_schema_yaml(source, schemas / "linkage-protocol.yaml")
        protocol_reference: Reference[LinkageProtocol] = Reference(
            owner, ("linkage_protocols",), entity_id
        )
        protocol_definition = LinkageProtocol(
            protocol_reference,
            source,
            entity_root,
            entity_id,
            tuple(
                LinkageStep(
                    QualifiedReference.parse(step["instruction"]),
                    step.get("form"),
                    QualifiedReference.parse(step["relocation"])
                    if "relocation" in step
                    else None,
                )
                for step in raw.get("steps", ())
            ),
            tuple(
                StateContract(
                    tuple(
                        QualifiedReference.parse(register)
                        for register in item["registers"]
                    ),
                    item["disposition"],
                )
                for item in raw.get("state", ())
            ),
            MappingProxyType(raw),
        )
        register_reference(protocol_index, protocol_reference, protocol_definition)
        protocols[entity_id] = protocol_definition

    tls_models: dict[str, TlsModel] = {}
    for entity_id in tls_inventory.declared:
        entity_root = tls_inventory.root / entity_id
        source = entity_root / "model.yaml"
        raw = load_schema_yaml(source, schemas / "tls-model.yaml")
        tls_reference: Reference[TlsModel] = Reference(
            owner, ("tls_models",), entity_id
        )
        tls_definition = TlsModel(
            tls_reference,
            source,
            entity_root,
            entity_id,
            QualifiedReference.parse(raw["base_register"])
            if "base_register" in raw
            else None,
            Reference.parse(raw["protocol"]) if "protocol" in raw else None,
            tuple(Reference.parse(item) for item in raw.get("relocations", ())),
            MappingProxyType(raw),
        )
        register_reference(tls_index, tls_reference, tls_definition)
        tls_models[entity_id] = tls_definition

    code_models: dict[str, CodeModel] = {}
    for entity_id in code_inventory.declared:
        entity_root = code_inventory.root / entity_id
        source = entity_root / "model.yaml"
        raw = load_schema_yaml(source, schemas / "code-model.yaml")
        code_reference: Reference[CodeModel] = Reference(
            owner, ("code_models",), entity_id
        )
        code_definition = CodeModel(
            code_reference,
            source,
            entity_root,
            entity_id,
            tuple(Reference.parse(item) for item in raw.get("default_relocations", ())),
            MappingProxyType(raw),
        )
        register_reference(code_model_index, code_reference, code_definition)
        code_models[entity_id] = code_definition

    register_groups: dict[str, ElfRegisterGroup] = {}
    entry_point: tuple[Register, str] | None = None
    stack: tuple[Register, int, tuple[str, ...]] | None = None
    segments: dict[str, Register] = {}
    tls_base: Register | None = None
    cleared: list[Register] = []
    entry_roles: dict[tuple[str, str], tuple[Path, Register]] = {}
    for entity_id in register_group_inventory.declared:
        entity_root = register_group_inventory.root / entity_id
        source = entity_root / "group.yaml"
        raw = load_schema_yaml(source, schemas / "register-group.yaml")
        register_group: QualifiedReference[RegisterGroup] = QualifiedReference.parse(
            raw["register_group"]
        )
        if register_group.domain != "isa":
            raise ValueError(f"{source}: register group must belong to isa")
        register_group_definition = isa.registers.groups.resolve(register_group.local)
        dwarf = tuple(
            DwarfRegisterRange(
                source,
                entity_id,
                register_group,
                int(item["first"]),
                None,
                str(item.get("status", "assigned")),
                None if item["registers"] == "all" else tuple(item["registers"]),
                item.get("condition"),
            )
            for item in raw.get("dwarf", ())
        )
        elf_group_reference: Reference[ElfRegisterGroup] = Reference(
            owner, ("registers",), entity_id
        )
        elf_group = ElfRegisterGroup(
            elf_group_reference,
            source,
            entity_root,
            entity_id,
            register_group,
            dwarf,
        )
        register_reference(register_group_index, elf_group_reference, elf_group)
        register_groups[entity_id] = elf_group
        for name, declaration in raw.get("entry", {}).items():
            try:
                register_definition = register_group_definition.registers[name]
            except KeyError as error:
                raise ValueError(
                    f"{source}: unknown register {name!r} in group "
                    f"{register_group_definition.id!r}"
                ) from error
            register = register_definition
            role = declaration["role"]
            if role != "cleared":
                role_key = (role, str(declaration["context"]) if role == "segment_context" else "")
                if role_key in entry_roles:
                    previous_source, previous_register = entry_roles[role_key]
                    raise ValueError(
                        f"{source}: process-entry role {role_key!r} for {register.reference!r} "
                        f"is already assigned by {previous_source} to {previous_register.reference!r}"
                    )
                entry_roles[role_key] = source, register
            if role == "entry_point":
                entry_point = (register, str(declaration["source"]))
            elif role == "stack_pointer":
                stack = (
                    register,
                    int(declaration["alignment_bytes"]),
                    tuple(declaration["permissions"]),
                )
            elif role == "segment_context":
                segments[str(declaration["context"])] = register
            elif role == "tls_base":
                tls_base = register
            elif role == "cleared":
                cleared.append(register)

    dwarf_raw = load_schema_yaml(root / "registers/dwarf.yaml", schemas / "dwarf.yaml")
    reserved = tuple(
        DwarfRegisterRange(
            root / "registers/dwarf.yaml",
            None,
            None,
            int(item["first"]),
            None if item["last"] == "unbounded" else int(item["last"]),
            "reserved",
            (),
            None,
        )
        for item in dwarf_raw["reserved_ranges"]
    )
    process_source = root / "process_entry.yaml"
    process_raw = load_schema_yaml(process_source, schemas / "process-entry.yaml")
    if (
        entry_point is None
        or stack is None
        or set(segments) != {"code", "data", "stack"}
    ):
        raise ValueError(
            f"{process_source}: register-group entry declarations are incomplete"
        )
    process_entry = EntryState(
        process_source,
        root,
        entry_point[0],
        entry_point[1],
        stack[0],
        stack[1],
        stack[2],
        MappingProxyType(segments),
        tls_base,
        tuple(process_raw["readiness"]),
        tuple(cleared),
        str(process_raw["payload_owner"]),
    )
    return ElfAbiNamespace(
        owner,
        root,
        relocation_inventory,
        protocol_inventory,
        tls_inventory,
        code_inventory,
        register_group_inventory,
        MappingProxyType(relocations),
        MappingProxyType(protocols),
        MappingProxyType(tls_models),
        MappingProxyType(code_models),
        MappingProxyType(register_groups),
        reserved,
        process_entry,
    )


def validate_debug_register_ranges(
    assignments: list[DebugRegisterAssignment],
) -> None:
    if not assignments:
        raise DebugRegisterRangeError(
            DebugRegisterRangeErrorReason.EMPTY,
            "DWARF register numbering requires at least one assignment",
        )
    previous_last = -1
    for index, assignment in enumerate(assignments):
        if assignment.first != previous_last + 1:
            raise DebugRegisterRangeError(
                DebugRegisterRangeErrorReason.NONCONTIGUOUS,
                f"{assignment.source}: DWARF register numbering must be contiguous"
            )
        if assignment.last is None:
            if index != len(assignments) - 1:
                raise DebugRegisterRangeError(
                    DebugRegisterRangeErrorReason.UNBOUNDED_NOT_LAST,
                    f"{assignment.source}: unbounded DWARF register range must be last"
                )
            if assignment.registers:
                raise DebugRegisterRangeError(
                    DebugRegisterRangeErrorReason.UNBOUNDED_ASSIGNED,
                    f"{assignment.source}: unbounded DWARF register range cannot "
                    "assign registers"
                )
            return
        if assignment.last < assignment.first:
            raise DebugRegisterRangeError(
                DebugRegisterRangeErrorReason.REVERSED,
                f"{assignment.source}: DWARF register range ends before it begins"
            )
        width = assignment.last - assignment.first + 1
        if assignment.status in {"assigned", "extension"}:
            if len(assignment.registers) != width:
                raise DebugRegisterRangeError(
                    DebugRegisterRangeErrorReason.ASSIGNMENT_WIDTH,
                    f"{assignment.source}: DWARF register range has width {width} "
                    f"but assigns {len(assignment.registers)} registers"
                )
        elif assignment.registers:
            raise DebugRegisterRangeError(
                DebugRegisterRangeErrorReason.RESERVED_ASSIGNED,
                f"{assignment.source}: reserved DWARF register range cannot assign "
                "registers"
            )
        previous_last = assignment.last
    raise DebugRegisterRangeError(
        DebugRegisterRangeErrorReason.MISSING_UNBOUNDED_TAIL,
        f"{assignments[-1].source}: DWARF register numbering must end with an "
        "unbounded reserved range"
    )


def _inventory(
    owner: str, root: Path, kind: str, plural: str
) -> DirectoryInventory:
    inventory = require_exact(inspect_inventory(owner=owner, kind=kind, source=root / plural / f"{plural}.yaml", root=root / plural, key=plural, exact_keys=True, name_pattern=r"[A-Za-z][A-Za-z0-9_-]*"))
    pattern = r"R_BEDROCK_[A-Z0-9_]+" if kind == "relocation" else r"[A-Z][A-Z0-9_]*"
    invalid = tuple(
        entity_id
        for entity_id in inventory.actual
        if re.fullmatch(pattern, entity_id) is None
    )
    if invalid:
        raise ValueError(
            f"{inventory.source}: invalid {kind} directory names {invalid}"
        )
    return inventory


def _register_reference(domain: str, register: Register) -> QualifiedReference[Register]:
    return QualifiedReference(domain, register.reference)


def _build_entities(
    relocations: Mapping[Reference[Relocation], Relocation],
    protocols: Mapping[Reference[LinkageProtocol], LinkageProtocol],
    tls_models: Mapping[Reference[TlsModel], TlsModel],
    code_models: Mapping[Reference[CodeModel], CodeModel],
    register_groups: Mapping[Reference[ElfRegisterGroup], ElfRegisterGroup],
) -> EntityCatalog:
    entries: list[tuple[Entity, str, EntityDisplayStyle]] = []
    for values in (
        relocations,
        protocols,
        tls_models,
        code_models,
        register_groups,
    ):
        for value in values.values():
            display = getattr(value, "id", None)
            if not isinstance(display, str):
                raise ValueError("ELF entity must provide a display identifier")
            entries.append((value, display, EntityDisplayStyle.CODE))
    return create_entity_catalog(entries)


def load_elf_abi( root: str | Path, isa: "IsaProject") -> "ElfAbiProject":
    domain_root = Path(root).resolve()
    schemas = domain_root / "schemas"
    relocations = {}
    protocols = {}
    tls_models = {}
    code_models = {}
    register_groups = {}
    base = _load_namespace(
        "base",
        domain_root,
        schemas,
        relocations,
        protocols,
        tls_models,
        code_models,
        register_groups,
        isa,
    )
    namespaces = MappingProxyType({"base": base})
    entities = _build_entities(
        relocations,
        protocols,
        tls_models,
        code_models,
        register_groups,
    )
    project = ElfAbiProject(
        root=domain_root,
        namespaces=namespaces,
        relocations=ReferenceIndex(relocations),
        linkage_protocols=ReferenceIndex(protocols),
        tls_models=ReferenceIndex(tls_models),
        code_models=ReferenceIndex(code_models),
        register_groups=ReferenceIndex(register_groups),
        dwarf_ranges=tuple(
            item for group in base.register_groups.values() for item in group.dwarf
        )
        + base.reserved_dwarf_ranges,
        process_entry=base.process_entry,
        entities=entities,
    )

    check_elf_abi(project, isa)
    return project


def check_elf_abi(project, isa):
    """Resolve structured ISA and ELF references."""
    from engine.isa.types import FieldType, PayloadType
    from engine.isa.catalog import InstructionBundle
    from engine.isa.registers import Register

    for relocation_definition in project.relocations.values():
        if relocation_definition.field is not None:
            if not isinstance(_isa_target(isa, relocation_definition.field), (FieldType, PayloadType)):
                raise ValueError(
                    f"{relocation_definition.source}: relocation field must name "
                    "an encoding field type or payload type"
                )
        for relaxation_reference in relocation_definition.relaxations:
            project.relocations.resolve(relaxation_reference)
    values: dict[int, Relocation] = {}
    for relocation_definition in project.relocations.values():
        previous_relocation = values.get(relocation_definition.value)
        if previous_relocation is not None:
            raise ValueError(
                f"{relocation_definition.source}: relocation value "
                f"{relocation_definition.value} is also assigned to "
                f"{previous_relocation.id}"
            )
        values[relocation_definition.value] = relocation_definition
    for protocol_definition in project.linkage_protocols.values():
        for step in protocol_definition.steps:
            if not isinstance(_isa_target(isa, step.instruction), InstructionBundle):
                raise ValueError(f"{protocol_definition.source}: linkage step must name an ISA instruction")
            if step.relocation is not None:
                if step.relocation.domain != "abi.elf":
                    raise ValueError(f"{protocol_definition.source}: linkage relocation must belong to abi.elf")
                target = project.relocations.resolve(step.relocation.local)
                if not isinstance(target, Relocation):
                    raise ValueError(
                        f"{protocol_definition.source}: linkage step names "
                        "an entity outside the ELF relocation catalog"
                    )
        for state_contract in protocol_definition.state:
            for state_register in state_contract.registers:
                if not isinstance(_isa_target(isa, state_register), Register):
                    raise ValueError(f"{protocol_definition.source}: linkage state must name ISA registers")
    for tls_model in project.tls_models.values():
        if tls_model.base_register is not None:
            if not isinstance(_isa_target(isa, tls_model.base_register), Register):
                raise ValueError(f"{tls_model.source}: TLS base must name an ISA register")
        if tls_model.protocol is not None:
            project.linkage_protocols.resolve(tls_model.protocol)
        for tls_relocation in tls_model.relocations:
            project.relocations.resolve(tls_relocation)
    for code_model in project.code_models.values():
        for code_relocation in code_model.default_relocations:
            project.relocations.resolve(code_relocation)
    debug_assignments = sorted(
        resolve_debug_registers(project, isa.registers), key=lambda item: item.first
    )
    validate_debug_register_ranges(tuple(debug_assignments))
    for assignment in debug_assignments:
        for assigned_register in assignment.registers:
            if isa.registers.registers.resolve(assigned_register.reference) is not assigned_register:
                raise ValueError(f"{assignment.source}: DWARF assignment must use canonical ISA registers")
    for namespace in project.namespaces.values():
        entry_state = namespace.process_entry
        for entry_register in (
            entry_state.entry_point, entry_state.stack,
            *entry_state.segment_contexts.values(), *entry_state.cleared,
            *((entry_state.tls_base,) if entry_state.tls_base is not None else ()),
        ):
            if isa.registers.registers.resolve(entry_register.reference) is not entry_register:
                raise ValueError(f"{entry_state.source}: process entry must use canonical ISA registers")


def resolve_debug_registers(project, registers):
    result: list[DebugRegisterAssignment] = []
    for item in project.dwarf_ranges:
        if item.register_group is None:
            members: tuple[Register, ...] = ()
            group = "RESERVED"
        else:
            if item.register_group.domain != "isa":
                raise ValueError(f"{item.source}: DWARF register group must belong to ISA")
            definition = registers.groups.resolve(item.register_group.local)
            available = definition.registers
            names = tuple(available) if item.register_names is None else item.register_names
            missing = tuple(name for name in names if name not in available)
            if missing:
                raise ValueError(
                    f"{item.source}: members {missing} are not in "
                    f"register group {definition.id}"
                )
            members = tuple(available[name] for name in names)
            group = item.group or definition.id
        last = item.last
        if last is None and members:
            last = item.first + len(members) - 1
        result.append(
            DebugRegisterAssignment(
                item.source, group, item.first, last, item.status,
                members, item.condition,
            )
        )
    return tuple(result)



def _isa_target(isa, reference):
    if reference.domain != "isa":
        raise ValueError(f"ELF ABI reference must name an ISA entity: {reference!r}")
    return isa.resolve(reference.local)
