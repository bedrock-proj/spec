"""Representation and validation of one effective-address mode YAML file."""

from __future__ import annotations


import ast
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from engine.source.yaml import load_yaml
from engine.source.inventory import DirectoryInventory, require_exact
from jsonschema import Draft202012Validator

from engine.entity import Entity
from engine.isa.types import (
    EffectiveAddressFieldType,
    FieldType,
    PayloadType,
    RegisterFieldType,
    RegisterSelectorFieldType,
    TypeSystem,
)
from engine.reference import Reference, ReferenceError
from engine.syntax.encoding import EncodingMetasyntax


class EAModeSchemaError(ValueError):
    """An EA mode document violates its structural schema."""


class EABaseSource(StrEnum):
    """Typed source of the base term in one EA mode expression."""

    NONE = "none"
    ENCODED = "encoded"
    STACK_POINTER = "SP"
    PROGRAM_COUNTER = "PC"
    ZERO = "zero"


@dataclass(frozen=True, slots=True)
class EALiteral:
    value: int


@dataclass(frozen=True, slots=True)
class EAIdentifier:
    name: str


@dataclass(frozen=True, slots=True)
class EANegation:
    operand: EAExpression


@dataclass(frozen=True, slots=True)
class EABinary:
    operator: str
    left: EAExpression
    right: EAExpression


EAExpression = EALiteral | EAIdentifier | EANegation | EABinary


def ea_gpr_index(name: str) -> int | None:
    """Decode an explicit scalar address-register name, R0 through R15."""
    return int(name[1:]) if re.fullmatch(r"R([0-9]|1[0-5])", name) else None


def _ea_syntax(pseudocode: str) -> ast.Module:
    tokens = re.findall(r"\w+|[^\s]", pseudocode)
    for token in tokens:
        if not re.fullmatch(r"(?:0|[1-9][0-9]*|[a-z][a-z0-9_]*|[A-Z][A-Z0-9_]*|[-+*()=])", token):
            raise ValueError(f"invalid EA pseudocode token {token!r}")
    return ast.parse(" ".join(tokens), mode="exec")


def ea_expression(expression: ast.expr) -> EAExpression:
    """Convert the validated source expression into its immutable semantic tree."""
    def convert(node: ast.expr) -> EAExpression:
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return EALiteral(node.value)
        if isinstance(node, ast.Name):
            return EAIdentifier(node.id)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return EANegation(convert(node.operand))
        if isinstance(node, ast.BinOp):
            operators = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*"}
            if type(node.op) in operators:
                return EABinary(operators[type(node.op)], convert(node.left), convert(node.right))
        raise ValueError(f"unsupported EA expression node {type(node).__name__}")

    return convert(expression)


def _load_name_list(path: Path, key: str) -> tuple[str, ...]:
    raw = load_yaml(path)
    values = raw.get(key) if isinstance(raw, Mapping) else None
    if not isinstance(values, list) or any(
        not isinstance(item, str) for item in values
    ):
        raise ValueError(f"{path}: expected a {key} list of names")
    if len(set(values)) != len(values):
        raise ValueError(f"{path}: {key} must not contain duplicates")
    return tuple(values)


@dataclass(frozen=True, slots=True)
class EAModeCatalog:
    """One explicitly named EA mode catalog in the architectural namespace."""

    source: Path
    owner: str
    profile: str
    mode_type: str
    name: str
    modes: tuple[str, ...]





    def __post_init__(self) -> None:
        object.__setattr__(self, "modes", tuple(self.modes))
        if len(set(self.modes)) != len(self.modes):
            raise ValueError(f"{self.source}: modes must not contain duplicates")

    def reference(self, mode_id: str) -> Reference["EAMode"]:
        if mode_id not in self.modes:
            raise ValueError(f"{self.source}: undeclared EA mode {mode_id!r}")
        return Reference(self.owner, (self.profile, "modes", self.mode_type), mode_id)

    def mode_path(self, mode_id: str) -> Path:
        self.reference(mode_id)
        return self.source.parent / mode_id / "mode.yaml"


@dataclass(frozen=True, slots=True)
class EAField:
    """One encoded field used by every encoding of an EA mode."""

    symbol: str
    role: str
    type: Reference[FieldType]


@dataclass(frozen=True, slots=True)
class EAPayload:
    """One payload consumed after an EA selector or descriptor."""

    role: str
    type: Reference[PayloadType]


@dataclass(frozen=True, slots=True)
class EAAutoupdate:
    """One architected register update attached to an EA encoding."""

    target: str
    update_type: str
    difference: int | str


