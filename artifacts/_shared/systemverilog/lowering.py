#!/usr/bin/env python3
"""Lower canonical Decode IR into file-level SystemVerilog sections."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from engine.isa.encoding_architecture import (
    ENCODING_CLASSES,
    ENCODING_CLASSES_BY_NAME,
    OPERATOR_SPACE_PREFIX_BITS,
    OPERATOR_SPACES,
    OperatorSpace,
)
from engine.isa.decoding import DecodeIR
from engine.isa.encoding import (
    ResolvedEncodingForm, ResolvedOperand, ResolvedFieldOperand,
    ResolvedEffectiveAddressOperand, ResolvedPayloadOperand,
    ResolvedFixedNameOperand, ResolvedFixedValueOperand,
    ResolvedEaRead, ResolvedPayloadRead,
)
from engine.isa.ea import ResolvedEAEncoding, CompactExtensionEAMode, ImmediateEAMode, MemoryEAMode, FieldEASegment, FixedEASegment
from engine.isa.encoding_space import form_cubes, forms_overlap
from artifacts._shared.systemverilog.templates import render_template


def _form_key(form: ResolvedEncodingForm) -> str:
    return f"{form.encoding_class.name}.{form.instruction.mnemonic}.{form.form.id}"


def _cpuid_key(field) -> str:
    reference = field.reference
    return ".".join((reference.owner, *reference.path, reference.element))


def _operand_type_name(operand: ResolvedOperand) -> str:
    if isinstance(operand, (ResolvedFieldOperand, ResolvedPayloadOperand)):
        definition = operand.field.definition if isinstance(operand, ResolvedFieldOperand) else operand.definition
        return {"CC": "condition", "FLBMP": "flags_bitmap", "MORDER": "memory_order", "PTLVL": "pt_level", "FCONST": "fconst_id"}.get(definition.id, definition.id.lower() if definition.id.startswith(("IMM", "DISP", "ABS")) else definition.id)
    if isinstance(operand, ResolvedFixedNameOperand):
        return operand.fixed_name
    if isinstance(operand, ResolvedFixedValueOperand):
        return "imm"
    raise TypeError(type(operand).__name__)


def _operand_ea_width(operand: ResolvedOperand) -> str:
    return operand.interpretation_width if isinstance(operand, ResolvedEffectiveAddressOperand) else ""


def _profile_name(owner: str, profile: str) -> str:
    return f"{owner}_{profile}"


def _family_name(family) -> str:
    return f"{family.owner}_{family.profile}_{family.name.lower()}"


def _descriptor_name(form: ResolvedEAEncoding) -> str:
    mode = form.mode
    return f"{mode.catalog.owner}_{mode.catalog.profile}_{mode.extension.id.lower()}" if isinstance(mode, CompactExtensionEAMode) else ""


def _ea_form_name(form: ResolvedEAEncoding) -> str:
    mode = form.mode
    index = next(index for index, item in enumerate(mode.encodings) if item is form.encoding)
    return f"{mode.catalog.owner}_{mode.catalog.profile}_{mode.catalog.mode_type}_{mode.id}_{index}"


def _ea_kind(form: ResolvedEAEncoding) -> str:
    return "escape" if isinstance(form.mode, CompactExtensionEAMode) else "immediate" if isinstance(form.mode, ImmediateEAMode) else "memory"


def _ea_segment(form: ResolvedEAEncoding) -> str:
    mode = form.mode
    if not isinstance(mode, MemoryEAMode):
        return ""
    return "explicit" if isinstance(mode.segment, FieldEASegment) else mode.segment.register if isinstance(mode.segment, FixedEASegment) else "default"


def _ea_base(form: ResolvedEAEncoding) -> str:
    if not isinstance(form.mode, MemoryEAMode):
        return ""
    base = form.mode.base_source.value
    return base if base not in {"none", "encoded"} else ""


def _update_difference(difference: int | str) -> str:
    return f"constant_{difference}" if isinstance(difference, int) else difference


EA_LOW_POSITIONS = (6, 5, 4, 3, 2, 1, 0)
EA_HIGH_POSITIONS = (13, 12, 11, 10, 9, 8, 7)
EA_MEDIUM_ALT_POSITIONS = (16, 15, 14, 3, 2, 1, 0)

EA_LAYOUT_NONE = "EA_LAYOUT_NONE"
EA_LAYOUT_LOW = "EA_LAYOUT_LOW"
EA_LAYOUT_ALT = "EA_LAYOUT_ALT"
EA_LAYOUT_ALT_THEN_LOW = "EA_LAYOUT_ALT_THEN_LOW"


def _width(count: int) -> int:
    return max(1, (count - 1).bit_length())


def _identifier(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()
    return text or "NONE"


def _identifiers(
    values: Iterable[str], prefix: str, *, reserved: Iterable[str] = ()
) -> dict[str, str]:
    result: dict[str, str] = {}
    used = set(reserved)
    for value in values:
        name = f"{prefix}_{_identifier(value)}"
        if name in used:
            raise ValueError(
                f"SystemVerilog identifier collision for {value!r}: {name}"
            )
        used.add(name)
        result[value] = name
    return result


def _enum(
    type_name: str,
    prefix: str,
    values: Iterable[str],
    *,
    invalid_name: str = "INVALID",
) -> tuple[str, dict[str, str]]:
    ordered = tuple(values)
    invalid = f"{prefix}_{invalid_name}"
    names = _identifiers(ordered, prefix, reserved=(invalid,))
    width = _width(len(ordered) + 1)
    items = [f"    {invalid} = {width}'d0"]
    items.extend(
        f"    // {value or '<empty>'}\n    {names[value]} = {width}'d{index}"
        for index, value in enumerate(ordered, 1)
    )
    return (
        f"typedef enum logic [{width - 1}:0] {{\n"
        + ",\n".join(items)
        + f"\n  }} {type_name};",
        names,
    )


def _ordered(values: Iterable[str], *, empty_first: bool = True) -> tuple[str, ...]:
    unique = set(values)
    if empty_first and "" in unique:
        return ("",) + tuple(sorted(unique - {""}))
    return tuple(sorted(unique))


def _hex(width: int, value: int) -> str:
    return f"{width}'h{value:0{(width + 3) // 4}x}"


def _casez(width: int, value: int, mask: int) -> str:
    limit = (1 << width) - 1
    if value & ~mask or (value | mask) & ~limit:
        raise ValueError(
            f"casez value/mask do not fit {width} bits: value={value:#x}, mask={mask:#x}"
        )
    bits = "".join(
        "1" if value & (1 << bit) else "0" if mask & (1 << bit) else "?"
        for bit in reversed(range(width))
    )
    grouped = "_".join(bits[index : index + 4] for index in range(0, len(bits), 4))
    return f"{width}'b{grouped}"


def _gather(signal: str, positions: tuple[int, ...]) -> str:
    if not positions:
        return "64'd0"
    bits = ", ".join(f"{signal}[{position}]" for position in positions)
    return f"{{{bits}}}"


def _ea_candidate_slot(form: ResolvedEncodingForm, operand: ResolvedOperand) -> int:
    source = operand
    if not isinstance(source, ResolvedEffectiveAddressOperand):
        raise ValueError(
            f"operand {_form_key(form)}/{operand.name} does not use an effective-address source"
        )
    if source.field.positions == EA_LOW_POSITIONS:
        return 0
    if form.encoding_class.name == "medium" and source.field.positions == EA_MEDIUM_ALT_POSITIONS:
        return 1
    if (
        form.encoding_class.name in ("long", "extralong", "xxlong")
        and source.field.positions == EA_HIGH_POSITIONS
    ):
        return 1
    raise ValueError(
        f"unsupported EA candidate position in {_form_key(form)}/{operand.name}: "
        f"{source.field.positions}"
    )


def _ea_layout(form: ResolvedEncodingForm) -> str:
    operands = tuple(read.operand for read in form.layout if isinstance(read, ResolvedEaRead))
    slots = tuple(_ea_candidate_slot(form, operand) for operand in operands)
    if not slots:
        return EA_LAYOUT_NONE
    if slots == (0,):
        return EA_LAYOUT_LOW
    if slots == (1,):
        return EA_LAYOUT_ALT
    if slots == (1, 0) and form.encoding_class.name in ("long", "extralong", "xxlong"):
        return EA_LAYOUT_ALT_THEN_LOW
    raise ValueError(f"unsupported EA candidate sequence in {_form_key(form)}: {slots}")


def _ea_candidate_widths(
    form: ResolvedEncodingForm,
    names: Names,
) -> tuple[str, str]:
    widths = ["EA_WIDTH_INVALID", "EA_WIDTH_INVALID"]
    for operand in tuple(read.operand for read in form.layout if isinstance(read, ResolvedEaRead)):
        widths[_ea_candidate_slot(form, operand)] = names.ea_width[_operand_ea_width(operand)]
    return widths[0], widths[1]


def _ea_candidate_profiles(
    form: ResolvedEncodingForm,
    names: Names,
) -> tuple[str, str]:
    profiles = ["EA_PROFILE_INVALID", "EA_PROFILE_INVALID"]
    for operand in tuple(read.operand for read in form.layout if isinstance(read, ResolvedEaRead)):
        source = operand
        assert isinstance(source, ResolvedEffectiveAddressOperand)
        profiles[_ea_candidate_slot(form, operand)] = names.ea_profile[_profile_name(source.owner, source.profile)]
    return profiles[0], profiles[1]




def _standalone_payload_cursor_expression(
    form: ResolvedEncodingForm,
    target: ResolvedPayloadRead,
) -> str:
    terms = ["{2'b0, d0_i.base_cursor}"]
    for operand in tuple(read.operand for read in form.layout if isinstance(read, ResolvedEaRead)):
        span = "low_span" if _ea_candidate_slot(form, operand) == 0 else "alt_span"
        terms.append(f"{span}.descriptor_bytes")
    for layout in form.layout:
        if layout is target:
            break
        if isinstance(layout, ResolvedEaRead):
            operand = next(
                item for item in form.operands if item.name == layout.operand.name
            )
            span = "low_span" if _ea_candidate_slot(form, operand) == 0 else "alt_span"
            terms.append(f"{span}.payload_bytes")
        else:
            terms.append(f"6'd{(layout.operand.definition.bytes * 8) // 8}")
    result = terms[0]
    for term in terms[1:]:
        result = f"advance_record_cursor({result}, {term})"
    return result


@dataclass(frozen=True)
class Names:
    form_ordinals: dict[int, int]
    owners: dict[int, str]
    required_cpuid_flags: dict[int, tuple[str, ...]]
    opcode_class: dict[str, str]
    form: dict[str, str]
    operation: dict[str, str]
    route: dict[str, str]
    instruction_set: dict[str, str]
    privilege: dict[str, str]
    predicate: dict[str, str]
    operand_type: dict[str, str]
    access: dict[str, str]
    ea_width: dict[str, str]
    ea_profile: dict[str, str]
    overlap_rule: dict[str, str]
    observed_kind: dict[str, str]
    ea_kind: dict[str, str]
    ea_segment: dict[str, str]
    ea_base: dict[str, str]
    ea_register: dict[str, str]
    update_mode: dict[str, str]
    update_difference: dict[str, str]
    ea_payload_role: dict[str, str]


@dataclass(frozen=True)
class PublicLayout:
    size_order: tuple[str, ...]
    cpuid_flag_order: tuple[str, ...]


def derive_public_layout(ir: DecodeIR) -> PublicLayout:
    """Derive mask orders from the current canonical decode model."""
    return PublicLayout(
        size_order=tuple(sorted({size for form in ir.forms for _, size in form.sizes})),
        cpuid_flag_order=tuple(_cpuid_key(item.field) for item in sorted(ir.cpuid_flags, key=lambda item: (item.leaf.class_value, item.leaf.leaf_value, item.query.indexes.first, item.field.lsb, _cpuid_key(item.field)))),
    )


def _mask_names(prefix: str, order: tuple[str, ...]) -> dict[str, str]:
    return _identifiers(order, f"BEDROCK_{prefix}_MASK")


def _render_mask_constants(
    dimension_name: str,
    prefix: str,
    order: tuple[str, ...],
) -> str:
    names = _mask_names(prefix, order)
    return "\n".join(
        f"  localparam logic [{dimension_name}-1:0] {names[value]} = "
        f"{_hex(len(order), 1 << index)}; // bit {index}: {value}"
        for index, value in enumerate(order)
    )


def _render_mask_assignment(
    target: str,
    values: Iterable[str],
    names: dict[str, str],
) -> list[str]:
    selected_values = set(values)
    selected = tuple(value for value in names if value in selected_values)
    if not selected:
        return [f"        {target} = '0;"]
    if len(selected) == 1:
        return [f"        {target} = {names[selected[0]]};"]
    return [
        f"        {target} =",
        *(
            f"          {names[value]}{' |' if index + 1 < len(selected) else ';'}"
            for index, value in enumerate(selected)
        ),
    ]


def _render_cpuid_assignment(
    form: ResolvedEncodingForm,
    public_layout: PublicLayout,
    lowering_names: Names,
) -> list[str]:
    names = _mask_names("CPUID_FLAG", public_layout.cpuid_flag_order)
    return _render_mask_assignment(
        "result_o.required_cpuid_flag_mask",
        lowering_names.required_cpuid_flags[id(form)],
        names,
    )


def _render_package(ir: DecodeIR) -> tuple[str, Names]:
    forms = ir.forms
    bundle_by_instruction = {id(bundle.instruction): bundle for bundle in ir.bundles}
    owners = {id(form): bundle_by_instruction[id(form.instruction)].owner for form in forms}
    required_flags = {id(form): tuple(_cpuid_key(field) for field in bundle_by_instruction[id(form.instruction)].required_cpuid_flags_for(form.form)) for form in forms}
    form_ordinals = {id(form): index for index, form in enumerate(forms)}
    ea_forms = tuple(form for profile in ir.effective_addresses.profiles for form in profile.compact_forms) + tuple(form for family in ir.effective_addresses.descriptor_families for form in family.forms)
    public_layout = derive_public_layout(ir)
    enums: list[str] = []

    def add(type_name: str, prefix: str, values: Iterable[str]):
        text, mapping = _enum(type_name, prefix, tuple(values))
        enums.append(text)
        return mapping

    opcode_classes = tuple(item.name for item in ENCODING_CLASSES)
    opcode_class = add("opcode_class_e", "OPCODE_CLASS", opcode_classes)
    operator_spaces = tuple(dict.fromkeys(space.name for space in OPERATOR_SPACES))
    operator_space_text, _ = _enum(
        "operator_space_e",
        "OPERATOR_SPACE",
        operator_spaces,
        invalid_name="NONE",
    )
    enums.append(operator_space_text)
    form = add("form_id_e", "FORM", (_form_key(item) for item in forms))
    operation = add("operation_e", "OP", tuple(sorted({form.instruction.mnemonic for form in ir.forms})))
    route = add("route_e", "ROUTE", _ordered(item.control.route for item in forms))
    instruction_set = add(
        "instruction_set_e",
        "INSTRUCTION_SET",
        _ordered(owners[id(item)] for item in forms),
    )
    privilege = add(
        "privilege_e", "PRIVILEGE", _ordered(('supervisor' if item.control.privileged else 'user') for item in forms)
    )
    predicate = add(
        "predicate_mode_e",
        "PREDICATE",
        _ordered(item.control.predicate_mode for item in forms),
    )
    operand_type = add(
        "operand_type_e",
        "OPERAND_TYPE",
        _ordered(_operand_type_name(x) for f in forms for x in f.operands),
    )
    access = add(
        "operand_access_e",
        "ACCESS",
        _ordered(x.access for f in forms for x in f.operands),
    )
    ea_width = add(
        "operand_ea_width_e",
        "EA_WIDTH",
        _ordered(_operand_ea_width(x) for f in forms for x in f.operands),
    )
    ea_profile = add(
        "ea_profile_e",
        "EA_PROFILE",
        (_profile_name(profile.definition.owner, profile.definition.profile) for profile in ir.effective_addresses.profiles),
    )
    overlap_rule = add(
        "overlap_rule_e", "OVERLAP", _ordered(x.overlap.type for f in forms for x in f.overlaps)
    )
    observed_kind = add(
        "repeat_observed_e",
        "REPEAT_OBSERVED",
        _ordered((f.control.repeat.observed_kind or '') for f in forms),
    )
    ea_kind = add("ea_kind_e", "EA_KIND", _ordered(_ea_kind(x) for x in ea_forms))
    ea_segment = add(
        "ea_segment_e", "EA_SEGMENT", _ordered(_ea_segment(x) for x in ea_forms)
    )
    ea_base = add("ea_base_e", "EA_BASE", _ordered(_ea_base(x) for x in ea_forms))
    ea_register = add(
        "ea_register_e", "EA_REGISTER", _ordered('' for x in ea_forms)
    )
    update_mode = add(
        "ea_update_mode_e", "EA_UPDATE_MODE", _ordered(value for x in ea_forms for value in ('', *(update.update_type for update in x.updates)))
    )
    update_difference = add(
        "ea_update_difference_e",
        "EA_UPDATE_DIFFERENCE",
        _ordered(value for x in ea_forms for value in ('', *(_update_difference(update.difference) for update in x.updates))),
    )
    ea_payload_role = add(
        "ea_payload_role_e", "EA_PAYLOAD_ROLE",
        tuple(sorted({payload.payload.role for form in ea_forms for payload in form.payloads})),
    )
    payload_slots = max(1, max(len(form.payloads) for form in ea_forms) * 2)
    payload_size_bits = _width(max(form.payload_width for form in ea_forms) * 2 + 1)
    names = Names(
        form_ordinals,
        owners,
        required_flags,
        opcode_class,
        form,
        operation,
        route,
        instruction_set,
        privilege,
        predicate,
        operand_type,
        access,
        ea_width,
        ea_profile,
        overlap_rule,
        observed_kind,
        ea_kind,
        ea_segment,
        ea_base,
        ea_register,
        update_mode,
        update_difference,
        ea_payload_role,
    )
    enums.insert(
        0,
        """typedef enum logic [1:0] {
    D0_INVALID_INPUT = 2'd0,
    D0_UNASSIGNED_OPCODE = 2'd1,
    D0_CONSTRAINT_REJECTED = 2'd2,
    D0_SUCCESS = 2'd3
  } d0_status_e;

  typedef enum logic [3:0] {
    D1_STAGE_D0_REJECTED = 4'd0,
    D1_STAGE_RECORD_BOUNDS = 4'd1,
    D1_STAGE_EA_DESCRIPTOR = 4'd2,
    D1_STAGE_EA_PAYLOAD = 4'd3,
    D1_STAGE_STANDALONE_PAYLOAD = 4'd4,
    D1_STAGE_STATIC_LEGALITY = 4'd5,
    D1_STAGE_RECORD_LENGTH = 4'd6,
    D1_STAGE_SUCCESS = 4'd7
  } decode_stage_e;

  typedef enum logic [1:0] {
    EA_LAYOUT_NONE = 2'd0,
    EA_LAYOUT_LOW = 2'd1,
    EA_LAYOUT_ALT = 2'd2,
    EA_LAYOUT_ALT_THEN_LOW = 2'd3
  } ea_layout_e;

