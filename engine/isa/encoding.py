"""Typed representation and local loading of instruction encodings."""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Iterator
import re
from engine.diagnostics import Diagnostic
from engine.diagnostics import _error
from engine.isa.ea import EAMode
from engine.isa.ea import immediate_ea_selector_values
from engine.isa.types import EffectiveAddressFieldType
from engine.isa.types import FieldType

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

from engine.isa.cpuid import CpuidCatalog, CpuidField
from engine.isa.types import FieldType, PayloadType
from engine.reference import Reference
from engine.source.yaml import load_schema_yaml
from engine.isa.encoding_architecture import encoding_class_for_width, frame_opcode
from engine.syntax.encoding import EncodingMetasyntax
from engine.syntax.instruction import InstructionMetasyntax

if TYPE_CHECKING:
    from engine.isa.instructions import Instruction, InstructionOperand
    from engine.isa.registers import Register, RegisterGroup
    from engine.isa.encoding_architecture import EncodingClass
    from engine.isa.ea import ResolvedEAEncoding

ConstraintValue = int | str
_CONSTRAINT_INTEGER_RE = re.compile(r"(?:0|[1-9][0-9]*|0x[0-9a-f]+)")


@dataclass(frozen=True, slots=True)
class FieldBinding:
    """One primary-encoding field marker and its logical representation."""

    marker: str
    role: str
    type: Reference[FieldType]
    access: str | None = None


@dataclass(frozen=True, slots=True)
class PayloadBinding:
    """One appended payload in encoded byte order."""

    role: str
    type: Reference[PayloadType]
    access: str | None = None


@dataclass(frozen=True, slots=True)
class OperandConstraint:
    """Common identity of a constraint on one field role's value domain."""

    role: str
    reason: str
    values: tuple[ConstraintValue, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", tuple(self.values))



@dataclass(frozen=True, slots=True)
class AllowedOperandConstraint(OperandConstraint):
    """The field value must belong to the authored set or ranges."""


@dataclass(frozen=True, slots=True)
class ExcludedOperandConstraint(OperandConstraint):
    """The field value must not belong to the authored set or ranges."""


@dataclass(frozen=True, slots=True)
class OperandOverlap:
    """The architectural aliasing relation between two encoded operands."""

    operands: tuple[str, str]
    type: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "operands", tuple(self.operands))



@dataclass(frozen=True, slots=True)
class EncodingForm:
    """One locally named instruction encoding form."""

    id: str
    pattern: EncodingMetasyntax
    syntax: InstructionMetasyntax
    fields: tuple[FieldBinding, ...] = ()
    payloads: tuple[PayloadBinding, ...] = ()
    constraints: tuple[OperandConstraint, ...] = ()
    overlaps: tuple[OperandOverlap, ...] = ()
    additional_cpuid_flags: tuple[CpuidField, ...] = ()
    fixed_operand_roles: tuple[str, ...] = ()


    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", tuple(self.fields))
        object.__setattr__(self, "payloads", tuple(self.payloads))
        object.__setattr__(self, "constraints", tuple(self.constraints))
        object.__setattr__(self, "overlaps", tuple(self.overlaps))
        object.__setattr__(self, "additional_cpuid_flags", tuple(self.additional_cpuid_flags))
        object.__setattr__(self, "fixed_operand_roles", tuple(self.fixed_operand_roles))

    def field_for_marker(self, marker: str) -> FieldBinding | None:
        return next((field for field in self.fields if field.marker == marker), None)

    def field_for_role(self, role: str) -> FieldBinding | None:
        return next((field for field in self.fields if field.role == role), None)


@dataclass(frozen=True, slots=True)
class EncodingCatalog:
    """The schema-decoded ``encodings.yaml`` for one instruction."""

    source: Path
    forms: tuple[EncodingForm, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "forms", tuple(self.forms))









class UnknownConstraintValueError(ValueError):
    """A symbolic constraint value is absent from its field type's domain."""

    def __init__(self, value: str, domain: str) -> None:
        self.value = value
        self.domain = domain
        super().__init__(f"constraint value {value!r} is not defined by {domain}")


class InvalidConstraintValueError(ValueError):
    """A numeric constraint value is not a valid member interval."""

    def __init__(self, value: object, width: int, detail: str) -> None:
        self.value = value
        self.width = width
        self.detail = detail
        super().__init__(
            f"constraint value {value!r} is invalid for an unsigned "
            f"{width}-bit field: {detail}"
        )


def numeric_bounds(value: int | str) -> tuple[int, int] | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value, value
    if isinstance(value, str) and ".." in value:
        lower, upper = value.split("..", 1)
        if (
            _CONSTRAINT_INTEGER_RE.fullmatch(lower) is None
            or _CONSTRAINT_INTEGER_RE.fullmatch(upper) is None
        ):
            raise ValueError("invalid numeric constraint interval")
        return int(lower, 0), int(upper, 0)
    return None


