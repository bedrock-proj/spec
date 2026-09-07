"""Values and operations owned by this specification boundary."""

from __future__ import annotations

from importlib import import_module

from engine.sail.dispatch import SailDispatchProjection

_registry = import_module("artifacts.sail-model.registry")


def render_sail_dispatch(projection: SailDispatchProjection) -> str:
    dispatch_function = (
        "execute_base_operation_entry"
        if projection.has_execution_provider
        else "execute_operation_entry"
    )
    lines = [
        "// Generated from instruction-local Sail entry declarations. Do not edit.",
        "",
        f"function {dispatch_function}(instruction : Decoded_instruction, state : Cpu_state)",
        "  -> Execution_result = match instruction.form.operation {",
    ]
    for entry in projection.entries:
        rejection = (
            "faulted(state, instruction.form.operation, IllegalInstruction, "
            '"local operation entry rejected its owning form")'
        )
        execution = (
            f"match {entry.entry}(instruction, state) "
            f"{{ Some(result) => result, None() => {rejection} }}"
        )
        lines.append(
            f"  {_registry.operation_constructor(entry.instruction)} => {execution},"
        )
    lines.extend([
        '  _ => faulted(state, instruction.form.operation, IllegalInstruction, "operation is not enabled by this ISA configuration"),',
        "}", ""
    ])
    if not projection.has_execution_provider:
        lines.extend(
            [
                "function event_from_fault(result : Execution_result) -> Event_record =",
                "  base_event_from_fault(result)",
                "",
                "function cpuid_flag_enabled(flag : Cpuid_flag, state : Cpu_state) -> bool =",
                "  base_cpuid_flag_enabled(flag, state)",
                "",
            ]
        )
    return "\n".join(lines)