@dataclass(frozen=True, slots=True)
class EAEncoding:
    """One normalized wire encoding of an EA mode."""

    patterns: tuple[str, ...]
    payloads: tuple[EAPayload, ...]
    autoupdate: EAAutoupdate | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "patterns", tuple(self.patterns))
        object.__setattr__(self, "payloads", tuple(self.payloads))



@dataclass(frozen=True, slots=True)
class FixedEASegment:
    register: str


@dataclass(frozen=True, slots=True)
class FieldEASegment:
    role: str


EASegment = FixedEASegment | FieldEASegment


@dataclass(frozen=True, slots=True)
class EAExtension:
    id: str
    bytes: int


@dataclass(frozen=True, slots=True)
class EAMode(Entity):
    """Common immutable identity and encodings of an effective-address mode."""

    reference: Reference["EAMode"]
    source: Path
    catalog: EAModeCatalog
    id: str
    name: str
    encodings: tuple[EAEncoding, ...]
    fields: tuple[EAField, ...]



    def __post_init__(self) -> None:
        object.__setattr__(self, "encodings", tuple(self.encodings))
        object.__setattr__(self, "fields", tuple(self.fields))
        if self.reference != self.catalog.reference(self.id) or self.source != self.catalog.mode_path(self.id):
            raise ValueError(f"{self.source}: EA mode identity or source differs from its declaring catalog")

    def field(self, symbol: str) -> EAField:
        return next(field for field in self.fields if field.symbol == symbol)

    def field_for_role(self, role: str) -> EAField | None:
        return next((field for field in self.fields if field.role == role), None)

    def field_type_reference(self, symbol: str) -> Reference[FieldType]:
        return self.field(symbol).type

    def payload_type_reference(
        self, encoding_index: int, payload_index: int
    ) -> Reference[PayloadType]:
        return self.encodings[encoding_index].payloads[payload_index].type



@dataclass(frozen=True, slots=True)
class ImmediateEAMode(EAMode):
    syntax: str
    expression: EAExpression


@dataclass(frozen=True, slots=True)
class MemoryEAMode(EAMode):
    syntax: str
    expression: EAExpression
    segment: EASegment | None
    base_source: EABaseSource


@dataclass(frozen=True, slots=True)
class CompactExtensionEAMode(EAMode):
    extension: EAExtension


def immediate_ea_selector_values(
    modes: Iterable[EAMode], field_type: EffectiveAddressFieldType
) -> frozenset[int]:
    """Return compact selectors whose mode yields an immediate EA value."""

    values: set[int] = set()
    for mode in modes:
        if (
            not isinstance(mode, ImmediateEAMode)
            or mode.catalog.owner != field_type.owner
            or mode.catalog.profile != field_type.profile
            or mode.catalog.mode_type != "compact"
        ):
            continue
        for encoding in mode.encodings:
            pattern = EncodingMetasyntax.parse(encoding.patterns)
            values.update(
                value
                for value in range(1 << field_type.bits)
                if pattern.matches(value)
            )
    return frozenset(values)


def validate_ea_modes(modes: tuple[EAMode, ...]) -> None:
    """Validate selectors, descriptor widths, and roles in each composed EA."""
    groups = {}
    for mode in modes:
        key = (mode.catalog.owner, mode.catalog.profile, mode.catalog.mode_type)
        patterns = groups.setdefault(key, [])
        for encoding in mode.encodings:
            pattern = EncodingMetasyntax.parse(encoding.patterns)
            for previous, _, other in patterns:
                if pattern.overlaps(other):
                    raise ValueError(
                        f"{mode.source}: EA encoding overlaps {previous.source}"
                    )
            patterns.append((mode, encoding, pattern))
    for mode in modes:
        if not isinstance(mode, CompactExtensionEAMode):
            continue
        key = (mode.catalog.owner, mode.catalog.profile, mode.extension.id)
        targets = groups.get(key)
        if not targets:
            raise ValueError(f"{mode.source}: unknown EA descriptor family {mode.extension.id!r}")
        compact_fields = {field.role for field in mode.fields}
        compact_payloads = {
            payload.role for encoding in mode.encodings for payload in encoding.payloads
        }
        for target, encoding, pattern in targets:
            if pattern.bit_width != mode.extension.bytes * 8:
                raise ValueError(
                    f"{target.source}: descriptor has {pattern.bit_width} bits; "
                    f"{mode.source} requires {mode.extension.bytes * 8}"
                )
            duplicate_fields = compact_fields & {field.role for field in target.fields}
            if duplicate_fields:
                raise ValueError(
                    f"{target.source}: EA field roles {sorted(duplicate_fields)} also "
                    f"declared by selecting compact mode {mode.source}"
                )
            duplicate_payloads = compact_payloads & {payload.role for payload in encoding.payloads}
            if duplicate_payloads:
                raise ValueError(
                    f"{target.source}: EA payload roles {sorted(duplicate_payloads)} also "
                    f"declared by selecting compact mode {mode.source}"
                )