def constraint_named_values(
    field_type: FieldType, *, ea_modes: Iterable[EAMode]
) -> Mapping[str, frozenset[int]]:
    """Return the symbolic constraint values owned by one field type."""

    if isinstance(field_type, EffectiveAddressFieldType):
        return {
            "immediate": immediate_ea_selector_values(ea_modes, field_type),
        }
    return {}


def _allowed_constraint_ranges(
    constraint,
    width: int,
    *,
    named_values: Mapping[str, Iterable[int]] | None = None,
    domain: str = "the field type",
) -> tuple[tuple[int, int], ...]:
    """Normalize a constraint into disjoint inclusive field-value intervals."""

    maximum = (1 << width) - 1
    intervals = []
    for item in constraint.values:
        try:
            bounds = numeric_bounds(item)
        except (TypeError, ValueError) as error:
            raise InvalidConstraintValueError(
                item, width, "expected an integer or integer interval"
            ) from error
        if bounds is not None:
            lower, upper = bounds
            if lower > upper or lower < 0 or upper > maximum:
                raise InvalidConstraintValueError(
                    item, width, "range lies outside the field value domain"
                )
            intervals.append((lower, upper))
        else:
            if named_values is None or item not in named_values:
                raise UnknownConstraintValueError(item, domain)
            for value in named_values[item]:
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value < 0
                    or value > maximum
                ):
                    raise InvalidConstraintValueError(
                        item,
                        width,
                        f"named domain {domain} contains out-of-range value {value!r}",
                    )
                intervals.append((value, value))
    merged = []
    for lower, upper in sorted(intervals):
        if merged and lower <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], upper))
        else:
            merged.append((lower, upper))
    if isinstance(constraint, AllowedOperandConstraint):
        result = tuple(merged)
    elif isinstance(constraint, ExcludedOperandConstraint):
        result = []
        cursor = 0
        for lower, upper in merged:
            if cursor < lower:
                result.append((cursor, lower - 1))
            cursor = upper + 1
        if cursor <= maximum:
            result.append((cursor, maximum))
        result = tuple(result)
    else:
        raise TypeError(f"unknown constraint {type(constraint).__name__}")
    if not result:
        raise InvalidConstraintValueError(
            tuple(constraint.values), width, "constraint leaves no accepted values"
        )
    return result


def constraint_ranges(
    form: EncodingForm,
    constraint,
    *,
    field_types,
    ea_modes: Iterable[EAMode],
) -> tuple[tuple[int, int], ...]:
    """Interpret one form constraint in its resolved field type's value domain."""

    field = form.field_for_role(constraint.role)
    if field is None:
        raise ValueError(f"{form.id}: constraint role {constraint.role!r} has no field")
    definition = field_types.resolve(field.type)
    width = form.pattern.field_width(field.marker)
    return _allowed_constraint_ranges(
        constraint,
        width,
        named_values=constraint_named_values(definition, ea_modes=ea_modes),
        domain=repr(field.type),
    )


def _validate_representation_access(
    source: Path,
    base: tuple[str, str],
    role: str,
    access: str | None,
    operands: Mapping[str, InstructionOperand],
    payload_index: int | None = None,
) -> Iterator[Diagnostic]:
    from engine.diagnostics import _error

    operand = operands.get(role)
    if operand is None:
        if role != "size":
            location: tuple[str | int, ...] = (
                (*base, "fields")
                if payload_index is None
                else (*base, "payloads", payload_index)
            )
            yield _error(
                "representation.role",
                source,
                f"role {role!r} is neither an instruction operand nor a known selector",
                *location,
            )
        return
    if access is not None and access == operand.access:
        yield _error(
            "representation.redundant-access",
            source,
            f"role {role!r} repeats inherited access {access!r}",
            *base,
        )


def _resolve_cpuid_flags(
    raw: tuple[str, ...] | list[str],
    cpuid: CpuidCatalog,
    source: Path,
    encoding_id: str,
) -> tuple[CpuidField, ...]:
    if not raw:
        return ()
    return tuple(cpuid.resolve_flag(reference, source) for reference in raw)


def _decode_field_binding(marker: str, raw: Mapping[str, Any], field_types) -> FieldBinding:
    reference = Reference.parse(raw["type"])
    field_types.resolve(reference)
    return FieldBinding(marker, raw["role"], reference, raw.get("access"))


def _decode_payload_binding(raw: Mapping[str, Any], payload_types) -> PayloadBinding:
    reference = Reference.parse(raw["type"])
    payload_types.resolve(reference)
    return PayloadBinding(raw["role"], reference, raw.get("access"))


