"""Serialize resolved ISA forms into the Sail catalog representation."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from importlib import import_module
from itertools import groupby
from types import MappingProxyType

from engine.isa.encoding_architecture import (
    ENCODING_CLASSES, MIN_EXTENDED_RECORD_BYTES, MAX_RECORD_BYTES,
    EXTENDED_CLASS_PREFIX_BITS, encoding_class_for_extended_prefix,
)
from engine.isa.encoding import (
    ResolvedConstraint, ResolvedEncodingForm, ResolvedFieldBinding,
    ResolvedOperand, ResolvedFieldOperand, ResolvedEffectiveAddressOperand,
    ResolvedPayloadOperand, ResolvedFixedNameOperand, ResolvedFixedValueOperand,
    ResolvedPayloadRead, representative_record,
)
from engine.isa.ea import (
    CompactExtensionEAMode, ImmediateEAMode, MemoryEAMode,
    FixedEASegment, FieldEASegment, ResolvedEAEncoding,
    EABinary, EAIdentifier, EALiteral, EANegation, ea_gpr_index,
)
from engine.isa.types import (
    FieldType, PayloadType, EffectiveAddressFieldType, EnumConditionFieldType,
    SizeSelectorFieldType, ImmediateFieldType, PageTableLevelFieldType,
    MemoryOrderFieldType, RegisterSelectorFieldType, RegisterPairSelectorFieldType,
    ImmediatePayloadType, PcDisplacementPayloadType, PcAbsolutePayloadType,
    RegisterSelectorPayloadType, ControlRegisterSelectorPayloadType,
    FloatingPointConstantIdPayloadType,
)

_registry = import_module("artifacts.sail-model.registry")
_CATALOG_CHUNK_SIZE = 24
CLASS_CONSTRUCTORS = MappingProxyType({
    "extrashort": "ExtraShort", "short": "Short", "medium": "Medium",
    "long": "Long", "extralong": "ExtraLong", "xxlong": "Xxlong",
})
ACCESS_CONSTRUCTORS = MappingProxyType({
    "read": "AccessRead", "write": "AccessWrite",
    "read_write": "AccessReadWrite", "address": "AccessAddress",
})
PREDICATE_CONSTRUCTORS = MappingProxyType({
    "none": "PredicateNone", "annul_on_false": "AnnulOnFalse",
    "temporary": "Temporary", "counter_and_condition": "CounterAndCondition",
    "write_boolean": "WriteBoolean",
})


RECORD_CLASS_CONSTRUCTORS = MappingProxyType({
    "extrashort": "FixedExtraShort", "short": "FixedShort",
    "medium": "ExtendedMedium", "long": "ExtendedLong",
    "extralong": "ExtendedExtraLong", "xxlong": "ExtendedXxlong",
})


def render_framing_declarations() -> tuple[str, ...]:
    """Serialize canonical framing facts before the authored decoder algorithms."""
    lines = [
        "enum Record_class = " + " | ".join(RECORD_CLASS_CONSTRUCTORS[owner.name] for owner in ENCODING_CLASSES),
        f"let architecture_max_record_bytes : int = {MAX_RECORD_BYTES}",
        "",
        "function class_minimum(record_class : Record_class) -> int = match record_class {",
        *(f"  {RECORD_CLASS_CONSTRUCTORS[owner.name]} => {owner.opcode_space_bytes}," for owner in ENCODING_CLASSES),
        "}", "",
        "function encoding_class_of_record(record_class : Record_class) -> Encoding_class = match record_class {",
        *(f"  {RECORD_CLASS_CONSTRUCTORS[owner.name]} => {CLASS_CONSTRUCTORS[owner.name]}," for owner in ENCODING_CLASSES),
        "}", "",
        "function encoding_primary_bytes(encoding_class : Encoding_class) -> int = match encoding_class {",
        *(f"  {CLASS_CONSTRUCTORS[owner.name]} => {owner.opcode_space_bytes}," for owner in ENCODING_CLASSES),
        "}", "",
        "function encoding_pattern_mask(encoding_class : Encoding_class) -> bits(64) = match encoding_class {",
        *(f"  {CLASS_CONSTRUCTORS[owner.name]} => 0x{(1 << owner.pattern_bits) - 1:016x}," for owner in ENCODING_CLASSES),
        "}", "",
        "function encoded_record_length(record_class : Record_class, byte0 : bits(8)) -> int = match record_class {",
    ]
    for owner in ENCODING_CLASSES:
        if owner.length_bits:
            high = 7 - len(owner.fixed_prefix)
            low = high - owner.length_bits + 1
            value = f"{MIN_EXTENDED_RECORD_BYTES} + unsigned(byte0[{high} .. {low}])"
        else:
            value = str(owner.opcode_space_bytes)
        lines.append(f"  {RECORD_CLASS_CONSTRUCTORS[owner.name]} => {value},")
    lines.extend(("}", "", f"function extended_class(prefix : bits({EXTENDED_CLASS_PREFIX_BITS})) -> Record_class ="))
    classified = tuple(encoding_class_for_extended_prefix(prefix) for prefix in range(1 << EXTENDED_CLASS_PREFIX_BITS))
    runs = tuple((owner, tuple(items)) for owner, items in groupby(enumerate(classified), key=lambda item: item[1]))
    for index, (owner, entries) in enumerate(runs):
        constructor = RECORD_CLASS_CONSTRUCTORS[owner.name]
        if index + 1 == len(runs):
            lines.append(f"  {constructor}")
        else:
            lines.append(f"  if unsigned(prefix) <= {entries[-1][0]} then {constructor} else")
    lines.extend(("", "function framing_class(byte0 : bits(8), byte1 : option(bits(8))) -> option(Record_class) ="))
    fixed = tuple(owner for owner in ENCODING_CLASSES if not owner.length_bits)
    for owner in fixed:
        low = 8 - len(owner.fixed_prefix)
        lines.append(f"  if byte0[7 .. {low}] == 0b{owner.fixed_prefix} then Some({RECORD_CLASS_CONSTRUCTORS[owner.name]}) else")
    extended = tuple(owner for owner in ENCODING_CLASSES if owner.length_bits)
    layouts = {(owner.fixed_prefix, owner.length_bits) for owner in extended}
    if len(layouts) != 1:
        raise ValueError("Sail extended classes require one architectural header layout")
    prefix, length_bits = next(iter(layouts))
    first_bits = 8 - len(prefix) - length_bits
    second_bits = EXTENDED_CLASS_PREFIX_BITS - first_bits
    lines.extend((
        f"  if byte0[7 .. {8 - len(prefix)}] == 0b{prefix} then match byte1 {{",
        f"    Some(second) => Some(extended_class(byte0[{first_bits - 1} .. 0] @ second[7 .. {8 - second_bits}])),",
        "    None() => None(),", "  } else None()", "",
    ))
    return tuple(lines)


def render_sail_catalog(program) -> str:
    entries_by_class = catalog_entries_by_class(program)
    catalog_sections: list[str] = []
    for name, constructor in CLASS_CONSTRUCTORS.items():
        entries = entries_by_class[name]
        chunk_names: list[str] = []
        for index, start in enumerate(range(0, len(entries), _CATALOG_CHUNK_SIZE)):
            chunk_name = f"primary_form_catalog_{name}_chunk_{index}"
            chunk_names.append(chunk_name)
            catalog_sections.extend(
                (
                    f"let {chunk_name} : list(Catalog_entry) = [|",
                    ",\n".join(entries[start : start + _CATALOG_CHUNK_SIZE]),
                    "|]",
                    "",
                )
            )
        catalog_sections.extend(
            (
                f"let primary_form_catalog_{name}_cache : list(Catalog_entry) =",
                "  append_catalog_entry_chunks([|" + ", ".join(chunk_names) + "|])",
                "",
            )
        )
    catalog_sections.extend(
        (
            "function primary_form_catalog_for(encoding_class : Encoding_class) -> list(Catalog_entry) =",
            "  match encoding_class {",
            *(
                f"    {constructor} => primary_form_catalog_{name}_cache,"
                for name, constructor in CLASS_CONSTRUCTORS.items()
            ),
            "  }",
            "",
        )
    )
    ea_entries = tuple(_render_ea_variant(item) for item in program.ea_forms)
    representatives = representative_record_entries(program)
    return "\n".join(
        [
            "// Generated from the typed ISA project. Do not edit.",
            "",
            *render_framing_declarations(),
            "function append_catalog_entries(left : list(Catalog_entry),",
            "                                right : list(Catalog_entry)) -> list(Catalog_entry) =",
            "  match left {",
            "    [||] => right,",
            "    head :: tail => head :: append_catalog_entries(tail, right),",
            "  }",
            "",
            "function append_catalog_entry_chunks(chunks : list(list(Catalog_entry))) -> list(Catalog_entry) =",
            "  match chunks {",
            "    [||] => [||],",
            "    chunk :: tail => append_catalog_entries(chunk, append_catalog_entry_chunks(tail)),",
            "  }",
            "",
            *catalog_sections,
            "let effective_address_catalog_cache : list(Ea_form) = [|",
            ",\n".join(ea_entries),
            "|]",
            "",
            "function effective_address_catalog() -> list(Ea_form) = effective_address_catalog_cache",
            "",
            "let representative_form_records_cache : list(Representative_record) = [|",
            ",\n".join(representatives),
            "|]",
            "",
            "function representative_form_records() -> list(Representative_record) = representative_form_records_cache",
            "",
        ]
    )

def _constructor(prefix: str, value: str) -> str:
    suffix = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not suffix or suffix[0].isdigit():
        suffix = "N" + suffix
    return prefix + suffix

def _list(items: Iterable[str]) -> str:
    return "[|" + ", ".join(items) + "|]"

def _option(value: str | None, prefix: str) -> str:
    return "None()" if value is None else f"Some({_constructor(prefix, value)})"

def _field_type_name(definition: FieldType) -> str:
    name = definition.id
    semantic_names = {
        "CC": "condition",
        "PTLVL": "pt_level",
        "FLBMP": "flags_bitmap",
        "MORDER": "memory_order",
    }
    if name in semantic_names:
        return semantic_names[name]
    if name.startswith("SIZE_"):
        return "size_" + name.removeprefix("SIZE_")
    if name.startswith("IMM"):
        return name.lower()
    return name

def _payload_type_name(operand: ResolvedPayloadOperand) -> str:
    definition = operand.definition
    if type(definition) is FloatingPointConstantIdPayloadType:
        return "fconst_id"
    if type(definition) in (
        ImmediatePayloadType, PcDisplacementPayloadType, PcAbsolutePayloadType,
        RegisterSelectorPayloadType, ControlRegisterSelectorPayloadType,
    ):
        return f"imm{definition.bytes * 8}{'s' if operand.signed else ''}"
    raise TypeError(f"unsupported payload type {type(definition).__name__}")

def _field_kind(definition: FieldType) -> str:
    if isinstance(definition, EffectiveAddressFieldType):
        return "FieldEa"
    if isinstance(definition, EnumConditionFieldType):
        return "FieldCondition"
    if isinstance(definition, SizeSelectorFieldType):
        return "FieldSize"
    if isinstance(
        definition, (ImmediateFieldType, PageTableLevelFieldType, MemoryOrderFieldType)
    ):
        return "FieldImmediate"
    if isinstance(
        definition, (RegisterSelectorFieldType, RegisterPairSelectorFieldType)
    ):
        return "FieldFreg" if definition.register_group.element == "FPR" else "FieldRn"
    return "FieldBits"

def _ea_update_difference_name(difference: int | str) -> str:
    return f"constant_{difference}" if isinstance(difference, int) else difference


def _form_key(form: ResolvedEncodingForm) -> str:
    return f"{form.encoding_class.name}.{form.instruction.mnemonic.lower()}.{form.form.id}"


def _operand_type_name(operand: ResolvedOperand) -> str:
    if isinstance(operand, ResolvedFieldOperand):
        return _field_type_name(operand.field.definition)
    if isinstance(operand, ResolvedPayloadOperand):
        return _payload_type_name(operand)
    if isinstance(operand, ResolvedFixedNameOperand):
        return operand.fixed_name
    if isinstance(operand, ResolvedFixedValueOperand):
        return "imm"
    raise TypeError(type(operand).__name__)


def _render_constraint(constraint: ResolvedConstraint) -> str:
    ranges = _list(f"struct {{ lower = {lower}, upper = {upper} }}" for lower, upper in constraint.ranges)
    positions = _list(map(str, constraint.field.positions))
    return f"struct {{ field_positions = {positions}, ranges = {ranges}, reason = {json.dumps(constraint.constraint.reason)} }}"


def _render_field(field: ResolvedFieldBinding) -> str:
    return f"struct {{ symbol = {_constructor('Field_', field.field.marker)}, operand_type = {_constructor('OperandType_', _field_type_name(field.definition))}, kind = {_field_kind(field.definition)}, positions = {_list(map(str, field.positions))} }}"


def _render_operand(operand: ResolvedOperand) -> str:
    field_symbol = "None()"
    positions = ()
    if isinstance(operand, ResolvedFieldOperand):
        field_symbol = f"Some({_constructor('Field_', operand.field.field.marker)})"
        positions = operand.field.positions
    domain = _option(operand.logical.domain, "OperandDomain_")
    ea_role = ea_width = ea_profile = "None()"
    if isinstance(operand, ResolvedEffectiveAddressOperand):
        ea_role = _option(operand.purpose, "EaRole_")
        ea_width = _option(operand.interpretation_width, "EaWidth_")
        ea_profile = _option(operand.profile, "EaProfile_")
    fixed = isinstance(operand, ResolvedFixedValueOperand)
    fixed_value = operand.fixed_value if fixed else 0
    return f"struct {{ name = {_constructor('Operand_', operand.name)}, operand_type = {_constructor('OperandType_', _operand_type_name(operand))}, access = {ACCESS_CONSTRUCTORS[operand.access]}, field_symbol = {field_symbol}, field_positions = {_list(map(str, positions))}, domain = {domain}, ea_role = {ea_role}, ea_width = {ea_width}, ea_profile = {ea_profile}, has_fixed_value = {str(fixed).lower()}, fixed_value = {fixed_value}, legal_values = [||] }}"


def _render_payload(operand: ResolvedPayloadOperand) -> str:
    return f"struct {{ operand_name = {_constructor('Operand_', operand.name)}, operand_type = {_constructor('OperandType_', _operand_type_name(operand))}, width = {operand.definition.bytes * 8}, signed = {str(operand.signed).lower()} }}"


def _render_entry(form: ResolvedEncodingForm, *, bundle, instruction_set: str) -> str:
    rendered_sizes = _list(f"({value}, {_constructor('Size_', code)})" for value, code in form.sizes)
    required = _list(_registry._cpuid_constructor(field) for field in bundle.required_cpuid_flags_for(form.form))
    repeat = form.control.repeat
    return f"  struct {{ form_id = {_constructor('Form_', _form_key(form))}, operation = {_registry.operation_constructor(form.instruction)}, route = {_registry.ROUTE_CONSTRUCTORS[form.control.route]}, instruction_set = {instruction_set}, privilege = {('SupervisorPrivilege' if form.control.privileged else 'UserPrivilege')}, predicate_mode = {PREDICATE_CONSTRUCTORS[form.control.predicate_mode]}, repeat_rep = {str(repeat.rep).lower()}, repeat_repcc = {str(repeat.repcc).lower()}, encoding_class = {CLASS_CONSTRUCTORS[form.encoding_class.name]}, value = 0x{form.form.pattern.fixed_value:016X}, mask = 0x{form.form.pattern.fixed_mask:016X}, constraints = {_list(_render_constraint(item) for item in form.constraints)}, fields = {_list(_render_field(item) for item in form.fields)}, operands = {_list(_render_operand(item) for item in form.operands)}, sizes = {rendered_sizes}, appended_payloads = {_list(_render_payload(read.operand) for read in form.layout if isinstance(read, ResolvedPayloadRead))}, required_cpuid_flags = {required} }}"


def _ea_name(variant: ResolvedEAEncoding) -> str:
    mode = variant.mode
    index = next(index for index, item in enumerate(mode.encodings) if item is variant.encoding)
    return f"{mode.catalog.owner}_{mode.catalog.profile}_{mode.catalog.mode_type}_{mode.id}_{index}"


def _render_ea_expression(variant: ResolvedEAEncoding) -> str | None:
    if variant.expression is None:
        return None
    field_roles = {field.field.role for field in variant.fields}
    payload_roles = set(variant.expression_payload_roles)

    def render(expression):
        match expression:
            case EALiteral(value):
                return [f"EaExprLiteral(0x{value % (1 << 64):016X})"]
            case EAIdentifier(name) if name in field_roles:
                return [f"EaExprField(EaRole_{name})"]
            case EAIdentifier(name) if name in payload_roles:
                return [f"EaExprPayload(EaPayloadRole_{name})"]
            case EAIdentifier(name) if name in ("scale", "vlen_bytes", "element_count"):
                return [f"EaExprScale(EaUpdateDifference_{name})"]
            case EAIdentifier(name) if name in ("SP", "PC"):
                return ["EaExprSp()" if name == "SP" else "EaExprPc()"]
            case EAIdentifier(name) if (index := ea_gpr_index(name)) is not None:
                return [f"EaExprGpr(0x{index:X})"]
            case EANegation(operand):
                return [*render(operand), "EaExprNegate()"]
            case EABinary(operator, left, right):
                constructor = {"+": "Add", "-": "Subtract", "*": "Multiply"}[operator]
                return [*render(left), *render(right), f"EaExpr{constructor}()"]
        raise ValueError(f"{variant.mode.source}: EA expression has no resolved Sail input for {expression!r}")

    return _list(render(variant.expression))


def _render_ea_variant(variant: ResolvedEAEncoding) -> str:
    mode = variant.mode
    chunks = variant.patterns
    patterns = _list(f"struct {{ width = {pattern.bit_width}, value = 0x{pattern.fixed_value:04X}, mask = 0x{pattern.fixed_mask:04X} }}" for pattern in chunks)
    fields = _list(f"struct {{ symbol = {_constructor('Field_', field.field.symbol)}, operand_type = {_constructor('OperandType_', _field_type_name(field.definition))}, role = {_constructor('EaRole_', field.field.role)}, positions = {_list(map(str, field.positions))} }}" for field in variant.fields)
    payloads = _list(f"struct {{ role = EaPayloadRole_{payload.payload.role}, width = {payload.definition.bytes * 8}, signed = {str(payload.signed).lower()} }}" for payload in variant.payloads)
    rendered_expression = _render_ea_expression(variant)
    expression = f"Some({rendered_expression})" if rendered_expression is not None else "None()"
    if len(variant.updates) > 1:
        raise ValueError(f"{mode.source}: one Sail catalog member cannot encode multiple updates")
    autoupdate = "None()"
    if variant.updates:
        update = variant.updates[0]
        autoupdate = f"Some(struct {{ target = {_constructor('EaUpdateTarget_', update.target)}, mode = {_constructor('EaUpdateMode_', update.update_type)}, difference = {_constructor('EaUpdateDifference_', _ea_update_difference_name(update.difference))} }})"
    descriptor = mode.extension.id.lower() if isinstance(mode, CompactExtensionEAMode) else None
    family = mode.catalog.mode_type.lower() if mode.catalog.mode_type != "compact" else None
    descriptor_bytes = mode.extension.bytes if isinstance(mode, CompactExtensionEAMode) else variant.pattern.bit_width // 8 if family else 0
    kind = "escape" if isinstance(mode, CompactExtensionEAMode) else "immediate" if isinstance(mode, ImmediateEAMode) else "memory"
    segment = base = None
    if isinstance(mode, MemoryEAMode):
        segment = "explicit" if isinstance(mode.segment, FieldEASegment) else mode.segment.register if isinstance(mode.segment, FixedEASegment) else "default"
        base = mode.base_source.value if mode.base_source.value not in {"none", "encoded"} else None
    return f"  struct {{ name = {_constructor('EaForm_', _ea_name(variant))}, profile = {_option(mode.catalog.profile, 'EaProfile_')}, descriptor_family = {_option(family, 'EaDescriptor_')}, descriptor_bytes = {descriptor_bytes}, patterns = {patterns}, kind = {_constructor('EaKind_', kind)}, fields = {fields}, segment = {_option(segment, 'EaSegment_')}, payloads = {payloads}, expression = {expression}, base = {_option(base, 'EaBase_')}, descriptor = {_option(descriptor, 'EaDescriptor_')}, autoupdate = {autoupdate} }}"


def catalog_entries_by_class(program) -> dict[str, tuple[str, ...]]:
    bundles = {id(bundle.instruction): bundle for bundle in program.bundles}
    entries = {name: [] for name in CLASS_CONSTRUCTORS}
    for form in program.forms:
        bundle = bundles[id(form.instruction)]
        entries[form.encoding_class.name].append(_render_entry(form, bundle=bundle, instruction_set=_registry._instruction_set(bundle.owner, program.registry)))
    return {name: tuple(items) for name, items in entries.items()}


def representative_record_entries(program) -> tuple[str, ...]:
    return tuple("  struct { form_id = " + _constructor('Form_', _form_key(form)) + ", bytes = " + _list(f"0x{byte:02X}" for byte in representative_record(form)) + " }" for form in program.forms)



def catalog_id_declarations(program) -> tuple[str, ...]:
    variants = program.declaration_ea_forms
    forms = ["Form_invalid", *(_constructor("Form_", _form_key(form)) for form in program.declaration_forms)]
    operand_types = {"imm"}
    fields, operands, sizes, domains = set(), set(), set(), set()
    for form in program.declaration_forms:
        sizes.update(code for _, code in form.sizes)
        fields.update(field.field.marker for field in form.fields)
        operand_types.update(_field_type_name(field.definition) for field in form.fields)
        for operand in form.operands:
            operands.add(operand.name)
            operand_types.add(_operand_type_name(operand))
            if operand.logical.domain is not None:
                domains.add(operand.logical.domain)
    for variant in variants:
        fields.update(field.field.symbol for field in variant.fields)
        operand_types.update(_field_type_name(field.definition) for field in variant.fields)
    groups = (
        ("Encoding_class", tuple(CLASS_CONSTRUCTORS[owner.name] for owner in ENCODING_CLASSES)),
        ("Form_id", forms),
        (
            "Operand_type",
            [_constructor("OperandType_", item) for item in sorted(operand_types)],
        ),
        ("Size_code", [_constructor("Size_", item) for item in sorted(sizes)]),
        ("Field_id", [_constructor("Field_", item) for item in sorted(fields)]),
        ("Operand_id", [_constructor("Operand_", item) for item in sorted(operands)]),
        (
            "Operand_domain",
            [_constructor("OperandDomain_", item) for item in sorted(domains)],
        ),
        (
            "Ea_role",
            [
                _constructor("EaRole_", item)
                for item in (
                    "address",
                    "base",
                    "control_target",
                    "index",
                    "segment",
                    "value",
                )
            ],
        ),
        (
            "Ea_width",
            [
                _constructor("EaWidth_", item)
                for item in ("B", "L", "Q", "W", "operation_size", "predicate")
            ],
        ),
        (
            "Ea_profile",
            sorted(
                {
                    _constructor("EaProfile_", item.mode.catalog.profile)
                    for item in variants
                    if item.mode.catalog.profile is not None
                }
            ),
        ),
        (
            "Ea_form_id",
            sorted({_constructor("EaForm_", _ea_name(item)) for item in variants}),
        ),
        (
            "Ea_descriptor_family",
            [_constructor("EaDescriptor_", item) for item in ("ext1", "ext2")],
        ),
        (
            "Ea_kind",
            [
                _constructor("EaKind_", item)
                for item in ("escape", "immediate", "memory")
            ],
        ),
        (
            "Ea_segment",
            [
                _constructor("EaSegment_", item)
                for item in ("CS", "SS", "default", "explicit")
            ],
        ),
        ("Ea_base", [_constructor("EaBase_", item) for item in ("PC", "SP", "zero")]),
        (
            "Ea_update_target",
            [_constructor("EaUpdateTarget_", item) for item in ("base", "index")],
        ),
        (
            "Ea_update_mode",
            [
                _constructor("EaUpdateMode_", item)
                for item in ("postincrement", "predecrement")
            ],
        ),
        (
            "Ea_update_difference",
            sorted(
                {
                    _constructor("EaUpdateDifference_", _ea_update_difference_name(update.difference))
                    for item in variants
                    for update in item.updates
                }
            ),
        ),
    )
    declarations = []
    for name, constructors in groups:
        values = list(constructors)
        if not values or len(values) != len(set(values)):
            raise ValueError(f"{name} constructors are empty or collide")
        declarations.append(f"enum {name} = " + " | ".join(values))
    return tuple(declarations)