def load_ea_catalog(source: str | Path, *, owner: str, profile: str, mode_type: str) -> EAModeCatalog:
    path = Path(source).resolve()
    raw = load_yaml(path)
    if not isinstance(raw, Mapping) or set(raw) != {"name", "modes"}:
        raise ValueError(f"{path}: expected exactly name and modes")
    name = raw["name"]
    modes = raw["modes"]
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{path}: name must be a non-empty string")
    if not isinstance(modes, list) or any(
        not isinstance(item, str) or re.fullmatch(r"[a-z][a-z0-9_]*", item) is None
        for item in modes
    ):
        raise ValueError(f"{path}: modes must be a list of names")
    try:
        Reference(owner, (profile, "modes"), mode_type)
        for mode_id in modes:
            Reference(owner, (profile, "modes", mode_type), mode_id)
    except ReferenceError as error:
        raise ValueError(f"{path}: invalid EA catalog identity: {error}") from error
    actual = tuple(sorted(member.name for member in path.parent.iterdir()
                          if member.is_dir() and not member.name.startswith(".")))
    require_exact(DirectoryInventory(owner, "EA mode", path, path.parent, tuple(modes), actual))
    return EAModeCatalog(path, owner, profile, mode_type, name.strip(), tuple(modes))


def load_ea_catalogs(root: str | Path, *, field_types, owners: tuple[str, ...]) -> tuple[EAModeCatalog, ...]:
    root = Path(root).resolve()
    profiles: dict[str, str] = {}
    for definition in field_types.values():
        if not isinstance(definition, EffectiveAddressFieldType):
            continue
        if definition.owner in profiles:
            raise ValueError(
                f"{root}: owner {definition.owner!r} has multiple EA profiles"
            )
        profiles[definition.owner] = definition.profile
    owner_order = owners
    catalogs: list[EAModeCatalog] = []
    for owner in owner_order:
        profile = profiles.get(owner)
        if profile is None:
            continue
        profile_root = (
            root / profile
            if owner == "base"
            else root / "extensions" / owner / profile
        )
        modes_root = profile_root / "modes"
        mode_types = _load_name_list(modes_root / "mode_types.yaml", "mode_types")
        for mode_type in mode_types:
            catalogs.append(
                load_ea_catalog(
                    modes_root / mode_type / "modes.yaml",
                    owner=owner,
                    profile=profile,
                    mode_type=mode_type,
                )
            )
    return tuple(catalogs)


def catalog_for_mode(source: str | Path, catalogs: Iterable[EAModeCatalog]) -> EAModeCatalog:
    source = Path(source).resolve()
    matches = [
        catalog
        for catalog in catalogs
        if source
        in {
            (catalog.source.parent / mode_id / "mode.yaml").resolve()
            for mode_id in catalog.modes
        }
    ]
    if len(matches) != 1:
        raise ValueError(
            f"{source}: expected exactly one declaring EA catalog, found "
            f"{len(matches)}"
        )
    return matches[0]


def _resolve_ea_types(data: Mapping[str, Any], source: Path):
    field_references: dict[str, Reference[FieldType]] = {}
    for symbol, field in data.get("fields", {}).items():
        try:
            field_references[symbol] = Reference.parse(field["type"])
        except ReferenceError as error:
            raise ValueError(
                f"{source}: invalid field type {field['type']!r}"
            ) from error

    payload_references: dict[tuple[int, int], Reference[PayloadType]] = {}
    for encoding_index, encoding in enumerate(data["encodings"]):
        for payload_index, payload in enumerate(encoding.get("payloads", ())):
            try:
                payload_references[encoding_index, payload_index] = Reference.parse(
                    payload["type"]
                )
            except ReferenceError as error:
                raise ValueError(
                    f"{source}: invalid payload type {payload['type']!r}"
                ) from error
    return field_references, payload_references


def _resolve_profile_field_type(source: Path, catalog: EAModeCatalog, field_types):
    profile = catalog.profile
    candidates = [
        (reference, definition)
        for reference, definition in field_types.items()
        if isinstance(definition, EffectiveAddressFieldType)
        and definition.profile == profile
        and definition.owner == catalog.owner
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"{source}: expected one effective-address field type for "
            f"profile {profile!r}; found {len(candidates)}"
        )
    return candidates[0]