def load_encodings(
    source: str | Path, *, schema: Mapping[str, object], field_types, payload_types,
    cpuid: CpuidCatalog,
) -> EncodingCatalog:
    source = Path(source).resolve()
    document = load_schema_yaml(source, schema)

    forms: list[EncodingForm] = []
    for encoding_id, raw_form in document["encodings"].items():
        fields = tuple(
            _decode_field_binding(marker, representation, field_types)
            for marker, representation in raw_form.get("fields", {}).items()
        )
        payloads = tuple(
            _decode_payload_binding(representation, payload_types)
            for representation in raw_form.get("payloads", ())
        )
        constraints = tuple(
            AllowedOperandConstraint(
                constraint["role"],
                constraint["reason"],
                tuple(constraint["allow"]),
            )
            if "allow" in constraint
            else ExcludedOperandConstraint(
                constraint["role"],
                constraint["reason"],
                tuple(constraint["exclude"]),
            )
            for constraint in raw_form.get("constraints", ())
        )
        overlaps = tuple(
            OperandOverlap(tuple(overlap["operands"]), overlap["type"])
            for overlap in raw_form.get("overlaps", ())
        )
        additional_cpuid_flags = _resolve_cpuid_flags(
            raw_form.get("additional_cpuid_flags", ()),
            cpuid,
            source,
            encoding_id,
        )
        forms.append(
            EncodingForm(
                id=encoding_id,
                pattern=EncodingMetasyntax.parse(raw_form["pattern"]),
                syntax=InstructionMetasyntax.parse(raw_form["syntax"]),
                fields=fields,
                payloads=payloads,
                constraints=constraints,
                overlaps=overlaps,
                additional_cpuid_flags=additional_cpuid_flags,
                fixed_operand_roles=tuple(raw_form.get("fixed_operand_roles", ())),
            )
        )
    return EncodingCatalog(source=source, forms=tuple(forms))



def _check_form_relations(
    instruction: Instruction,
    form: EncodingForm,
    *,
    field_types,
    payload_types,
    ea_modes,
    registers,
    source: Path,
) -> Iterator[Diagnostic]:
    """Check an encoding's operand bindings and constraint domain."""
    from engine.isa.types import SizeSelectorFieldType
    from engine.reference import UnknownReferenceError
    from engine.syntax.instruction import OperandReference

    mnemonic = instruction.mnemonic
    operands = instruction.operands
    base = ("encodings", form.id)
    if form.syntax.mnemonic != mnemonic:
        yield _error(
            "syntax.mnemonic",
            source,
            f"syntax names {form.syntax.mnemonic}, expected {mnemonic}",
            *base,
            "syntax",
        )
    if form.syntax.encoding_id != form.id:
        yield _error(
            "syntax.encoding-id",
            source,
            f"syntax derives encoding ID {form.syntax.encoding_id!r}",
            *base,
            "syntax",
        )

    marker_sequence = tuple(field.marker for field in form.fields)
    markers = frozenset(marker_sequence)
    for marker in sorted(
        marker for marker in markers if marker_sequence.count(marker) > 1
    ):
        yield _error(
            "field.duplicate-marker",
            source,
            f"pattern marker {marker!r} has more than one field binding",
            *base,
            "fields",
            marker,
        )
    if markers != form.pattern.fields:
        yield _error(
            "pattern.fields",
            source,
            f"pattern fields {sorted(form.pattern.fields)} do not match "
            f"bindings {sorted(markers)}",
            *base,
            "fields",
        )

    representation_roles = [
        *(field.role for field in form.fields),
        *(payload.role for payload in form.payloads),
    ]
    duplicates = sorted(
        role
        for role in set(representation_roles)
        if representation_roles.count(role) > 1
    )
    for role in duplicates:
        yield _error(
            "representation.duplicate-role",
            source,
            f"role {role!r} has more than one field or payload representation",
            *base,
        )

    for field in form.fields:
        definition = field_types.resolve(field.type)
        width = definition.bits
        if field.marker in form.pattern.fields:
            actual = form.pattern.field_width(field.marker)
            if width != actual:
                yield _error(
                    "field.width",
                    source,
                    f"field {field.marker!r} occupies {actual} bits but "
                    f"{field.type!r} declares {width}",
                    *base,
                    "fields",
                    field.marker,
                )
        yield from _validate_representation_access(
            source, base, field.role, field.access, operands
        )

    for payload_index, payload in enumerate(form.payloads):
        yield from _validate_representation_access(
            source, base, payload.role, payload.access, operands, payload_index
        )

    for displayed in form.syntax.displayed_operands:
        if (
            isinstance(displayed, OperandReference)
            and displayed.field is not None
            and displayed.field not in markers
        ):
            yield _error(
                "syntax.unknown-field",
                source,
                f"syntax references field {displayed.field!r} with no binding",
                *base,
                "syntax",
            )

    if form.syntax.size_field is not None:
        size_binding = form.field_for_marker(form.syntax.size_field)
        if size_binding is None or size_binding.role != "size":
            yield _error(
                "syntax.size-field",
                source,
                f"selected size field {form.syntax.size_field!r} is not bound "
                "to role 'size'",
                *base,
                "syntax",
            )
        elif size_binding is not None:
            definition = field_types.resolve(size_binding.type)
            declared_codes = (
                tuple(value.code for value in definition.values)
                if isinstance(definition, SizeSelectorFieldType)
                else ()
            )
            if not set(form.syntax.selected_size_codes).issubset(declared_codes):
                yield _error(
                    "syntax.size-codes",
                    source,
                    f"size alternatives {form.syntax.selected_size_codes} are not "
                    f"a subset of {size_binding.type!r} values {declared_codes}",
                    *base,
                    "syntax",
                )

    for index, constraint in enumerate(form.constraints):
        constraint_field = form.field_for_role(constraint.role)
        if constraint_field is None:
            yield _error(
                "constraint.role",
                source,
                f"constraint role {constraint.role!r} does not resolve to a field",
                *base,
                "constraints",
                index,
                "role",
            )
            continue
        try:
            constraint_field_type = field_types.resolve(
                constraint_field.type
            )
        except UnknownReferenceError:
            continue
        if constraint_field.marker not in form.pattern.fields:
            continue
        try:
            constraint_ranges(
                form,
                constraint,
                field_types=field_types,
                ea_modes=ea_modes.values(),
            )
        except UnknownConstraintValueError as error:
            yield _error(
                "constraint.value",
                source,
                str(error),
                *base,
                "constraints",
                index,
            )
        except (InvalidConstraintValueError, TypeError) as error:
            yield _error(
                "constraint.range",
                source,
                str(error),
                *base,
                "constraints",
                index,
            )

    for index, overlap in enumerate(form.overlaps):
        for role in overlap.operands:
            operand = operands.get(role)
            overlap_field = form.field_for_role(role)
            if operand is None or overlap_field is None:
                yield _error(
                    "overlap.operand",
                    source,
                    f"overlap operand {role!r} must be a field-backed logical operand",
                    *base,
                    "overlaps",
                    index,
                )
            elif overlap.type == "same_value" and operand.access not in {
                "write",
                "read_write",
            }:
                yield _error(
                    "overlap.access",
                    source,
                    f"same_value overlap operand {role!r} is not writable",
                    *base,
                    "overlaps",
                    index,
                )


