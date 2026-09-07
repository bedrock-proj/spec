"""Memory-record drawing geometry for the ISA manual."""

from __future__ import annotations

from engine.documents.fragments.memory_records import MemoryRecordComponentProjection
from engine.isa.memory_records import ElementByteSize
from engine.isa.memory_records import LinearByteExpression
from engine.isa.memory_records import MemoryRecord
from engine.isa.memory_records import MemoryRecordBitField
from engine.isa.memory_records import MemoryRecordComponent
from dataclasses import dataclass
from fractions import Fraction
from artifacts._shared.latex import tex_escape
from engine.isa.memory_records import (
    ElementByteSize,
    MemoryRecord,
    MemoryRecordComponent,
)
from engine.documents.fragments.memory_records import MemoryRecordProjection


@dataclass(frozen=True, slots=True)
class MemoryRecordRowProjection:
    """One visible member row or one omitted middle run."""

    index: int | None
    height: Fraction


def render_memory_record(projection: MemoryRecordProjection) -> str:
    record = projection.record
    lines = [_shape_sentence(record), "", *_render_record_diagram(projection)]
    formatted = tuple(
        component
        for component in projection.components
        if component.component.bit_format is not None
    )
    if formatted:
        lines.extend(("", *_render_bit_formats(record, formatted)))
    return "\n".join(lines)


def _row_height(record: MemoryRecord, size: ElementByteSize) -> Fraction:
    parameter_value = (
        record.parameter.values[0] if record.parameter is not None else None
    )
    return max(Fraction(1), Fraction(size.evaluate(parameter_value), 8))


def _render_record_diagram(projection: MemoryRecordProjection) -> list[str]:
    record = projection.record
    component_rows = tuple(
        _rows(record, item.component) for item in projection.components
    )
    padding_height = (
        max(Fraction(1), Fraction(min(projection.padding.values), 8))
        if projection.padding is not None
        else Fraction(0)
    )
    height = sum(
        (row.height for rows in component_rows for row in rows), start=padding_height
    )
    needspace = float(Fraction(4, 5) + height * Fraction(4, 25))
    lines = [
        rf"\begin{{BedrockMemoryRecordDiagram}}{{{tex_escape(record.name)}}}"
        rf"{{{needspace:.2f}in}}"
    ]
    for projected, rows in zip(projection.components, component_rows):
        component = projected.component
        offset = _offset_tex(projected.offset)
        if component.count == 1:
            label = (
                f"{tex_escape(component.label)} "
                f"({_element_size_tex(component.element_bytes)} bytes"
                f"{'; 0' if component.fixed_value == 'zero' else ''})"
            )
            macro = (
                "BedrockMemoryRecordZeroSlot"
                if component.fixed_value == "zero"
                else "BedrockMemoryRecordSlot"
            )
            lines.append(
                rf"\{macro}{{{offset}}}{{{_number(rows[0].height)}}}" rf"{{{label}}}"
            )
            continue

        aggregate = _linear_tex(projected.size, constants_as_hex=False)
        lines.append(
            rf"\BedrockMemoryRecordSeriesBegin{{{offset}}}" rf"{{${aggregate}$ bytes}}"
        )
        for row in rows:
            if row.index is None:
                lines.append(
                    rf"\BedrockMemoryRecordSeriesEllipsis" rf"{{{_number(row.height)}}}"
                )
                continue
            assert row.index is not None
            label = (
                f"{tex_escape(component.label)}{row.index} "
                f"({_element_size_tex(component.element_bytes)} bytes)"
            )
            lines.append(
                rf"\BedrockMemoryRecordSeriesRow{{{_number(row.height)}}}"
                rf"{{{label}}}"
            )
        lines.append(r"\BedrockMemoryRecordSeriesEnd")

    if projection.padding is not None:
        values = _value_list(projection.padding.values)
        lines.append(
            rf"\BedrockMemoryRecordZeroSlot{{{_offset_tex(projection.padding.offset)}}}"
            rf"{{{_number(padding_height)}}}"
            rf"{{Padding ({values} bytes; 0)}}"
        )
    lines.append(r"\end{BedrockMemoryRecordDiagram}")
    return lines


