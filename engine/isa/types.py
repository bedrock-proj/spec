"""Typed field and payload definition namespaces for one ISA tree."""

from __future__ import annotations

from collections.abc import Iterator
from engine.diagnostics import Diagnostic
from engine.diagnostics import _error
from engine.isa.registers import RegisterCatalog
from engine.isa.registers import RegisterGroup
from engine.reference import Reference
from engine.reference import ReferenceError

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from engine.source.yaml import load_yaml

from engine.entity import Entity
from engine.isa.extensions import extension_owner_roots, load_extension_inventory
from engine.reference import Reference, ReferenceIndex, register_reference
from engine.source.inventory import DirectoryInventory

if TYPE_CHECKING:
    from engine.isa.registers import RegisterGroup


@dataclass(frozen=True, slots=True)
class FieldValue:
    """One authored value/code pair for a selector field."""

    value: int
    code: str


@dataclass(frozen=True, slots=True)
class FieldType(Entity):
    """Common identity and width of a primary-encoding field type."""

    reference: Reference["FieldType"]
    source: Path
    owner: str
    id: str
    bits: int



@dataclass(frozen=True, slots=True)
class RegisterSelectorFieldType(FieldType):
    register_group: Reference["RegisterGroup"]


@dataclass(frozen=True, slots=True)
class EffectiveAddressFieldType(FieldType):
    profile: str


@dataclass(frozen=True, slots=True)
class RegisterFieldType(FieldType):
    register_group: Reference["RegisterGroup"]


@dataclass(frozen=True, slots=True)
class EnumConditionFieldType(FieldType):
    pass


@dataclass(frozen=True, slots=True)
class RegisterPairSelectorFieldType(FieldType):
    register_group: Reference["RegisterGroup"]


@dataclass(frozen=True, slots=True)
class PageTableLevelFieldType(FieldType):
    pass


@dataclass(frozen=True, slots=True)
class FlagsFieldType(FieldType):
    pass


@dataclass(frozen=True, slots=True)
class ImmediateFieldType(FieldType):
    value_type: str


@dataclass(frozen=True, slots=True)
class MemoryOrderFieldType(FieldType):
    pass


