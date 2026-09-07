"""Generate LLVM TableGen records from the canonical ISA catalog."""

from __future__ import annotations

from pathlib import Path
import re
from typing import NamedTuple

from engine.isa.configuration import IsaConfiguration
from engine.isa.decoding import DecodeIR, project_decode
from engine.isa.ea import CompactExtensionEAMode
from engine.isa.encoding import (
    ResolvedEncodingForm, ResolvedFieldOperand, ResolvedEffectiveAddressOperand,
    ResolvedPayloadOperand, ResolvedFixedNameOperand, ResolvedFixedValueOperand,
    ResolvedEaRead, ResolvedPayloadRead,
)
from engine.isa.encoding_architecture import (
    ENCODING_CLASSES, EXTENDED_CLASS_PREFIX_BITS, MAX_RECORD_BYTES,
    MIN_EXTENDED_RECORD_BYTES, encoding_class_for_extended_prefix,
)
from engine.artifacts.generate import ArtifactGenerationContext
from engine.artifacts.definition import GeneratedArtifact, GeneratedArtifactSet
from engine.isa.registers import register_selector_members
from engine.isa.control_registers import ControlRegister, control_register_selector_members
from engine.isa.types import (
    ControlRegisterSelectorPayloadType, EnumConditionFieldType,
    FloatingPointConstantIdPayloadType, ImmediateFieldType, ImmediatePayloadType,
    MemoryOrderFieldType, PageTableLevelFieldType, PcDisplacementPayloadType,
    RegisterPairSelectorFieldType, RegisterSelectorPayloadType, FlagsFieldType,
)
from engine.syntax.instruction import (
    AddressExpression, AddressOperator, OperandReference, ScaleReference,
)


class _RenderedForm(NamedTuple):
    record_name: str
    identifier: str
    mnemonic: str
    syntax: str
    pattern: str
    encoding_class: str
    owner: str
    primary_bytes: int
    fixed_payload_bytes: int
    has_effective_address: bool
    has_variable_length: bool
    tablegen_codec_candidate: bool
    field_markers: str
    field_roles: str
    field_types: str
    payload_roles: str
    payload_types: str


class _VectorOperand(NamedTuple):
    kind: int
    field: int
    width: int
    allow_immediate_ea: bool
    signed: bool


class _VectorForm(NamedTuple):
    record_name: str
    identifier: str
    mnemonic: str
    owner: str
    pattern: str
    suffixes: str
    encoding_class: str
    suffix_field: int
    allowed_suffix_mask: int
    allowed_condition_mask: int
    has_condition: bool
    has_width_only_aliases: bool
    operands: tuple[_VectorOperand, ...]
    address_syntax: int
    address_operand_start: int
    tail_operand_order: tuple[int, ...]
    ea_operand_count: int
    distinct_operand_mask: int
    constraint_predicate: str


class _RepeatEntry(NamedTuple):
    record_name: str
    mnemonic: str
    has_condition: bool
    allows_rep: bool
    allows_repcc: bool


class _ScalarOperand(NamedTuple):
    kind: int
    field: int
    width: int
    signed: bool
    allow_immediate_ea: bool
    fixed_value: int
    allowed_mask: int


class _ScalarForm(NamedTuple):
    record_name: str
    identifier: str
    mnemonic: str
    pattern: str
    primary_bytes: int
    fixed_payload_bytes: int
    suffixes: str
    suffix_field: int
    allowed_suffix_mask: int
    condition_field: int
    allowed_condition_mask: int
    operands: tuple[_ScalarOperand, ...]
    tail_operand_order: tuple[int, ...]
    ea_operand_count: int
    distinct_operand_mask: int


class _RegisterSelector(NamedTuple):
    group: int
    name: str
    encoding: int


class _FramingClass(NamedTuple):
    name: str
    primary_bytes: int
    pattern_bits: int
    header_mask: int
    header_value: int
    length_mask: int
    length_shift: int


class _FramingPrefix(NamedTuple):
    prefix: int
    class_name: str


class _EaLayout(NamedTuple):
    profile: str
    selector: int
    descriptor_bytes: int
    payload_bytes: int


class TableGenProjection(NamedTuple):
    """Typed records selected for the LLVM TableGen consumer."""

    forms: tuple[_RenderedForm, ...]
    scalar_forms: tuple[_ScalarForm, ...]
    vector_forms: tuple[_VectorForm, ...]
    repeat_entries: tuple[_RepeatEntry, ...]
    register_selectors: tuple[_RegisterSelector, ...]
    framing_classes: tuple[_FramingClass, ...]
    framing_prefixes: tuple[_FramingPrefix, ...]
    framing_constants: tuple[tuple[str, int], ...]
    ea_layouts: tuple[_EaLayout, ...]


_OPERAND_NONE = 0
_OPERAND_CONDITION = 1
_OPERAND_GPR = 2
_OPERAND_FPR = 3
_OPERAND_VECTOR = 4
_OPERAND_PREDICATE = 5
_OPERAND_EA = 6
_OPERAND_VEA = 7
_OPERAND_IMMEDIATE = 8
_OPERAND_TAIL_SIGNED = 9
_OPERAND_TAIL_UNSIGNED = 10
_OPERAND_SEGMENT = 11
_OPERAND_FIXED_SP = 12
_OPERAND_FIXED_CS = 13
_OPERAND_FIXED_IMMEDIATE = 14
_OPERAND_FEA = 15
_OPERAND_MEMORY_ORDER = 16
_OPERAND_REGISTER_SELECTOR = 17

_VECTOR_ADDRESS_NONE = 0
_VECTOR_ADDRESS_GPR_GPR = 1
_VECTOR_ADDRESS_VECTOR = 2
_VECTOR_ADDRESS_GPR_VECTOR = 3
_VECTOR_ADDRESS_GPR_VECTOR_SCALED = 4
_VECTOR_ADDRESS_GPR_VECTOR_SCALED_OFFSET = 5

