"""Values and operations owned by this specification boundary."""

from __future__ import annotations

from types import MappingProxyType

from collections.abc import Iterable
from engine.sail.registry import SailRegistryProjection


ROUTE_CONSTRUCTORS = MappingProxyType({
    "atomics": "RouteAtomics",
    "bounds": "RouteBounds",
    "cache": "RouteCache",
    "control_flow": "RouteControlFlow",
    "core_control": "RouteCoreControl",
    "data_movement": "RouteDataMovement",
    "ea_utility": "RouteEaUtility",
    "fpu": "RouteFpu",
    "fpu_transcendental_approx": "RouteFpuTranscendental",
    "integer_alu": "RouteIntegerAlu",
    "integer_bitfield": "RouteIntegerBitfield",
    "integer_mul_div": "RouteIntegerMulDiv",
    "integer_unary": "RouteIntegerUnary",
    "system_registers": "RouteSystemRegisters",
    "tlb_and_context": "RouteTlbContext",
    "vector": "RouteVector",
})

BASE_FAULT_CONSTRUCTORS = (
    "NoFault",
    "IllegalInstruction",
    "PrivilegeFault",
    "ExtensionUnavailable",
    "InvalidControlState",
    "InvalidControlSelectorFault",
    "ReservedControlBitsFault",
    "InvalidControlImageFault",
    "InvalidControlTransitionFault",
    "DivideByZero",
    "DivideOverflow",
    "BoundsFault",
    "AlignmentFault",
    "TranslationFault",
    "AccessFault",
    "EventFault",
)

BASE_EFFECT_CONSTRUCTORS = (
    "NoEffect",
    "ReadMemory",
    "WriteMemory",
    "AtomicMemory",
    "TranslateAddress",
    "CacheOperation",
    "TlbOperation",
    "ControlRegisterAccess",
    "EventDelivery",
    "TraceMarker",
    "HaltProcessor",
    "ResetProcessor",
    "RepeatBody",
    "FenceOperation",
    "IntegerCompute",
)