def _validate_ea_source(data, source: Path, *, schema, catalog, field_types, payload_types):
    """Validate this file without performing catalog-wide checks."""

    compact = catalog.mode_type == "compact"
    errors = sorted(
        Draft202012Validator(schema).iter_errors(data),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path)
        where = f" at {location}" if location else ""
        raise EAModeSchemaError(f"{source}{where}: {error.message}")

    field_type_references, payload_type_references = _resolve_ea_types(data, source)

    variants = [
        (
            EncodingMetasyntax.parse(encoding["pattern"]),
            encoding.get("payloads", []),
            encoding.get("autoupdate"),
            index,
        )
        for index, encoding in enumerate(data["encodings"])
    ]
    patterns = [pattern for pattern, _, _, _ in variants]
    if len(patterns) != len(set(patterns)):
        raise ValueError(f"{source}: encoding patterns must be unique")

    if compact:
        profile_type, profile_definition = _resolve_profile_field_type(source, catalog, field_types)
        expected = profile_definition.bits
        for pattern in patterns:
            if pattern.bit_width != expected:
                raise ValueError(
                    f"{source}: compact pattern has {pattern.bit_width} bits; "
                    f"{profile_type!r} requires {expected}"
                )

    fields = data.get("fields", {})
    for pattern, _, _, index in variants:
        if set(fields) != pattern.fields:
            variant = f" encoding {index}" if index is not None else ""
            raise ValueError(
                f"{source}:{variant} fields {sorted(fields)} do not match "
                f"pattern characters {sorted(pattern.fields)}"
            )

    field_roles: list[str] = []
    for character, field in fields.items():
        field_type = field["type"]
        try:
            field_definition = field_types.resolve(field_type_references[character])
        except ReferenceError as error:
            raise ValueError(
                f"{source}: unknown field type {field_type!r}"
            ) from error
        role = field["role"]
        required_kind = RegisterFieldType if role == "segment" else RegisterSelectorFieldType
        required_group = Reference("base", ("registers",), "SEGMENT" if role == "segment" else "GPR")
        if (
            not isinstance(field_definition, required_kind)
            or field_definition.register_group != required_group
        ):
            raise ValueError(
                f"{source}: field {character!r} with role {role!r} requires "
                f"a {required_kind.__name__} for base.registers.{required_group.element}"
            )
        expected = field_definition.bits
        for pattern, _, _, index in variants:
            actual = pattern.field_width(character)
            if actual != expected:
                variant = f" encoding {index}" if index is not None else ""
                raise ValueError(
                    f"{source}:{variant} field {character!r} occupies "
                    f"{actual} bits; {field_type} requires {expected}"
                )
        field_roles.append(field["role"])
    if len(field_roles) != len(set(field_roles)):
        raise ValueError(f"{source}: field roles must be unique")

    payload_roles: list[str] = []
    for _, payloads, _, index in variants:
        variant_roles: list[str] = []
        for payload_index, payload in enumerate(payloads):
            payload_type = payload["type"]
            try:
                payload_types.resolve(payload_type_references[index, payload_index])
            except ReferenceError as error:
                raise ValueError(
                    f"{source}: unknown payload type {payload_type!r}"
                ) from error
            role = payload["role"]
            variant_roles.append(role)
            if role not in payload_roles:
                payload_roles.append(role)
        if len(variant_roles) != len(set(variant_roles)):
            variant = f" encoding {index}" if index is not None else ""
            raise ValueError(
                f"{source}:{variant} payload roles must be unique"
            )

    segment = data.get("segment")
    if (
        segment
        and segment["source"] == "field"
        and segment["role"] not in field_roles
    ):
        raise ValueError(f"{source}: segment role has no matching field")
    for _, _, autoupdate, index in variants:
        if autoupdate and autoupdate["target"] not in field_roles:
            raise ValueError(
                f"{source}: encoding {index} autoupdate target has no matching field"
            )
    expression = _validate_ea_expression(data, source, field_roles, payload_roles, compact)
    return field_type_references, payload_type_references, expression


