#!/usr/bin/env python3
"""Call-layout projection of a resolved C calling-convention object."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from collections.abc import Mapping

from engine.reference import Reference

from typing import TYPE_CHECKING

from .project import (
    CAbiProject,
    RegisterClass,
    ResolvedCallingConvention,
    ResolvedRegisterClass,
    ResolvedValueClass,
)

if TYPE_CHECKING:
    from engine.isa.registers import Register


@dataclass(frozen=True, slots=True)
class Argument:
    name: str
    kind: str
    named: bool = True
    size: int | None = None


@dataclass(frozen=True, slots=True)
class ReturnValue:
    kind: str
    size: int | None = None


@dataclass(frozen=True, slots=True)
class Call:
    arguments: tuple[Argument, ...]
    return_value: ReturnValue
    variadic: bool = False
    prototyped: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", tuple(self.arguments))




@dataclass(frozen=True, slots=True)
class RegisterLocation:
    registers: tuple[Register, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "registers", tuple(self.registers))



@dataclass(frozen=True, slots=True)
class StackLocation:
    register: Register
    offset_bytes: int


@dataclass(frozen=True, slots=True)
class ArgumentAssignment:
    argument: Argument
    effective_kind: str
    mode: str
    location: RegisterLocation | StackLocation


@dataclass(frozen=True, slots=True)
class CallLayout:
    sret: Register | None
    return_location: RegisterLocation | None
    arguments: tuple[ArgumentAssignment, ...]
    stack_size: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", tuple(self.arguments))



def check_call(call: Call, rules: ResolvedCallingConvention) -> None:
    if not isinstance(call.variadic, bool) or not isinstance(call.prototyped, bool):
        raise ValueError("variadic and prototyped must be boolean")
    if call.variadic and not call.prototyped:
        raise ValueError("a call cannot be both variadic and unprototyped")
    unnamed = False
    for argument in call.arguments:
        if not isinstance(argument.name, str) or not argument.name:
            raise ValueError("argument name must be nonempty")
        rules.value_class(argument.kind)
        _positive_aggregate_size(argument.kind, argument.size, argument.name)
        if not isinstance(argument.named, bool):
            raise ValueError(f"argument {argument.name}: named must be boolean")
        if argument.named and unnamed:
            raise ValueError("named arguments must precede variadic arguments")
        if not argument.named:
            if not call.variadic:
                raise ValueError("unnamed arguments require a variadic call")
            unnamed = True
    if call.return_value.kind != "void":
        rules.value_class(call.return_value.kind)
    _positive_aggregate_size(call.return_value.kind, call.return_value.size, "return value")


def _positive_aggregate_size(kind: str, size: int | None, context: str) -> None:
    if kind == "aggregate":
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ValueError(f"{context}: aggregate size must be a positive integer")
    elif size is not None:
        raise ValueError(f"{context}: size is valid only for aggregate values")


def _argument(data: dict[str, Any], index: int, rules: ResolvedCallingConvention) -> Argument:
    if not isinstance(data, Mapping):
        raise ValueError(f"argument {index}: expected an object")
    name = data.get("name")
    kind = data.get("kind")
    if not isinstance(name, str) or not name:
        raise ValueError(f"argument {index}: name must be a nonempty string")
    if not isinstance(kind, str) or kind not in rules.value_classes:
        raise ValueError(f"argument {name}: unsupported kind {kind!r}")
    named = data.get("named", True)
    if not isinstance(named, bool):
        raise ValueError(f"argument {name}: named must be boolean")
    size = data.get("size")
    _positive_aggregate_size(kind, size, f"argument {name}")
    return Argument(name=name, kind=kind, named=named, size=size)


def _return_value(data: dict[str, Any], rules: ResolvedCallingConvention) -> ReturnValue:
    kind = data.get("kind")
    if kind != "void" and (
        not isinstance(kind, str) or kind not in rules.value_classes
    ):
        raise ValueError(f"return value: unsupported kind {kind!r}")
    size = data.get("size")
    _positive_aggregate_size(kind, size, "return value")
    return ReturnValue(kind=kind, size=size)


def parse_call(data: dict[str, Any], rules: ResolvedCallingConvention) -> Call:
    if not isinstance(data, Mapping):
        raise ValueError("call must be an object")
    arguments_data = data.get("arguments", [])
    if not isinstance(arguments_data, list):
        raise ValueError("call arguments must be a list")
    arguments = tuple(
        _argument(item, index, rules) for index, item in enumerate(arguments_data)
    )
    return_data = data.get("return", {"kind": "void"})
    if not isinstance(return_data, dict):
        raise ValueError("call return must be an object")
    variadic = data.get("variadic", False)
    prototyped = data.get("prototyped", True)
    if not isinstance(variadic, bool) or not isinstance(prototyped, bool):
        raise ValueError("variadic and prototyped must be boolean")
    if variadic and not prototyped:
        raise ValueError("a call cannot be both variadic and unprototyped")
    if not variadic and any(not argument.named for argument in arguments):
        raise ValueError("unnamed arguments require a variadic call")
    seen_unnamed = False
    for argument in arguments:
        if argument.named:
            if seen_unnamed:
                raise ValueError("named arguments must precede variadic arguments")
        else:
            seen_unnamed = True
    call = Call(
        arguments=arguments,
        return_value=_return_value(return_data, rules),
        variadic=variadic,
        prototyped=prototyped,
    )
    check_call(call, rules)
    return call


def _uses_sret(value: ReturnValue, rules: ResolvedCallingConvention) -> bool:
    if value.kind == "void":
        return False
    policy = rules.value_class(value.kind).definition.result
    if policy.mode == "sret":
        return True
    if policy.mode == "size_dependent":
        assert policy.direct_maximum_bytes is not None
        assert value.size is not None
        return value.size > policy.direct_maximum_bytes
    return False






def return_location(
    value: ReturnValue, rules: ResolvedCallingConvention
) -> RegisterLocation | None:
    """Return the canonical result registers selected by the ABI policy."""

    _positive_aggregate_size(value.kind, value.size, "return value")
    if value.kind == "void":
        return None
    if _uses_sret(value, rules):
        return RegisterLocation((rules.sret_register,))
    value_class = rules.value_class(value.kind)
    return RegisterLocation(value_class.result_registers)


def layout_call(call: Call, rules: ResolvedCallingConvention) -> CallLayout:
    """Project one call signature through the resolved calling convention."""

    check_call(call, rules)
    stack = rules.definition.stack
    uses_sret = _uses_sret(call.return_value, rules)
    cursors = {reference: 0 for reference in rules.register_classes}
    exhausted: set[Reference[RegisterClass]] = set()
    general = next(
        item for item in rules.register_classes.values()
        if rules.sret_register in item.arguments
    )
    if uses_sret:
        cursors[general.definition.reference] = 1
    next_stack_offset = stack.first_argument_offset_bytes
    assignments: list[ArgumentAssignment] = []

    def stack_location() -> StackLocation:
        nonlocal next_stack_offset
        location = StackLocation(rules.stack_pointer, next_stack_offset)
        next_stack_offset += stack.argument_slot_bytes
        return location

    def assign_registers(
        register_class: ResolvedRegisterClass,
        *,
        units: int,
        alignment: int,
        kind: str,
    ) -> RegisterLocation | None:
        reference = register_class.definition.reference
        cursor = cursors[reference]
        aligned = ((cursor + alignment - 1) // alignment) * alignment
        if reference in exhausted or aligned + units > len(register_class.arguments):
            if register_class.definition.exhaustion == "permanent":
                exhausted.add(reference)
                cursors[reference] = len(register_class.arguments)
            return None
        selected = register_class.arguments[aligned : aligned + units]
        cursors[reference] = aligned + units
        return RegisterLocation(selected)

    for argument in call.arguments:
        force_stack = (not call.prototyped) or (call.variadic and not argument.named)
        effective_kind = (
            rules.promotions.get(argument.kind, argument.kind)
            if force_stack
            else argument.kind
        )
        value_class = rules.value_class(effective_kind)
        policy = value_class.definition.argument
        mode = policy.mode
        location: RegisterLocation | StackLocation | None

        if force_stack:
            if policy.mode == "copy_address" or effective_kind in {"vector", "predicate"}:
                mode = "copy_address"
            location = stack_location()
        else:
            register_class = value_class.argument_register_class
            location = assign_registers(
                register_class,
                units=policy.units,
                alignment=policy.alignment_units,
                kind=effective_kind,
            )
            if (
                location is None
                and register_class.definition.exhaustion == "indirect"
            ):
                mode = "copy_address"
                location = assign_registers(
                    general, units=1, alignment=1, kind="pointer"
                )
            if location is None:
                location = stack_location()

        assert location is not None

        assignments.append(ArgumentAssignment(argument, effective_kind, mode, location))

    return CallLayout(
        rules.sret_register if uses_sret else None,
        return_location(call.return_value, rules), tuple(assignments),
        next_stack_offset - stack.first_argument_offset_bytes,
    )
