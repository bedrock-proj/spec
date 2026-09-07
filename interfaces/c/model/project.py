"""Typed catalogs for the Bedrock C target interface."""

from __future__ import annotations

from engine.source.inventory import inspect_inventory, require_exact

from engine.entity import create_entity_catalog
from engine.source.inventory import inspect_inventory

from dataclasses import dataclass
from pathlib import Path
import re
from types import MappingProxyType
from collections.abc import Mapping
from typing import TYPE_CHECKING, TypeAlias, TypeVar, cast

from engine.entity import EntityDependency
from engine.entity import Entity, EntityCatalog, EntityDisplayStyle
from engine.source.inventory import DirectoryInventory
from engine.reference import (
    QualifiedReference,
    Reference,
    ReferenceIndex,
    register_reference,
)
from engine.source.yaml import freeze_source, load_schema_yaml, load_yaml

if TYPE_CHECKING:
    from engine.isa.catalog import InstructionBundle
    from engine.isa.registers import RegisterGroup


SignatureType: TypeAlias = str | QualifiedReference["InterfaceType"]
_T = TypeVar("_T")
_PRIMITIVES = frozenset(("void", "u8", "u16", "u32", "u64", "f32", "f64", "size", "void_pointer", "const_void_pointer", "u8_pointer", "u16_pointer", "u32_pointer", "u64_pointer", "f32_pointer", "f64_pointer"))


@dataclass(frozen=True, slots=True)
class InterfaceGroup(Entity):
    reference: Reference["InterfaceGroup"]
    id: str
    title: str
    source: Path
    data: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", freeze_source(self.data))


@dataclass(frozen=True, slots=True)
class InterfaceType(Entity):
    reference: Reference["InterfaceType"]
    id: str
    owner: str
    group: str
    kind: str
    source: Path
    data: Mapping[str, object]
    enum_source: QualifiedReference["RegisterGroup"] | None
    field_types: tuple[SignatureType, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", freeze_source(self.data))
        object.__setattr__(self, "field_types", tuple(self.field_types))


@dataclass(frozen=True, slots=True)
class InterfaceIntrinsic(Entity):
    reference: Reference["InterfaceIntrinsic"]
    id: str
    owner: str
    group: str
    source: Path
    operation: QualifiedReference['InstructionBundle']
    data: Mapping[str, object]
    result_type: SignatureType
    parameter_types: tuple[SignatureType, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", freeze_source(self.data))
        object.__setattr__(self, "parameter_types", tuple(self.parameter_types))




@dataclass(frozen=True, slots=True)
class InterfaceExtension:
    id: str
    requires: tuple[str, ...]
    requires_isa: tuple[str, ...]
    source: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "requires", tuple(self.requires))
        object.__setattr__(self, "requires_isa", tuple(self.requires_isa))



@dataclass(frozen=True, slots=True)
class InterfaceUtility(Entity):
    reference: Reference["InterfaceUtility"]
    id: str
    owner: str
    group: str
    source: Path
    data: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", freeze_source(self.data))


@dataclass(frozen=True, slots=True)
class InterfaceCollection:
    id: str
    groups: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "groups", tuple(self.groups))