def _validate_ea_expression(data, source: Path, field_roles, payload_roles, compact: bool) -> EAExpression | None:
    kind = data["kind"]
    if kind == "extension":
        return None

    text = data["pseudocode"]

    try:
        tree = _ea_syntax(text)
    except (SyntaxError, ValueError) as error:
        raise ValueError(f"{source}: invalid pseudocode expression") from error
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assign):
        raise ValueError(f"{source}: pseudocode must contain one assignment")
    assignment = tree.body[0]
    expected_result = "offset" if kind == "memory" else "value"
    if (
        len(assignment.targets) != 1
        or not isinstance(assignment.targets[0], ast.Name)
        or assignment.targets[0].id != expected_result
    ):
        raise ValueError(
            f"{source}: {kind} pseudocode must assign to {expected_result}"
        )

    allowed_lower = set(field_roles) | set(payload_roles) | {"scale"}
    if not compact:
        allowed_lower.update(("displacement", "absolute", "immediate", "vlen_bytes", "element_count"))
    allowed_nodes = (
        ast.Module,
        ast.Assign,
        ast.Name,
        ast.Load,
        ast.Store,
        ast.Constant,
        ast.BinOp,
        ast.UnaryOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.USub,
    )
    for node in ast.walk(tree):
        if not isinstance(node, allowed_nodes):
            raise ValueError(
                f"{source}: pseudocode does not allow {type(node).__name__}"
            )
        if isinstance(node, ast.Constant) and (
            not isinstance(node.value, int) or isinstance(node.value, bool)
        ):
            raise ValueError(f"{source}: pseudocode literals must be integers")
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            identifier = node.id
            if (
                identifier not in allowed_lower
                and identifier not in ("SP", "PC")
                and ea_gpr_index(identifier) is None
            ):
                raise ValueError(
                    f"{source}: unavailable pseudocode identifier {identifier!r}"
                )

    return ea_expression(assignment.value)


def _decode_ea_mode(data, source: Path, catalog, field_type_references, payload_type_references, expression) -> EAMode:
    mode_id = source.parent.name
    reference = catalog.reference(mode_id)
    fields = tuple(
        EAField(symbol, raw["role"], field_type_references[symbol])
        for symbol, raw in data.get("fields", {}).items()
    )
    encodings = tuple(
        EAEncoding(
            (raw["pattern"],)
            if isinstance(raw["pattern"], str)
            else tuple(raw["pattern"]),
            tuple(
                EAPayload(
                    payload["role"],
                    payload_type_references[encoding_index, payload_index],
                )
                for payload_index, payload in enumerate(raw.get("payloads", ()))
            ),
            EAAutoupdate(
                raw["autoupdate"]["target"],
                raw["autoupdate"]["type"],
                raw["autoupdate"]["difference"],
            )
            if "autoupdate" in raw
            else None,
        )
        for encoding_index, raw in enumerate(data["encodings"])
    )
    common = (
        reference,
        source,
        catalog,
        mode_id,
        data["name"],
        encodings,
        fields,
    )
    kind = data["kind"]
    if kind == "immediate":
        return ImmediateEAMode(
            *common, data["syntax"], expression
        )
    if kind == "memory":
        raw_segment = data.get("segment")
        segment: EASegment | None
        if raw_segment is None:
            segment = None
        elif raw_segment["source"] == "fixed":
            segment = FixedEASegment(raw_segment["register"])
        else:
            segment = FieldEASegment(raw_segment["role"])
        return MemoryEAMode(
            *common,
            data["syntax"],
            expression,
            segment,
            _decode_base_source(data, expression),
        )
    extension = EAExtension(
        data["extension"]["id"], data["extension"]["bytes"]
    )
    return CompactExtensionEAMode(*common, extension)


def _decode_base_source(data, expression: EAExpression) -> EABaseSource:
    names: set[str] = set()
    has_zero = False

    def visit(node):
        nonlocal has_zero
        if isinstance(node, EAIdentifier):
            names.add(node.name)
        elif isinstance(node, EALiteral):
            has_zero = has_zero or node.value == 0
        elif isinstance(node, EANegation):
            visit(node.operand)
        elif isinstance(node, EABinary):
            visit(node.left)
            visit(node.right)

    visit(expression)
    roles = {field["role"] for field in data.get("fields", {}).values()}
    candidates = []
    if "base" in roles and "base" in names:
        candidates.append(EABaseSource.ENCODED)
    if "SP" in names:
        candidates.append(EABaseSource.STACK_POINTER)
    if "PC" in names:
        candidates.append(EABaseSource.PROGRAM_COUNTER)
    if not candidates:
        return EABaseSource.ZERO if has_zero else EABaseSource.NONE
    return candidates[0] if len(candidates) == 1 else EABaseSource.NONE


def load_ea_mode(source: str | Path, *, schema, field_types, payload_types, catalog: EAModeCatalog) -> EAMode:
    source = Path(source).resolve()
    expected = catalog.mode_path(source.parent.name).resolve()
    if source.resolve() != expected:
        raise ValueError(f"{source}: source is outside its declaring EA catalog")
    data = load_yaml(source)
    fields, payloads, expression = _validate_ea_source(
        data, source, schema=schema, catalog=catalog,
        field_types=field_types, payload_types=payload_types,
    )
    return _decode_ea_mode(data, source, catalog, fields, payloads, expression)