@dataclass(frozen=True, slots=True)
class SizeSelectorFieldType(FieldType):
    values: tuple[FieldValue, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", tuple(self.values))
        if any(not isinstance(value, FieldValue) for value in self.values):
            raise TypeError("size selector values must be FieldValue instances")


@dataclass(frozen=True, slots=True)
class PayloadType(Entity):
    """Common identity and width of an appended payload type."""

    reference: Reference["PayloadType"]
    source: Path
    owner: str
    id: str
    bytes: int



@dataclass(frozen=True, slots=True)
class ImmediatePayloadType(PayloadType):
    value_type: str


@dataclass(frozen=True, slots=True)
class PcDisplacementPayloadType(PayloadType):
    signed: bool


@dataclass(frozen=True, slots=True)
class PcAbsolutePayloadType(PayloadType):
    signed: bool


@dataclass(frozen=True, slots=True)
class FloatingPointConstantIdPayloadType(PayloadType):
    pass


@dataclass(frozen=True, slots=True)
class RegisterSelectorPayloadType(PayloadType):
    register_group: Reference["RegisterGroup"]


@dataclass(frozen=True, slots=True)
class ControlRegisterSelectorPayloadType(PayloadType):
    pass


def field_type_source_name(definition: FieldType) -> str:
    """Return the authored type spelling for machine-facing projections."""

    names = {
        RegisterSelectorFieldType: "register_selector",
        EffectiveAddressFieldType: "effective_address",
        RegisterFieldType: "register",
        EnumConditionFieldType: "enum_condition",
        RegisterPairSelectorFieldType: "register_pair_selector",
        PageTableLevelFieldType: "page_table_level",
        FlagsFieldType: "flags",
        ImmediateFieldType: "immediate",
        MemoryOrderFieldType: "memory_order",
        SizeSelectorFieldType: "size_selector",
    }
    try:
        return names[type(definition)]
    except KeyError as error:
        raise TypeError(
            f"unsupported field type {type(definition).__name__}"
        ) from error


def payload_type_source_name(definition: PayloadType) -> str:
    """Return the authored type spelling for machine-facing projections."""

    names = {
        ImmediatePayloadType: "immediate",
        PcDisplacementPayloadType: "pc_displacement",
        PcAbsolutePayloadType: "pc_absolute",
        FloatingPointConstantIdPayloadType: "floating_point_constant_id",
        RegisterSelectorPayloadType: "register_selector",
        ControlRegisterSelectorPayloadType: "control_register_selector",
    }
    try:
        return names[type(definition)]
    except KeyError as error:
        raise TypeError(
            f"unsupported payload type {type(definition).__name__}"
        ) from error


def payload_type_is_signed(definition: PayloadType) -> bool:
    """Return integer extension behavior from the closed payload type contract."""
    kind = type(definition)
    if kind in (PcDisplacementPayloadType, PcAbsolutePayloadType):
        if not isinstance(definition.signed, bool):
            raise ValueError("address payload signedness must be boolean")
        return definition.signed
    if kind is ImmediatePayloadType:
        if definition.value_type == "signed_integer":
            return True
        if definition.value_type in {"unsigned_integer", "ieee754_binary32", "ieee754_binary64"}:
            return False
        raise ValueError(f"unknown immediate payload value type {definition.value_type!r}")
    if kind in (RegisterSelectorPayloadType, ControlRegisterSelectorPayloadType, FloatingPointConstantIdPayloadType):
        return False
    raise TypeError(f"unsupported payload type {kind.__name__}")


@dataclass(frozen=True, slots=True)
class TypeNamespace:
    """Field and payload types owned by base or one declared extension."""

    owner: str
    root: Path
    field_types: ReferenceIndex[FieldType]
    payload_types: ReferenceIndex[PayloadType]

    def __post_init__(self) -> None:
        for name, kind in (("field_types", FieldType), ("payload_types", PayloadType)):
            index = getattr(self, name)
            if not isinstance(index, ReferenceIndex):
                index = ReferenceIndex(index)
                object.__setattr__(self, name, index)
            for reference, member in index.items():
                if not isinstance(member, kind):
                    raise TypeError(f"{name} contains a value of the wrong type")
                if reference != member.reference or member.owner != self.owner or reference != Reference(self.owner, (name,), member.id):
                    raise ValueError("type namespace and member identities differ")
                if member.source != self.root / f"{name}.yaml":
                    raise ValueError(f"{member.source}: type source differs from its owning namespace")




@dataclass(frozen=True, slots=True)
class TypeSystem:
    """Global type indexes projected from base and declared extension namespaces."""

    base: TypeNamespace
    extensions: Mapping[str, TypeNamespace]
    field_types: ReferenceIndex[FieldType]
    payload_types: ReferenceIndex[PayloadType]


    def __post_init__(self) -> None:
        object.__setattr__(self, "extensions", MappingProxyType(dict(self.extensions)))
        if self.base.owner != "base" or "base" in self.extensions:
            raise ValueError("type system base and extension scopes must be distinct")
        if any(owner != namespace.owner for owner, namespace in self.extensions.items()):
            raise ValueError("type namespace key differs from its owner")
        for name in ("field_types", "payload_types"):
            index = getattr(self, name)
            if not isinstance(index, ReferenceIndex):
                index = ReferenceIndex(index)
                object.__setattr__(self, name, index)
            members = {}
            for namespace in (self.base, *self.extensions.values()):
                for reference, member in getattr(namespace, name).items():
                    register_reference(members, reference, member)
            if set(index) != set(members) or any(index[reference] is not member for reference, member in members.items()):
                raise ValueError(f"{name} index does not preserve canonical namespace members")

    def namespace(self, owner: str) -> TypeNamespace:
        """Resolve the type namespace owned by base or a declared extension."""

        if owner == "base":
            return self.base
        try:
            return self.extensions[owner]
        except KeyError as error:
            raise ValueError(f"unknown type namespace {owner!r}") from error


def _load_field_types(owner: str, path: Path) -> dict[Reference[FieldType], FieldType]:
    index = {}
    for name, definition in _load_definitions(path, "field_types").items():
        reference: Reference[FieldType] = Reference(owner, ("field_types",), name)
        register_reference(
            index,
            reference,
            decode_field_type(reference, path, owner, name, definition),
        )
    return index


def _load_payload_types(owner: str, path: Path) -> dict[Reference[PayloadType], PayloadType]:
    index = {}
    for name, definition in _load_definitions(path, "payload_types").items():
        reference: Reference[PayloadType] = Reference(owner, ("payload_types",), name)
        register_reference(
            index,
            reference,
            decode_payload_type(reference, path, owner, name, definition),
        )
    return index


def _load_definitions(path: Path, collection: str) -> dict[str, Mapping[str, Any]]:
    if not path.is_file():
        return {}
    document = load_yaml(path)
    if not isinstance(document, Mapping):
        raise ValueError(f"{path}: expected a YAML mapping")
    definitions = document.get(collection)
    if not isinstance(definitions, Mapping):
        raise ValueError(f"{path}: {collection} must be a mapping")
    result: dict[str, Mapping[str, Any]] = {}
    for name, definition in definitions.items():
        if not isinstance(name, str) or not isinstance(definition, Mapping):
            raise ValueError(f"{path}: {collection} entries must be named mappings")
        result[name] = definition
    return result


def _reject_unknown_properties(
    type_id: str, data: Mapping[str, Any], allowed: set[str]
) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{type_id}: unknown properties {unknown}")


def _required_string(type_id: str, data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{type_id}: {key} must be a non-empty string")
    return value


def _required_reference(
    type_id: str, data: Mapping[str, Any], key: str
) -> Reference["RegisterGroup"]:
    return Reference.parse(_required_string(type_id, data, key))


def _positive_int(type_id: str, value: object, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{type_id}: {key} must be a positive integer")
    return value


def _field_values(
    type_id: str, raw_values: object, bits: int
) -> tuple[FieldValue, ...]:
    if not isinstance(raw_values, (list, tuple)):
        raise ValueError(f"{type_id}: values must be an array")
    values: list[FieldValue] = []
    for index, raw in enumerate(raw_values):
        if not isinstance(raw, Mapping) or set(raw) != {"value", "code"}:
            raise ValueError(f"{type_id}: values[{index}] must contain value and code")
        value = raw["value"]
        code = raw["code"]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{type_id}: values[{index}].value must be an integer")
        if value < 0 or value >= 1 << bits:
            raise ValueError(
                f"{type_id}: selector value {value} does not fit in {bits} bits"
            )
        if not isinstance(code, str) or not code:
            raise ValueError(
                f"{type_id}: values[{index}].code must be a non-empty string"
            )
        values.append(FieldValue(value, code))
    if len({item.value for item in values}) != len(values):
        raise ValueError(f"{type_id}: selector values must be unique")
    if len({item.code for item in values}) != len(values):
        raise ValueError(f"{type_id}: selector codes must be unique")
    return tuple(values)


def check_register_types(
    catalog: RegisterCatalog, types: TypeSystem
) -> Iterator[Diagnostic]:
    from engine.diagnostics import _error

    for field in types.field_types.values():
        if not isinstance(
            field,
            (
                RegisterFieldType,
                RegisterSelectorFieldType,
                RegisterPairSelectorFieldType,
            ),
        ):
            continue
        group = _resolve_group(catalog, field.register_group)
        if group is None:
            yield _error(
                "register.group.unknown",
                field.source,
                f"field type {field.id!r} uses an unknown register group",
                "field_types",
                field.id,
                "register_group",
            )
            continue
        if isinstance(field, RegisterPairSelectorFieldType):
            yield from _validate_pair_type(group, field.bits, field.source)
        else:
            yield from _validate_direct_type(
                group,
                field.bits,
                field.source,
                require_complete=isinstance(field, RegisterSelectorFieldType),
            )

    for payload in types.payload_types.values():
        if not isinstance(payload, RegisterSelectorPayloadType):
            continue
        group = _resolve_group(catalog, payload.register_group)
        if group is None:
            yield _error(
                "register.group.unknown",
                payload.source,
                f"payload type {payload.id!r} uses an unknown register group",
                "payload_types",
                payload.id,
                "register_group",
            )
            continue
        yield from _validate_direct_type(
            group, payload.bytes * 8, payload.source, require_complete=True
        )


def _resolve_group(
    catalog: RegisterCatalog,
    reference: Reference[RegisterGroup],
) -> RegisterGroup | None:
    from engine.reference import ReferenceError

    try:
        return catalog.groups.resolve(reference)
    except (ReferenceError, ValueError):
        return None


def _validate_direct_type(
    group: RegisterGroup,
    bits: int,
    source: Path,
    *,
    require_complete: bool,
) -> Iterator[Diagnostic]:
    from engine.diagnostics import _error

    limit = 1 << bits
    for register in group.registers.values():
        if register.encoding is None:
            if require_complete:
                yield _error(
                    "register.encoding.missing",
                    register.source,
                    f"register {register.id!r} has no encoding for the "
                    f"selector declared in {source}",
                    "encoding",
                )
            continue
        if register.encoding >= limit:
            yield _error(
                "register.encoding.range",
                register.source,
                f"register {register.id!r} encoding {register.encoding} does "
                f"not fit the {bits}-bit selector declared in {source}",
                "encoding",
            )


def _validate_pair_type(
    group: RegisterGroup, bits: int, source: Path
) -> Iterator[Diagnostic]:
    from engine.diagnostics import _error

    encodings = sorted(
        register.encoding
        for register in group.registers.values()
        if register.encoding is not None
    )
    if len(encodings) != len(group.registers) or encodings != list(
        range(len(group.registers))
    ):
        yield _error(
            "register.pair.layout",
            group.source,
            f"pair-selected group {group.id!r} must use contiguous "
            "zero-based member encodings",
        )
        return
    if len(group.registers) % 2:
        yield _error(
            "register.pair.odd-count",
            group.source,
            f"pair-selected group {group.id!r} has an odd member count",
        )
    if (len(group.registers) + 1) // 2 > 1 << bits:
        yield _error(
            "register.pair.range",
            group.source,
            f"register pairs in {group.id!r} do not fit the {bits}-bit "
            f"selector declared in {source}",
        )


def decode_field_type(
    reference: Reference["FieldType"],
    source: Path,
    owner: str,
    type_id: str,
    data: Mapping[str, Any],
) -> "FieldType":
    raw_kind = _required_string(type_id, data, "type")
    bits = _positive_int(type_id, data.get("bits"), "bits")
    common = (reference, source, owner, type_id, bits)
    if raw_kind == "register_selector":
        _reject_unknown_properties(
            type_id, data, {"type", "bits", "register_group"}
        )
        return RegisterSelectorFieldType(
            *common, _required_reference(type_id, data, "register_group")
        )
    if raw_kind == "effective_address":
        _reject_unknown_properties(type_id, data, {"type", "bits", "profile"})
        return EffectiveAddressFieldType(
            *common, _required_string(type_id, data, "profile")
        )
    if raw_kind == "register":
        _reject_unknown_properties(
            type_id, data, {"type", "bits", "register_group"}
        )
        return RegisterFieldType(
            *common, _required_reference(type_id, data, "register_group")
        )
    if raw_kind == "enum_condition":
        _reject_unknown_properties(type_id, data, {"type", "bits"})
        return EnumConditionFieldType(*common)
    if raw_kind == "register_pair_selector":
        _reject_unknown_properties(
            type_id, data, {"type", "bits", "register_group"}
        )
        return RegisterPairSelectorFieldType(
            *common, _required_reference(type_id, data, "register_group")
        )
    if raw_kind == "page_table_level":
        _reject_unknown_properties(type_id, data, {"type", "bits"})
        return PageTableLevelFieldType(*common)
    if raw_kind == "flags":
        _reject_unknown_properties(type_id, data, {"type", "bits"})
        return FlagsFieldType(*common)
    if raw_kind == "immediate":
        _reject_unknown_properties(type_id, data, {"type", "bits", "value_type"})
        return ImmediateFieldType(
            *common, _required_string(type_id, data, "value_type")
        )
    if raw_kind == "memory_order":
        _reject_unknown_properties(type_id, data, {"type", "bits"})
        return MemoryOrderFieldType(*common)
    if raw_kind == "size_selector":
        _reject_unknown_properties(type_id, data, {"type", "bits", "values"})
        values = _field_values(type_id, data.get("values", ()), bits)
        if not values:
            raise ValueError(f"{source}: {type_id}: size_selector requires values")
        return SizeSelectorFieldType(*common, values)
    raise ValueError(f"{source}: {type_id}: unknown field type {raw_kind!r}")


def decode_payload_type(
    reference: Reference["PayloadType"],
    source: Path,
    owner: str,
    type_id: str,
    data: Mapping[str, Any],
) -> "PayloadType":
    raw_kind = _required_string(type_id, data, "type")
    byte_count = _positive_int(type_id, data.get("bytes"), "bytes")
    common = (reference, source, owner, type_id, byte_count)
    if raw_kind == "immediate":
        _reject_unknown_properties(type_id, data, {"type", "bytes", "value_type"})
        definition = ImmediatePayloadType(*common, _required_string(type_id, data, "value_type"))
        payload_type_is_signed(definition)
        return definition
    if raw_kind in {"pc_displacement", "pc_absolute"}:
        _reject_unknown_properties(type_id, data, {"type", "bytes", "signed"})
        signed = data.get("signed")
        if not isinstance(signed, bool):
            raise ValueError(f"{source}: {type_id}: {raw_kind} requires boolean signed")
        if raw_kind == "pc_displacement":
            return PcDisplacementPayloadType(*common, signed)
        return PcAbsolutePayloadType(*common, signed)
    if raw_kind == "floating_point_constant_id":
        _reject_unknown_properties(type_id, data, {"type", "bytes"})
        return FloatingPointConstantIdPayloadType(*common)
    if raw_kind == "register_selector":
        _reject_unknown_properties(
            type_id, data, {"type", "bytes", "register_group"}
        )
        return RegisterSelectorPayloadType(
            *common, _required_reference(type_id, data, "register_group")
        )
    if raw_kind == "control_register_selector":
        _reject_unknown_properties(type_id, data, {"type", "bytes"})
        return ControlRegisterSelectorPayloadType(*common)
    raise ValueError(f"{source}: {type_id}: unknown payload type {raw_kind!r}")


def load_type_namespace( owner: str, root: str | Path) -> "TypeNamespace":
    namespace_root = Path(root)
    return TypeNamespace(
        owner=owner,
        root=namespace_root,
        field_types=ReferenceIndex(
            _load_field_types(owner, namespace_root / "field_types.yaml")
        ),
        payload_types=ReferenceIndex(
            _load_payload_types(owner, namespace_root / "payload_types.yaml")
        ),
    )


def load_type_system(
    isa_root: str | Path,
    *,
    extensions: DirectoryInventory,
) -> "TypeSystem":
    root = Path(isa_root).resolve()
    catalog = extensions
    owner_roots = extension_owner_roots(catalog)
    base_owner, base_root = owner_roots[0]
    base = load_type_namespace(base_owner, base_root)
    extension_namespaces: dict[str, TypeNamespace] = {}
    for extension_id, extension_root in owner_roots[1:]:
        if extension_root.is_dir():
            extension_namespaces[extension_id] = load_type_namespace(
                extension_id, extension_root
            )

    field_types = {}
    payload_types = {}
    for namespace in (base, *extension_namespaces.values()):
        for field_reference, field_definition in namespace.field_types.items():
            register_reference(field_types, field_reference, field_definition)
        for (
            payload_reference,
            payload_definition,
        ) in namespace.payload_types.items():
            register_reference(payload_types, payload_reference, payload_definition)

    return TypeSystem(
        base=base,
        extensions=MappingProxyType(extension_namespaces),
        field_types=ReferenceIndex(field_types),
        payload_types=ReferenceIndex(payload_types),
    )