def project_tablegen(
    decoded: DecodeIR,
    *,
    control_register_selectors: tuple[tuple[ControlRegister, int], ...],
) -> TableGenProjection:
    """Project resolved ISA values into the LLVM catalog representation."""
    selector_groups = {operand.register_group.reference: operand.register_group for form in decoded.forms for operand in form.operands if isinstance(operand, ResolvedPayloadOperand) and operand.register_group is not None}
    selector_group_ids = {reference: index + 1 for index, reference in enumerate(sorted(selector_groups))}
    control_group = len(selector_group_ids) + 1
    selectors = [_RegisterSelector(selector_group_ids[reference], register.id.lower(), value) for reference in sorted(selector_groups) for register, value in register_selector_members(selector_groups[reference])]
    if any(isinstance(operand, ResolvedPayloadOperand) and isinstance(operand.definition, ControlRegisterSelectorPayloadType) for form in decoded.forms for operand in form.operands):
        selectors.extend(_RegisterSelector(control_group, register.id.lower(), value) for register, value in control_register_selectors)
    forms, scalar, vector, repeats, names = [], [], [], [], set()
    by_instruction = {}
    owners = {id(bundle.instruction): bundle.owner for bundle in decoded.bundles}
    for resolved in decoded.forms:
        form, instruction = resolved.form, resolved.instruction
        owner = owners[id(instruction)]
        identifier = f"{owner}.{instruction.mnemonic}.{form.id}"
        by_instruction.setdefault(id(instruction), resolved)
        fields = resolved.fields
        ordered_payloads = tuple(operation.operand for operation in resolved.layout if isinstance(operation, ResolvedPayloadRead))
        has_ea = any(isinstance(operand, ResolvedEffectiveAddressOperand) for operand in resolved.operands)
        fixed_payload_bytes = resolved.fixed_required_bytes - resolved.encoding_class.opcode_space_bytes
        forms.append(_RenderedForm(
            _record_name(identifier, names), identifier, form.syntax.mnemonic.lower(), form.syntax.code,
            form.pattern.code, resolved.encoding_class.name, owner,
            resolved.encoding_class.opcode_space_bytes, fixed_payload_bytes, has_ea,
            resolved.minimum_required_bytes != resolved.maximum_required_bytes,
            not has_ea and resolved.fixed_required_bytes <= 8,
            ";".join(field.field.marker for field in fields), ";".join(field.field.role for field in fields),
            ";".join(field.definition.id for field in fields), ";".join(operand.name for operand in ordered_payloads),
            ";".join(operand.definition.id for operand in ordered_payloads),
        ))
        if resolved.control.route == "vector":
            vector.append(_render_vector_form(resolved, owner, identifier, names))
        else:
            value = _render_scalar_form(resolved, identifier, names, selector_group_ids, control_group)
            if value is not None:
                scalar.append(value)
    for resolved in by_instruction.values():
        instruction = resolved.instruction
        mnemonic = instruction.mnemonic
        has_condition = any(isinstance(field.definition, EnumConditionFieldType) for field in resolved.fields)
        repeat = resolved.control.repeat
        repeats.append(_RepeatEntry(_record_name(f"repeat.{owners[id(instruction)]}.{mnemonic}", names), (mnemonic[:-2] if has_condition else mnemonic).lower(), has_condition, repeat.rep, repeat.repcc))
    framing_classes = tuple(
        _FramingClass(
            owner.name, owner.opcode_space_bytes, owner.pattern_bits,
            ((1 << len(owner.fixed_prefix)) - 1) << (8 - len(owner.fixed_prefix)),
            int(owner.fixed_prefix, 2) << (8 - len(owner.fixed_prefix)),
            ((1 << owner.length_bits) - 1) << (8 - owner.framing_bits),
            8 - owner.framing_bits,
        )
        for owner in ENCODING_CLASSES
    )
    framing_prefixes = tuple(
        _FramingPrefix(prefix, encoding_class_for_extended_prefix(prefix).name)
        for prefix in range(1 << EXTENDED_CLASS_PREFIX_BITS)
    )
    descriptor_families = {
        (family.owner, family.profile, family.name): family
        for family in decoded.effective_addresses.descriptor_families
    }
    ea_layouts = []
    for profile in decoded.effective_addresses.profiles:
        for entry in profile.compact_entries:
            if entry.form is None:
                continue
            descriptor_bytes = 0
            if isinstance(entry.form.mode, CompactExtensionEAMode):
                family = descriptor_families.get((
                    entry.form.mode.catalog.owner,
                    entry.form.mode.catalog.profile,
                    entry.form.mode.extension.id,
                ))
                if family is None:
                    raise ValueError(
                        f"{entry.form.mode.source}: LLVM codec has no declared "
                        f"descriptor family {entry.form.mode.extension.id!r}"
                    )
                if family.descriptor_bytes != entry.form.mode.extension.bytes:
                    raise ValueError(
                        f"{entry.form.mode.source}: LLVM codec descriptor width "
                        "differs from the compact extension declaration"
                    )
                if any(form.payloads for form in family.forms):
                    raise ValueError(
                        f"{entry.form.mode.source}: LLVM codec cannot represent "
                        "descriptor-owned payloads"
                    )
                descriptor_bytes = family.descriptor_bytes
            ea_layouts.append(_EaLayout(
                profile.definition.profile,
                entry.raw,
                descriptor_bytes,
                entry.form.payload_width // 8,
            ))
    return TableGenProjection(
        tuple(forms), tuple(scalar), tuple(vector), tuple(repeats), tuple(selectors),
        framing_classes, framing_prefixes,
        (("MinExtendedRecordBytes", MIN_EXTENDED_RECORD_BYTES),
         ("MaxRecordBytes", MAX_RECORD_BYTES),
         ("ExtendedClassPrefixBits", EXTENDED_CLASS_PREFIX_BITS)),
        tuple(ea_layouts),
    )


def _tablegen_projection(context: ArtifactGenerationContext) -> TableGenProjection:
    isa = context.workspace.require_provider("isa")
    configuration = IsaConfiguration.resolve(isa.catalog)
    decoded = context.shared_result((project_decode, id(isa), configuration), lambda: project_decode(
        tuple(isa.catalog.instructions.resolve(reference) for reference in isa.catalog.instruction_order),
        configuration=configuration, field_types=isa.types.field_types,
        payload_types=isa.types.payload_types, ea_modes=isa.catalog.ea_modes,
        registers=isa.registers, cpuid=isa.cpuid,
    ))
    controls = tuple(
        entry
        for namespace in isa.control_registers.namespaces.values()
        if namespace.owner in configuration.owners
        for entry in control_register_selector_members(namespace)
    ) if any(
        isinstance(operand, ResolvedPayloadOperand)
        and isinstance(operand.definition, ControlRegisterSelectorPayloadType)
        for form in decoded.forms for operand in form.operands
    ) else ()
    return context.shared_result(
        (project_tablegen, id(decoded), id(isa.control_registers)),
        lambda: project_tablegen(decoded, control_register_selectors=controls),
    )


