"""Parser and value objects for ELF relocation calculations."""

from __future__ import annotations

from types import MappingProxyType

from collections.abc import Mapping
from dataclasses import dataclass, field
import re

from engine.syntax.metasyntax import Metasyntax, MetasyntaxError


class RelocationMetasyntaxError(MetasyntaxError):
    """Raised when a relocation calculation is malformed."""


@dataclass(frozen=True, slots=True)
class RelocationExpression:
    """One typed relocation expression node."""

    kind: str
    name: str | None = None
    value: int | None = None
    operands: tuple["RelocationExpression", ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "operands", tuple(self.operands))



_TERMS = frozenset(
    {
        "addend",
        "got_base",
        "load_base",
        "next_pc",
        "place",
        "section_base",
        "symbol",
        "symbol_size",
    }
)
_FUNCTION_ARITY = MappingProxyType({
    "copy": 2,
    "got": 1,
    "plt": 1,
    "resolver": 1,
    "tls": 1,
    "tls_descriptor": 1,
    "tlsdesc": 1,
})


@dataclass(frozen=True, slots=True)
class RelocationMetasyntax(Metasyntax):
    """A validated relocation calculation and its parsed expression."""

    expression: RelocationExpression = field(init=False)

    def __post_init__(self) -> None:
        expression = _RelocationParser(self.code).parse()
        object.__setattr__(self, "expression", expression)

    def _validate(self) -> None:
        _RelocationParser(self.code).parse()

    def evaluate(self, context: Mapping[str, object]) -> int | tuple[int, int]:
        """Evaluate an integer relocation expression in one symbol context."""

        return _evaluate(self.expression, context)


class _RelocationParser:
    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or not code:
            raise RelocationMetasyntaxError(
                "relocation calculation must be a non-empty string"
            )
        self.code = code
        self.index = 0

    def fail(self, message: str) -> None:
        raise RelocationMetasyntaxError(
            f"relocation metasyntax: {message} at character {self.index + 1}"
        )

    def parse(self) -> RelocationExpression:
        expression = self.expression()
        self.space()
        if self.index != len(self.code):
            self.fail("expected end of expression")
        return expression

    def expression(self) -> RelocationExpression:
        result = self.unary()
        while True:
            self.space()
            if self.take("+"):
                result = RelocationExpression("add", operands=(result, self.unary()))
            elif self.take("-"):
                result = RelocationExpression("sub", operands=(result, self.unary()))
            else:
                return result

    def unary(self) -> RelocationExpression:
        self.space()
        if self.take("-"):
            return RelocationExpression("neg", operands=(self.unary(),))
        return self.primary()

    def primary(self) -> RelocationExpression:
        self.space()
        if self.take("("):
            result = self.expression()
            self.space()
            self.require(")")
            return result
        integer = re.match(r"(?:0[xX][0-9A-Fa-f]+|[0-9]+)", self.code[self.index :])
        if integer is not None:
            spelling = integer.group(0)
            self.index += len(spelling)
            return RelocationExpression("integer", value=int(spelling, 0))
        name = self.identifier()
        self.space()
        if not self.take("("):
            if name not in _TERMS:
                self.fail(f"unknown relocation term {name!r}")
            return RelocationExpression("term", name=name)
        operands: list[RelocationExpression] = []
        self.space()
        if not self.take(")"):
            while True:
                operands.append(self.expression())
                self.space()
                if self.take(")"):
                    break
                self.require(",")
        expected = _FUNCTION_ARITY.get(name)
        if expected is None:
            self.fail(f"unknown relocation function {name!r}")
        if len(operands) != expected:
            self.fail(
                f"relocation function {name!r} expects {expected} arguments"
            )
        return RelocationExpression("call", name=name, operands=tuple(operands))

    def identifier(self) -> str:
        match = re.match(r"[a-z][a-z0-9_]*", self.code[self.index :])
        if match is None:
            self.fail("expected relocation term")
        result = match.group(0)
        self.index += len(result)
        return result

    def space(self) -> None:
        while self.index < len(self.code) and self.code[self.index].isspace():
            self.index += 1

    def take(self, literal: str) -> bool:
        if self.code.startswith(literal, self.index):
            self.index += len(literal)
            return True
        return False

    def require(self, literal: str) -> None:
        if not self.take(literal):
            self.fail(f"expected {literal!r}")


def _evaluate(
    expression: RelocationExpression, context: Mapping[str, object]
) -> int | tuple[int, int]:
    if expression.kind == "integer":
        assert expression.value is not None
        return expression.value
    if expression.kind == "term":
        assert expression.name is not None
        try:
            return _integer_context(context[expression.name], expression.name)
        except KeyError as error:
            raise ValueError(
                f"missing relocation term {expression.name!r}"
            ) from error
    if expression.kind == "neg":
        value = _integer(_evaluate(expression.operands[0], context))
        return -value
    if expression.kind in {"add", "sub"}:
        left = _integer(_evaluate(expression.operands[0], context))
        right = _integer(_evaluate(expression.operands[1], context))
        return left + right if expression.kind == "add" else left - right
    assert expression.kind == "call" and expression.name is not None
    values = tuple(_integer(_evaluate(item, context)) for item in expression.operands)
    if expression.name in {"got", "plt", "tls", "tlsdesc"}:
        try:
            key = f"{expression.name}:{values[0]}"
            return _integer_context(context[key], key)
        except KeyError as error:
            raise ValueError(
                f"missing relocation function value {expression.name}({values[0]})"
            ) from error
    if expression.name == "resolver":
        resolver = context.get("resolver")
        if callable(resolver):
            return _integer_context(resolver(values[0]), "resolver")
        raise ValueError("relocation resolver context requires a callable resolver")
    if expression.name == "copy":
        return values[0]
    if expression.name == "tls_descriptor":
        return (
            _integer_context(
                context.get(f"tls_descriptor_function:{values[0]}", 0),
                "tls_descriptor_function",
            ),
            _integer_context(
                context.get(f"tls_descriptor_argument:{values[0]}", 0),
                "tls_descriptor_argument",
            ),
        )
    raise AssertionError(f"unhandled relocation function {expression.name}")


def _integer(value: int | tuple[int, int]) -> int:
    if isinstance(value, tuple):
        raise ValueError("relocation expression requires an integer operand")
    return value


def _integer_context(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"relocation context value {name!r} must be an integer")
    return value