@dataclass(frozen=True, slots=True)
class CInterfaceProject:
    """Loaded C target-interface groups, types, and intrinsics."""

    root: Path
    extensions: Mapping[str, InterfaceExtension]
    type_groups: ReferenceIndex[InterfaceGroup]
    intrinsic_groups: ReferenceIndex[InterfaceGroup]
    utility_groups: ReferenceIndex[InterfaceGroup]
    types: ReferenceIndex[InterfaceType]
    intrinsics: ReferenceIndex[InterfaceIntrinsic]
    utilities: ReferenceIndex[InterfaceUtility]
    collections: Mapping[str, InterfaceCollection]
    entities: EntityCatalog



    def __post_init__(self) -> None:
        object.__setattr__(self, "extensions", MappingProxyType(dict(self.extensions)))
        object.__setattr__(self, "type_groups", (self.type_groups if isinstance(self.type_groups, ReferenceIndex) else ReferenceIndex(self.type_groups)))
        object.__setattr__(self, "intrinsic_groups", (self.intrinsic_groups if isinstance(self.intrinsic_groups, ReferenceIndex) else ReferenceIndex(self.intrinsic_groups)))
        object.__setattr__(self, "utility_groups", (self.utility_groups if isinstance(self.utility_groups, ReferenceIndex) else ReferenceIndex(self.utility_groups)))
        object.__setattr__(self, "types", (self.types if isinstance(self.types, ReferenceIndex) else ReferenceIndex(self.types)))
        object.__setattr__(self, "intrinsics", (self.intrinsics if isinstance(self.intrinsics, ReferenceIndex) else ReferenceIndex(self.intrinsics)))
        object.__setattr__(self, "utilities", (self.utilities if isinstance(self.utilities, ReferenceIndex) else ReferenceIndex(self.utilities)))
        object.__setattr__(self, "collections", MappingProxyType(dict(self.collections)))
        members = {}
        for index, expected_type in (
            (self.type_groups, InterfaceGroup),
            (self.intrinsic_groups, InterfaceGroup),
            (self.utility_groups, InterfaceGroup),
            (self.types, InterfaceType),
            (self.intrinsics, InterfaceIntrinsic),
            (self.utilities, InterfaceUtility),
        ):
            for reference, member in index.items():
                if not isinstance(member, expected_type) or member.reference != reference:
                    raise ValueError("C interface index key or member kind is invalid")
                if reference in members:
                    raise ValueError(f"duplicate C interface member {reference!r}")
                members[reference] = member
        if set(members) != set(self.entities.references):
            raise ValueError("C interface entity and domain indexes differ in membership")
        if any(self.entities.references[reference] is not member
               for reference, member in members.items()):
            raise ValueError("C interface entity index must retain canonical domain members")
        if any(key != value.id for key, value in self.extensions.items()):
            raise ValueError("C interface extension key differs from its identity")
        if any(key != value.id for key, value in self.collections.items()):
            raise ValueError("C interface collection key differs from its identity")

    def resolve(self, reference: Reference[_T]) -> _T:
        return cast(
            _T,
            self.entities.resolve(cast(Reference[Entity], reference)),
        )

    def entity_dependencies(self) -> tuple[EntityDependency, ...]:
        """Return target-interface relationships intentionally exposed to tooling."""

        result: list[EntityDependency] = []
        for definition in self.types.values():
            source = cast(Reference[object], definition.reference)
            if definition.enum_source is not None:
                result.append(
                    EntityDependency(
                        source,
                        cast(QualifiedReference[object], definition.enum_source),
                        "enum-register-group",
                    )
                )
            for target in definition.field_types:
                if isinstance(target, QualifiedReference):
                    result.append(
                        EntityDependency(
                            source,
                            cast(QualifiedReference[object], target),
                            "interface-field-type",
                        )
                    )
        for definition in self.intrinsics.values():
            source = cast(Reference[object], definition.reference)
            result.append(
                EntityDependency(
                    source,
                    cast(QualifiedReference[object], definition.operation),
                    "intrinsic-instruction",
                )
            )
            for target in (definition.result_type, *definition.parameter_types):
                if isinstance(target, QualifiedReference):
                    result.append(
                        EntityDependency(
                            source,
                            cast(QualifiedReference[object], target),
                            "signature-type",
                        )
                    )
        return tuple(result)





