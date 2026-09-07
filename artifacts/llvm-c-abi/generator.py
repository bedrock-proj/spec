"""Generate LLVM/Clang include data from the typed C ABI catalog."""

from __future__ import annotations

from types import MappingProxyType

import re
from typing import NamedTuple

from abi.c.model.project import LocationPolicy, ResolvedRegisterClass
from abi.c.model.projection import CAbiProjection, project_c_abi
from engine.artifacts.generate import ArtifactGenerationContext
from engine.artifacts.definition import GeneratedArtifact, GeneratedArtifactSet


_MACROS = (
    "BEDROCK_C_TYPE",
    "BEDROCK_C_CALLING_CONVENTION",
    "BEDROCK_C_REGISTER_CLASS",
    "BEDROCK_C_ARGUMENT_REGISTER",
    "BEDROCK_C_RESULT_REGISTER",
    "BEDROCK_C_VALUE_CLASS",
    "BEDROCK_C_VALUE_KIND",
    "BEDROCK_C_LOCATION_POLICY",
    "BEDROCK_C_PROMOTION",
    "BEDROCK_C_PRESERVATION_REGISTER",
    "BEDROCK_C_RUNTIME_HELPER",
    "BEDROCK_C_RUNTIME_PARAMETER",
    "BEDROCK_C_MEMORY_ORDER",
    "BEDROCK_C_MEMORY_ORDER_STEP",
    "BEDROCK_C_ATOMIC_LOWERING",
    "BEDROCK_C_ATOMIC_OPERATION",
    "BEDROCK_C_ATOMIC_INSTRUCTION",
)

_LLVM_RETURN_TYPES = MappingProxyType({
    "GENERAL_SCALAR": ("i1", "i8", "i16", "i32", "i64"),
    "FLOAT_SCALAR": ("f32", "f64"),
    "VECTOR_VALUE": (
        "nxv16i8",
        "nxv8i16",
        "nxv4i32",
        "nxv2i64",
        "nxv8f16",
        "nxv4f32",
        "nxv2f64",
    ),
    "PREDICATE_VALUE": ("nxv16i1", "nxv8i1", "nxv4i1", "nxv2i1"),
})
_LLVM_RETURN_CLASS_ORDER = (
    "PREDICATE_VALUE",
    "VECTOR_VALUE",
    "FLOAT_SCALAR",
    "GENERAL_SCALAR",
)
_SPILLABLE_REGISTER_GROUPS = frozenset({"GPR", "FPR", "VECTOR", "PREDICATE"})


class CallingConventionReturnRule(NamedTuple):
    value_class: str
    llvm_types: tuple[str, ...]
    registers: tuple[str, ...]


class CallingConventionProjection(NamedTuple):
    """The C ABI relations consumed by LLVM calling-convention TableGen."""

    return_rules: tuple[CallingConventionReturnRule, ...]
    spillable_callee_saved: tuple[str, ...]
    all_callee_saved: tuple[str, ...]


def generate(definition, context: ArtifactGenerationContext) -> GeneratedArtifactSet:
    project = _inputs(context)
    _validate_llvm_projection(project)
    calling_convention = _project_calling_convention(project)
    return GeneratedArtifactSet(
        (
            GeneratedArtifact(
                definition.outputs["calling-convention"], _render_calling_convention(calling_convention)
            ),
            GeneratedArtifact(
                definition.outputs["catalog"], _render_catalog(project)
            ),
        ),
        definition.id,
    )


def _inputs(context) -> CAbiProjection:
    project = context.workspace.require_provider("abi.c")
    isa = context.workspace.require_provider("isa")
    return context.shared_result((project_c_abi, id(project), id(isa)), lambda: project_c_abi(project, isa))


def validate(definition, context) -> None:
    project = _inputs(context)
    _validate_llvm_projection(project)
    _project_calling_convention(project)


def _validate_llvm_projection(project: CAbiProjection) -> None:
    convention = project.calling_convention
    classes = {
        value.definition.id for value in convention.register_classes.values()
    }
    required_classes = {"GENERAL", "FLOATING", "VECTOR", "PREDICATE"}
    if classes != required_classes:
        raise ValueError(
            f"{convention.definition.source}: LLVM projection requires register classes "
            f"{sorted(required_classes)}, got {sorted(classes)}"
        )
    values = {value.definition.id: value for value in project.value_classes}
    missing = set(_LLVM_RETURN_TYPES) - set(values)
    if missing:
        raise ValueError(
            f"{convention.definition.source}: LLVM return projection lacks value classes "
            f"{sorted(missing)}"
        )
    expected_result_classes = {
        "GENERAL_SCALAR": "GENERAL",
        "FLOAT_SCALAR": "FLOATING",
        "VECTOR_VALUE": "VECTOR",
        "PREDICATE_VALUE": "PREDICATE",
    }
    for value_id, class_id in expected_result_classes.items():
        value = values[value_id]
        policy = value.definition.result
        actual = value.result_register_class.definition.id
        if policy.mode != "value" or policy.units != 1 or actual != class_id:
            raise ValueError(
                f"{value.definition.source}: LLVM return projection expects "
                f"one {class_id} value register"
            )