def select_ea_modes(modes, targets: Iterable[str | Path | Reference[EAMode]] = ()) -> tuple[EAMode, ...]:
    selected = []
    seen = set()
    requested = tuple(targets)
    if not requested:
        return tuple(modes.values())
    for target in requested:
        if isinstance(target, Reference):
            mode = modes.resolve(target)
        else:
            matches = tuple(mode for mode in modes.values() if mode.source.resolve() == Path(target).resolve())
            if len(matches) != 1:
                raise ValueError(f"{target!r}: expected one declared EA mode")
            mode = matches[0]
        if mode.reference not in seen:
            selected.append(mode)
            seen.add(mode.reference)
    return tuple(selected)



@dataclass(frozen=True, slots=True)
class ResolvedEAField:
    mode: EAMode
    encoding: EAEncoding
    field: EAField
    definition: FieldType
    positions: tuple[int, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "positions", tuple(self.positions))



@dataclass(frozen=True, slots=True)
class ResolvedEAPayload:
    mode: EAMode
    encoding: EAEncoding
    payload: EAPayload
    definition: PayloadType
    signed: bool


@dataclass(frozen=True, slots=True)
class ResolvedEAEncoding:
    """A selected canonical EA encoding and its resolved source relationships."""

    mode: EAMode
    encoding: EAEncoding
    pattern: EncodingMetasyntax
    patterns: tuple[EncodingMetasyntax, ...]
    fields: tuple[ResolvedEAField, ...]
    payloads: tuple[ResolvedEAPayload, ...]
    expression: EAExpression | None
    expression_payload_roles: tuple[str, ...]
    updates: tuple[EAAutoupdate, ...]
    descriptor: ResolvedEAEncoding | None = None


    def __post_init__(self) -> None:
        object.__setattr__(self, "patterns", tuple(self.patterns))
        object.__setattr__(self, "fields", tuple(self.fields))
        object.__setattr__(self, "payloads", tuple(self.payloads))
        object.__setattr__(self, "expression_payload_roles", tuple(self.expression_payload_roles))
        object.__setattr__(self, "updates", tuple(self.updates))
        if not any(self.encoding is member for member in self.mode.encodings):
            raise ValueError(f"{self.mode.source}: resolved EA encoding must belong to its mode")
        descriptor = self.descriptor
        if descriptor is not None and (
            not isinstance(self.mode, CompactExtensionEAMode)
            or descriptor.descriptor is not None
            or (descriptor.mode.catalog.owner, descriptor.mode.catalog.profile, descriptor.mode.catalog.mode_type)
            != (self.mode.catalog.owner, self.mode.catalog.profile, self.mode.extension.id)
        ):
            raise ValueError(f"{self.mode.source}: descriptor must belong to the selected EA family")
        own_fields = self.fields[:len(self.mode.fields)]
        own_payloads = self.payloads[:len(self.encoding.payloads)]
        for resolved, member in zip(own_fields, self.mode.fields):
            if resolved.mode is not self.mode or resolved.encoding is not self.encoding or resolved.field is not member or resolved.definition.reference != member.type:
                raise ValueError(f"{self.mode.source}: resolved EA field differs from its canonical source binding")
        for resolved, member in zip(own_payloads, self.encoding.payloads):
            if resolved.mode is not self.mode or resolved.encoding is not self.encoding or resolved.payload is not member or resolved.definition.reference != member.type:
                raise ValueError(f"{self.mode.source}: resolved EA payload differs from its canonical source binding")
        extra_fields = () if descriptor is None else descriptor.fields
        extra_payloads = () if descriptor is None else descriptor.payloads
        if len(self.fields) != len(self.mode.fields) + len(extra_fields) or any(left is not right for left, right in zip(self.fields[len(self.mode.fields):], extra_fields)):
            raise ValueError(f"{self.mode.source}: composed EA fields must retain canonical descriptor bindings")
        if len(self.payloads) != len(self.encoding.payloads) + len(extra_payloads) or any(left is not right for left, right in zip(self.payloads[len(self.encoding.payloads):], extra_payloads)):
            raise ValueError(f"{self.mode.source}: composed EA payloads must retain canonical descriptor bindings")
        updates = (
            *((self.encoding.autoupdate,) if self.encoding.autoupdate is not None else ()),
            *(() if descriptor is None else descriptor.updates),
        )
        if len(updates) != len(self.updates) or {id(item) for item in updates} != {id(item) for item in self.updates}:
            raise ValueError(f"{self.mode.source}: EA updates must preserve their canonical source members")

    @property
    def payload_width(self) -> int:
        return sum(payload.definition.bytes * 8 for payload in self.payloads)

    @property
    def appended_bytes(self) -> int:
        descriptor_bytes = 0 if self.descriptor is None else self.descriptor.pattern.bit_width // 8
        return descriptor_bytes + self.payload_width // 8