def check_encoding_form(instruction, form, *, field_types, payload_types, ea_modes, registers, source):
    """Report typed form relations independently of catalog placement."""
    base = ("encodings", form.id)
    yield from _check_form_relations(
        instruction, form, field_types=field_types, payload_types=payload_types,
        ea_modes=ea_modes, registers=registers, source=source,
    )
    try:
        resolve_encoding_form(instruction, form, field_types=field_types, payload_types=payload_types, ea_modes=ea_modes, registers=registers)
    except (ValueError, KeyError) as error:
        yield _error("encoding.relation", source, str(error), *base)


@dataclass(frozen=True, slots=True)
class ResolvedFieldBinding:
    field: FieldBinding
    definition: FieldType
    positions: tuple[int, ...]
    ranges: tuple[tuple[int, int], ...]
    register_group: RegisterGroup | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "positions", tuple(self.positions))
        object.__setattr__(self, "ranges", tuple(tuple(item0) for item0 in self.ranges))



@dataclass(frozen=True, slots=True)
class ResolvedConstraint:
    constraint: OperandConstraint
    field: ResolvedFieldBinding
    ranges: tuple[tuple[int, int], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "ranges", tuple(tuple(item0) for item0 in self.ranges))



@dataclass(frozen=True, slots=True)
class ResolvedOperand:
    name: str
    logical: InstructionOperand
    access: str


@dataclass(frozen=True, slots=True)
class ResolvedFieldOperand(ResolvedOperand):
    field: ResolvedFieldBinding


@dataclass(frozen=True, slots=True)
class ResolvedEffectiveAddressOperand(ResolvedFieldOperand):
    owner: str
    profile: str
    purpose: str
    interpretation_width: str
    allows_immediate: bool


@dataclass(frozen=True, slots=True)
class ResolvedPayloadOperand(ResolvedOperand):
    payload: PayloadBinding
    definition: PayloadType
    signed: bool
    register_group: RegisterGroup | None = None


@dataclass(frozen=True, slots=True)
class ResolvedFixedNameOperand(ResolvedOperand):
    fixed_name: str
    register: Register


@dataclass(frozen=True, slots=True)
class ResolvedFixedValueOperand(ResolvedOperand):
    fixed_value: int


ResolvedOperandSource = (
    ResolvedFieldOperand | ResolvedEffectiveAddressOperand | ResolvedPayloadOperand
    | ResolvedFixedNameOperand | ResolvedFixedValueOperand
)


@dataclass(frozen=True, slots=True)
class ResolvedEaRead:
    operand: ResolvedEffectiveAddressOperand
    alternatives: tuple[ResolvedEAEncoding, ...]
    minimum_bytes: int
    maximum_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "alternatives", tuple(self.alternatives))



@dataclass(frozen=True, slots=True)
class ResolvedPayloadRead:
    operand: ResolvedPayloadOperand


ResolvedLayoutOperation = ResolvedEaRead | ResolvedPayloadRead


@dataclass(frozen=True, slots=True)
class ResolvedRepeatControl:
    rep: bool
    repcc: bool
    observed_kind: str | None
    observed_operand: ResolvedOperand | None


