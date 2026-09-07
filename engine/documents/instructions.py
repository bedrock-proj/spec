"""Values and operations owned by this specification boundary."""

from __future__ import annotations

from dataclasses import dataclass
from engine.isa.catalog import InstructionBundle
from engine.isa.encoding import EncodingForm
from engine.isa.encoding_architecture import encoding_class_for_width


@dataclass(frozen=True, slots=True)
class InstructionBitSegment:
    label: str
    width: int
    fixed: bool


@dataclass(frozen=True, slots=True)
class InstructionByteProjection:
    index: int
    segments: tuple[InstructionBitSegment, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "segments", tuple(self.segments))



@dataclass(frozen=True, slots=True)
class InstructionFormatProjection:
    """One instruction form split into its reader-facing byte layout."""

    form: "EncodingForm"
    bytes: tuple[InstructionByteProjection, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "bytes", tuple(self.bytes))



def project_instruction_formats(
    bundle: InstructionBundle,
) -> tuple[InstructionFormatProjection, ...]:
    return tuple(
        InstructionFormatProjection(
            form,
            tuple(
                InstructionByteProjection(
                    index,
                    tuple(
                        InstructionBitSegment(
                            label,
                            width,
                            set(label) <= {"0", "1"},
                        )
                        for label, width in segments
                    ),
                )
                for index, segments in enumerate(
                    _instruction_entry_instruction_bytes(form)
                )
            ),
        )
        for form in bundle.encodings.forms
    )


def _instruction_entry_instruction_bytes(
    form: "EncodingForm",
) -> list[list[tuple[str, int]]]:
    owner = encoding_class_for_width(form.pattern.bit_width)
    segments = [(owner.fixed_prefix, len(owner.fixed_prefix))]
    if owner.length_bits:
        segments.append(("L", owner.length_bits))
    segments.extend(_instruction_entry_bit_segments(form.pattern.code))
    byte_segments = []
    for _ in range(owner.opcode_space_bytes):
        byte, segments = _instruction_entry_split_segments(segments, 8)
        byte_segments.append(byte)
    if segments:
        raise ValueError(f"encoding form {form.id!r} does not end at a byte boundary")
    for byte_index, byte in enumerate(byte_segments):
        byte_width = sum(segment_width for _label, segment_width in byte)
        if byte_width != 8:
            raise ValueError(
                f"encoding form {form.id!r} byte {byte_index} has "
                f"{byte_width} bits; expected 8"
            )
    return byte_segments


def _instruction_entry_bit_segments(bits: str) -> list[tuple[str, int]]:
    if not bits:
        return []
    segments: list[tuple[str, int]] = []
    start = 0

    def segment_class(character: str) -> str:
        return "fixed" if character in "01" else character

    current = segment_class(bits[0])
    for index, character in enumerate(bits[1:], start=1):
        kind = segment_class(character)
        if kind == current:
            continue
        chunk = bits[start:index]
        segments.append((chunk if current == "fixed" else chunk[0], len(chunk)))
        start = index
        current = kind
    chunk = bits[start:]
    segments.append((chunk if current == "fixed" else chunk[0], len(chunk)))
    return segments


def _instruction_entry_split_segments(
    segments: list[tuple[str, int]], width: int
) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    left: list[tuple[str, int]] = []
    right: list[tuple[str, int]] = []
    remaining = width
    for label, segment_width in segments:
        if remaining <= 0:
            right.append((label, segment_width))
        elif segment_width <= remaining:
            left.append((label, segment_width))
            remaining -= segment_width
        else:
            left_label = label
            right_label = label
            if len(label) == segment_width and set(label) <= {"0", "1"}:
                left_label = label[:remaining]
                right_label = label[remaining:]
            left.append((left_label, remaining))
            right.append((right_label, segment_width - remaining))
            remaining = 0
    return left, right


@dataclass(frozen=True, slots=True)
class InstructionMetadata:
    summary: str
    syntax: tuple[str, ...]
    privileged: bool
    required_cpuid_flags: tuple
    repeat: object

    def __post_init__(self) -> None:
        object.__setattr__(self, "syntax", tuple(self.syntax))



def project_instruction_metadata(bundle: InstructionBundle) -> InstructionMetadata:
    return InstructionMetadata(bundle.instruction.summary, tuple(form.syntax.code for form in bundle.encodings.forms), bundle.instruction.privileged, bundle.required_cpuid_flags, bundle.instruction.repeat)