@dataclass(frozen=True, slots=True)
class EACapture:
    binding: ResolvedEAField | ResolvedEAPayload


@dataclass(frozen=True, slots=True)
class EARegisterUpdate:
    binding: ResolvedEAField
    update: EAAutoupdate


@dataclass(frozen=True, slots=True)
class EACalculate:
    expression: EAExpression


def ea_evaluation_steps(resolved: ResolvedEAEncoding) -> tuple[EACapture | EARegisterUpdate | EACalculate, ...]:
    """Expose register capture and state updates in architectural evaluation order."""
    if resolved.expression is None:
        return ()
    fields = {item.field.role: item for item in resolved.fields}
    payloads = {item.payload.role: item for item in resolved.payloads}
    updates = {item.target: item for item in resolved.updates}
    steps = []
    roles = ("base", "index", "displacement", "segment")
    for role in (*roles, *sorted((set(fields) | set(payloads)) - set(roles))):
        binding = fields.get(role) or payloads.get(role)
        if binding is None:
            continue
        update = updates.get(role)
        if update is not None:
            if not isinstance(binding, ResolvedEAField):
                raise ValueError(f"EA update {role!r} must target a register field")
            if update.update_type == "predecrement":
                steps.append(EARegisterUpdate(binding, update))
        steps.append(EACapture(binding))
        if update is not None and update.update_type == "postincrement":
            steps.append(EARegisterUpdate(binding, update))
    steps.append(EACalculate(resolved.expression))
    return tuple(steps)


def ea_syntax(resolved: ResolvedEAEncoding) -> str | None:
    """Bind authored assembler syntax to one selected encoding alternative."""
    mode = resolved.mode
    if not isinstance(mode, (ImmediateEAMode, MemoryEAMode)):
        return None
    syntax = mode.syntax
    payloads = {item.payload.role: item.definition.id.lower() for item in resolved.payloads}
    displacement = payloads.get("displacement")
    syntax = syntax.replace("[+ displacement]", f"+ {displacement}" if displacement is not None else "")
    matches = tuple(re.finditer(r"update\(([^()]+\([^()]+\))\)", syntax))
    if syntax.count("update(") != len(matches):
        raise ValueError(f"{mode.source}: malformed update operand in EA syntax")
    for match in reversed(matches):
        operand = match.group(1)
        bindings = tuple(field for field in resolved.fields if operand == f"{field.definition.id}({field.field.symbol})")
        if len(bindings) != 1:
            raise ValueError(f"{mode.source}: update syntax must name one declared register field")
        role = bindings[0].field.role
        update = next((item for item in resolved.updates if item.target == role), None)
        replacement = operand if update is None else operand + "++" if update.update_type == "postincrement" else "--" + operand
        syntax = syntax[:match.start()] + replacement + syntax[match.end():]
    for role, spelling in payloads.items():
        syntax = re.sub(rf"\b{re.escape(role)}\b", spelling, syntax)
    return re.sub(r"\s+", " ", syntax).replace(" ]", "]")


def resolve_ea_encoding(
    mode: EAMode, encoding: EAEncoding, *, field_types, payload_types,
) -> ResolvedEAEncoding:
    """Resolve one selection while retaining its canonical member identities."""
    from engine.isa.types import payload_type_is_signed

    if not any(encoding is member for member in mode.encodings):
        raise ValueError(f"{mode.source}: selected encoding is not a member of this mode")
    if isinstance(mode, CompactExtensionEAMode) and mode.catalog.mode_type != "compact":
        raise ValueError(f"{mode.source}: a descriptor cannot select another extension")
    patterns = tuple(EncodingMetasyntax.parse(chunk) for chunk in encoding.patterns)
    pattern = EncodingMetasyntax.parse(encoding.patterns)
    fields = tuple(
        ResolvedEAField(mode, encoding, field, field_types.resolve(field.type), pattern.field_positions(field.symbol))
        for field in mode.fields
    )
    payloads = tuple(
        ResolvedEAPayload(mode, encoding, payload, definition, payload_type_is_signed(definition))
        for payload in encoding.payloads
        for definition in (payload_types.resolve(payload.type),)
    )
    roles = [*(field.field.role for field in fields), *(payload.payload.role for payload in payloads)]
    if len(roles) != len(set(roles)):
        raise ValueError(f"{mode.source}: a field or payload role has multiple representations")
    for field in fields:
        if len(field.positions) != field.definition.bits:
            raise ValueError(f"{mode.source}: field {field.field.symbol!r} width disagrees with its type")
    update = encoding.autoupdate
    if update is not None:
        if update.target not in {field.field.role for field in fields}:
            raise ValueError(f"{mode.source}: auto-update target has no field")
        if update.target not in {"base", "index"}:
            raise ValueError(f"{mode.source}: only base and index admit address auto-update")
    expression = mode.expression if isinstance(mode, (ImmediateEAMode, MemoryEAMode)) else None
    if mode.catalog.mode_type == "compact":
        expression = _bind_ea_expression(expression, fields, payloads, source=mode.source)
    expression_payload_roles = tuple(sorted(
        {payload.payload.role for payload in payloads}
        | (_ea_identifiers(expression) & {"displacement", "absolute", "immediate"})
    ))
    return ResolvedEAEncoding(
        mode, encoding, pattern, patterns, fields, payloads,
        expression, expression_payload_roles, () if update is None else (update,),
    )