@dataclass(frozen=True, slots=True)
class ResolvedInstructionControl:
    route: str
    privileged: bool
    predicate_mode: str
    repeat: ResolvedRepeatControl


@dataclass(frozen=True, slots=True)
class ResolvedOperandOverlap:
    overlap: OperandOverlap
    left: ResolvedFieldOperand
    right: ResolvedFieldOperand


@dataclass(frozen=True, slots=True)
class ResolvedEncodingForm:
    """One canonical form's resolved meaning, independent of output numbering."""

    instruction: Instruction
    form: EncodingForm
    encoding_class: EncodingClass
    fields: tuple[ResolvedFieldBinding, ...]
    constraints: tuple[ResolvedConstraint, ...]
    operands: tuple[ResolvedOperand, ...]
    display_order: tuple[ResolvedOperand, ...]
    layout: tuple[ResolvedLayoutOperation, ...]
    sizes: tuple[tuple[int, str], ...]
    overlaps: tuple[ResolvedOperandOverlap, ...]
    control: ResolvedInstructionControl
    fixed_required_bytes: int
    minimum_required_bytes: int
    maximum_required_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", tuple(self.fields))
        object.__setattr__(self, "constraints", tuple(self.constraints))
        object.__setattr__(self, "operands", tuple(self.operands))
        object.__setattr__(self, "display_order", tuple(self.display_order))
        object.__setattr__(self, "layout", tuple(self.layout))
        object.__setattr__(self, "sizes", tuple(tuple(item0) for item0 in self.sizes))
        object.__setattr__(self, "overlaps", tuple(self.overlaps))
        if len(self.fields) != len(self.form.fields) or any(resolved.field is not member or resolved.definition.reference != member.type for resolved, member in zip(self.fields, self.form.fields)):
            raise ValueError(f"{self.instruction.source}: resolved fields must preserve canonical form bindings")
        by_name = {}
        for operand in self.operands:
            if operand.name in by_name or self.instruction.operands.get(operand.name) is not operand.logical:
                raise ValueError(f"{self.instruction.source}: resolved operand must use the instruction's canonical logical member")
            by_name[operand.name] = operand
            if isinstance(operand, ResolvedFieldOperand) and not any(operand.field is member for member in self.fields):
                raise ValueError(f"{self.instruction.source}: operand field is not a canonical resolved binding")
            if isinstance(operand, ResolvedPayloadOperand) and (
                not any(operand.payload is member for member in self.form.payloads)
                or operand.definition.reference != operand.payload.type
            ):
                raise ValueError(f"{self.instruction.source}: operand payload differs from its canonical form binding")
        for operand in (*self.display_order, *(operation.operand for operation in self.layout)):
            if by_name.get(operand.name) is not operand:
                raise ValueError(f"{self.instruction.source}: display/layout must use canonical resolved operands")
        observed = self.control.repeat.observed_operand
        if observed is not None and by_name.get(observed.name) is not observed:
            raise ValueError(f"{self.instruction.source}: repeat must observe a canonical resolved operand")
        for resolved in self.constraints:
            if not any(resolved.constraint is member for member in self.form.constraints) or not any(resolved.field is member for member in self.fields):
                raise ValueError(f"{self.instruction.source}: resolved constraint must use canonical form members")
        for resolved in self.overlaps:
            if not any(resolved.overlap is member for member in self.form.overlaps) or by_name.get(resolved.left.name) is not resolved.left or by_name.get(resolved.right.name) is not resolved.right:
                raise ValueError(f"{self.instruction.source}: resolved overlap must use canonical form operands")



def _resolve_fixed_operands(instruction: Instruction, form: EncodingForm, *, registers):
    from engine.syntax.instruction import DecimalLiteral, OperandReference

    fixed = tuple(
        item for item in form.syntax.displayed_operands
        if isinstance(item, DecimalLiteral)
        or (isinstance(item, OperandReference) and item.field is None and not item.angled)
    )
    roles = form.fixed_operand_roles
    if len(roles) != len(fixed) or len(set(roles)) != len(roles):
        raise ValueError(f"{instruction.source}: {form.id}: fixed_operand_roles must identify each fixed occurrence once")
    represented = {item.role for item in (*form.fields, *form.payloads)}
    result = []
    for name, item in zip(roles, fixed, strict=True):
        if name in represented or name not in instruction.operands:
            raise ValueError(f"{instruction.source}: {form.id}: invalid or repeated fixed operand role {name!r}")
        logical = instruction.operands[name]
        if isinstance(item, DecimalLiteral):
            operand = ResolvedFixedValueOperand(name, logical, logical.access, item.literal)
        else:
            matches = tuple(register for register in registers.registers.values() if register.id == item.name)
            if len(matches) != 1:
                raise ValueError(f"{instruction.source}: {form.id}: fixed register {item.name!r} is unknown or ambiguous")
            operand = ResolvedFixedNameOperand(name, logical, logical.access, item.name, matches[0])
        result.append((item, operand))
    return tuple(result)