def _project_calling_convention(project: CAbiProjection) -> CallingConventionProjection:
    value_classes = {value.definition.id: value for value in project.value_classes}
    return_rules: list[CallingConventionReturnRule] = []
    for value_id in _LLVM_RETURN_CLASS_ORDER:
        value_class = value_classes[value_id]
        result_register_class = value_class.result_register_class
        return_rules.append(
            CallingConventionReturnRule(
                value_id,
                _LLVM_RETURN_TYPES[value_id],
                tuple(item.id for item in result_register_class.results),
            )
        )
    callee_saved = tuple(
        register
        for disposition, registers in project.preservation
        if disposition == "callee_saved"
        for register in registers
    )
    all_callee_saved = tuple(item.id for item in callee_saved)
    spillable = tuple(
        item.id
        for item in callee_saved
        if item.group in _SPILLABLE_REGISTER_GROUPS
    )
    return CallingConventionProjection(tuple(return_rules), spillable, all_callee_saved)


def _render_calling_convention(projection: CallingConventionProjection) -> str:
    lines = [
        "//===-- BedrockGenCallingConv.td - generated C ABI -------*- tablegen -*-===//",
        "//",
        "// Generated by spec/artifacts/llvm-c-abi. Do not edit.",
        "// The argument state machine consumes BedrockGenCABI.inc; TableGen owns",
        "// the declarative return and call-preserved register records below.",
        "//",
        "//===----------------------------------------------------------------------===//",
        "",
        "def RetCC_Bedrock : CallingConv<[",
        "  CCIfType<[i1, i8, i16], CCPromoteToType<i32>>,",
    ]
    return_rules = [
        f"  CCIfType<[{', '.join(rule.llvm_types)}], "
        f"CCAssignToReg<[{', '.join(rule.registers)}]>>"
        for rule in projection.return_rules
    ]
    lines.extend(",\n".join(return_rules).splitlines())
    lines.extend(("]>;", ""))

    lines.extend(
        _callee_saved_record("CSR_Bedrock_Save", projection.spillable_callee_saved)
    )
    lines.append("")
    lines.extend(
        (
            "// Architectural state excluded from register allocation is also excluded",
            "// from PEI spills, but remains in the complete C ABI call-preserved mask.",
        )
    )
    lines.extend(_callee_saved_record("CSR_Bedrock", projection.all_callee_saved))
    lines.append("")
    return "\n".join(lines)


def _callee_saved_record(name: str, names: tuple[str, ...]) -> list[str]:
    members = " " + ", ".join(names) if names else ""
    return [f"def {name} : CalleeSavedRegs<(add{members})>;"]