def _number(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{float(value):.3f}".rstrip("0").rstrip(".")


def _rows(
    record: MemoryRecord, component: MemoryRecordComponent
) -> tuple[MemoryRecordRowProjection, ...]:
    indexes = (
        tuple(range(component.count))
        if component.count <= 5
        else (0, 1, None, component.count - 2, component.count - 1)
    )
    height = _row_height(record, component.element_bytes)
    return tuple(MemoryRecordRowProjection(index, height) for index in indexes)


def _shape_sentence(record: MemoryRecord) -> str:
    alignment = record.alignment_bytes
    if record.parameter is None:
        total = record.total_bytes()
        return f"This record is {total} bytes and {alignment}-byte aligned."
    parameter = record.parameter
    values = ", ".join(str(value) for value in parameter.values)
    total = _aligned_total_tex(record)
    return (
        rf"Let ${tex_escape(parameter.id)}$ be {tex_escape(parameter.description)}, "
        rf"with ${tex_escape(parameter.id)} \in \{{{values}\}}$. "
        f"This record is {alignment}-byte aligned and has total size "
        rf"${total}$ bytes."
    )


def _aligned_total_tex(record: MemoryRecord) -> str:
    expression = record.payload_expression
    alignment = record.alignment_bytes
    if not expression.coefficient:
        return str(record.total_bytes())
    parameter = tex_escape(expression.parameter or "")
    if expression.constant % alignment == 0:
        prefix = (
            rf"\texttt{{{_hex(expression.constant)}}} + " if expression.constant else ""
        )
        numerator = expression.coefficient.numerator
        denominator = expression.coefficient.denominator * alignment
        term = _fractional_parameter_tex(numerator, denominator, parameter)
        return rf"{prefix}{alignment}\lceil {term}\rceil"
    inner = _linear_tex(expression, constants_as_hex=True)
    return rf"{alignment}\lceil ({inner})/{alignment}\rceil"


def _render_bit_formats(
    record: MemoryRecord,
    components: tuple[MemoryRecordComponentProjection, ...],
) -> list[str]:
    lines = [
        rf"\begin{{BedrockListedFormatDiagram}}"
        rf"{{{tex_escape(record.name)} Bit Layouts}}",
        r"\BedrockMemoryRecordFormatSpacing",
    ]
    for projected in components:
        component = projected.component
        bit_format = component.bit_format
        assert bit_format is not None
        fields = tuple(
            sorted(bit_format.fields, key=lambda field: field.lsb, reverse=True)
        )
        label = _format_row_label(component, fields, bit_format.bits)
        lines.append(
            rf"\BedrockFormatRowRange{{{label}}}" rf"{{{bit_format.bits - 1}}}{{0}}{{%"
        )
        cursor = bit_format.bits
        for field in fields:
            gap = cursor - field.msb - 1
            if gap:
                lines.append(rf"\BedrockFormatReserved{{0}}{{{gap}}}")
            display = tex_escape(field.diagram_label or field.label)
            lines.append(rf"\BedrockFormatField{{{display}}}{{{field.bits}}}")
            cursor = field.lsb
        if cursor:
            lines.append(rf"\BedrockFormatReserved{{0}}{{{cursor}}}")
        lines.append("}")
    lines.append(r"\end{BedrockListedFormatDiagram}")
    return lines


def _format_row_label(
    component: MemoryRecordComponent,
    fields: tuple[MemoryRecordBitField, ...],
    bits: int,
) -> str:
    title = f"{tex_escape(component.label)}[{bits - 1}:0]"
    legends = []
    for field in sorted(fields, key=lambda item: item.lsb):
        if field.diagram_label is None or field.diagram_label == field.label:
            continue
        bit_range = str(field.lsb) if field.bits == 1 else f"{field.msb}:{field.lsb}"
        legends.append(
            rf"{tex_escape(field.diagram_label)} = "
            rf"{tex_escape(field.label)}[{bit_range}]"
        )
    if not legends:
        return title
    return r"\shortstack{" + r"\\".join((title, *legends)) + "}"


def _offset_tex(expression: LinearByteExpression) -> str:
    if not expression.coefficient:
        return rf"\texttt{{{_hex(expression.constant)}}}"
    return rf"${_linear_tex(expression, constants_as_hex=True)}$"


def _element_size_tex(size: ElementByteSize) -> str:
    if size.fixed is not None:
        return str(size.fixed)
    assert size.parameter is not None
    parameter = tex_escape(size.parameter)
    if size.divisor == 1:
        return rf"${parameter}$"
    return rf"${parameter}/{size.divisor}$"


def _linear_tex(expression: LinearByteExpression, *, constants_as_hex: bool) -> str:
    parts: list[str] = []
    if expression.constant:
        parts.append(
            rf"\texttt{{{_hex(expression.constant)}}}"
            if constants_as_hex
            else str(expression.constant)
        )
    if expression.coefficient:
        parameter = tex_escape(expression.parameter or "")
        coefficient = expression.coefficient
        parts.append(
            _fractional_parameter_tex(
                coefficient.numerator, coefficient.denominator, parameter
            )
        )
    return " + ".join(parts) if parts else "0"


def _fractional_parameter_tex(numerator: int, denominator: int, parameter: str) -> str:
    numerator_text = parameter if numerator == 1 else f"{numerator}{parameter}"
    if denominator == 1:
        return numerator_text
    return f"{numerator_text}/{denominator}"


def _hex(value: int) -> str:
    width = max(2, len(f"{value:x}"))
    return f"0x{value:0{width}x}"


def _value_list(values: tuple[int, ...]) -> str:
    if len(values) == 1:
        return str(values[0])
    if len(values) == 2:
        return f"{values[0]} or {values[1]}"
    return ", ".join(str(value) for value in values[:-1]) + f", or {values[-1]}"
