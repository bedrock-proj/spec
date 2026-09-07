"""Parser and value objects for the Bedrock instruction metasyntax."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from engine.syntax.metasyntax import Metasyntax, MetasyntaxError


class InstructionMetasyntaxError(MetasyntaxError):
    """Raised when an instruction metasyntax value is malformed."""


class InstructionMetasyntaxOperand:
    """Base class for one parsed instruction-metasyntax node."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class OperandReference(InstructionMetasyntaxOperand):
    """A named operand, optionally bound to an encoding-field marker."""

    name: str
    angled: bool = False
    field: str | None = None


@dataclass(frozen=True, slots=True)
class ScaleReference(InstructionMetasyntaxOperand):
    """The distinguished scale term inside an address expression."""

    angled: bool = False
    field: str | None = None


@dataclass(frozen=True, slots=True)
class DecimalLiteral(InstructionMetasyntaxOperand):
    """A decimal literal operand or address-expression member."""

    literal: int


@dataclass(frozen=True, slots=True)
class AddressOperator(InstructionMetasyntaxOperand):
    """An addition or multiplication operator inside an address expression."""

    operator: str


@dataclass(frozen=True, slots=True)
class AddressExpression(InstructionMetasyntaxOperand):
    """A top-level bracketed effective-address expression."""

    members: tuple[InstructionMetasyntaxOperand, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "members", tuple(self.members))



@dataclass(frozen=True, slots=True)
class LaneIndexExpression(InstructionMetasyntaxOperand):
    """A nested bracketed lane-index expression."""

    members: tuple[InstructionMetasyntaxOperand, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "members", tuple(self.members))



@dataclass(frozen=True, slots=True)
class BracedOperandGroup(InstructionMetasyntaxOperand):
    """A repeated operand group written with braces and an ellipsis."""

    name: str
    angled: bool = False


@dataclass(frozen=True, slots=True)
class ParenthesizedOperandGroup(InstructionMetasyntaxOperand):
    """An operand group written in parentheses."""

    name: str
    angled: bool = False


DisplayedInstructionOperand = OperandReference | DecimalLiteral


@dataclass(frozen=True, slots=True)
class _ParsedInstruction:
    mnemonic: str
    fixed_size_suffix: str | None
    selected_size_codes: tuple[str, ...]
    size_field: str | None
    order_field: str | None
    operands: tuple[InstructionMetasyntaxOperand, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "selected_size_codes", tuple(self.selected_size_codes))
        object.__setattr__(self, "operands", tuple(self.operands))



@dataclass(frozen=True, slots=True)
class InstructionMetasyntax(Metasyntax):
    """A validated instruction presentation template and its parsed structure."""

    mnemonic: str = field(init=False)
    fixed_size_suffix: str | None = field(init=False)
    selected_size_codes: tuple[str, ...] = field(init=False)
    size_field: str | None = field(init=False)
    order_field: str | None = field(init=False)
    operands: tuple[InstructionMetasyntaxOperand, ...] = field(init=False)

    def __post_init__(self) -> None:
        parsed = _InstructionMetasyntaxParser(self.code).parse()
        for name in (
            "mnemonic",
            "fixed_size_suffix",
            "selected_size_codes",
            "size_field",
            "order_field",
            "operands",
        ):
            object.__setattr__(self, name, getattr(parsed, name))

    def _validate(self) -> None:
        _InstructionMetasyntaxParser(self.code).parse()

    @property
    def displayed_operands(self) -> tuple[DisplayedInstructionOperand, ...]:
        """Return displayed operands with address references flattened."""

        def address_references(
            operand: InstructionMetasyntaxOperand,
        ) -> list[OperandReference]:
            if not isinstance(operand, (AddressExpression, LaneIndexExpression)):
                return []
            result: list[OperandReference] = []
            for member in operand.members:
                if isinstance(member, OperandReference):
                    result.append(member)
                elif isinstance(member, LaneIndexExpression):
                    result.extend(address_references(member))
            return result

        result: list[DisplayedInstructionOperand] = []
        for operand in self.operands:
            if isinstance(operand, AddressExpression):
                result.extend(address_references(operand))
            elif isinstance(operand, (OperandReference, DecimalLiteral)):
                result.append(operand)
        return tuple(result)

    @property
    def encoding_id(self) -> str:
        """Return the canonical local encoding ID derived without the mnemonic."""

        remainder = self.code[len(self.mnemonic) :]
        remainder = remainder.replace("+", "_add_").replace("*", "_mul_")
        normalized = re.sub(r"[^A-Za-z0-9_]+", "_", remainder)
        normalized = re.sub(r"_+", "_", normalized).strip("_").lower()
        return normalized or "plain"