def _intersect_ranges(left, right) -> tuple[tuple[int, int], ...]:
    result = []
    for left_low, left_high in left:
        for right_low, right_high in right:
            low, high = max(left_low, right_low), min(left_high, right_high)
            if low <= high:
                result.append((low, high))
    return tuple(sorted(result))


def _value_ranges(values) -> tuple[tuple[int, int], ...]:
    result = []
    for value in sorted(set(values)):
        if result and value == result[-1][1] + 1:
            result[-1] = (result[-1][0], value)
        else:
            result.append((value, value))
    return tuple(result)


def _pattern_intersects_range(pattern: EncodingMetasyntax, lower: int, upper: int) -> bool:
    """Decide bounded pattern membership with at most four states per bit."""
    memo = {}

    def visit(bit, lower_tight, upper_tight):
        if bit < 0:
            return True
        key = (bit, lower_tight, upper_tight)
        if key in memo:
            return memo[key]
        minimum = (lower >> bit) & 1 if lower_tight else 0
        maximum = (upper >> bit) & 1 if upper_tight else 1
        choices = (((pattern.fixed_value >> bit) & 1),) if (pattern.fixed_mask >> bit) & 1 else (0, 1)
        result = any(
            minimum <= value <= maximum
            and visit(bit - 1, lower_tight and value == minimum, upper_tight and value == maximum)
            for value in choices
        )
        memo[key] = result
        return result

    maximum = (1 << pattern.bit_width) - 1
    lower, upper = max(0, lower), min(maximum, upper)
    return lower <= upper and visit(pattern.bit_width - 1, True, True)


def _ea_alternatives(field, modes, *, field_types, payload_types):
    from engine.isa.ea import CompactExtensionEAMode, compose_ea_encoding, resolve_ea_encoding

    values = tuple(modes.values())
    result = []
    for mode in values:
        if (mode.catalog.owner, mode.catalog.profile, mode.catalog.mode_type) != (field.definition.owner, field.definition.profile, "compact"):
            continue
        for encoding in mode.encodings:
            pattern = EncodingMetasyntax.parse(encoding.patterns)
            if not any(_pattern_intersects_range(pattern, low, high) for low, high in field.ranges):
                continue
            if isinstance(mode, CompactExtensionEAMode):
                for descriptor in values:
                    if (descriptor.catalog.owner, descriptor.catalog.profile, descriptor.catalog.mode_type) != (mode.catalog.owner, mode.catalog.profile, mode.extension.id):
                        continue
                    for selected in descriptor.encodings:
                        result.append(compose_ea_encoding(mode, encoding, descriptor, selected, field_types=field_types, payload_types=payload_types))
            else:
                result.append(resolve_ea_encoding(mode, encoding, field_types=field_types, payload_types=payload_types))
    if not result:
        raise ValueError(f"{field.definition.source}: EA field has no accepted declared mode")
    return tuple(result)