def _render_catalog(project: CAbiProjection) -> str:
    lines = [
        "//===-- BedrockGenCABI.inc - generated C ABI data --------*- C++ -*-===//",
        "//",
        "// Generated by spec/artifacts/llvm-c-abi. Do not edit.",
        "// Define any BEDROCK_C_* macro before including this file. Undefined",
        "// record-family macros default to no-ops and are removed afterwards.",
        "//",
        "//===----------------------------------------------------------------------===//",
        "",
        *_macro_defaults(),
    ]
    for item in project.types:
        fixed = isinstance(item.size_bits, int)
        lines.append(
            "BEDROCK_C_TYPE("
            + ", ".join(
                (
                    item.id,
                    _c_string(item.spelling),
                    _token(item.call_kind),
                    "FIXED" if fixed else "SYMBOLIC",
                    str(item.size_bits) if fixed else "0",
                    _c_string("") if fixed else _c_string(str(item.size_bits)),
                    str(item.alignment_bytes),
                    _token(item.representation or "none"),
                )
            )
            + ")"
        )

    convention = project.calling_convention
    stack = convention.definition.stack
    lines.append(
        "BEDROCK_C_CALLING_CONVENTION("
        + ", ".join(
            (
                convention.stack_pointer.id,
                _token(stack.growth),
                str(stack.entry_alignment_bytes),
                str(stack.first_argument_offset_bytes),
                str(stack.argument_slot_bytes),
                convention.sret_register.id,
                str(stack.red_zone_bytes),
            )
        )
        + ")"
    )
    for resolved_class in convention.register_classes.values():
        register_class = resolved_class.definition
        lines.append(
            f"BEDROCK_C_REGISTER_CLASS({register_class.id}, "
            f"{_token(register_class.assignment_order)}, "
            f"{register_class.tuple_alignment}, "
            f"{_token(register_class.exhaustion)})"
        )
        for index, register in enumerate(resolved_class.arguments):
            lines.append(
                f"BEDROCK_C_ARGUMENT_REGISTER({register_class.id}, {index}, "
                f"{register.id})"
            )
        for index, register in enumerate(resolved_class.results):
            lines.append(
                f"BEDROCK_C_RESULT_REGISTER({register_class.id}, {index}, "
                f"{register.id})"
            )
    for resolved_value in project.value_classes:
        value_class = resolved_value.definition
        lines.append(f"BEDROCK_C_VALUE_CLASS({value_class.id})")
        for kind in value_class.kinds:
            lines.append(f"BEDROCK_C_VALUE_KIND({value_class.id}, {_token(kind)})")
        lines.append(
            _location_line(value_class.id, "ARGUMENT", value_class.argument, resolved_value.argument_register_class)
        )
        lines.append(
            _location_line(value_class.id, "RESULT", value_class.result, resolved_value.result_register_class)
        )
    for promotion in project.promotions:
        for source_kind in promotion.source_kinds:
            lines.append(
                f"BEDROCK_C_PROMOTION({promotion.id}, {_token(source_kind)}, "
                f"{_token(promotion.target_kind)})"
            )
    for disposition, registers in project.preservation:
        for register in registers:
            lines.append(
                f"BEDROCK_C_PRESERVATION_REGISTER("
                f"{_token(disposition)}, {register.id})"
            )

    for helper, result, parameters in project.runtime_helpers:
        lines.append(
            f"BEDROCK_C_RUNTIME_HELPER({helper.id}, {_c_string(helper.symbol)}, "
            f"{result.id})"
        )
        for index, parameter in enumerate(parameters):
            lines.append(
                f"BEDROCK_C_RUNTIME_PARAMETER({helper.id}, {index}, "
                f"{parameter.id})"
            )

    for mapping, load, store, thread_fence in project.memory_orders:
        lines.append(
            f"BEDROCK_C_MEMORY_ORDER({mapping.id}, "
            f"{_token(mapping.instruction_order)}, "
            f"{int(mapping.load is not None)}, {int(mapping.store is not None)})"
        )
        for operation, sequence in (
            ("LOAD", load),
            ("STORE", store),
            ("THREAD_FENCE", thread_fence),
        ):
            for index, step in enumerate(sequence or ()):
                kind = "ACCESS" if step == "access" else "INSTRUCTION"
                value = (
                    "NONE"
                    if step == "access"
                    else step.instruction.mnemonic
                )
                lines.append(
                    f"BEDROCK_C_MEMORY_ORDER_STEP({mapping.id}, {operation}, "
                    f"{index}, {kind}, {value})"
                )

    for lowering, instructions in project.atomic_lowerings:
        lines.append(
            f"BEDROCK_C_ATOMIC_LOWERING({lowering.id}, {_token(lowering.strategy)})"
        )
        for operation in lowering.c_operations:
            lines.append(
                f"BEDROCK_C_ATOMIC_OPERATION({lowering.id}, {_token(operation)})"
            )
        for index, instruction in enumerate(instructions):
            lines.append(
                f"BEDROCK_C_ATOMIC_INSTRUCTION({lowering.id}, {index}, "
                f"{instruction.instruction.mnemonic})"
            )
    lines.extend(_macro_cleanup())
    lines.append("")
    return "\n".join(lines)


def _location_line(
    value_class: str, direction: str, policy: LocationPolicy, resolved_class: ResolvedRegisterClass
) -> str:
    register_class = resolved_class.definition.id
    return (
        f"BEDROCK_C_LOCATION_POLICY({value_class}, {direction}, "
        f"{_token(policy.mode)}, {register_class}, {policy.units}, "
        f"{policy.alignment_units}, {policy.direct_maximum_bytes or 0})"
    )


def _macro_defaults() -> list[str]:
    lines: list[str] = []
    for macro in _MACROS:
        marker = f"BEDROCK_GEN_DEFINED_{macro}"
        lines.extend(
            (
                f"#ifndef {macro}",
                f"#define {macro}(...)",
                f"#define {marker}",
                "#endif",
            )
        )
    lines.append("")
    return lines


def _macro_cleanup() -> list[str]:
    lines = [""]
    for macro in _MACROS:
        marker = f"BEDROCK_GEN_DEFINED_{macro}"
        lines.extend(
            (
                f"#ifdef {marker}",
                f"#undef {macro}",
                f"#undef {marker}",
                "#endif",
            )
        )
    return lines


def _token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()
    if not token or token[0].isdigit():
        token = "VALUE_" + token
    return token


def _c_string(value: str) -> str:
    escaped = []
    for character in value:
        if character in ('\\', '"', '?'):
            escaped.append('\\' + character)
        elif ord(character) < 32 or ord(character) == 127:
            escaped.append(f"\\{ord(character):03o}")
        else:
            escaped.append(character)
    return '"' + ''.join(escaped) + '"'