def _build_entities(
    type_groups: ReferenceIndex[InterfaceGroup],
    intrinsic_groups: ReferenceIndex[InterfaceGroup],
    utility_groups: ReferenceIndex[InterfaceGroup],
    types: ReferenceIndex[InterfaceType],
    intrinsics: ReferenceIndex[InterfaceIntrinsic],
    utilities: ReferenceIndex[InterfaceUtility],
) -> EntityCatalog:
    entries: list[tuple[Entity, str, EntityDisplayStyle]] = []
    for values, style in (
        (type_groups, EntityDisplayStyle.TEXT),
        (intrinsic_groups, EntityDisplayStyle.TEXT),
        (utility_groups, EntityDisplayStyle.TEXT),
        (types, EntityDisplayStyle.CODE),
        (intrinsics, EntityDisplayStyle.CODE),
        (utilities, EntityDisplayStyle.CODE),
    ):
        for value in values.values():
            display = (
                value.title if isinstance(value, InterfaceGroup) else value.id
            )
            entries.append((value, display, style))
    return create_entity_catalog(entries)


def _load_groups(
    root: Path,
    kind: str,
    singular: str,
    extensions: Mapping[str, InterfaceExtension],
) -> tuple[ReferenceIndex[InterfaceGroup], ReferenceIndex[object]]:
    groups_root = root / kind / "groups"
    group_inventory = _load_inventory(
        groups_root / "groups.yaml", "groups", r"[a-z][a-z0-9_]*"
    )
    group_schema = root / "schemas/group.yaml"
    entity_schema = root / f"schemas/{singular}.yaml"
    groups = {}
    entities = {}
    for group_id in group_inventory.actual:
        group_root = groups_root / group_id
        group_data = load_schema_yaml(group_root / "group.yaml", group_schema)
        group_reference: Reference[InterfaceGroup] = Reference(
            "base", (kind,), group_id
        )
        register_reference(
            groups,
            group_reference,
            InterfaceGroup(
                group_reference,
                group_id,
                str(group_data["title"]),
                group_root / "group.yaml",
                MappingProxyType(group_data),
            ),
        )
        entities_root = group_root / kind
        entity_inventory = _load_inventory(
            entities_root / f"{kind}.yaml", kind, r"[a-z][a-z0-9_]*"
        )
        for entity_id in entity_inventory.actual:
            source = entities_root / entity_id / f"{singular}.yaml"
            data = load_schema_yaml(source, entity_schema)
            owner = str(data["owner"])
            if owner != "base" and owner not in extensions:
                raise ValueError(f"{source}: unknown interface owner {owner!r}")
            reference: Reference[object] = Reference(owner, (kind, group_id), entity_id)
            entity: object
            if kind == "types":
                enum_source = _enum_source(data)
                field_types = _struct_field_types(data, source)
                entity = InterfaceType(
                    cast(Reference[InterfaceType], reference),
                    entity_id,
                    owner,
                    group_id,
                    str(data["kind"]),
                    source,
                    MappingProxyType(data),
                    enum_source,
                    field_types,
                )
            elif kind == "intrinsics":
                operation = cast(
                    "QualifiedReference[InstructionBundle]",
                    QualifiedReference.parse(data["lowering"]["operation"]),
                )
                if operation.domain != "isa":
                    raise ValueError(f"{source}: lowering operation must be in isa")
                result_type, parameter_types = _signature_types(data, source)
                entity = InterfaceIntrinsic(
                    cast(Reference[InterfaceIntrinsic], reference),
                    entity_id,
                    owner,
                    group_id,
                    source,
                    operation,
                    MappingProxyType(data),
                    result_type,
                    parameter_types,
                )
            else:
                entity = InterfaceUtility(
                    cast(Reference[InterfaceUtility], reference),
                    entity_id,
                    owner,
                    group_id,
                    source,
                    MappingProxyType(data),
                )
            register_reference(entities, reference, entity)
    return ReferenceIndex(groups), ReferenceIndex(entities)


def _load_inventory(path: Path, key: str, name_pattern: str) -> DirectoryInventory:
    inventory = require_exact(inspect_inventory(
        owner="interfaces.c",
        kind=key,
        source=path,
        root=path.parent,
        key=key,
        exact_keys=True,
        validate_names=True,
    ))
    invalid = tuple(
        name for name in inventory.actual if re.fullmatch(name_pattern, name) is None
    )
    if invalid:
        raise ValueError(f"{path}: invalid {key} directory names {invalid}")
    return inventory