def render_sail_registry(
    projection: SailRegistryProjection, id_declarations: tuple[str, ...]
) -> str:
    routes = tuple(dict.fromkeys(item.instruction.route for item in projection.operations))
    cpuid_names = tuple(_cpuid_constructor(field) for field in projection.cpuid_flags)
    if len(set(cpuid_names)) != len(cpuid_names):
        raise ValueError("Sail CPUID constructor names collide across qualified fields")
    instruction_sets = ("BaseSet", *(item.instruction_set for item in projection.type_contributions if item.instruction_set is not None))
    fault_kinds = (*BASE_FAULT_CONSTRUCTORS, *(name for item in projection.type_contributions for name in item.fault_kinds))
    effect_kinds = (*BASE_EFFECT_CONSTRUCTORS, *(name for item in projection.type_contributions for name in item.effect_kinds))
    lines = [
        "// Generated from selected ISA catalogs. Do not edit.",
        "",
        *id_declarations,
        "",
        "enum Cpuid_flag =",
        *_constructors(cpuid_names),
        "",
        "enum Semantic_route =",
        *_constructors(ROUTE_CONSTRUCTORS[route] for route in routes),
        "",
        "enum Instruction_set =",
        *_constructors(instruction_sets),
        "",
        "enum Fault_kind =",
        *_constructors(fault_kinds),
        "",
        "enum Effect_kind =",
        *_constructors(effect_kinds),
        "",
        "enum Event_frame_type =",
        *_constructors(
            (
                "EventFrameBasic",
                "EventFrameError",
                "EventFramePage",
                "EventFrameAuxiliary",
            )
        ),
        "",
        "enum Event_family =",
        *_constructors(
            (
                "EventFamilyNone",
                *(f"EventFamily_{family}" for family in projection.event_families),
            )
        ),
        "",
        "enum Architectural_event =",
        *_constructors(f"Event_{item.event_id}" for item in projection.events),
        "",
        "enum Control_register =",
        *_constructors(_control_register_constructor(item.owner, item.id) for item in projection.control_registers),
        "",
        "struct Control_state = {",
        *(
            f"  {_control_register_state_field(item.owner, item.id)} : bits(64),"
            for item in projection.control_registers
            if item.generated_state
        ),
        "  interrupt_file : Interrupt_file,",
        "  debug_trigger_file : Debug_trigger_file,",
        "}",
        "",
        "enum Semantic_operation =",
        *_constructors(operation_constructor(item.instruction) for item in projection.operations),
        "",
        "function semantic_route(operation : Semantic_operation) -> Semantic_route = match operation {",
    ]
    lines.extend(
        f"  {operation_constructor(item.instruction)} => {ROUTE_CONSTRUCTORS[item.instruction.route]},"
        for item in projection.operations
    )
    lines.extend(
        [
            "}",
            "",
            "function semantic_mnemonic(operation : Semantic_operation) -> string = match operation {",
        ]
    )
    lines.extend(
        f'  {operation_constructor(item.instruction)} => "{item.instruction.mnemonic}",' for item in projection.operations
    )
    lines.extend(
        [
            "}",
            "",
            "function all_semantic_operations() -> list(Semantic_operation) = [|",
            "  " + ", ".join(operation_constructor(item.instruction) for item in projection.operations if item.reference in projection.active_operations),
            "|]",
            "",
            "function architectural_event_class(event : Architectural_event) -> bits(8) = match event {",
        ]
    )
    for item in projection.events:
        lines.append(f"  Event_{item.event_id} => 0x{item.class_value:02x},")
    lines.extend(
        [
            "}",
            "",
            "function architectural_event_selector(event : Architectural_event) -> option(bits(24)) = match event {",
        ]
    )
    lines.extend(
        f"  Event_{item.event_id} => "
        + (f"Some(0x{item.selector:06x})" if item.selector is not None else "None()")
        + ","
        for item in projection.events
    )
    lines.extend(
        [
            "}",
            "",
            "function architectural_event_frame(event : Architectural_event) -> Event_frame_type = match event {",
        ]
    )
    frame_constructors = {
        "basic": "EventFrameBasic",
        "error": "EventFrameError",
        "page": "EventFramePage",
        "auxiliary": "EventFrameAuxiliary",
    }
    lines.extend(
        f"  Event_{item.event_id} => {frame_constructors[item.frame]},"
        for item in projection.events
    )
    lines.extend(
        [
            "}",
            "",
            "function architectural_event_family(event : Architectural_event) -> Event_family = match event {",
        ]
    )
    lines.extend(
        f"  Event_{item.event_id} => "
        f"{f'EventFamily_{item.family}' if item.family is not None else 'EventFamilyNone'},"
        for item in projection.events
    )
    lines.extend(
        [
            "}",
            "",
            "function all_architectural_events() -> list(Architectural_event) = [|",
            "  " + ", ".join(f"Event_{item.event_id}" for item in projection.events if item.reference in projection.active_events),
            "|]",
            "",
        ]
    )
    lines.extend(
        [
            "function control_register_from_selector(selector : int) -> option(Control_register) =",
        ]
    )
    for index, item in enumerate(
        item for item in projection.control_registers
        if item.reference in projection.active_control_registers
    ):
        prefix = "  if" if index == 0 else "  else if"
        lines.append(
            f"{prefix} selector == {item.selector} then Some({_control_register_constructor(item.owner, item.id)})"
        )
    lines.extend(("  else None()", ""))
    lines.extend(("function initial_control_state() -> Control_state = struct {",))
    lines.extend(
        f"  {_control_register_state_field(item.owner, item.id)} = 0x0000000000000000,"
        for item in projection.control_registers
        if item.generated_state
    )
    lines.extend(
        (
            "  interrupt_file = initial_interrupt_file(),",
            "  debug_trigger_file = initial_debug_trigger_file(),",
            "}",
            "",
            "val control_state_value : (Control_state, Control_register) -> bits(64)",
            "",
            "scattered function control_state_value",
            "",
        )
    )
    lines.extend(
        f"function clause control_state_value(state, {_control_register_constructor(item.owner, item.id)}) = "
        f"state.{_control_register_state_field(item.owner, item.id)}"
        for item in projection.control_registers
        if item.generated_state
    )
    lines.extend(
        (
            "",
            "val control_state_with_value :",
            "  (Control_state, Control_register, bits(64)) -> Control_state",
            "",
            "scattered function control_state_with_value",
            "",
        )
    )
    lines.extend(
        f"function clause control_state_with_value(state, {_control_register_constructor(item.owner, item.id)}, value) = "
        f"{{ state with {_control_register_state_field(item.owner, item.id)} = value }}"
        for item in projection.control_registers
        if item.generated_state
    )
    lines.extend(("",))
    return "\n".join(lines)


def _constructors(values: Iterable[str]) -> list[str]:
    return [
        f"  {value}" if index == 0 else f"| {value}"
        for index, value in enumerate(values)
    ]


def operation_constructor(instruction) -> str:
    return f"Op_{instruction.mnemonic}"


def _control_register_constructor(owner: str, register_id: str) -> str:
    return f"ControlRegister_{owner.upper()}_{register_id}"


def _control_register_state_field(owner: str, register_id: str) -> str:
    return f"{owner.lower()}_{register_id.lower()}"


def _cpuid_constructor(field) -> str:
    return f"CpuidFlag_{field.id}"


def _instruction_set(owner: str, projection: SailRegistryProjection) -> str:
    if owner == "base":
        return "BaseSet"
    contribution = next(item for item in projection.type_contributions if item.owner == owner)
    if contribution.instruction_set is None:
        raise ValueError(f"{owner}: no declared Sail instruction-set binding")
    return contribution.instruction_set
