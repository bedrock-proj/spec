"""Architectural opcode classes and named operator-space partitions."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType


class OperatorSpaceUnavailableError(ValueError):
    """The requested encoding class does not own the named operator space."""


@dataclass(frozen=True, slots=True)
class EncodingClass:
    """One opcode namespace in the instruction framing grammar."""

    name: str
    opcode_space_bytes: int
    fixed_prefix: str
    length_bits: int
    selectors: tuple[str, ...] = ()


    def __post_init__(self) -> None:
        object.__setattr__(self, "selectors", tuple(self.selectors))

    @property
    def framing_bits(self) -> int:
        return len(self.fixed_prefix) + self.length_bits

    @property
    def pattern_bits(self) -> int:
        return self.opcode_space_bytes * 8 - self.framing_bits

    @property
    def namespace(self) -> tuple[str, ...]:
        if not self.selectors:
            return ("?" * self.pattern_bits,)
        return tuple(
            selector.replace("x", "?") + "?" * (self.pattern_bits - len(selector))
            for selector in self.selectors
        )


@dataclass(frozen=True, slots=True)
class OperatorSpace:
    """A named prefix partition within one encoding class."""

    encoding_class: str
    name: str
    prefix: str


MIN_EXTENDED_RECORD_BYTES = 3
EXTENDED_LENGTH_BITS = 4
EXTENDED_CLASS_PREFIX_BITS = 8
MAX_RECORD_BYTES = MIN_EXTENDED_RECORD_BYTES + (1 << EXTENDED_LENGTH_BITS) - 1

ENCODING_CLASSES = (
    EncodingClass("extrashort", 1, "0", 0),
    EncodingClass("short", 2, "10", 0),
    EncodingClass(
        "medium",
        3,
        "11", EXTENDED_LENGTH_BITS,
        ("0xxxxxxx", "10xxxxxx", "110xxxxx", "1110xxxx"),
    ),
    EncodingClass("long", 4, "11", EXTENDED_LENGTH_BITS, ("11110xxx", "111110xx")),
    EncodingClass("extralong", 5, "11", EXTENDED_LENGTH_BITS, ("1111110x", "11111110")),
    EncodingClass("xxlong", 6, "11", EXTENDED_LENGTH_BITS, ("11111111",)),
)
ENCODING_CLASSES_BY_NAME = MappingProxyType({item.name: item for item in ENCODING_CLASSES})
ENCODING_CLASSES_BY_WIDTH = MappingProxyType({item.pattern_bits: item for item in ENCODING_CLASSES})

OPERATOR_SPACES = (
    OperatorSpace("extralong", "base", "111111000?"),
    OperatorSpace("extralong", "fpu", "111111001?"),
    OperatorSpace("extralong", "vector", "11111101??"),
    OperatorSpace("xxlong", "vector", "1111111100"),
    OperatorSpace("xxlong", "wait", "1111111101"),
)

OPERATOR_SPACE_PREFIX_BITS = 10


def encoding_class(value: str) -> EncodingClass:
    """Resolve an encoding class by its architectural name."""

    result = ENCODING_CLASSES_BY_NAME.get(value)
    if result is None:
        names = ", ".join(item.name for item in ENCODING_CLASSES)
        raise ValueError(f"unknown encoding class {value!r}; choose one of: {names}")
    return result


def encoding_class_for_width(width: int) -> EncodingClass:
    """Resolve the class of an unframed opcode pattern."""
    try:
        return ENCODING_CLASSES_BY_WIDTH[width]
    except KeyError as error:
        raise ValueError(f"pattern width {width} has no encoding class") from error


def encoding_class_for_extended_prefix(prefix: int) -> EncodingClass:
    """Classify the eight opcode-prefix bits of an extended header."""
    if isinstance(prefix, bool) or not isinstance(prefix, int) or not 0 <= prefix < (1 << EXTENDED_CLASS_PREFIX_BITS):
        raise ValueError("extended opcode prefix must be an unsigned eight-bit value")
    pattern = format(prefix, f"0{EXTENDED_CLASS_PREFIX_BITS}b")
    matches = tuple(owner for owner in ENCODING_CLASSES if owner.length_bits and any(
        _pattern_subset(pattern, namespace[:EXTENDED_CLASS_PREFIX_BITS]) for namespace in owner.namespace
    ))
    if len(matches) != 1:
        raise ValueError("extended opcode prefix does not identify one encoding class")
    return matches[0]


def frame_opcode(owner: EncodingClass, opcode_value: int, encoded_bytes: int) -> tuple[int, ...]:
    """Pack prefix, byte-oriented length, and opcode in wire byte order."""
    if isinstance(opcode_value, bool) or not isinstance(opcode_value, int) or not 0 <= opcode_value < (1 << owner.pattern_bits):
        raise ValueError(f"opcode value does not fit the {owner.name} pattern width")
    pattern = format(opcode_value, f"0{owner.pattern_bits}b")
    if not any(_pattern_subset(pattern, namespace) for namespace in owner.namespace):
        raise ValueError(f"opcode value is outside the {owner.name} namespace")
    if isinstance(encoded_bytes, bool) or not isinstance(encoded_bytes, int):
        raise ValueError("encoded instruction length must be an integer")
    if owner.length_bits:
        if not owner.opcode_space_bytes <= encoded_bytes <= MAX_RECORD_BYTES:
            raise ValueError(f"{owner.name} encoded length must be {owner.opcode_space_bytes}..{MAX_RECORD_BYTES} bytes")
        length = encoded_bytes - MIN_EXTENDED_RECORD_BYTES
    else:
        if encoded_bytes != owner.opcode_space_bytes:
            raise ValueError(f"{owner.name} encoded length must be exactly {owner.opcode_space_bytes} bytes")
        length = 0
    prefix_shift = owner.pattern_bits + owner.length_bits
    framed = (int(owner.fixed_prefix, 2) << prefix_shift) | (length << owner.pattern_bits) | opcode_value
    return tuple(framed.to_bytes(owner.opcode_space_bytes, "big"))


def operator_space(class_name: str, name: str) -> OperatorSpace:
    """Resolve a named operator space scoped by encoding class."""

    result = next(
        (
            item
            for item in OPERATOR_SPACES
            if item.encoding_class == class_name and item.name == name
        ),
        None,
    )
    if result is None:
        available = sorted(
            item.name for item in OPERATOR_SPACES if item.encoding_class == class_name
        )
        suffix = f"; choose one of: {', '.join(available)}" if available else ""
        raise OperatorSpaceUnavailableError(
            f"encoding class {class_name!r} has no operator space {name!r}{suffix}"
        )
    return result


def _patterns_overlap(left: str, right: str) -> bool:
    return all(
        a == "?" or b == "?" or a == b
        for a, b in zip(left.replace("x", "?"), right.replace("x", "?"), strict=True)
    )


def _pattern_subset(pattern: str, container: str) -> bool:
    return all(
        outer in "x?" or inner == outer
        for inner, outer in zip(pattern, container, strict=True)
    )


def _validate() -> None:
    if len(ENCODING_CLASSES_BY_NAME) != len(ENCODING_CLASSES):
        raise ValueError("duplicate encoding class name")
    if len(ENCODING_CLASSES_BY_WIDTH) != len(ENCODING_CLASSES):
        raise ValueError("duplicate encoding class width")
    for item in ENCODING_CLASSES:
        if any(len(pattern) != item.pattern_bits for pattern in item.namespace):
            raise ValueError(f"{item.name}: namespace width mismatch")
    for index, space in enumerate(OPERATOR_SPACES):
        owner = encoding_class(space.encoding_class)
        pattern = space.prefix.replace("x", "?") + "?" * (
            owner.pattern_bits - len(space.prefix)
        )
        if len(space.prefix) > owner.pattern_bits or set(space.prefix) - set("01x?"):
            raise ValueError(f"{space.name}: invalid operator-space prefix")
        if not any(
            _pattern_subset(pattern, namespace) for namespace in owner.namespace
        ):
            raise ValueError(f"{space.name}: prefix is outside {owner.name}")
        for previous in OPERATOR_SPACES[:index]:
            if previous.encoding_class == space.encoding_class and _patterns_overlap(
                previous.prefix, space.prefix
            ):
                raise ValueError(f"overlapping operator spaces: {previous}, {space}")


_validate()