""",
    )
    enum_text = "\n\n  ".join(enums)
    mask_constants = "\n".join(
        (
            _render_mask_constants(
                "BEDROCK_SIZE_MASK_BITS", "SIZE", public_layout.size_order
            ),
            _render_mask_constants(
                "BEDROCK_CPUID_FLAG_MASK_BITS",
                "CPUID_FLAG",
                public_layout.cpuid_flag_order,
            ),
        )
    )
    declarations = f"""  localparam logic [9:0] BEDROCK_OPCODE_BITS = 10'd{ir.limits.max_opcode_width};
  localparam logic [9:0] BEDROCK_RECORD_BYTES = 10'd{ir.limits.max_record_bytes};
  localparam integer BEDROCK_EA_DESCRIPTOR_BYTES = {max(1, ir.limits.max_descriptor_bytes)};
  localparam integer BEDROCK_EA_DESCRIPTOR_BITS = BEDROCK_EA_DESCRIPTOR_BYTES * 8;
  localparam logic [{_width(ir.limits.form_count + 1)-1}:0] BEDROCK_FORM_COUNT = {_width(ir.limits.form_count + 1)}'d{ir.limits.form_count};
  localparam integer BEDROCK_FORM_HIGH_COUNT = {ir.limits.form_count // 32 + 1};
  localparam logic [9:0] BEDROCK_OPERAND_SLOTS = 10'd{ir.limits.max_operands};
  localparam logic [9:0] BEDROCK_EA_SLOTS = 10'd{ir.limits.max_ea_operands};
  localparam logic [9:0] BEDROCK_OVERLAP_SLOTS = 10'd{ir.limits.max_overlaps};
  localparam logic [9:0] BEDROCK_SIZE_MASK_BITS = 10'd{len(public_layout.size_order)};
  localparam logic [9:0] BEDROCK_CPUID_FLAG_MASK_BITS = 10'd{len(public_layout.cpuid_flag_order)};
  localparam logic [0:0] BEDROCK_EA_LOW_SLOT = 1'd0;
  localparam logic [0:0] BEDROCK_EA_ALT_SLOT = 1'd1;

{mask_constants}

  {enum_text}

  typedef struct packed {{
    d0_status_e status;
    opcode_class_e opcode_class;
    operator_space_e operator_space;
    form_id_e form;
    logic [BEDROCK_FORM_HIGH_COUNT-1:0] form_high_decode;
    logic [31:0] form_low_decode;
    logic [BEDROCK_OPCODE_BITS-1:0] opcode;
    logic [6:0] alt_raw;
    logic [3:0] base_cursor;
    ea_layout_e ea_layout;
    operand_ea_width_e [BEDROCK_EA_SLOTS-1:0] ea_widths;
    ea_profile_e [BEDROCK_EA_SLOTS-1:0] ea_profiles;
  }} d0_result_t;

  typedef struct packed {{
    d0_status_e status;
    ea_layout_e ea_layout;
    operand_ea_width_e [BEDROCK_EA_SLOTS-1:0] ea_widths;
    ea_profile_e [BEDROCK_EA_SLOTS-1:0] ea_profiles;
    logic [6:0] low_raw;
    logic [6:0] alt_raw;
    logic [3:0] base_cursor;
    logic [3:0] post_alt_cursor;
  }} d0_ea_result_t;

  typedef struct packed {{
    logic valid;
    operand_type_e type_name;
    operand_access_e access;
    operand_ea_width_e ea_width;
    logic [63:0] value;
    logic payload_signed;
    logic ea_valid;
    logic [0:0] ea_slot;
  }} decoded_operand_t;

  localparam integer BEDROCK_EA_PAYLOAD_SLOTS = {payload_slots};
  typedef struct packed {{
    ea_payload_role_e role;
    logic [{payload_size_bits - 1}:0] width_bits;
    logic is_signed;
    logic [{payload_size_bits - 1}:0] bit_offset;
  }} ea_payload_binding_t;

  typedef struct packed {{
    ea_update_mode_e mode;
    ea_update_difference_e difference;
  }} ea_autoupdate_t;

  typedef struct packed {{
    logic valid;
    ea_profile_e profile;
    ea_kind_e kind;
    ea_segment_e segment;
    ea_base_e base;
    ea_register_e register_name;
    operand_ea_width_e operand_width;
    ea_autoupdate_t base_update;
    ea_autoupdate_t index_update;
    logic [{payload_size_bits - 1}:0] payload_width;
    logic [{_width(payload_slots + 1) - 1}:0] payload_count;
    ea_payload_binding_t [BEDROCK_EA_PAYLOAD_SLOTS-1:0] payloads;
    logic direct_register_valid;
    logic [3:0] direct_register;
    logic base_register_valid;
    logic [3:0] base_register;
    logic index_register_valid;
    logic [3:0] index_register;
    logic stride_register_valid;
    logic [3:0] stride_register;
    logic segment_register_valid;
    logic [3:0] segment_register;
    logic [BEDROCK_RECORD_BYTES*8-1:0] payload;
  }} decoded_ea_t;

  typedef struct packed {{
    logic valid;
    overlap_rule_e rule;
    logic [{_width(ir.limits.max_operands) - 1}:0] left_operand;
    logic [{_width(ir.limits.max_operands) - 1}:0] right_operand;
  }} overlap_descriptor_t;

  typedef struct packed {{
    route_e route;
    instruction_set_e instruction_set;
    privilege_e privilege;
    predicate_mode_e predicate_mode;
    repeat_observed_e repeat_observed;
    logic repeat_rep;
    logic repeat_repcc;
    logic [1:0] repeat_observed_operand;
    logic has_ea_operand;
  }} control_metadata_t;

  typedef struct packed {{
    logic valid;
    logic [5:0] encoded_bytes;
    logic [5:0] descriptor_bytes;
    logic [5:0] payload_bytes;
  }} ea_span_result_t;

  typedef struct packed {{
    logic valid;
    decode_stage_e stage;
    form_id_e form;
    operation_e operation;
    control_metadata_t control;
    logic [BEDROCK_SIZE_MASK_BITS-1:0] size_mask;
    logic [BEDROCK_CPUID_FLAG_MASK_BITS-1:0] required_cpuid_flag_mask;
    logic [2:0] operand_count;
    decoded_operand_t [BEDROCK_OPERAND_SLOTS-1:0] operands;
    logic [1:0] overlap_count;
    overlap_descriptor_t [BEDROCK_OVERLAP_SLOTS-1:0] overlaps;
    logic [5:0] required_bytes;
    logic [4:0] encoded_bytes;
  }} d1_opcode_result_t;

  typedef struct packed {{
    logic valid;
    decode_stage_e stage;
    logic [1:0] ea_count;
    decoded_ea_t [BEDROCK_EA_SLOTS-1:0] eas;
    logic [5:0] required_bytes;
  }} ea_decode_result_t;

  typedef struct packed {{
    logic ok;
    decode_stage_e stage;
    logic [5:0] next_cursor;
    decoded_ea_t ea;
  }} ea_parse_result_t;

  function automatic logic [5:0] advance_record_cursor(
    input logic [5:0] cursor, input logic [5:0] count
  );
    advance_record_cursor = 6'((int'(cursor) + int'(count) > BEDROCK_RECORD_BYTES)
      ? BEDROCK_RECORD_BYTES + 1 : int'(cursor) + int'(count));
  endfunction

  function automatic logic [BEDROCK_EA_DESCRIPTOR_BITS-1:0] ea_descriptor_bits(
    input logic [BEDROCK_RECORD_BYTES*8-1:0] record, input logic [5:0] cursor
  );
    begin
      ea_descriptor_bits = '0;
      for (integer index = 0; index < BEDROCK_EA_DESCRIPTOR_BYTES; index = index + 1)
        if (int'(cursor) + index < BEDROCK_RECORD_BYTES)
          ea_descriptor_bits[BEDROCK_EA_DESCRIPTOR_BITS - index*8 - 1 -: 8] =
            record[(int'(cursor) + index)*8 +: 8];
    end
  endfunction
"""
    return render_template("bedrock_decode_pkg.sv.in", {"PACKAGE_DECLARATIONS": declarations}), names






def _balanced_tree(
    leaves: list[str],
    *,
    prefix: str,
    type_name: str,
    expression: str,
) -> tuple[list[str], list[str], str, int]:
    """Render a balanced binary tree and return declarations, assignments, root, depth."""
    if not leaves:
        raise ValueError(f"empty balanced tree {prefix}")
    declarations: list[str] = []
    assignments: list[str] = []
    node_index = 0

    def build(items: list[str]) -> tuple[str, int]:
        nonlocal node_index
        if len(items) == 1:
            return items[0], 0
        midpoint = len(items) // 2
        left, left_depth = build(items[:midpoint])
        right, right_depth = build(items[midpoint:])
        node = f"{prefix}_node_{node_index:03d}"
        node_index += 1
        declarations.append(f"  {type_name} {node};")
        assignments.append(
            f"  assign {node} = " + expression.format(left=left, right=right) + ";"
        )
        return node, max(left_depth, right_depth) + 1

    root, depth = build(leaves)
    return declarations, assignments, root, depth


def _render_opcode_class_bytes_function(ir: DecodeIR) -> str:
    class_bytes = {item.name: item.opcode_space_bytes for item in ENCODING_CLASSES}
    byte_cases = "\n".join(
        f"        OPCODE_CLASS_{_identifier(opcode_class)}: "
        f"opcode_class_bytes = 6'd{class_bytes[opcode_class]};"
        for opcode_class in sorted(class_bytes)
    )
    return f"""  function automatic logic [5:0] opcode_class_bytes(
    input opcode_class_e opcode_class
  );
    begin
      opcode_class_bytes = '0;
      unique case (opcode_class)
{byte_cases}
        default: begin end
      endcase
    end
  endfunction"""


def _render_operator_space_function(names: Names) -> str:
    cases: list[str] = []
    spaces_by_class: dict[str, list[OperatorSpace]] = {}
    for space in OPERATOR_SPACES:
        spaces_by_class.setdefault(space.encoding_class, []).append(space)
    for encoding_class, spaces in sorted(spaces_by_class.items()):
        pattern_bits = ENCODING_CLASSES_BY_NAME[encoding_class].pattern_bits
        prefix_high = pattern_bits - 1
        rows = "\n".join(
            f"            {OPERATOR_SPACE_PREFIX_BITS}'b{space.prefix}: "
            f"operator_space_from_opcode = "
            f"OPERATOR_SPACE_{_identifier(space.name)};"
            for space in spaces
        )
        cases.append(
            "\n".join(
                [
                    f"        {names.opcode_class[encoding_class]}: begin",
                    f"          unique casez (opcode[{prefix_high} -: "
                    f"{OPERATOR_SPACE_PREFIX_BITS}])",
                    rows,
                    "            default: begin end",
                    "          endcase",
                    "        end",
                ]
            )
        )
    return f"""  function automatic operator_space_e operator_space_from_opcode(
    input opcode_class_e opcode_class,
    input logic [BEDROCK_OPCODE_BITS-1:0] opcode
  );
    begin
      operator_space_from_opcode = OPERATOR_SPACE_NONE;
      unique case (opcode_class)
{chr(10).join(cases)}
        default: begin end
      endcase
    end
  endfunction"""


def _render_d0(ir: DecodeIR, names: Names) -> str:
    for index, left in enumerate(ir.forms):
        for right in ir.forms[index + 1:]:
            if forms_overlap(left, right):
                raise ValueError(f"accepted decoder forms overlap: {_form_key(left)}, {_form_key(right)}")
    form_bits_type = f"logic [{_width(len(ir.forms) + 1) - 1}:0]"
    by_class: dict[str, list[ResolvedEncodingForm]] = {}
    for form in ir.forms:
        by_class.setdefault(form.encoding_class.name, []).append(form)
    form_declarations: list[str] = []
    form_assignments: list[str] = []
    tree_declarations: list[str] = []
    tree_assignments: list[str] = []
    class_cases: list[str] = []
    for opcode_class in sorted(by_class):
        class_forms = by_class[opcode_class]
        class_name = _identifier(opcode_class).lower()
        raw_leaves: list[str] = []
        form_leaves: list[str] = []
        selection_leaves: list[str] = []
        for form in class_forms:
            raw_signal = f"form_{names.form_ordinals[id(form)]:03d}_raw_match"
            accepted_signal = f"form_{names.form_ordinals[id(form)]:03d}_accepted_match"
            form_signal = f"form_{names.form_ordinals[id(form)]:03d}_onehot_form"
            selection_signal = f"form_{names.form_ordinals[id(form)]:03d}_selection"
            raw_leaves.append(raw_signal)
            form_leaves.append(form_signal)
            selection_leaves.append(selection_signal)
            raw = (
                f"((opcode_i & {_hex(ir.limits.max_opcode_width, form.form.pattern.fixed_mask)}) == "
                f"{_hex(ir.limits.max_opcode_width, form.form.pattern.fixed_value)})"
            )
            low_width, alt_width = _ea_candidate_widths(form, names)
            low_profile, alt_profile = _ea_candidate_profiles(form, names)
            accepted_cubes = form_cubes(form)
            accepted = " || ".join(f"((opcode_i & {_hex(ir.limits.max_opcode_width, cube.mask)}) == {_hex(ir.limits.max_opcode_width, cube.value)})" for cube in accepted_cubes) or "1'b0"
            form_declarations.extend(
                [
                    f"  // form {names.form_ordinals[id(form)]}: {_form_key(form)}",
                    f"  logic {raw_signal};",
                    f"  logic {accepted_signal};",
                    f"  d0_selection_t {selection_signal};",
                    f"  {form_bits_type} {form_signal};",
                ]
            )
            form_assignments.append(f"  assign {raw_signal} = {raw};")
            form_assignments.append(f"  assign {accepted_signal} = {accepted};")
            form_assignments.extend(
                [
                    f"  assign {selection_signal}.valid = {accepted_signal};",
                    f"  assign {selection_signal}.form = FORM_INVALID;",
                    f"  assign {form_signal} = {accepted_signal} ? "
                    f"{names.form[_form_key(form)]} : FORM_INVALID;",
                    f"  assign {selection_signal}.ea_layout = {_ea_layout(form)};",
                    f"  assign {selection_signal}.ea_widths[BEDROCK_EA_LOW_SLOT] = {low_width};",
                    f"  assign {selection_signal}.ea_widths[BEDROCK_EA_ALT_SLOT] = {alt_width};",
                    f"  assign {selection_signal}.ea_profiles[BEDROCK_EA_LOW_SLOT] = {low_profile};",
                    f"  assign {selection_signal}.ea_profiles[BEDROCK_EA_ALT_SLOT] = {alt_profile};",
                ]
            )
        raw_declarations, raw_assignments, raw_root, raw_depth = _balanced_tree(
            raw_leaves,
            prefix=f"{class_name}_raw",
            type_name="logic",
            expression="({left} | {right})",
        )
        form_tree_declarations, form_tree_assignments, form_root, form_depth = (
            _balanced_tree(
                form_leaves,
                prefix=f"{class_name}_form",
                type_name=form_bits_type,
                expression="({left} | {right})",
            )
        )
        (
            selection_declarations,
            selection_assignments,
            selection_root,
            selection_depth,
        ) = _balanced_tree(
            selection_leaves,
            prefix=f"{class_name}_selection",
            type_name="d0_selection_t",
            expression="{left}.valid ? {left} : {right}",
        )
        if len(selection_declarations) != len(form_tree_declarations):
            raise ValueError(f"mismatched D0 tree nodes for {opcode_class}")
        # Keep corresponding tree lanes adjacent through elaboration. The
        # target Yosys/ABC flow maps this ordering materially better.
        selection_form_declarations = [
            declaration
            for pair in zip(selection_declarations, form_tree_declarations)
            for declaration in pair
        ]
        selection_form_assignments = [
            assignment
            for pair in zip(selection_assignments, form_tree_assignments)
            for assignment in pair
        ]
        tree_declarations.extend(
            [
                f"  // {opcode_class}: {len(class_forms)} parallel form leaves, "
                f"{selection_depth} balanced form-OR/EA-priority levels",
                *raw_declarations,
                *selection_form_declarations,
            ]
        )
        tree_assignments.extend([*raw_assignments, *selection_form_assignments])
        if raw_depth != form_depth or form_depth != selection_depth:
            raise ValueError(f"mismatched D0 tree depths for {opcode_class}")
        class_cases.append(
            "\n".join(
                [
                    f"      {names.opcode_class[opcode_class]}: begin",
                    f"        class_raw_match = {raw_root};",
                    f"        class_selection = {selection_root};",
                    f"        class_form = {form_root};",
                    "      end",
                ]
            )
        )
    return render_template("bedrock_decode_d0.sv.in",
        {
            "FORM_BITS_TYPE": form_bits_type,
            "FORM_DECLARATIONS": "\n".join(form_declarations),
            "TREE_DECLARATIONS": "\n".join(tree_declarations),
            "EA_SPAN_FUNCTION": _render_ea_span_function(ir, names),
            "OPCODE_CLASS_BYTES_FUNCTION": _render_opcode_class_bytes_function(ir),
            "OPERATOR_SPACE_FUNCTION": _render_operator_space_function(names),
            "FORM_ASSIGNMENTS": "\n".join(form_assignments),
            "TREE_ASSIGNMENTS": "\n".join(tree_assignments),
            "CLASS_CASES": "\n".join(class_cases),
        }
    )


def _ea_static_assignments(
    target: str,
    form: ResolvedEAEncoding,
    names: Names,
    *,
    raw_signal: str,
) -> list[str]:
    lines = [
        f"{target}.kind = {names.ea_kind[_ea_kind(form)]};",
        f"{target}.segment = {names.ea_segment[_ea_segment(form)]};",
        f"{target}.base = {names.ea_base[_ea_base(form)]};",
        f"{target}.register_name = {names.ea_register['']};",
    ]
    for role in ("base", "index"):
        update = next((update for update in form.updates if update.target == role), None)
        lines.extend((
            f"{target}.{role}_update.mode = {names.update_mode[update.update_type if update is not None else '']};",
            f"{target}.{role}_update.difference = {names.update_difference[_update_difference(update.difference) if update is not None else '']};",
        ))
    lines.extend((
        f"{target}.payload_width = {form.payload_width};",
        f"{target}.payload_count = {len(form.payloads)};",
    ))
    offset = 0
    for index, payload in enumerate(form.payloads):
        lines.extend((
            f"{target}.payloads[{index}].role = {names.ea_payload_role[payload.payload.role]};",
            f"{target}.payloads[{index}].width_bits = {(payload.definition.bytes * 8)};",
            f"{target}.payloads[{index}].is_signed = 1'b{int(payload.signed)};",
            f"{target}.payloads[{index}].bit_offset = {offset};",
        ))
        offset += (payload.definition.bytes * 8)
    for field in form.fields:
        value = _gather(raw_signal, field.positions)
        if field.field.role == "value":
            lines.extend(
                [
                    f"{target}.direct_register_valid = 1'b1;",
                    f"{target}.direct_register = 4'({value});",
                ]
            )
        elif field.field.role == "base":
            lines.extend(
                [
                    f"{target}.base_register_valid = 1'b1;",
                    f"{target}.base_register = 4'({value});",
                ]
            )
        elif field.field.role == "index":
            lines.extend(
                [
                    f"{target}.index_register_valid = 1'b1;",
                    f"{target}.index_register = 4'({value});",
                ]
            )
        elif field.field.role == "stride":
            lines.extend(
                [
                    f"{target}.stride_register_valid = 1'b1;",
                    f"{target}.stride_register = 4'({value});",
                ]
            )
        elif field.field.role == "segment":
            lines.extend(
                [
                    f"{target}.segment_register_valid = 1'b1;",
                    f"{target}.segment_register = 4'({value});",
                ]
            )
    return lines






def _render_ea_span_function(ir: DecodeIR, names: Names) -> str:
    profile_cases: list[str] = []
    for profile in ir.effective_addresses.profiles:
        raw_cases = []
        for entry in profile.compact_entries:
            lines = [
                f"          7'h{entry.raw:02x}: begin // "
                f"{_ea_form_name(entry.form) if entry.form is not None else 'unassigned compact selector'}"
            ]
            if entry.form is not None:
                compact = entry.form
                encoded_bytes = (compact.mode.extension.bytes if isinstance(compact.mode, CompactExtensionEAMode) else 0) + compact.payload_width // 8
                lines.extend(
                    [
                        "            encoded_ea_span.valid = 1'b1;",
                        f"            encoded_ea_span.encoded_bytes = 6'd{min(ir.limits.max_record_bytes + 1, encoded_bytes)};",
                        f"            encoded_ea_span.descriptor_bytes = 6'd{min(ir.limits.max_record_bytes + 1, (compact.mode.extension.bytes if isinstance(compact.mode, CompactExtensionEAMode) else 0))};",
                        f"            encoded_ea_span.payload_bytes = 6'd{min(ir.limits.max_record_bytes + 1, compact.payload_width // 8)};",
                    ]
                )
            lines.append("          end")
            raw_cases.append("\n".join(lines))
        profile_cases.append(
            f"      {names.ea_profile[_profile_name(profile.definition.owner, profile.definition.profile)]}: begin\n"
            "        unique case (compact_raw)\n"
            + "\n".join(raw_cases)
            + "\n          default: begin end\n"
            "        endcase\n"
            "      end"
        )
    return f"""  function automatic ea_span_result_t encoded_ea_span(
    input ea_profile_e profile,
    input logic [6:0] compact_raw
  );
    begin
      encoded_ea_span = '0;
      unique case (profile)
{chr(10).join(profile_cases)}
        default: begin end
      endcase
    end
  endfunction"""


def _render_ea_function(ir: DecodeIR, names: Names) -> str:
    families = ir.effective_addresses.descriptor_families
    family_names = {
        _family_name(family): f"EA_DESCRIPTOR_FAMILY_{_identifier(_family_name(family))}"
        for family in families
    }
    family_width = _width(len(families) + 1)
    family_enum = [
        f"    EA_DESCRIPTOR_FAMILY_NONE = {family_width}'d0",
        *(
            f"    {family_names[_family_name(family)]} = {family_width}'d{index}"
            for index, family in enumerate(families, 1)
        ),
    ]

    def compact_assignments(compact: ResolvedEAEncoding) -> tuple[str, ...]:
        descriptor_family = (
            family_names[_descriptor_name(compact)]
            if _descriptor_name(compact)
            else "EA_DESCRIPTOR_FAMILY_NONE"
        )
        return (
            "decode_compact_ea.valid = 1'b1;",
            f"decode_compact_ea.descriptor_family = {descriptor_family};",
            "decode_compact_ea.ea.valid = 1'b1;",
            *_ea_static_assignments(
                "decode_compact_ea.ea",
                compact,
                names,
                raw_signal="compact_raw",
            ),
        )

    def compact_case(
        label: str, compact: ResolvedEAEncoding, comment: str, *, indent: int
    ) -> str:
        prefix = " " * indent
        lines = [f"{prefix}{label}: begin // {comment}"]
        lines.extend(
            f"{prefix}  {assignment}" for assignment in compact_assignments(compact)
        )
        lines.append(f"{prefix}end")
        return "\n".join(lines)

    profile_forms: dict[str, dict[int, ResolvedEAEncoding]] = {}
    for profile in ir.effective_addresses.profiles:
        profile_forms[_profile_name(profile.definition.owner, profile.definition.profile)] = {
            entry.raw: entry.form
            for entry in profile.compact_entries
            if entry.form is not None
        }

    profiles = ir.effective_addresses.profiles
    common_raws = {
        raw
        for raw in range(1 << ir.effective_addresses.compact_width)
        if all(raw in profile_forms[_profile_name(profile.definition.owner, profile.definition.profile)] for profile in profiles)
        and len(
            {
                compact_assignments(profile_forms[_profile_name(profile.definition.owner, profile.definition.profile)][raw])
                for profile in profiles
            }
        )
        == 1
    }
    common_cases: list[str] = []
    covered_common_raws: set[int] = set()
    for raw in sorted(common_raws):
        if raw in covered_common_raws:
            continue
        compact = profile_forms[_profile_name(profiles[0].definition.owner, profiles[0].definition.profile)][raw]
        pattern_raws = {
            candidate
            for candidate in range(1 << ir.effective_addresses.compact_width)
            if (candidate & compact.pattern.fixed_mask) == compact.pattern.fixed_value
        }
        signature = compact_assignments(compact)
        if not pattern_raws.issubset(common_raws) or any(
            compact_assignments(profile_forms[_profile_name(profiles[0].definition.owner, profiles[0].definition.profile)][candidate]) != signature
            for candidate in pattern_raws
        ):
            pattern_raws = {raw}
            label = _hex(ir.effective_addresses.compact_width, raw)
        else:
            label = _casez(
                ir.effective_addresses.compact_width,
                compact.pattern.fixed_value,
                compact.pattern.fixed_mask,
            )
        covered_common_raws.update(pattern_raws)
        common_cases.append(compact_case(label, compact, _ea_form_name(compact), indent=6))

    profile_specific_cases: list[str] = []
    for profile in profiles:
        cases = [
            compact_case(
                _hex(ir.effective_addresses.compact_width, raw),
                compact,
                _ea_form_name(compact),
                indent=10,
            )
            for raw, compact in sorted(profile_forms[_profile_name(profile.definition.owner, profile.definition.profile)].items())
            if raw not in common_raws
        ]
        if not cases:
            continue
        profile_specific_cases.append(
            f"        {names.ea_profile[_profile_name(profile.definition.owner, profile.definition.profile)]}: begin\n"
            "          unique case (compact_raw)\n"
            + "\n".join(cases)
            + "\n            default: begin end\n"
            "          endcase\n"
            "        end"
        )
    valid_profiles = ", ".join(names.ea_profile[_profile_name(profile.definition.owner, profile.definition.profile)] for profile in profiles)

    descriptor_functions: list[str] = []
    for family in families:
        descriptor_width = family.descriptor_bytes * 8
        function_name = f"decode_{_identifier(_family_name(family)).lower()}_descriptor"
        lines = [
            f"  function automatic descriptor_decode_t {function_name}(",
            f"    input logic [{descriptor_width - 1}:0] descriptor",
            "  );",
            "    begin",
            f"      {function_name} = '0;",
            "      unique casez (descriptor)",
        ]
        for descriptor_form in family.forms:
            lines.extend(
                [
                    f"      {_casez(descriptor_width, descriptor_form.pattern.fixed_value, descriptor_form.pattern.fixed_mask)}: begin // {_ea_form_name(descriptor_form)}",
                    f"        {function_name}.valid = 1'b1;",
                ]
            )
            lines.extend(
                "        " + assignment
                for assignment in _ea_static_assignments(
                    f"{function_name}.ea",
                    descriptor_form,
                    names,
                    raw_signal="descriptor",
                )
            )
            lines.append("      end")
        lines.extend(
            [
                "        default: begin end",
                "      endcase",
                "    end",
                "  endfunction",
            ]
        )
        descriptor_functions.append("\n".join(lines))

    descriptor_selection_cases: list[str] = []
    descriptor_parse_cases: list[str] = []
    for family in families:
        descriptor_bytes = family.descriptor_bytes
        descriptor_raw = f"descriptor_raw[BEDROCK_EA_DESCRIPTOR_BITS-1 -: {descriptor_bytes * 8}]"
        function_name = f"decode_{_identifier(_family_name(family)).lower()}_descriptor"
        descriptor_selection_cases.append(
            f"        {family_names[_family_name(family)]}: "
            f"descriptor_decode = {function_name}({descriptor_raw});"
        )
        descriptor_parse_cases.append(
            f"""      {family_names[_family_name(family)]}: begin
        if ((cursor_in + {descriptor_bytes}) > byte_count ||
            (cursor_in + {descriptor_bytes}) > BEDROCK_RECORD_BYTES) begin
          resolve_ea_descriptor.next_cursor = advance_record_cursor(cursor_in, 6'd{min(ir.limits.max_record_bytes + 1, descriptor_bytes)});
        end else if (descriptor_decode.valid) begin
          resolve_ea_descriptor.ok = 1'b1;
          resolve_ea_descriptor.stage = D1_STAGE_SUCCESS;
          resolve_ea_descriptor.next_cursor = advance_record_cursor(cursor_in, 6'd{min(ir.limits.max_record_bytes + 1, descriptor_bytes)});
          resolve_ea_descriptor.ea = merge_descriptor_ea(
            compact_decode.ea,
            descriptor_decode.ea
          );
        end
      end"""
        )
    descriptor_byte_count_cases = [
        f"      {family_names[_family_name(family)]}: "
        f"ea_descriptor_byte_count = 6'd{min(ir.limits.max_record_bytes + 1, family.descriptor_bytes)};"
        for family in families
    ]
    family_enum_text = ",\n".join(family_enum)

    return f"""  typedef enum logic [{family_width - 1}:0] {{
{family_enum_text}
  }} ea_descriptor_family_e;

  typedef struct packed {{
    logic valid;
    ea_descriptor_family_e descriptor_family;
    decoded_ea_t ea;
  }} compact_ea_decode_t;

  typedef struct packed {{
    logic valid;
    decoded_ea_t ea;
  }} descriptor_decode_t;

  function automatic compact_ea_decode_t decode_compact_ea(
    input ea_profile_e profile,
    input logic [6:0] compact_raw
  );
    begin
      decode_compact_ea = '0;
      unique case (profile)
      {valid_profiles}: begin
        unique casez (compact_raw)
{chr(10).join(common_cases)}
        default: begin
          unique case (profile)
{chr(10).join(profile_specific_cases)}
            default: begin end
          endcase
        end
        endcase
      end
        default: begin end
      endcase
    end
  endfunction

{chr(10).join(descriptor_functions)}

  function automatic decoded_ea_t merge_descriptor_ea(
    input decoded_ea_t compact_ea,
    input decoded_ea_t descriptor_ea
  );
    begin
      merge_descriptor_ea = compact_ea;
      merge_descriptor_ea.payload_width = compact_ea.payload_width + descriptor_ea.payload_width;
      merge_descriptor_ea.payload_count = compact_ea.payload_count + descriptor_ea.payload_count;
      for (integer index = 0; index < BEDROCK_EA_PAYLOAD_SLOTS; index = index + 1)
        if (index < int'(descriptor_ea.payload_count)) begin
          merge_descriptor_ea.payloads[int'(compact_ea.payload_count) + index] = descriptor_ea.payloads[index];
          merge_descriptor_ea.payloads[int'(compact_ea.payload_count) + index].bit_offset =
            compact_ea.payload_width + descriptor_ea.payloads[index].bit_offset;
        end
      merge_descriptor_ea.kind = descriptor_ea.kind;
      merge_descriptor_ea.segment = descriptor_ea.segment;
      merge_descriptor_ea.base = descriptor_ea.base;
      merge_descriptor_ea.register_name = descriptor_ea.register_name;
      if (descriptor_ea.base_update.mode != {names.update_mode['']})
        merge_descriptor_ea.base_update = descriptor_ea.base_update;
      if (descriptor_ea.index_update.mode != {names.update_mode['']})
        merge_descriptor_ea.index_update = descriptor_ea.index_update;
      if (descriptor_ea.direct_register_valid) begin
        merge_descriptor_ea.direct_register_valid = 1'b1;
        merge_descriptor_ea.direct_register = descriptor_ea.direct_register;
      end
      if (descriptor_ea.base_register_valid) begin
        merge_descriptor_ea.base_register_valid = 1'b1;
        merge_descriptor_ea.base_register = descriptor_ea.base_register;
      end
      if (descriptor_ea.index_register_valid) begin
        merge_descriptor_ea.index_register_valid = 1'b1;
        merge_descriptor_ea.index_register = descriptor_ea.index_register;
      end
      if (descriptor_ea.stride_register_valid) begin
        merge_descriptor_ea.stride_register_valid = 1'b1;
        merge_descriptor_ea.stride_register = descriptor_ea.stride_register;
      end
      if (descriptor_ea.segment_register_valid) begin
        merge_descriptor_ea.segment_register_valid = 1'b1;
        merge_descriptor_ea.segment_register = descriptor_ea.segment_register;
      end
    end
  endfunction

  function automatic ea_parse_result_t parse_ea_payload(
    input decoded_ea_t ea_in,
    input logic [BEDROCK_RECORD_BYTES*8-1:0] record,
    input logic [4:0] byte_count,
    input logic [5:0] cursor
  );
    begin
      parse_ea_payload = '0;
      parse_ea_payload.stage = D1_STAGE_EA_PAYLOAD;
      parse_ea_payload.next_cursor = cursor;
      parse_ea_payload.ea = ea_in;
      parse_ea_payload.ea.payload = '0;
      if ((int'(cursor) + int'(ea_in.payload_width) / 8) <= int'(byte_count) &&
          (int'(cursor) + int'(ea_in.payload_width) / 8) <= BEDROCK_RECORD_BYTES) begin
        for (integer byte_index = 0; byte_index < BEDROCK_RECORD_BYTES; byte_index = byte_index + 1)
          if (byte_index < int'(ea_in.payload_width) / 8)
            parse_ea_payload.ea.payload[byte_index*8 +: 8] = record[(int'(cursor) + byte_index)*8 +: 8];
        parse_ea_payload.ok = 1'b1;
        parse_ea_payload.stage = D1_STAGE_SUCCESS;
      end
      parse_ea_payload.next_cursor = 6'(int'(cursor) + int'(ea_in.payload_width) / 8);
    end
  endfunction

  function automatic logic [5:0] ea_payload_byte_count(
    input decoded_ea_t ea_in
  );
    begin
      ea_payload_byte_count = 6'(int'(ea_in.payload_width) / 8);
    end
  endfunction

  function automatic logic [5:0] ea_descriptor_byte_count(
    input compact_ea_decode_t compact_decode
  );
    begin
      ea_descriptor_byte_count = 6'd0;
      unique case (compact_decode.descriptor_family)
{chr(10).join(descriptor_byte_count_cases)}
        default: begin end
      endcase
    end
  endfunction

  function automatic ea_parse_result_t resolve_ea_descriptor(
    input ea_profile_e profile,
    input operand_ea_width_e operand_width,
    input compact_ea_decode_t compact_decode,
    input logic [BEDROCK_RECORD_BYTES*8-1:0] record,
    input logic [4:0] byte_count,
    input logic [5:0] cursor_in
  );
    descriptor_decode_t descriptor_decode;
    logic [BEDROCK_EA_DESCRIPTOR_BITS-1:0] descriptor_raw;
    begin
      resolve_ea_descriptor = '0;
      resolve_ea_descriptor.stage = D1_STAGE_EA_DESCRIPTOR;
      resolve_ea_descriptor.next_cursor = cursor_in;
      resolve_ea_descriptor.ea = compact_decode.ea;
      descriptor_decode = '0;
      descriptor_raw = ea_descriptor_bits(record, cursor_in);
      unique case (compact_decode.descriptor_family)
{chr(10).join(descriptor_selection_cases)}
        default: begin end
      endcase
      if (compact_decode.valid) unique case (compact_decode.descriptor_family)
      EA_DESCRIPTOR_FAMILY_NONE: begin
        resolve_ea_descriptor.ok = 1'b1;
        resolve_ea_descriptor.stage = D1_STAGE_SUCCESS;
      end
{chr(10).join(descriptor_parse_cases)}
        default: begin end
      endcase
      resolve_ea_descriptor.ea.profile = profile;
      resolve_ea_descriptor.ea.operand_width = operand_width;
    end
  endfunction

  function automatic ea_parse_result_t combine_ea_parse(
    input ea_parse_result_t descriptor_parse,
    input ea_parse_result_t payload_parse
  );
    begin
      combine_ea_parse = descriptor_parse;
      if (descriptor_parse.ok) begin
        combine_ea_parse.ok = payload_parse.ok;
        combine_ea_parse.stage = payload_parse.stage;
        combine_ea_parse.next_cursor = payload_parse.next_cursor;
        combine_ea_parse.ea.payload = payload_parse.ea.payload;
      end
    end
  endfunction"""


def _render_form_case(
    form: ResolvedEncodingForm,
    ir: DecodeIR,
    names: Names,
    public_layout: PublicLayout,
    case_label: str | None = None,
) -> str:
    payload_layouts = tuple(
        layout for layout in form.layout
        if isinstance(layout, ResolvedPayloadRead)
    )
    size_names = _mask_names("SIZE", public_layout.size_order)
    lines = [
        f"          {case_label or names.form[_form_key(form)]}: begin // {names.form_ordinals[id(form)]}: {_form_key(form)}",
        f"        decoded_result.operation = {names.operation[form.instruction.mnemonic]};",
        f"        decoded_result.control.route = {names.route[form.control.route]};",
        f"        decoded_result.control.instruction_set = {names.instruction_set[names.owners[id(form)]]};",
        f"        decoded_result.control.privilege = {names.privilege[('supervisor' if form.control.privileged else 'user')]};",
        f"        decoded_result.control.predicate_mode = {names.predicate[form.control.predicate_mode]};",
        f"        decoded_result.control.repeat_observed = {names.observed_kind[(form.control.repeat.observed_kind or '')]};",
        f"        decoded_result.control.repeat_rep = 1'b{int(form.control.repeat.rep)};",
        f"        decoded_result.control.repeat_repcc = 1'b{int(form.control.repeat.repcc)};",
        f"        decoded_result.control.has_ea_operand = 1'b{int(any(isinstance(operand, ResolvedEffectiveAddressOperand) for operand in form.operands))};",
    ]
    lines.extend(
        _render_mask_assignment("decoded_result.size_mask", (code for _, code in form.sizes), size_names)
    )
    lines.extend(
        line.replace("result_o.", "decoded_result.")
        for line in _render_cpuid_assignment(form, public_layout, names)
    )
    lines.extend(
        [
            f"        decoded_result.operand_count = 3'd{len(form.operands)};",
            "        decoded_result.required_bytes = layout_cursor;",
        ]
    )
    observed_slot = next(
        (
            i
            for i, x in enumerate(form.operands)
            if x is form.control.repeat.observed_operand
        ),
        0,
    )
    lines.append(
        f"        decoded_result.control.repeat_observed_operand = 2'd{observed_slot};"
    )
    for slot, operand in enumerate(form.operands):
        source = operand
        lines.extend(
            [
                f"        decoded_result.operands[{slot}].valid = 1'b1;",
                f"        decoded_result.operands[{slot}].type_name = {names.operand_type[_operand_type_name(operand)]};",
                f"        decoded_result.operands[{slot}].access = {names.access[operand.access]};",
                f"        decoded_result.operands[{slot}].ea_width = {names.ea_width[_operand_ea_width(operand)]};",
                f"        decoded_result.operands[{slot}].payload_signed = 1'b{int(isinstance(source, ResolvedPayloadOperand) and source.signed)};",
            ]
        )
        if isinstance(
            source, (ResolvedFieldOperand, ResolvedEffectiveAddressOperand)
        ):
            lines.append(
                f"        decoded_result.operands[{slot}].value = "
                f"64'({_gather('d0_i.opcode', source.field.positions)});"
            )
        elif isinstance(source, (ResolvedFixedNameOperand, ResolvedFixedValueOperand)):
            lines.append(
                f"        decoded_result.operands[{slot}].value = "
                f"64'h{(source.fixed_value if isinstance(source, ResolvedFixedValueOperand) else 0):016x};"
            )
        if isinstance(source, ResolvedEffectiveAddressOperand):
            candidate_slot = _ea_candidate_slot(form, operand)
            lines.extend(
                [
                    f"        decoded_result.operands[{slot}].ea_valid = 1'b1;",
                    f"        decoded_result.operands[{slot}].ea_slot = 1'd{candidate_slot};",
                ]
            )
    if form.overlaps:
        slots = {operand.name: index for index, operand in enumerate(form.operands)}
        lines.append(f"        decoded_result.overlap_count = 2'd{len(form.overlaps)};")
        for index, overlap in enumerate(form.overlaps):
            lines.extend(
                [
                    f"        decoded_result.overlaps[{index}].valid = 1'b1;",
                    f"        decoded_result.overlaps[{index}].rule = {names.overlap_rule[overlap.overlap.type]};",
                    f"        decoded_result.overlaps[{index}].left_operand = {_width(ir.limits.max_operands)}'d{slots[overlap.left.name]};",
                    f"        decoded_result.overlaps[{index}].right_operand = {_width(ir.limits.max_operands)}'d{slots[overlap.right.name]};",
                ]
            )
    operand_slots = {operand.name: index for index, operand in enumerate(form.operands)}
    lines.extend(
        [
            "        if (!layout_valid) begin",
            "          layout_failed = 1'b1;",
            "          decoded_result.stage = D1_STAGE_EA_DESCRIPTOR;",
            "        end",
        ]
    )
    payload_bytes_read = 0
    for payload_layout in payload_layouts:
        slot = operand_slots[payload_layout.operand.name]
        byte_width = payload_layout.operand.definition.bytes
        payload_bytes_read += byte_width
        payload_cursor = _standalone_payload_cursor_expression(form, payload_layout)
        lines.extend(
            [
                "        if (!layout_failed) begin",
                f"          decoded_result.required_bytes = layout_cursor + 6'd{payload_bytes_read};",
                "          if (decoded_result.required_bytes > byte_count_i || decoded_result.required_bytes > BEDROCK_RECORD_BYTES) begin",
                "            layout_failed = 1'b1;",
                "            decoded_result.stage = D1_STAGE_STANDALONE_PAYLOAD;",
                "          end else begin",
            ]
        )
        for byte in range(byte_width):
            lines.append(
                f"            decoded_result.operands[{slot}].value[{byte * 8} +: 8] = "
                f"record_i[((({payload_cursor}) + {byte}) * 8) +: 8];"
            )
        lines.extend(["          end", "        end"])
    lines.extend(
        [
            "        if (!layout_failed) begin",
            "          if (decoded_result.required_bytes > byte_count_i || decoded_result.required_bytes > BEDROCK_RECORD_BYTES)",
            "            decoded_result.stage = D1_STAGE_RECORD_LENGTH;",
            "          else begin",
            "            decoded_result.valid = 1'b1;",
            "            decoded_result.stage = D1_STAGE_SUCCESS;",
            "          end",
            "        end",
            "      end",
        ]
    )
    return "\n".join(lines)


def _render_descriptor_payload_span(ir: DecodeIR, names: Names) -> str:
    cases = []
    families = {_family_name(family): family for family in ir.effective_addresses.descriptor_families}
    for profile in ir.effective_addresses.profiles:
        lines = [f"      {names.ea_profile[_profile_name(profile.definition.owner, profile.definition.profile)]}: begin"]
        for compact in profile.compact_forms:
            family = families.get(_descriptor_name(compact))
            if family is None:
                continue
            lines.append(
                f"        if ((compact_raw & {_hex(7, compact.pattern.fixed_mask)}) == {_hex(7, compact.pattern.fixed_value)}) begin"
            )
            for form in family.forms:
                raw = f"descriptor[BEDROCK_EA_DESCRIPTOR_BITS-1 -: {form.pattern.bit_width}]"
                lines.append(
                    f"          if (({raw} & {_hex(form.pattern.bit_width, form.pattern.fixed_mask)}) == {_hex(form.pattern.bit_width, form.pattern.fixed_value)}) "
                    f"descriptor_payload_bytes = 6'd{min(ir.limits.max_record_bytes + 1, form.payload_width // 8)};"
                )
            lines.append("        end")
        lines.append("      end")
        cases.append("\n".join(lines))
    return "\n".join((
        "  function automatic logic [5:0] descriptor_payload_bytes(",
        "    input ea_profile_e profile, input logic [6:0] compact_raw, input logic [BEDROCK_EA_DESCRIPTOR_BITS-1:0] descriptor);",
        "    begin",
        "      descriptor_payload_bytes = 6'd0;",
        "      unique case (profile)",
        *cases,
        "        default: begin end",
        "      endcase",
        "    end",
        "  endfunction",
    ))


def _render_d1(ir: DecodeIR, names: Names) -> str:
    public_layout = derive_public_layout(ir)
    cases = []
    for form in ir.forms:
        value = names.form_ordinals[id(form)] + 1
        high = value >> 5
        low = value & 31
        high_count = ir.limits.form_count // 32 + 1
        pattern = ["?"] * (high_count + 32)
        pattern[high_count - 1 - high] = "1"
        pattern[high_count + 31 - low] = "1"
        cases.append(
            _render_form_case(
                form,
                ir,
                names,
                public_layout,
                case_label=f"{len(pattern)}'b{''.join(pattern)}",
            )
        )
    return render_template("bedrock_decode_d1.sv.in", {
        "EA_SPAN_FUNCTION": _render_ea_span_function(ir, names) + "\n\n" + _render_descriptor_payload_span(ir, names),
        "FORM_CASES": "\n".join(cases),
    })




@dataclass(frozen=True)
class LoweredOutputs:
    package: str
    d0: str
    d1: str
    ea: str


def lower(ir: DecodeIR) -> LoweredOutputs:
    """Lower one Decode IR snapshot, reading templates without writing outputs."""
    package, names = _render_package(ir)
    return LoweredOutputs(
        package=package,
        d0=_render_d0(ir, names),
        d1=_render_d1(ir, names),
        ea=render_template("bedrock_decode_ea.sv.in", {"EA_PARSING_FUNCTIONS": _render_ea_function(ir, names)}),
    )