def generate(definition, context: ArtifactGenerationContext) -> GeneratedArtifactSet:
    projection = _tablegen_projection(context)
    return GeneratedArtifactSet((GeneratedArtifact(definition.outputs["catalog"], _render(projection)),), definition.id)


def validate(definition, context) -> None:
    if set(definition.outputs) != {"catalog"}:
        raise ValueError(f"{definition.source}: LLVM MC output must select one catalog")
    _tablegen_projection(context)


def _allowed_mask(field, capacity):
    if any(lower < 0 or upper >= capacity for lower, upper in field.ranges):
        raise ValueError(f"{field.definition.source}: accepted domain exceeds LLVM mask capacity {capacity}")
    return sum(1 << value for lower, upper in field.ranges for value in range(lower, upper + 1))


def _distinct_operand_mask(resolved, roles) -> int:
    mask = 0
    for relation in resolved.overlaps:
        if relation.overlap.type == "same_value":
            continue
        left, right = sorted(roles.index(operand.name) for operand in (relation.left, relation.right))
        if left == right:
            raise ValueError(f"{resolved.form.id}: distinct operands must name different roles")
        mask |= 1 << (right * (right - 1) // 2 + left)
    return mask


def _vector_constraint_predicate(resolved: ResolvedEncodingForm) -> str:
    declarations, conditions = [], []
    for index, field in enumerate(resolved.fields):
        name = f"field_{index}"
        positions = field.positions
        value = " | ".join(f"(((encoding >> {position}) & 1ULL) << {len(positions) - offset - 1})" for offset, position in enumerate(positions))
        declarations.append(f"const uint64_t {name} = {value};")
        alternatives = " || ".join(f"({lower}ULL <= {name} && {name} <= {upper}ULL)" for lower, upper in field.ranges)
        conditions.append(f"({alternatives})")
    parameter = "uint64_t encoding" if declarations else "uint64_t"
    body = " ".join((*declarations, f"return {' && '.join(conditions) or 'true'};"))
    return f"+[]({parameter}) {{ {body} }}"


def _suffix_projection(resolved, capacity):
    form = resolved.form
    if form.syntax.size_field is None:
        return "", 0, 1
    count = max(value for value, _ in resolved.sizes) + 1
    if count > capacity:
        raise ValueError(f"{form.id}: LLVM suffix mask capacity is {capacity} encoded values")
    chars = ["?"] * count
    for value, code in resolved.sizes:
        if len(code) != 1:
            raise ValueError(f"{form.id}: LLVM suffix must be one character")
        chars[value] = code.lower()
    return "".join(chars), ord(form.syntax.size_field), sum(1 << value for value, _ in resolved.sizes)


def _mnemonic(resolved, conditions):
    name = resolved.form.syntax.mnemonic.lower()
    if conditions:
        if not name.endswith("cc"):
            raise ValueError(f"{resolved.form.id}: LLVM conditional spelling requires a cc suffix")
        name = name[:-2]
    if resolved.form.syntax.fixed_size_suffix is not None:
        name += resolved.form.syntax.fixed_size_suffix.lower()
    return name


def _field_operand_kind(operand, identifier):
    if isinstance(operand, ResolvedEffectiveAddressOperand):
        return {"ea": _OPERAND_EA, "vea": _OPERAND_VEA, "fea": _OPERAND_FEA}[operand.profile]
    definition = operand.field.definition
    if isinstance(definition, ImmediateFieldType):
        return _OPERAND_IMMEDIATE
    if operand.field.register_group is not None:
        kinds = {"GPR": _OPERAND_GPR, "FPR": _OPERAND_FPR, "VECTOR": _OPERAND_VECTOR, "PREDICATE": _OPERAND_PREDICATE, "SEGMENT": _OPERAND_SEGMENT}
        group = operand.field.register_group.id
        if group in kinds:
            return kinds[group]
    raise ValueError(f"{identifier}: unsupported LLVM vector operand {definition.id!r}")


def _tail_operand_order(resolved, roles):
    order = []
    saw_standalone = False
    for operation in resolved.layout:
        if isinstance(operation, ResolvedEaRead):
            if saw_standalone:
                raise ValueError(
                    f"{resolved.form.id}: LLVM codec requires all effective-address "
                    "descriptors before standalone payloads"
                )
        elif isinstance(operation, ResolvedPayloadRead):
            saw_standalone = True
        else:
            raise ValueError(
                f"{resolved.form.id}: LLVM codec cannot represent layout operation "
                f"{type(operation).__name__}"
            )
        try:
            index = roles.index(operation.operand.name)
        except ValueError as error:
            raise ValueError(
                f"{resolved.form.id}: LLVM codec has no operand for layout role "
                f"{operation.operand.name!r}"
            ) from error
        if index in order:
            raise ValueError(
                f"{resolved.form.id}: LLVM codec layout repeats operand index {index}"
            )
        order.append(index)
    ea_count = sum(
        isinstance(operation, ResolvedEaRead) for operation in resolved.layout
    )
    return tuple(order), ea_count


def _render_vector_form(resolved, owner, identifier, names) -> _VectorForm:
    encoding_class = resolved.encoding_class.name
    conditions = tuple(field for field in resolved.fields if isinstance(field.definition, EnumConditionFieldType))
    if len(conditions) > 1:
        raise ValueError(f"{identifier}: LLVM vector codec supports at most one condition")
    operands = [_VectorOperand(_OPERAND_CONDITION, ord(field.field.marker), 0, False, False) for field in conditions]
    roles = [field.field.role for field in conditions]
    for operand in resolved.display_order:
        roles.append(operand.name)
        if isinstance(operand, ResolvedFieldOperand):
            kind = _field_operand_kind(operand, identifier)
            operands.append(_VectorOperand(kind, ord(operand.field.field.marker), operand.field.definition.bits if kind == _OPERAND_IMMEDIATE else 0, isinstance(operand, ResolvedEffectiveAddressOperand) and operand.allows_immediate, isinstance(operand.field.definition, ImmediateFieldType) and operand.field.definition.value_type == "signed_integer"))
        elif isinstance(operand, ResolvedPayloadOperand):
            operands.append(_VectorOperand(_OPERAND_TAIL_SIGNED if operand.signed else _OPERAND_TAIL_UNSIGNED, 0, operand.definition.bytes * 8, False, operand.signed))
        else:
            raise ValueError(f"{identifier}: LLVM vector codec cannot represent fixed operands")
    if len(operands) > 6:
        raise ValueError(f"{identifier}: LLVM vector codec supports at most six operands")
    address_syntax, address_operand_start = _vector_address_projection(
        resolved, conditions, identifier
    )
    tail_operand_order, ea_operand_count = _tail_operand_order(resolved, roles)
    operands.extend([_VectorOperand(_OPERAND_NONE, 0, 0, False, False)] * (6 - len(operands)))
    suffixes, suffix_field, suffix_mask = _suffix_projection(resolved, 8)
    return _VectorForm(_record_name("vector." + identifier, names), identifier, _mnemonic(resolved, conditions), owner, resolved.form.pattern.code, suffixes, encoding_class, suffix_field, suffix_mask, _allowed_mask(conditions[0], 16) if conditions else 0xFFFF, bool(conditions), resolved.instruction.width_suffix_aliases, tuple(operands), address_syntax, address_operand_start, tail_operand_order, ea_operand_count, _distinct_operand_mask(resolved, [operand.name for operand in resolved.display_order]), _vector_constraint_predicate(resolved))


def _vector_address_projection(resolved, conditions, identifier):
    addresses = tuple(
        operand
        for operand in resolved.form.syntax.operands
        if isinstance(operand, AddressExpression)
    )
    if not addresses:
        return _VECTOR_ADDRESS_NONE, 0
    if len(addresses) != 1:
        raise ValueError(
            f"{identifier}: LLVM vector codec supports one address expression"
        )

    display = tuple(resolved.display_order)

    def resolve_reference(reference):
        if reference.field is not None:
            matches = tuple(
                (index, operand)
                for index, operand in enumerate(display)
                if isinstance(operand, ResolvedFieldOperand)
                and operand.field.field.marker == reference.field
            )
        else:
            matches = tuple(
                (index, operand)
                for index, operand in enumerate(display)
                if operand.name == reference.name
            )
        if len(matches) != 1:
            raise ValueError(
                f"{identifier}: address reference {reference!r} does not select "
                "one displayed operand"
            )
        return matches[0]

    signature = []
    references = []
    for member in addresses[0].members:
        if isinstance(member, OperandReference):
            index, operand = resolve_reference(member)
            references.append((index, operand))
            if isinstance(operand, ResolvedFieldOperand):
                signature.append(("operand", _field_operand_kind(operand, identifier)))
            elif isinstance(operand, ResolvedPayloadOperand):
                signature.append(
                    (
                        "operand",
                        _OPERAND_TAIL_SIGNED
                        if operand.signed
                        else _OPERAND_TAIL_UNSIGNED,
                    )
                )
            else:
                raise ValueError(
                    f"{identifier}: unsupported LLVM vector address operand"
                )
        elif isinstance(member, AddressOperator):
            signature.append(("operator", member.operator))
        elif isinstance(member, ScaleReference):
            signature.append(("scale",))
        else:
            raise ValueError(
                f"{identifier}: unsupported LLVM vector address member "
                f"{type(member).__name__}"
            )

    shapes = {
        (("operand", _OPERAND_GPR), ("operator", "+"),
         ("operand", _OPERAND_GPR)): _VECTOR_ADDRESS_GPR_GPR,
        (("operand", _OPERAND_VECTOR),): _VECTOR_ADDRESS_VECTOR,
        (("operand", _OPERAND_GPR), ("operator", "+"),
         ("operand", _OPERAND_VECTOR)): _VECTOR_ADDRESS_GPR_VECTOR,
        (("operand", _OPERAND_GPR), ("operator", "+"),
         ("operand", _OPERAND_VECTOR), ("operator", "*"),
         ("scale",)): _VECTOR_ADDRESS_GPR_VECTOR_SCALED,
        (("operand", _OPERAND_GPR), ("operator", "+"),
         ("operand", _OPERAND_VECTOR), ("operator", "*"), ("scale",),
         ("operator", "+"),
         ("operand", _OPERAND_TAIL_SIGNED)):
            _VECTOR_ADDRESS_GPR_VECTOR_SCALED_OFFSET,
        (("operand", _OPERAND_GPR), ("operator", "+"),
         ("operand", _OPERAND_VECTOR), ("operator", "*"), ("scale",),
         ("operator", "+"),
         ("operand", _OPERAND_TAIL_UNSIGNED)):
            _VECTOR_ADDRESS_GPR_VECTOR_SCALED_OFFSET,
    }
    try:
        shape = shapes[tuple(signature)]
    except KeyError as error:
        raise ValueError(
            f"{identifier}: unsupported LLVM vector address syntax"
        ) from error
    indices = tuple(index for index, _ in references)
    if not indices or indices != tuple(range(indices[0], indices[0] + len(indices))):
        raise ValueError(
            f"{identifier}: address operands must be contiguous in display order"
        )
    if shape in (
        _VECTOR_ADDRESS_GPR_VECTOR_SCALED,
        _VECTOR_ADDRESS_GPR_VECTOR_SCALED_OFFSET,
    ) and resolved.form.syntax.size_field is None:
        raise ValueError(
            f"{identifier}: scaled vector address requires a size field"
        )
    return shape, len(conditions) + indices[0]


def _scalar_field_operand_kind(operand):
    definition = operand.field.definition
    if isinstance(operand, ResolvedEffectiveAddressOperand):
        return {"ea": _OPERAND_EA, "fea": _OPERAND_FEA}.get(operand.profile)
    if isinstance(definition, (ImmediateFieldType, RegisterPairSelectorFieldType, PageTableLevelFieldType, FlagsFieldType)):
        return _OPERAND_IMMEDIATE
    if operand.field.register_group is not None:
        return {"GPR": _OPERAND_GPR, "FPR": _OPERAND_FPR, "SEGMENT": _OPERAND_SEGMENT}.get(operand.field.register_group.id)
    return None


def _render_scalar_form(resolved, identifier, names, selector_group_ids, control_group) -> _ScalarForm | None:
    conditions = tuple(field for field in resolved.fields if isinstance(field.definition, EnumConditionFieldType))
    if len(conditions) > 1:
        return None
    operands, roles, consumed = [], [], set()
    if resolved.form.syntax.order_field is not None:
        field = next(field for field in resolved.fields if field.field.marker == resolved.form.syntax.order_field)
        if not isinstance(field.definition, MemoryOrderFieldType):
            raise ValueError(f"{identifier}: memory-order syntax has the wrong field type")
        operands.append(_ScalarOperand(_OPERAND_MEMORY_ORDER, ord(field.field.marker), field.definition.bits, False, False, 0, _allowed_mask(field, 256)))
        roles.append(field.field.role)
        consumed.add(field.field.marker)
    for operand in resolved.display_order:
        roles.append(operand.name)
        if isinstance(operand, ResolvedFixedValueOperand):
            operands.append(_ScalarOperand(_OPERAND_FIXED_IMMEDIATE, 0, 0, False, False, operand.fixed_value, 1))
        elif isinstance(operand, ResolvedFixedNameOperand):
            kind = {"SP": _OPERAND_FIXED_SP, "CS": _OPERAND_FIXED_CS}.get(operand.register.id)
            if kind is None:
                return None
            operands.append(_ScalarOperand(kind, 0, 0, False, False, 0, 1))
        elif isinstance(operand, ResolvedPayloadOperand):
            definition = operand.definition
            if not isinstance(definition, (ImmediatePayloadType, PcDisplacementPayloadType, FloatingPointConstantIdPayloadType, RegisterSelectorPayloadType, ControlRegisterSelectorPayloadType)):
                return None
            selector = isinstance(definition, (RegisterSelectorPayloadType, ControlRegisterSelectorPayloadType))
            group = selector_group_ids[operand.register_group.reference] if isinstance(definition, RegisterSelectorPayloadType) else control_group if selector else 0
            kind = _OPERAND_REGISTER_SELECTOR if selector else _OPERAND_TAIL_SIGNED if operand.signed else _OPERAND_TAIL_UNSIGNED
            operands.append(_ScalarOperand(kind, 0, definition.bytes * 8, operand.signed, False, group, 1))
        elif isinstance(operand, ResolvedFieldOperand):
            field = operand.field
            kind = _scalar_field_operand_kind(operand)
            if kind is None or field.definition.bits > 8:
                return None
            operands.append(_ScalarOperand(kind, ord(field.field.marker), field.definition.bits, isinstance(field.definition, ImmediateFieldType) and field.definition.value_type == "signed_integer", isinstance(operand, ResolvedEffectiveAddressOperand) and operand.allows_immediate, 0, _allowed_mask(field, 256)))
            consumed.add(field.field.marker)
        else:
            raise TypeError(f"{identifier}: unresolved scalar operand {type(operand).__name__}")
    implicit = {resolved.form.syntax.size_field, *(field.field.marker for field in conditions)}
    if any(field.field.marker not in consumed | implicit for field in resolved.fields) or len(operands) > 4:
        return None
    tail_operand_order, ea_operand_count = _tail_operand_order(resolved, roles)
    operands.extend([_ScalarOperand(_OPERAND_NONE, 0, 0, False, False, 0, 1)] * (4 - len(operands)))
    suffixes, suffix_field, suffix_mask = _suffix_projection(resolved, 16)
    return _ScalarForm(_record_name("scalar." + identifier, names), identifier, _mnemonic(resolved, conditions), resolved.form.pattern.code, resolved.encoding_class.opcode_space_bytes, resolved.fixed_required_bytes - resolved.encoding_class.opcode_space_bytes, suffixes, suffix_field, suffix_mask, ord(conditions[0].field.marker) if conditions else 0, _allowed_mask(conditions[0], 16) if conditions else 0xFFFF, tuple(operands), tail_operand_order, ea_operand_count, _distinct_operand_mask(resolved, roles))


def _record_name(identifier: str, used: set[str]) -> str:
    stem = "BRForm_" + re.sub(r"[^A-Za-z0-9_]+", "_", identifier).strip("_")
    candidate = stem
    suffix = 2
    while candidate in used:
        candidate = f"{stem}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _td_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _td_bool(value: bool) -> str:
    return "1" if value else "0"


def _render(projection: TableGenProjection) -> str:
    lines = [
        "//===-- BedrockGenISACatalog.td - generated ISA catalog -*- tablegen -*-===//",
        "//",
        "// Generated by spec/artifacts/llvm-mc-tablegen. Do not edit.",
        "//",
        "//===----------------------------------------------------------------------===//",
        "",
        "class BedrockISAForm<string id, string mnemonic, string syntax,",
        "                     string pattern, string encodingClass, string owner,",
        "                     bits<8> primaryBytes, bits<8> fixedPayloadBytes,",
        "                     bit hasEffectiveAddress, bit hasVariableLength,",
        "                     bit tableGenCodecCandidate, string fieldMarkers,",
        "                     string fieldRoles, string fieldTypes,",
        "                     string payloadRoles, string payloadTypes> {",
        "  string Id = id;",
        "  string Mnemonic = mnemonic;",
        "  string Syntax = syntax;",
        "  string Pattern = pattern;",
        "  string EncodingClass = encodingClass;",
        "  string Owner = owner;",
        "  bits<8> PrimaryBytes = primaryBytes;",
        "  bits<8> FixedPayloadBytes = fixedPayloadBytes;",
        "  bit HasEffectiveAddress = hasEffectiveAddress;",
        "  bit HasVariableLength = hasVariableLength;",
        "  bit TableGenCodecCandidate = tableGenCodecCandidate;",
        "  string FieldMarkers = fieldMarkers;",
        "  string FieldRoles = fieldRoles;",
        "  string FieldTypes = fieldTypes;",
        "  string PayloadRoles = payloadRoles;",
        "  string PayloadTypes = payloadTypes;",
        "}",
        "",
        "def BedrockISAForms : GenericTable {",
        '  let FilterClass = "BedrockISAForm";',
        '  let Fields = ["Id", "Mnemonic", "Syntax", "Pattern",',
        '                "EncodingClass", "Owner", "PrimaryBytes",',
        '                "FixedPayloadBytes", "HasEffectiveAddress",',
        '                "HasVariableLength", "TableGenCodecCandidate",',
        '                "FieldMarkers", "FieldRoles", "FieldTypes",',
        '                "PayloadRoles", "PayloadTypes"];',
        "}",
        "",
        "def lookupBedrockISAFormById : SearchIndex {",
        "  let Table = BedrockISAForms;",
        '  let Key = ["Id"];',
        "}",
        "",
    ]
    lines.extend(_render_framing_tables(projection))
    lines.extend(_render_ea_layout_table(projection.ea_layouts))
    for form in projection.forms:
        args = (
            _td_string(form.identifier),
            _td_string(form.mnemonic),
            _td_string(form.syntax),
            _td_string(form.pattern),
            _td_string(form.encoding_class),
            _td_string(form.owner),
            str(form.primary_bytes),
            str(form.fixed_payload_bytes),
            _td_bool(form.has_effective_address),
            _td_bool(form.has_variable_length),
            _td_bool(form.tablegen_codec_candidate),
            _td_string(form.field_markers),
            _td_string(form.field_roles),
            _td_string(form.field_types),
            _td_string(form.payload_roles),
            _td_string(form.payload_types),
        )
        lines.extend(
            (
                f"def {form.record_name} : BedrockISAForm<",
                "  " + ", ".join(args[:6]) + ",",
                "  " + ", ".join(args[6:11]) + ",",
                "  " + ", ".join(args[11:]) + ">;",
            )
        )
    lines.extend(_render_scalar_table(projection.scalar_forms))
    lines.extend(_render_register_selector_table(projection.register_selectors))
    lines.extend(_render_vector_tables(projection.vector_forms))
    lines.extend(_render_repeat_table(projection.repeat_entries))
    lines.append("")
    return "\n".join(lines)


def _render_framing_tables(projection: TableGenProjection) -> list[str]:
    lines = [
        "class BedrockFramingClass<string name, bits<8> primaryBytes,",
        "    bits<8> patternBits, bits<8> headerMask, bits<8> headerValue,",
        "    bits<8> lengthMask, bits<8> lengthShift> {",
        "  string Name = name;",
        "  bits<8> PrimaryBytes = primaryBytes;",
        "  bits<8> PatternBits = patternBits;",
        "  bits<8> HeaderMask = headerMask;",
        "  bits<8> HeaderValue = headerValue;",
        "  bits<8> LengthMask = lengthMask;",
        "  bits<8> LengthShift = lengthShift;",
        "}",
        "def BedrockFramingClasses : GenericTable {",
        '  let FilterClass = "BedrockFramingClass";',
        '  let Fields = ["Name", "PrimaryBytes", "PatternBits", "HeaderMask",',
        '                "HeaderValue", "LengthMask", "LengthShift"];',
        "}",
        "def lookupBedrockFramingClass : SearchIndex {",
        "  let Table = BedrockFramingClasses;",
        '  let Key = ["Name"];',
        "}",
        "def lookupBedrockFramingClassByPatternBits : SearchIndex {",
        "  let Table = BedrockFramingClasses;",
        '  let Key = ["PatternBits"];',
        "}",
        "class BedrockFramingPrefix<bits<8> prefix, string className> {",
        "  bits<8> Prefix = prefix;",
        "  string ClassName = className;",
        "}",
        "def BedrockFramingPrefixes : GenericTable {",
        '  let FilterClass = "BedrockFramingPrefix";',
        '  let Fields = ["Prefix", "ClassName"];',
        '  let PrimaryKey = ["Prefix"];',
        '  let PrimaryKeyName = "lookupBedrockFramingPrefix";',
        "}",
        "class BedrockFramingConstant<string name, bits<8> value> {",
        "  string Name = name;",
        "  bits<8> Value = value;",
        "}",
        "def BedrockFramingConstants : GenericEnum {",
        '  let FilterClass = "BedrockFramingConstant";',
        '  let NameField = "Name";',
        '  let ValueField = "Value";',
        "}",
    ]
    for entry in projection.framing_classes:
        values = (_td_string(entry.name), *(str(value) for value in entry[1:]))
        lines.append(f"def FramingClass_{entry.name} : BedrockFramingClass<{', '.join(values)}>;")
    for entry in projection.framing_prefixes:
        lines.append(f"def FramingPrefix_{entry.prefix} : BedrockFramingPrefix<{entry.prefix}, {_td_string(entry.class_name)}>;")
    for name, value in projection.framing_constants:
        lines.append(f"def FramingConstant_{name} : BedrockFramingConstant<{_td_string(name)}, {value}>;")
    lines.append("")
    return lines


def _render_ea_layout_table(layouts: tuple[_EaLayout, ...]) -> list[str]:
    lines = [
        "class BedrockEaLayout<string profile, bits<8> selector,",
        "                      bits<8> descriptorBytes, bits<8> payloadBytes> {",
        "  string Profile = profile;",
        "  bits<8> Selector = selector;",
        "  bits<8> DescriptorBytes = descriptorBytes;",
        "  bits<8> PayloadBytes = payloadBytes;",
        "}",
        "def BedrockEaLayouts : GenericTable {",
        '  let FilterClass = "BedrockEaLayout";',
        '  let Fields = ["Profile", "Selector", "DescriptorBytes", "PayloadBytes"];',
        "}",
        "def lookupBedrockEaLayout : SearchIndex {",
        "  let Table = BedrockEaLayouts;",
        '  let Key = ["Profile", "Selector"];',
        "}",
    ]
    for index, layout in enumerate(layouts):
        lines.append(
            f"def BREaLayout_{index} : BedrockEaLayout<"
            f"{_td_string(layout.profile)}, {layout.selector}, "
            f"{layout.descriptor_bytes}, {layout.payload_bytes}>;"
        )
    lines.append("")
    return lines


def _render_register_selector_table(
    selectors: list[_RegisterSelector],
) -> list[str]:
    lines = [
        "",
        "class BedrockRegisterSelector<bits<8> group, string name,",
        "                              bits<64> encoding> {",
        "  bits<8> Group = group;",
        "  string Name = name;",
        "  bits<64> Encoding = encoding;",
        "}",
        "",
        "def BedrockRegisterSelectors : GenericTable {",
        '  let FilterClass = "BedrockRegisterSelector";',
        '  let CppTypeName = "RegisterSelector";',
        '  let Fields = ["Group", "Name", "Encoding"];',
        "}",
        "",
    ]
    for index, selector in enumerate(selectors):
        lines.append(
            f"def BRRegisterSelector_{index} : BedrockRegisterSelector<"
            f"{selector.group}, {_td_string(selector.name)}, "
            f"{selector.encoding}>;"
        )
    return lines


def _render_scalar_table(forms: list[_ScalarForm]) -> list[str]:
    operand_parameters = []
    operand_assignments = []
    operand_fields = []
    for index in range(4):
        for name, field_type in (
            ("Kind", "bits<8>"),
            ("Field", "bits<8>"),
            ("Width", "bits<8>"),
            ("Signed", "bit"),
            ("AllowImmediateEA", "bit"),
            ("FixedValue", "bits<64>"),
            ("AllowedMask0", "bits<64>"),
            ("AllowedMask1", "bits<64>"),
            ("AllowedMask2", "bits<64>"),
            ("AllowedMask3", "bits<64>"),
        ):
            field = f"Operand{index}{name}"
            parameter = field[0].lower() + field[1:]
            operand_parameters.append(f"{field_type} {parameter}")
            operand_assignments.append(f"  {field_type} {field} = {parameter};")
            operand_fields.append(f'"{field}"')
    lines = [
        "",
        "class BedrockScalarEncodingForm<",
        "    string id, string mnemonic, string pattern, bits<8> primaryBytes, bits<8> fixedPayloadBytes,",
        "    string suffixes,",
        "    bits<8> suffixField, bits<16> allowedSuffixMask,",
        "    bits<8> conditionField, bits<16> allowedConditionMask,",
        "    bits<8> operandCount, " + ", ".join(operand_parameters) + ",",
        "    bits<8> tailOperandCount, bits<8> eaOperandCount,",
        "    bits<8> tailOperand0, bits<8> tailOperand1,",
        "    bits<8> tailOperand2, bits<8> tailOperand3,",
        "    bits<16> distinctOperandMask> {",
        "  string Id = id;",
        "  string Mnemonic = mnemonic;",
        "  string Pattern = pattern;",
        "  bits<8> PrimaryBytes = primaryBytes;",
        "  bits<8> FixedPayloadBytes = fixedPayloadBytes;",
        "  string Suffixes = suffixes;",
        "  bits<8> SuffixField = suffixField;",
        "  bits<16> AllowedSuffixMask = allowedSuffixMask;",
        "  bits<8> ConditionField = conditionField;",
        "  bits<16> AllowedConditionMask = allowedConditionMask;",
        "  bits<8> OperandCount = operandCount;",
        *operand_assignments,
        "  bits<8> TailOperandCount = tailOperandCount;",
        "  bits<8> EaOperandCount = eaOperandCount;",
        "  bits<8> TailOperand0 = tailOperand0;",
        "  bits<8> TailOperand1 = tailOperand1;",
        "  bits<8> TailOperand2 = tailOperand2;",
        "  bits<8> TailOperand3 = tailOperand3;",
        "  bits<16> DistinctOperandMask = distinctOperandMask;",
        "}",
        "",
        "def BedrockScalarEncodingForms : GenericTable {",
        '  let FilterClass = "BedrockScalarEncodingForm";',
        '  let CppTypeName = "ScalarEncodingForm";',
        '  let Fields = ["Id", "Mnemonic", "Pattern",',
        '                "PrimaryBytes", "FixedPayloadBytes", "Suffixes",',
        '                "SuffixField", "AllowedSuffixMask",',
        '                "ConditionField", "AllowedConditionMask",',
        '                "OperandCount",',
        "                " + ", ".join(operand_fields) + ",",
        '                "TailOperandCount", "EaOperandCount",',
        '                "TailOperand0", "TailOperand1",',
        '                "TailOperand2", "TailOperand3",',
        '                "DistinctOperandMask"];',
        "}",
        "",
    ]
    for form in forms:
        values = [
            _td_string(form.identifier),
            _td_string(form.mnemonic),
            _td_string(form.pattern),
            str(form.primary_bytes),
            str(form.fixed_payload_bytes),
            _td_string(form.suffixes),
            str(form.suffix_field),
            str(form.allowed_suffix_mask),
            str(form.condition_field),
            str(form.allowed_condition_mask),
            str(sum(operand.kind != _OPERAND_NONE for operand in form.operands)),
        ]
        for operand in form.operands:
            values.extend(
                (
                    str(operand.kind),
                    str(operand.field),
                    str(operand.width),
                    _td_bool(operand.signed),
                    _td_bool(operand.allow_immediate_ea),
                    str(operand.fixed_value),
                    *(
                        str((operand.allowed_mask >> (64 * index)) & ((1 << 64) - 1))
                        for index in range(4)
                    ),
                )
            )
        tail_order = (
            *form.tail_operand_order,
            *(0 for _ in range(4 - len(form.tail_operand_order))),
        )
        values.extend(
            (
                str(len(form.tail_operand_order)),
                str(form.ea_operand_count),
                *(str(index) for index in tail_order),
                str(form.distinct_operand_mask),
            )
        )
        lines.extend(
            (
                f"def {form.record_name} : BedrockScalarEncodingForm<",
                "  " + ", ".join(values[:10]) + ",",
                "  " + ", ".join(values[10:28]) + ",",
                "  " + ", ".join(values[28:]) + ">;",
            )
        )
    return lines


def _render_vector_tables(forms: list[_VectorForm]) -> list[str]:
    operand_parameters = []
    operand_assignments = []
    operand_fields = []
    for index in range(6):
        for name, field_type in (
            ("Kind", "bits<8>"),
            ("Field", "bits<8>"),
            ("Width", "bits<8>"),
            ("AllowImmediateEA", "bit"),
            ("Signed", "bit"),
        ):
            field = f"Operand{index}{name}"
            parameter = field[0].lower() + field[1:]
            operand_parameters.append(f"{field_type} {parameter}")
            operand_assignments.append(f"  {field_type} {field} = {parameter};")
            operand_fields.append(f'"{field}"')

    lines = [
        "",
        "class BedrockVectorEncodingForm<",
        "    string id, string mnemonic, string owner, string pattern, string suffixes,",
        "    string encodingClass, bits<8> suffixField,",
        "    bits<8> allowedSuffixMask, bits<16> allowedConditionMask,",
        "    bit hasCondition, bit hasWidthOnlyAliases, bits<8> operandCount,",
        "    " + ", ".join(operand_parameters) + ",",
        "    bits<8> addressSyntax, bits<8> addressOperandStart,",
        "    bits<8> tailOperandCount, bits<8> eaOperandCount,",
        "    bits<8> tailOperand0, bits<8> tailOperand1, bits<8> tailOperand2,",
        "    bits<8> tailOperand3, bits<8> tailOperand4, bits<8> tailOperand5,",
        "    bits<16> distinctOperandMask, code constraintsMatch> {",
        "  string Id = id;",
        "  string Mnemonic = mnemonic;",
        "  string Owner = owner;",
        "  string Pattern = pattern;",
        "  string Suffixes = suffixes;",
        "  string EncodingClass = encodingClass;",
        "  bits<8> SuffixField = suffixField;",
        "  bits<8> AllowedSuffixMask = allowedSuffixMask;",
        "  bits<16> AllowedConditionMask = allowedConditionMask;",
        "  bit HasCondition = hasCondition;",
        "  bit HasWidthOnlyAliases = hasWidthOnlyAliases;",
        "  bits<8> OperandCount = operandCount;",
        *operand_assignments,
        "  bits<8> AddressSyntax = addressSyntax;",
        "  bits<8> AddressOperandStart = addressOperandStart;",
        "  bits<8> TailOperandCount = tailOperandCount;",
        "  bits<8> EaOperandCount = eaOperandCount;",
        "  bits<8> TailOperand0 = tailOperand0;",
        "  bits<8> TailOperand1 = tailOperand1;",
        "  bits<8> TailOperand2 = tailOperand2;",
        "  bits<8> TailOperand3 = tailOperand3;",
        "  bits<8> TailOperand4 = tailOperand4;",
        "  bits<8> TailOperand5 = tailOperand5;",
        "  bits<16> DistinctOperandMask = distinctOperandMask;",
        "  code ConstraintsMatch = constraintsMatch;",
        "}",
        "",
        "def BedrockVectorEncodingForms : GenericTable {",
        '  let FilterClass = "BedrockVectorEncodingForm";',
        '  let CppTypeName = "VectorEncodingForm";',
        '  let Fields = ["Id", "Mnemonic", "Owner", "Pattern", "Suffixes",',
        '                "EncodingClass", "SuffixField",',
        '                "AllowedSuffixMask", "AllowedConditionMask",',
        '                "HasCondition", "HasWidthOnlyAliases",',
        '                "OperandCount",',
        "                " + ", ".join(operand_fields) + ",",
        '                "AddressSyntax", "AddressOperandStart",',
        '                "TailOperandCount", "EaOperandCount",',
        '                "TailOperand0", "TailOperand1", "TailOperand2",',
        '                "TailOperand3", "TailOperand4", "TailOperand5",',
        '                "DistinctOperandMask", "ConstraintsMatch"];',
        "}",
        "",
    ]
    for form in forms:
        values = [
            _td_string(form.identifier),
            _td_string(form.mnemonic),
            _td_string(form.owner),
            _td_string(form.pattern),
            _td_string(form.suffixes),
            _td_string(form.encoding_class),
            str(form.suffix_field),
            str(form.allowed_suffix_mask),
            str(form.allowed_condition_mask),
            _td_bool(form.has_condition),
            _td_bool(form.has_width_only_aliases),
            str(sum(operand.kind != _OPERAND_NONE for operand in form.operands)),
        ]
        for operand in form.operands:
            values.extend(
                (
                    str(operand.kind),
                    str(operand.field),
                    str(operand.width),
                    _td_bool(operand.allow_immediate_ea),
                    _td_bool(operand.signed),
                )
            )
        values.extend((str(form.address_syntax), str(form.address_operand_start)))
        tail_order = (
            *form.tail_operand_order,
            *(0 for _ in range(6 - len(form.tail_operand_order))),
        )
        values.extend(
            (
                str(len(form.tail_operand_order)),
                str(form.ea_operand_count),
                *(str(index) for index in tail_order),
                str(form.distinct_operand_mask),
                "[{ " + form.constraint_predicate + " }]",
            )
        )
        lines.extend(
            (
                f"def {form.record_name} : BedrockVectorEncodingForm<",
                "  " + ", ".join(values[:12]) + ",",
                "  " + ", ".join(values[12:28]) + ",",
                "  " + ", ".join(values[28:]) + ">;",
            )
        )
    return lines


def _render_repeat_table(entries: list[_RepeatEntry]) -> list[str]:
    lines = [
        "",
        "class BedrockRepeatEligibility<string mnemonic, bit hasCondition,",
        "                                bit allowsREP, bit allowsREPcc> {",
        "  string Mnemonic = mnemonic;",
        "  bit HasCondition = hasCondition;",
        "  bit AllowsREP = allowsREP;",
        "  bit AllowsREPcc = allowsREPcc;",
        "}",
        "",
        "def BedrockRepeatEligibilityTable : GenericTable {",
        '  let FilterClass = "BedrockRepeatEligibility";',
        '  let CppTypeName = "RepeatEligibility";',
        '  let Fields = ["Mnemonic", "HasCondition", "AllowsREP",',
        '                "AllowsREPcc"];',
        "}",
        "",
    ]
    for entry in entries:
        lines.append(
            f"def {entry.record_name} : BedrockRepeatEligibility<"
            f"{_td_string(entry.mnemonic)}, {_td_bool(entry.has_condition)}, "
            f"{_td_bool(entry.allows_rep)}, {_td_bool(entry.allows_repcc)}>;"
        )
    return lines