def resolve_encoding_form(
    instruction: Instruction, form: EncodingForm, *, field_types, payload_types,
    ea_modes, registers,
) -> ResolvedEncodingForm:
    """Resolve the canonical form's bindings, legal domains, controls and reads."""
    from engine.isa.instructions import Repcc
    from engine.isa.types import (
        RegisterFieldType, RegisterSelectorFieldType, RegisterPairSelectorFieldType,
        RegisterSelectorPayloadType, SizeSelectorFieldType, payload_type_is_signed,
    )
    from engine.syntax.instruction import DecimalLiteral, OperandReference

    source = instruction.source.parent / "encodings.yaml"
    diagnostics = tuple(_check_form_relations(
        instruction, form, field_types=field_types, payload_types=payload_types,
        ea_modes=ea_modes, registers=registers, source=source,
    ))
    if diagnostics:
        error = diagnostics[0]
        raise ValueError(f"{error.source}: {error.code}: {error.message}; path={error.path!r}")
    encoding_class = encoding_class_for_width(form.pattern.bit_width)
    fields = []
    resolved_constraints = []
    for field in form.fields:
        definition = field_types.resolve(field.type)
        ranges = ((0, (1 << definition.bits) - 1),)
        group = None
        if isinstance(definition, SizeSelectorFieldType):
            ranges = _value_ranges(item.value for item in definition.values)
            if form.syntax.size_field == field.marker:
                ranges = _intersect_ranges(ranges, _value_ranges(item.value for item in definition.values if item.code in form.syntax.selected_size_codes))
        elif isinstance(definition, (RegisterFieldType, RegisterSelectorFieldType, RegisterPairSelectorFieldType)):
            group = registers.groups.resolve(definition.register_group)
            codes = {register.encoding for register in group.registers.values() if register.encoding is not None}
            if isinstance(definition, RegisterPairSelectorFieldType):
                codes = {code // 2 for code in codes if code % 2 == 0 and code + 1 in codes}
            ranges = _intersect_ranges(ranges, _value_ranges(codes))
        constraints = []
        for constraint in form.constraints:
            if constraint.role == field.role:
                accepted = constraint_ranges(form, constraint, field_types=field_types, ea_modes=ea_modes.values())
                constraints.append((constraint, accepted))
                ranges = _intersect_ranges(ranges, accepted)
        if not ranges:
            raise ValueError(f"{source}: {form.id}: field {field.role!r} has an empty accepted domain")
        resolved = ResolvedFieldBinding(field, definition, form.pattern.field_positions(field.marker), ranges, group)
        fields.append(resolved)
        resolved_constraints.extend(ResolvedConstraint(constraint, resolved, accepted) for constraint, accepted in constraints)
    by_marker = {field.field.marker: field for field in fields}
    by_role = {}
    for field in fields:
        name = field.field.role
        if name not in instruction.operands:
            continue
        logical = instruction.operands[name]
        access = field.field.access or logical.access
        if isinstance(field.definition, EffectiveAddressFieldType):
            purpose = logical.role if logical.role in {"address", "control_target"} else "value"
            width = "predicate" if logical.value_type == "predicate" else "operation_size" if form.syntax.size_field or form.syntax.fixed_size_suffix else "Q"
            immediate = immediate_ea_selector_values(ea_modes.values(), field.definition)
            allows_immediate = access != "write" and any(low <= value <= high for value in immediate for low, high in field.ranges)
            operand = ResolvedEffectiveAddressOperand(name, logical, access, field, field.definition.owner, field.definition.profile, purpose, width, allows_immediate)
        else:
            operand = ResolvedFieldOperand(name, logical, access, field)
        by_role[name] = operand
    for payload in form.payloads:
        if payload.role not in instruction.operands:
            raise ValueError(f"{source}: {form.id}: payload has no logical operand {payload.role!r}")
        logical = instruction.operands[payload.role]
        definition = payload_types.resolve(payload.type)
        group = registers.groups.resolve(definition.register_group) if isinstance(definition, RegisterSelectorPayloadType) else None
        by_role[payload.role] = ResolvedPayloadOperand(payload.role, logical, payload.access or logical.access, payload, definition, payload_type_is_signed(definition), group)
    fixed = _resolve_fixed_operands(instruction, form, registers=registers)
    for _, operand in fixed:
        by_role[operand.name] = operand

    display = []
    payloads = iter(form.payloads)
    fixed_iter = iter(fixed)
    for item in form.syntax.displayed_operands:
        if isinstance(item, OperandReference) and item.field is not None:
            field = by_marker[item.field]
            if field.field.role in by_role:
                display.append(by_role[field.field.role])
        elif isinstance(item, OperandReference) and item.angled:
            payload = next(payloads, None)
            if payload is None:
                raise ValueError(f"{source}: {form.id}: displayed payload has no declared binding")
            display.append(by_role[payload.role])
        elif isinstance(item, (OperandReference, DecimalLiteral)):
            _, operand = next(fixed_iter)
            display.append(operand)
    displayed_names = {operand.name for operand in display}
    hidden = tuple(
        by_role[name]
        for name in instruction.operands
        if name in by_role and name not in displayed_names
    )
    operands = list(hidden)
    operand_names = {operand.name for operand in operands}
    for operand in display:
        if operand.name not in operand_names:
            operands.append(operand)
            operand_names.add(operand.name)
    operands = tuple(operands)
    ea_order = tuple(operand for operand in display if isinstance(operand, ResolvedEffectiveAddressOperand))
    if len({operand.name for operand in ea_order}) != len(ea_order):
        raise ValueError(f"{source}: {form.id}: an EA operand is displayed more than once")
    hidden_ea = {operand.name for operand in operands if isinstance(operand, ResolvedEffectiveAddressOperand)} - {operand.name for operand in ea_order}
    if hidden_ea:
        raise ValueError(f"{source}: {form.id}: EA byte order requires a displayed operand for {sorted(hidden_ea)}")
    reads = []
    for operand in ea_order:
        alternatives = _ea_alternatives(operand.field, ea_modes, field_types=field_types, payload_types=payload_types)
        sizes = tuple(alternative.appended_bytes for alternative in alternatives)
        reads.append(ResolvedEaRead(operand, alternatives, min(sizes), max(sizes)))
    reads.extend(ResolvedPayloadRead(by_role[payload.role]) for payload in form.payloads)
    sizes = ()
    if form.syntax.size_field is not None:
        selector = by_marker[form.syntax.size_field]
        sizes = tuple((item.value, item.code) for item in selector.definition.values if any(low <= item.value <= high for low, high in selector.ranges))
    elif form.syntax.fixed_size_suffix is not None:
        sizes = ((0, form.syntax.fixed_size_suffix.removeprefix(".")),)
    overlaps = []
    for overlap in form.overlaps:
        left, right = (by_role[name] for name in overlap.operands)
        if not isinstance(left, ResolvedFieldOperand) or not isinstance(right, ResolvedFieldOperand):
            raise ValueError(f"{source}: {form.id}: operand overlap requires two field operands")
        overlaps.append(ResolvedOperandOverlap(overlap, left, right))
    repeat = instruction.repeat
    observed = None
    kind = None
    if isinstance(repeat, Repcc):
        if repeat.observed_value == "computed":
            kind = "computed"
        else:
            observed = by_role.get(repeat.observed_value)
            if observed is None:
                raise ValueError(f"{source}: {form.id}: repeat observation has no operand in this form")
            kind = "source" if observed.logical.role == "source" else "result"
    control = ResolvedInstructionControl(
        instruction.route, instruction.privileged, _predicate_mode(instruction.mnemonic),
        ResolvedRepeatControl(repeat is not None, isinstance(repeat, Repcc), kind, observed),
    )
    fixed_bytes = encoding_class.opcode_space_bytes + sum(payload_types.resolve(payload.type).bytes for payload in form.payloads)
    return ResolvedEncodingForm(
        instruction, form, encoding_class, tuple(fields), tuple(resolved_constraints),
        operands, tuple(display), tuple(reads), sizes, tuple(overlaps), control,
        fixed_bytes,
        fixed_bytes + sum(read.minimum_bytes for read in reads if isinstance(read, ResolvedEaRead)),
        fixed_bytes + sum(read.maximum_bytes for read in reads if isinstance(read, ResolvedEaRead)),
    )


def _predicate_mode(mnemonic: str) -> str:
    modes = {
        "MOVcc": "annul_on_false", "FMOVcc": "annul_on_false", "Jcc": "annul_on_false",
        "CMPJcc": "temporary", "TESTJcc": "temporary", "IJcc": "counter_and_condition",
        "DJcc": "counter_and_condition", "REPcc": "counter_and_condition", "SETcc": "write_boolean",
    }
    return modes.get(mnemonic, "none")


def _insert_field(value: int, positions: tuple[int, ...], field_value: int) -> int:
    if field_value < 0 or field_value >= 1 << len(positions):
        raise ValueError("field value exceeds its encoded width")
    for offset, position in enumerate(positions):
        bit = len(positions) - offset - 1
        value = (value & ~(1 << position)) | (((field_value >> bit) & 1) << position)
    return value


def _pattern_range_witness(pattern, lower: int, upper: int) -> int | None:
    from functools import lru_cache
    width, mask, fixed = pattern.bit_width, pattern.fixed_mask, pattern.fixed_value
    lower, upper = max(0, lower), min((1 << width) - 1, upper)
    if lower > upper:
        return None

    @lru_cache(None)
    def visit(bit, lower_tight, upper_tight):
        if bit < 0:
            return 0
        minimum = (lower >> bit) & 1 if lower_tight else 0
        maximum = (upper >> bit) & 1 if upper_tight else 1
        choices = (((fixed >> bit) & 1),) if mask & (1 << bit) else (0, 1)
        for choice in choices:
            if minimum <= choice <= maximum:
                suffix = visit(bit - 1, lower_tight and choice == minimum, upper_tight and choice == maximum)
                if suffix is not None:
                    return (choice << bit) | suffix
        return None

    return visit(width - 1, True, True)


def representative_record(resolved_form: ResolvedEncodingForm) -> tuple[int, ...]:
    """Choose a record from the complete resolved legal field/layout relation."""
    value = resolved_form.form.pattern.fixed_value
    selected_ea = {}
    for read in resolved_form.layout:
        if not isinstance(read, ResolvedEaRead):
            continue
        candidates = (
            (witness, alternative)
            for alternative in read.alternatives
            for lower, upper in read.operand.field.ranges
            if (witness := _pattern_range_witness(alternative.pattern, lower, upper)) is not None
        )
        try:
            selected_ea[read.operand.name] = min(candidates, key=lambda item: (item[0], item[1].appended_bytes))
        except ValueError as error:
            raise ValueError(f"{resolved_form.form.id}: no legal EA representative") from error
    for field in resolved_form.fields:
        witness = selected_ea[field.field.role][0] if field.field.role in selected_ea else field.ranges[0][0]
        value = _insert_field(value, field.positions, witness)
    descriptors = []
    ea_payloads = []
    payloads = []
    for read in resolved_form.layout:
        if isinstance(read, ResolvedEaRead):
            _, alternative = selected_ea[read.operand.name]
            if alternative.descriptor is not None:
                pattern = alternative.descriptor.pattern
                descriptors.extend(pattern.fixed_value.to_bytes(pattern.bit_width // 8, "big"))
            ea_payloads.extend(bytes(alternative.payload_width // 8))
        else:
            operand = read.operand
            encoded = min(register.encoding for register in operand.register_group.registers.values() if register.encoding is not None) if operand.register_group is not None else 0
            payloads.extend(encoded.to_bytes(operand.definition.bytes, "little"))
    trailing = (*descriptors, *ea_payloads, *payloads)
    opcode_bytes = resolved_form.encoding_class.opcode_space_bytes
    total = opcode_bytes + len(trailing)
    opcode = frame_opcode(resolved_form.encoding_class, value, total)
    return (*opcode, *trailing)