def compose_ea_encoding(
    compact_mode: EAMode, compact_encoding: EAEncoding,
    descriptor_mode: EAMode, descriptor_encoding: EAEncoding,
    *, field_types, payload_types,
) -> ResolvedEAEncoding:
    """Combine a compact escape and its descriptor without cloning either mode."""
    if not isinstance(compact_mode, CompactExtensionEAMode):
        raise ValueError(f"{compact_mode.source}: descriptor selection requires a compact extension form")
    if isinstance(descriptor_mode, CompactExtensionEAMode):
        raise ValueError(f"{descriptor_mode.source}: a descriptor cannot select another extension")
    if (
        descriptor_mode.catalog.owner != compact_mode.catalog.owner
        or descriptor_mode.catalog.profile != compact_mode.catalog.profile
        or descriptor_mode.catalog.mode_type != compact_mode.extension.id
    ):
        raise ValueError(f"{descriptor_mode.source}: descriptor does not belong to the selected owner/profile/family")
    compact = resolve_ea_encoding(compact_mode, compact_encoding, field_types=field_types, payload_types=payload_types)
    descriptor = resolve_ea_encoding(descriptor_mode, descriptor_encoding, field_types=field_types, payload_types=payload_types)
    if descriptor.pattern.bit_width != compact_mode.extension.bytes * 8:
        raise ValueError(
            f"{descriptor_mode.source}: descriptor has {descriptor.pattern.bit_width} bits; "
            f"{compact_mode.source} declares {compact_mode.extension.bytes * 8}"
        )
    fields = (*compact.fields, *descriptor.fields)
    payloads = (*compact.payloads, *descriptor.payloads)
    roles = [*(field.field.role for field in fields), *(payload.payload.role for payload in payloads)]
    if len(roles) != len(set(roles)):
        raise ValueError(
            f"{descriptor_mode.source}: compact and descriptor field/payload roles must be unique"
        )
    updates = tuple(sorted((*compact.updates, *descriptor.updates), key=lambda update: ("base", "index").index(update.target)))
    if len({update.target for update in updates}) != len(updates):
        raise ValueError(f"{descriptor_mode.source}: a role has multiple auto-updates")
    return ResolvedEAEncoding(
        compact_mode, compact_encoding, compact.pattern, compact.patterns, fields, payloads,
        _bind_ea_expression(descriptor.expression, fields, payloads, source=descriptor_mode.source),
        tuple(payload.payload.role for payload in payloads), updates, descriptor,
    )


def _ea_identifiers(expression: EAExpression | None) -> set[str]:
    match expression:
        case EAIdentifier(name):
            return {name}
        case EANegation(operand):
            return _ea_identifiers(operand)
        case EABinary(_, left, right):
            return _ea_identifiers(left) | _ea_identifiers(right)
        case _:
            return set()


def _bind_ea_expression(expression, fields, payloads, *, source):
    roles = {field.field.role for field in fields} | {payload.payload.role for payload in payloads}
    def bind(node):
        match node:
            case EAIdentifier(name) if name in roles:
                return node
            case EAIdentifier("displacement"):
                return EALiteral(0)
            case EAIdentifier(name) if name in {"scale", "vlen_bytes", "element_count", "SP", "PC"} or ea_gpr_index(name) is not None:
                return node
            case EAIdentifier(name):
                raise ValueError(f"{source}: selected EA composition does not supply expression role {name!r}")
            case EANegation(operand):
                resolved = bind(operand)
                return node if resolved is operand else EANegation(resolved)
            case EABinary(operator, left, right):
                bound_left, bound_right = bind(left), bind(right)
                return node if bound_left is left and bound_right is right else EABinary(operator, bound_left, bound_right)
            case _:
                return node
    return bind(expression)
