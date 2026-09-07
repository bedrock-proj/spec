"""Hierarchical architectural-register definitions and lookup."""

from __future__ import annotations

from engine.source.inventory import inspect_inventory


from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from engine.diagnostics import Diagnostic
from engine.entity import Entity
from engine.isa.extensions import extension_owner_roots, load_extension_inventory
from engine.reference import Reference, ReferenceIndex, register_reference
from engine.source.inventory import DirectoryInventory
from engine.source.yaml import load_schema_yaml, load_yaml


class RegisterGroupSourceConflictError(ValueError):
    """A register group declares more than one member source topology."""


class RegisterWidthDomainOrderError(ValueError):
    """A variable-width domain is not a strictly increasing set."""


@dataclass(frozen=True, slots=True)
class VariableRegisterWidth:
    """One symbolic register width and its permitted concrete bit widths."""

    expression: str
    values: tuple[int, ...]


    def __post_init__(self) -> None:
        object.__setattr__(self, "values", tuple(self.values))

    @property
    def minimum(self) -> int:
        return self.values[0]

    @property
    def maximum(self) -> int:
        return self.values[-1]


RegisterWidth = int | VariableRegisterWidth


class ResetSpec:
    """Base class for one architected reset policy."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class ConstantReset(ResetSpec):
    value: int


@dataclass(frozen=True, slots=True)
class SourcedReset(ResetSpec):
    source: Reference["Register"]


@dataclass(frozen=True, slots=True)
class LifecycleReset(ResetSpec):
    cold: str
    warm: str


@dataclass(frozen=True, slots=True)
class RegisterField(Entity):
    """One named field in an architectural register image."""

    reference: Reference["RegisterField"]
    source: Path
    id: str
    lsb: int
    bits: int
    summary: str | None = None

    @property
    def msb(self) -> int:
        return self.lsb + self.bits - 1

    def overlaps(self, other: "RegisterField") -> bool:
        return self.lsb <= other.msb and other.lsb <= self.msb


@dataclass(frozen=True, slots=True)
class RegisterLayout:
    """A fixed companion describing one shared or register-local image."""

    source: Path
    bits: int
    fields: tuple[RegisterField, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", tuple(self.fields))



@dataclass(frozen=True, slots=True)
class Register(Entity):
    """One architectural register, authored explicitly or expanded from a series."""

    reference: Reference["Register"]
    source: Path
    root: Path
    owner: str
    group: str
    id: str
    width: RegisterWidth
    encoding: int | None
    summary: str | None
    reset: ResetSpec | None
    layout: RegisterLayout | None


@dataclass(frozen=True, slots=True)
class RegisterSeries:
    """One homogeneous, consecutively encoded register family."""

    prefix: str
    count: int


@dataclass(frozen=True, slots=True)
class RegisterGroup(Entity):
    """A homogeneous architectural register group."""

    reference: Reference["RegisterGroup"]
    source: Path
    root: Path
    owner: str
    id: str
    width: RegisterWidth
    summary: str | None
    reset: ResetSpec | None
    layout: RegisterLayout | None
    registers: Mapping[str, Register]

    def __post_init__(self) -> None:
        object.__setattr__(self, "registers", MappingProxyType(dict(self.registers)))



@dataclass(frozen=True, slots=True)
class SeriesRegisterGroup(RegisterGroup):
    series: RegisterSeries


@dataclass(frozen=True, slots=True)
class ExplicitRegisterGroup(RegisterGroup):
    register_inventory: DirectoryInventory


@dataclass(frozen=True, slots=True)
class RegisterNamespace:
    """Register groups owned by base or one declared extension."""

    owner: str
    root: Path
    group_inventory: DirectoryInventory
    groups: Mapping[str, RegisterGroup]

    def __post_init__(self) -> None:
        object.__setattr__(self, "groups", MappingProxyType(dict(self.groups)))



@dataclass(frozen=True, slots=True)
class RegisterCatalog:
    """The union of hierarchical base and extension register namespaces."""

    namespaces: Mapping[str, RegisterNamespace]
    groups: ReferenceIndex[RegisterGroup]
    registers: ReferenceIndex[Register]
    fields: ReferenceIndex[RegisterField]



    def __post_init__(self) -> None:
        namespaces = dict(self.namespaces)
        groups: dict[Reference[RegisterGroup], RegisterGroup] = {}
        registers: dict[Reference[Register], Register] = {}
        fields: dict[Reference[RegisterField], RegisterField] = {}
        layouts: dict[Reference[RegisterField], RegisterLayout] = {}

        def include_layout(layout: RegisterLayout | None, owner: Reference) -> None:
            if layout is None:
                return
            seen: set[Reference[RegisterField]] = set()
            for field in layout.fields:
                if field.reference != Reference(owner.owner, (*owner.path, owner.element), field.id):
                    raise ValueError(f"{layout.source}: register field identity differs from its owning layout")
                if field.reference in seen:
                    raise ValueError(f"{layout.source}: duplicate register field {field.reference!r}")
                seen.add(field.reference)
                if field.reference in fields:
                    if fields[field.reference] is not field or layouts[field.reference] is not layout:
                        raise ValueError(f"{layout.source}: register field has more than one canonical layout")
                else:
                    register_reference(fields, field.reference, field)
                    layouts[field.reference] = layout

        for owner, namespace in namespaces.items():
            if owner != namespace.owner:
                raise ValueError("register namespace key differs from its owner")
            for group_id, group in namespace.groups.items():
                if group_id != group.id or group.owner != owner or group.reference != Reference(owner, ("registers",), group_id):
                    raise ValueError(f"{group.source}: register group identity differs from its namespace")
                register_reference(groups, group.reference, group)
                include_layout(group.layout, group.reference)
                for register_id, register in group.registers.items():
                    if register_id != register.id or register.owner != owner or register.group != group_id or register.reference != Reference(owner, ("registers", group_id), register_id):
                        raise ValueError(f"{register.source}: register identity differs from its group")
                    if group.layout is not None and register.layout is not group.layout:
                        raise ValueError(f"{register.source}: register must use its group's canonical layout")
                    register_reference(registers, register.reference, register)
                    include_layout(register.layout, group.reference if group.layout is not None else register.reference)
        for name, expected in (("groups", groups), ("registers", registers), ("fields", fields)):
            supplied = getattr(self, name)
            index = supplied if isinstance(supplied, ReferenceIndex) else ReferenceIndex(supplied)
            if set(index) != set(expected) or any(index[reference] is not value for reference, value in expected.items()):
                raise ValueError(f"register {name} index differs from its canonical tree")
            object.__setattr__(self, name, index)
        object.__setattr__(self, "namespaces", MappingProxyType(namespaces))

    @property
    def base(self) -> RegisterNamespace:
        return self.namespaces["base"]

    def namespace(self, owner: str) -> RegisterNamespace:
        try:
            return self.namespaces[owner]
        except KeyError as error:
            raise ValueError(f"unknown register namespace {owner!r}") from error


def _load_namespace(
    owner: str,
    namespace_root: Path,
    schemas: Mapping[str, Mapping[str, object]],
    references: dict,
) -> RegisterNamespace:
    groups_root = namespace_root / "registers/groups"
    inventory = _load_inventory(owner, "group", groups_root, "groups")
    groups: dict[str, RegisterGroup] = {}
    for group_id in inventory.declared:
        group_root = groups_root / group_id
        if group_id in groups or not group_root.is_dir():
            continue
        group = _load_group(owner, group_root, schemas, references)
        register_reference(references["groups"], group.reference, group)
        groups[group_id] = group
    return RegisterNamespace(owner, namespace_root, inventory, MappingProxyType(groups))


def _load_group(
    owner: str,
    root: Path,
    schemas: Mapping[str, Mapping[str, object]],
    references: dict,
) -> RegisterGroup:
    source = root / "group.yaml"
    raw = load_schema_yaml(source, schemas["group"])
    group_id = root.name
    reference: Reference[RegisterGroup] = Reference(owner, ("registers",), group_id)
    width = _decode_width(raw["width"], source)
    reset = decode_reset(raw.get("reset"))
    layout = load_register_layout(root / "layout.yaml", schemas["layout"], reference)
    if layout is not None:
        for field in layout.fields:
            register_reference(references["fields"], field.reference, field)
    registers_root = root / "registers"
    has_series = "series" in raw
    has_explicit = registers_root.is_dir()
    if has_series == has_explicit:
        raise RegisterGroupSourceConflictError(
            f"{source}: register group must own exactly one of a series or a registers directory"
        )

    register_inventory: DirectoryInventory | None = None
    series_spec: RegisterSeries | None = None
    registers: dict[str, Register] = {}
    if has_series:
        series = raw["series"]
        series_spec = RegisterSeries(series["prefix"], series["count"])
        for encoding in range(series_spec.count):
            register_id = f"{series_spec.prefix}{encoding}"
            register = Register(
                reference=Reference(owner, ("registers", group_id), register_id),
                source=source,
                root=root,
                owner=owner,
                group=group_id,
                id=register_id,
                width=width,
                encoding=encoding,
                summary=None,
                reset=reset,
                layout=layout,
            )
            register_reference(references["registers"], register.reference, register)
            registers[register_id] = register
    else:
        register_inventory = _load_inventory(
            owner, "register", registers_root, "registers"
        )
        for register_id in register_inventory.declared:
            register_root = registers_root / register_id
            if register_id in registers or not register_root.is_dir():
                continue
            register = _load_register(
                owner,
                group_id,
                register_root,
                width,
                reset,
                layout,
                schemas,
            )
            register_reference(references["registers"], register.reference, register)
            if register.layout is not None and register.layout is not layout:
                for field in register.layout.fields:
                    register_reference(references["fields"], field.reference, field)
            registers[register_id] = register

    common = (
        reference,
        source,
        root,
        owner,
        group_id,
        width,
        raw.get("summary"),
        reset,
        layout,
        MappingProxyType(registers),
    )
    if series_spec is not None:
        return SeriesRegisterGroup(*common, series_spec)
    assert register_inventory is not None
    return ExplicitRegisterGroup(*common, register_inventory)


def _load_register(
    owner: str,
    group_id: str,
    root: Path,
    width: RegisterWidth,
    group_reset: ResetSpec | None,
    group_layout: RegisterLayout | None,
    schemas: Mapping[str, Mapping[str, object]],
) -> Register:
    source = root / "register.yaml"
    raw = load_schema_yaml(source, schemas["register"])
    register_id = root.name
    reference: Reference[Register] = Reference(
        owner, ("registers", group_id), register_id
    )
    local_layout = load_register_layout(root / "layout.yaml", schemas["layout"], reference)
    if group_layout is not None and local_layout is not None:
        raise ValueError(
            f"{root}: register-local layout cannot replace the group's fixed layout"
        )
    return Register(
        reference=reference,
        source=source,
        root=root,
        owner=owner,
        group=group_id,
        id=register_id,
        width=width,
        encoding=raw.get("encoding"),
        summary=raw.get("summary"),
        reset=decode_reset(raw["reset"]) if "reset" in raw else group_reset,
        layout=local_layout or group_layout,
    )


def _decode_width(raw: Any, source: Path) -> RegisterWidth:
    if isinstance(raw, int):
        return raw
    if not isinstance(raw, dict) or len(raw) != 1:
        raise ValueError(
            f"{source}: variable register width must contain exactly one expression"
        )
    expression, raw_values = next(iter(raw.items()))
    values = tuple(raw_values)
    if values != tuple(sorted(set(values))):
        raise RegisterWidthDomainOrderError(
            f"{source}: widths for {expression!r} must be unique and increasing"
        )
    return VariableRegisterWidth(expression, values)


def load_register_layout(
    source: Path,
    schema: Mapping[str, object] | Path,
    owner: Reference[object],
) -> RegisterLayout | None:
    if not source.is_file():
        return None
    raw = load_schema_yaml(source, schema)
    return RegisterLayout(
        source=source,
        bits=raw["bits"],
        fields=tuple(
            RegisterField(
                reference=Reference(
                    owner.owner,
                    (*owner.path, owner.element),
                    field["id"],
                ),
                source=source,
                id=field["id"],
                lsb=field["lsb"],
                bits=field["bits"],
                summary=field.get("summary"),
            )
            for field in raw["fields"]
        ),
    )


def _load_inventory(owner: str, kind: str, root: Path, key: str) -> DirectoryInventory:
    return inspect_inventory(
        owner=owner,
        kind=kind,
        source=root / f"{key}.yaml",
        root=root,
        key=key,
        allow_missing=True,
        name_pattern=r"[A-Z][A-Z0-9_]*",
    )


def decode_reset(raw: object) -> ResetSpec | None:
    if raw is None:
        return None
    if isinstance(raw, int) and not isinstance(raw, bool):
        return ConstantReset(raw)
    if not isinstance(raw, Mapping):
        raise ValueError("reset must be an integer, a source reference, or a cold/warm policy")
    if set(raw) == {"from"}:
        return SourcedReset(Reference.parse(raw["from"]))
    if set(raw) != {"cold", "warm"} or not all(isinstance(raw[key], str) for key in ("cold", "warm")):
        raise ValueError("reset policy requires cold and warm strings")
    return LifecycleReset(raw["cold"], raw["warm"])






def check_registers(
    catalog: RegisterCatalog,
) -> Iterator[Diagnostic]:
    yield from _registers_validate_inventories(catalog)
    yield from _registers_validate_groups(catalog)


def _registers_validate_inventories(catalog: RegisterCatalog) -> Iterator[Diagnostic]:
    for namespace in catalog.namespaces.values():
        inventories = [namespace.group_inventory]
        inventories.extend(
            group.register_inventory
            for group in namespace.groups.values()
            if isinstance(group, ExplicitRegisterGroup)
        )
        for inventory in inventories:
            yield from _registers_validate_inventory(inventory)


def _registers_validate_inventory(
    inventory: DirectoryInventory,
) -> Iterator[Diagnostic]:
    from engine.diagnostics import _error

    for missing in inventory.missing:
        yield _error(
            f"register.{inventory.kind}.missing-directory",
            inventory.source,
            f"declared register {inventory.kind} {missing!r} has no directory",
        )
    for undeclared in inventory.undeclared:
        yield _error(
            f"register.{inventory.kind}.undeclared-directory",
            inventory.root / undeclared,
            f"register {inventory.kind} directory {undeclared!r} is not in "
            f"{inventory.source.name}",
        )
    for duplicate in inventory.duplicates:
        yield _error(
            f"register.{inventory.kind}.duplicate",
            inventory.source,
            f"register {inventory.kind} {duplicate!r} is listed more than once",
        )


def _registers_validate_groups(catalog: RegisterCatalog) -> Iterator[Diagnostic]:
    from engine.diagnostics import RelatedLocation, _error

    for namespace in catalog.namespaces.values():
        for group in namespace.groups.values():
            encodings: dict[int, Register] = {}
            for register in group.registers.values():
                if register.encoding is not None:
                    previous = encodings.get(register.encoding)
                    if previous is not None:
                        yield _error(
                            "register.encoding.duplicate",
                            register.source,
                            f"encoding {register.encoding} in group {group.id!r} "
                            "is used more than once",
                            "encoding",
                            related=(
                                RelatedLocation(
                                    previous.source, "conflicting register encoding"
                                ),
                            ),
                        )
                    else:
                        encodings[register.encoding] = register
                layout = register.layout
                if layout is None:
                    continue
                if isinstance(register.width, int) and layout.bits != register.width:
                    yield _error(
                        "register.layout.width",
                        layout.source,
                        f"layout has {layout.bits} bits but register {register.id!r} "
                        f"has width {register.width}",
                        "bits",
                    )
                for field in layout.fields:
                    if field.msb >= layout.bits:
                        yield _error(
                            "register.layout.field-range",
                            layout.source,
                            f"field {field.id!r} occupies bits {field.msb}..{field.lsb} "
                            f"outside a {layout.bits}-bit layout",
                            "fields",
                        )
                for index, left in enumerate(layout.fields):
                    for right in layout.fields[index + 1 :]:
                        if left.overlaps(right):
                            yield _error(
                                "register.layout.field-overlap",
                                layout.source,
                                f"fields {left.id!r} and {right.id!r} overlap",
                                "fields",
                            )














def load_registers(
    isa_root: str | Path,
    *,
    extensions: DirectoryInventory,
) -> "RegisterCatalog":
    root = Path(isa_root).resolve()
    schema_root = root / "schemas"
    owner_roots = extension_owner_roots(extensions)
    manifests = tuple(
        namespace_root / "registers/groups/groups.yaml"
        for _, namespace_root in owner_roots
    )
    schemas = (
        {
            "group": load_yaml(schema_root / "register-group.yaml"),
            "register": load_yaml(schema_root / "register.yaml"),
            "layout": load_yaml(schema_root / "register-layout.yaml"),
        }
        if any(path.is_file() for path in manifests)
        else {}
    )
    references = {"groups": {}, "registers": {}, "fields": {}}
    namespaces: dict[str, RegisterNamespace] = {}
    for owner, namespace_root in owner_roots:
        namespaces[owner] = _load_namespace(
            owner, namespace_root, schemas, references
        )
    return RegisterCatalog(
        namespaces=MappingProxyType(namespaces),
        groups=ReferenceIndex(references["groups"]),
        registers=ReferenceIndex(references["registers"]),
        fields=ReferenceIndex(references["fields"]),
    )


def register_selector_members(group: RegisterGroup) -> tuple[tuple[Register, int], ...]:
    return tuple(sorted(((register, register.encoding) for register in group.registers.values() if register.encoding is not None), key=lambda item: item[1]))