def _load_extensions(root: Path) -> Mapping[str, InterfaceExtension]:
    extensions_root = root / "extensions"
    inventory = _load_inventory(
        extensions_root / "extensions.yaml", "extensions", r"[A-Z][A-Z0-9_]*"
    )
    schema = root / "schemas/extension.yaml"
    loaded: dict[str, InterfaceExtension] = {}
    for extension_id in inventory.actual:
        source = extensions_root / extension_id / "extension.yaml"
        data = load_schema_yaml(source, schema)
        loaded[extension_id] = InterfaceExtension(
            extension_id,
            tuple(data.get("requires", ())),
            tuple(data.get("requires-isa", ())),
            source,
        )
    for extension in loaded.values():
        unknown = sorted(set(extension.requires) - set(loaded))
        if unknown:
            raise ValueError(f"{extension.source}: unknown requirements {unknown}")
    _validate_extension_cycles(loaded)
    return MappingProxyType(loaded)


def _validate_extension_cycles(extensions: Mapping[str, InterfaceExtension]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(extension_id: str) -> None:
        if extension_id in visiting:
            raise ValueError(f"cyclic interface extension dependency at {extension_id}")
        if extension_id in visited:
            return
        visiting.add(extension_id)
        for required in extensions[extension_id].requires:
            visit(required)
        visiting.remove(extension_id)
        visited.add(extension_id)

    for extension_id in extensions:
        visit(extension_id)


def _looks_like_local_reference(value: str) -> bool:
    return value.startswith("base.") or bool(
        value and value.split(".", 1)[0].isupper() and "." in value
    )


def _signature_type(value: object, source: Path) -> SignatureType:
    if not isinstance(value, str):
        raise ValueError(f"{source}: signature type must be a string")
    if ":" not in value and not _looks_like_local_reference(value):
        if value not in _PRIMITIVES:
            raise ValueError(f"{source}: unknown primitive C interface type {value!r}")
        return value
    return cast(
        QualifiedReference[InterfaceType],
        QualifiedReference.parse(value, current_domain="interfaces.c"),
    )


def _signature_types(
    data: Mapping[str, object], source: Path
) -> tuple[SignatureType, tuple[SignatureType, ...]]:
    signature = data["signature"]
    if not isinstance(signature, Mapping):
        raise ValueError(f"{source}: signature must be a mapping")
    parameters = signature["parameters"]
    if not isinstance(parameters, list):
        raise ValueError(f"{source}: signature parameters must be a list")
    return (
        _signature_type(signature["result"], source),
        tuple(
            _signature_parameter_type(parameter, source) for parameter in parameters
        ),
    )


def _signature_parameter_type(parameter: object, source: Path) -> SignatureType:
    if not isinstance(parameter, Mapping):
        raise ValueError(f"{source}: signature parameter must be a mapping")
    return _signature_type(parameter["type"], source)


def _struct_field_types(
    data: Mapping[str, object], source: Path
) -> tuple[SignatureType, ...]:
    fields = data.get("fields", ())
    if not isinstance(fields, (list, tuple)):
        raise ValueError(f"{source}: struct fields must be a list")
    result: list[SignatureType] = []
    for field in fields:
        if not isinstance(field, Mapping):
            raise ValueError(f"{source}: struct field must be a mapping")
        result.append(_signature_type(field.get("type"), source))
    return tuple(result)


def _enum_source(
    data: Mapping[str, object],
) -> QualifiedReference[RegisterGroup] | None:
    values = data.get("values")
    if not isinstance(values, Mapping) or "source" not in values:
        return None
    return cast(
        "QualifiedReference[RegisterGroup]",
        QualifiedReference.parse(values["source"]),
    )


def _load_collections(
    root: Path, groups: ReferenceIndex[InterfaceGroup]
) -> Mapping[str, InterfaceCollection]:
    source = root / "intrinsics/collections/collections.yaml"
    raw = load_yaml(source).get("collections")
    if not isinstance(raw, list):
        raise ValueError(f"{source}: expected a collections list")
    available = {group.id for group in groups.values()}
    collections: dict[str, InterfaceCollection] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError(f"{source}: collection entries must be mappings")
        collection_id = item.get("id")
        members = item.get("groups")
        if not isinstance(collection_id, str) or not isinstance(members, list):
            raise ValueError(f"{source}: collection needs id and groups")
        if collection_id in collections:
            raise ValueError(f"{source}: duplicate collection {collection_id!r}")
        member_ids = tuple(str(member) for member in members)
        unknown = sorted(set(member_ids) - available)
        if unknown:
            raise ValueError(f"{source}: unknown groups {unknown}")
        collections[collection_id] = InterfaceCollection(collection_id, member_ids)
    return MappingProxyType(collections)


def load_c_interface(root: str | Path, isa, c_abi) -> "CInterfaceProject":
    domain_root = Path(root).resolve()
    extensions = _load_extensions(domain_root)
    type_groups, loaded_types = _load_groups(
        domain_root, "types", "type", extensions
    )
    intrinsic_groups, loaded_intrinsics = _load_groups(
        domain_root, "intrinsics", "intrinsic", extensions
    )
    utility_groups, loaded_utilities = _load_groups(
        domain_root, "utilities", "utility", extensions
    )
    types = cast(ReferenceIndex[InterfaceType], loaded_types)
    intrinsics = cast(ReferenceIndex[InterfaceIntrinsic], loaded_intrinsics)
    utilities = cast(ReferenceIndex[InterfaceUtility], loaded_utilities)
    collections = _load_collections(domain_root, intrinsic_groups)
    entities = _build_entities(
        type_groups,
        intrinsic_groups,
        utility_groups,
        types,
        intrinsics,
        utilities,
    )
    project = CInterfaceProject(
        domain_root,
        extensions,
        type_groups,
        intrinsic_groups,
        utility_groups,
        types,
        intrinsics,
        utilities,
        collections,
        entities,
    )

    check_c_interface(project, isa, c_abi)
    return project


def check_c_interface(project, isa, c_abi):
    """Resolve every cross-domain and local entity reference."""

    for intrinsic in project.intrinsics.values():
        operation = _isa_target(isa, intrinsic.operation)
        allowed_isa_owners = _allowed_isa_owners(project, intrinsic.owner)
        if operation.owner not in allowed_isa_owners:
            raise ValueError(
                f"{intrinsic.source}: owner {intrinsic.owner!r} cannot lower "
                f"to ISA owner {operation.owner!r}; "
                f"allowed ISA owners are "
                f"{sorted(allowed_isa_owners)}"
            )
        for type_name in (intrinsic.result_type, *intrinsic.parameter_types):
            if isinstance(type_name, QualifiedReference):
                target_type = _interface_type(project, type_name)
                if not isinstance(target_type, InterfaceType):
                    raise ValueError(
                        f"{intrinsic.source}: signature reference does not "
                        "name an interface type"
                    )
    for interface_type in project.types.values():
        if interface_type.enum_source is not None:
            _isa_target(isa, interface_type.enum_source)
        for field_type in interface_type.field_types:
            if isinstance(field_type, QualifiedReference):
                target_type = _interface_type(project, field_type)
                if not isinstance(target_type, InterfaceType):
                    raise ValueError(
                        f"{interface_type.source}: field reference does not "
                        "name an interface type"
                    )
    resolve_type_layouts(project, isa, c_abi)


def resolve_type_layouts(project, isa, c_abi):
    ordered = order_types(project, tuple(project.types.values()))
    abi = c_abi
    primitive_layouts = {}
    pointer_layout = enum_layout = None
    for c_type in abi.types.values():
        if isinstance(c_type.size_bits, int):
            layout = (c_type.size_bits // 8, c_type.alignment_bytes)
            previous = primitive_layouts.setdefault(c_type.call_kind, layout)
            if previous != layout:
                raise ValueError(f"{c_type.source}: call kind has conflicting fixed C layouts")
            if c_type.id == "SIZE_T":
                primitive_layouts["size"] = layout
            if c_type.id == "OBJECT_POINTER":
                pointer_layout = layout
            if c_type.id == "INT":
                enum_layout = layout
    layouts = {}
    record_fields = {}
    for definition in ordered:
        if definition.kind == "enum":
            if enum_layout is None:
                raise ValueError(f"{definition.source}: enum requires the C int layout")
            layouts[definition.reference] = enum_layout
            continue
        if definition.kind != "struct":
            continue
        offset, alignment = 0, 1
        fields = []
        for field, field_type in zip(definition.data["fields"], definition.field_types, strict=True):
            if isinstance(field_type, QualifiedReference):
                member_layout = layouts.get(field_type.local)
            elif field_type.endswith("_pointer"):
                member_layout = pointer_layout
            else:
                member_layout = primitive_layouts.get(field_type)
            if member_layout is None:
                raise ValueError(f"{definition.source}: field has no fixed C layout: {field_type!r}")
            size, member_alignment = member_layout
            count = field.get("count", 1)
            if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
                raise ValueError(f"{definition.source}: field count must be positive")
            offset = (offset + member_alignment - 1) // member_alignment * member_alignment
            semantic_type = _interface_type(project, field_type) if isinstance(field_type, QualifiedReference) else field_type
            fields.append((str(field["id"]), semantic_type, field.get("count"), field.get("role") == "reserved", offset, count * size, member_alignment))
            offset += count * size
            alignment = max(alignment, member_alignment)
        size = (offset + alignment - 1) // alignment * alignment
        declared = definition.data.get("layout")
        if declared is not None and (declared.get("size"), declared.get("alignment")) != (size, alignment):
            raise ValueError(
                f"{definition.source}: declared layout disagrees with fields; "
                f"size is {size}, alignment is {alignment}"
            )
        layouts[definition.reference] = (size, alignment)
        record_fields[definition.reference] = tuple(fields)

    return MappingProxyType(layouts), MappingProxyType(record_fields)


def order_types(project, selected):
    """Order by-value type dependencies before their users; reject cycles."""
    active = set()
    visited = set()
    result = []

    def visit(definition):
        reference = definition.reference
        if reference in active:
            raise ValueError(f"{definition.source}: cyclic by-value C type dependency")
        if reference in visited:
            return
        active.add(reference)
        for field_type in definition.field_types:
            if isinstance(field_type, QualifiedReference):
                if field_type.domain != "interfaces.c":
                    raise ValueError(f"{definition.source}: field must name a C interface type")
                visit(project.types.resolve(field_type.local))
        active.remove(reference)
        visited.add(reference)
        result.append(definition)

    for definition in selected:
        visit(definition)
    return tuple(result)


def _allowed_isa_owners(project, owner):
    allowed = {"base"}
    pending = [owner] if owner != "base" else []
    seen: set[str] = set()
    while pending:
        extension_id = pending.pop()
        if extension_id in seen:
            continue
        seen.add(extension_id)
        extension = project.extensions[extension_id]
        allowed.update(extension.requires_isa)
        pending.extend(extension.requires)
    return allowed



def _isa_target(isa, reference):
    if reference.domain != "isa":
        raise ValueError(f"C interface reference must name an ISA entity: {reference!r}")
    return isa.resolve(reference.local)


def _interface_type(project, reference):
    if reference.domain != "interfaces.c":
        raise ValueError(f"signature must name a C interface type: {reference!r}")
    return project.types.resolve(reference.local)