class _InstructionMetasyntaxParser:
    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise InstructionMetasyntaxError(
                "instruction metasyntax must be a non-empty string"
            )
        self.value = value
        self.index = 0

    def fail(self, message: str) -> None:
        raise InstructionMetasyntaxError(
            f"instruction metasyntax: {message} at character {self.index + 1}"
        )

    def take(self, literal: str) -> bool:
        if self.value.startswith(literal, self.index):
            self.index += len(literal)
            return True
        return False

    def require(self, literal: str) -> None:
        if not self.take(literal):
            self.fail(f"expected {literal!r}")

    def identifier(self, label: str, *, mnemonic: bool = False) -> str:
        pattern = r"[A-Za-z][A-Za-z0-9]*" if mnemonic else r"[A-Za-z][A-Za-z0-9_]*"
        match = re.match(pattern, self.value[self.index :])
        if match is None:
            self.fail(f"expected {label}")
        result = match.group(0)
        self.index += len(result)
        return result

    def field_expression(self) -> str:
        self.require("(")
        if (
            self.index >= len(self.value)
            or self.value[self.index] not in "abcdefghijklmnopqrstuvwxyz"
        ):
            self.fail("expected lowercase field marker")
        marker = self.value[self.index]
        self.index += 1
        self.require(")")
        return marker

    def operand_reference(self) -> tuple[str, bool]:
        angled = self.take("<")
        name = self.identifier("operand name")
        if angled:
            self.require(">")
        return name, angled

    def address_expression(self, *, lane_index: bool) -> InstructionMetasyntaxOperand:
        members: list[InstructionMetasyntaxOperand] = []
        while True:
            if self.index >= len(self.value):
                self.fail("unterminated address expression")
            if self.take("]"):
                expression = LaneIndexExpression if lane_index else AddressExpression
                return expression(tuple(members))
            if self.value[self.index].isspace():
                self.index += 1
                continue
            if self.take("["):
                members.append(self.address_expression(lane_index=True))
                continue
            if self.value[self.index] in "+*":
                members.append(AddressOperator(self.value[self.index]))
                self.index += 1
                continue
            decimal = re.match(r"[0-9]+", self.value[self.index :])
            if decimal is not None:
                spelling = decimal.group(0)
                self.index += len(spelling)
                members.append(DecimalLiteral(int(spelling, 10)))
                continue
            name, angled = self.operand_reference()
            marker = (
                self.field_expression()
                if self.index < len(self.value) and self.value[self.index] == "("
                else None
            )
            if name == "scale":
                members.append(ScaleReference(angled=angled, field=marker))
            else:
                members.append(OperandReference(name, angled, marker))

    def operand(self) -> InstructionMetasyntaxOperand:
        if self.take("["):
            return self.address_expression(lane_index=False)
        if self.take("{ "):
            name, angled = self.operand_reference()
            self.require("... }")
            return BracedOperandGroup(name, angled)
        if self.take("("):
            name, angled = self.operand_reference()
            self.require(")")
            return ParenthesizedOperandGroup(name, angled)
        decimal = re.match(r"[0-9]+", self.value[self.index :])
        if decimal is not None:
            spelling = decimal.group(0)
            self.index += len(spelling)
            return DecimalLiteral(int(spelling, 10))
        name, angled = self.operand_reference()
        marker = (
            self.field_expression()
            if self.index < len(self.value) and self.value[self.index] == "("
            else None
        )
        return OperandReference(name, angled, marker)

    def parse(self) -> _ParsedInstruction:
        mnemonic = self.identifier("mnemonic name", mnemonic=True)
        fixed_size_suffix: str | None = None
        selected_size_codes: tuple[str, ...] = ()
        size_field: str | None = None
        order_field: str | None = None
        if self.take("."):
            if self.take("{"):
                codes = [self.identifier("public size suffix")]
                while self.take("|"):
                    codes.append(self.identifier("public size suffix"))
                self.require("}")
                if len(set(codes)) != len(codes):
                    self.fail("repeated public size suffix")
                selected_size_codes = tuple(codes)
                size_field = self.field_expression()
                if self.take("/order"):
                    order_field = self.field_expression()
            else:
                fixed_size_suffix = "." + self.identifier("fixed size suffix")
                if self.take("/order"):
                    order_field = self.field_expression()
        elif self.take("/order"):
            order_field = self.field_expression()

        operands: list[InstructionMetasyntaxOperand] = []
        if self.index < len(self.value):
            self.require(" ")
            operands.append(self.operand())
            while self.index < len(self.value):
                self.require(", ")
                operands.append(self.operand())
        if self.index != len(self.value):
            self.fail("unexpected text")
        return _ParsedInstruction(
            mnemonic=mnemonic,
            fixed_size_suffix=fixed_size_suffix,
            selected_size_codes=selected_size_codes,
            size_field=size_field,
            order_field=order_field,
            operands=tuple(operands),
        )
