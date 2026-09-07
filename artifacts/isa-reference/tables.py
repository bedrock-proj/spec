"""ISA manual tables built from engine-selected content."""

from artifacts._shared.latex import tex_escape
from engine.documents.fragments.events import EventCodeRow
from engine.isa.events import ArchitecturalEvent


def render_implementation_disclosure(projection):
    rows = tuple(f"{tex_escape(item.item)} & {tex_escape(', '.join(item.defining_rules))} & {tex_escape(item.publication)}" + r"\\" for item in projection.disclosures)
    return "\n".join(
        [
            r"\Needspace{3.0in}",
            r"\BedrockTableCaption{Implementation-Defined Disclosure Register}",
            r"\begin{BedrockLongTable}{@{}>{\raggedright\arraybackslash}p{1.35in}>{\raggedright\arraybackslash}p{2.20in}>{\raggedright\arraybackslash}p{2.10in}@{}}",
            r"\toprule",
            r"\rowcolor{BedrockHeaderFill}",
            r"\textbf{Item} & \textbf{Defining rule} & \textbf{Publication}\\",
            r"\midrule",
            r"\endfirsthead",
            r"\multicolumn{3}{l}{\scriptsize\itshape Table \theBedrockTable\ (continued)}\\",
            r"\toprule",
            r"\rowcolor{BedrockHeaderFill}",
            r"\textbf{Item} & \textbf{Defining rule} & \textbf{Publication}\\",
            r"\midrule",
            r"\endhead",
            *rows,
            r"\bottomrule",
            r"\end{BedrockLongTable}",
        ]
    )


def render_control_register_reference(projection):
    rows = tuple(rf"\texttt{{0x{item.selector:04X}}} & {_identifier(item.id)} & {tex_escape(item.summary)}" + r"\\" for item in projection.registers)
    return "\n".join(
        [
            r"\Needspace{1.25in}",
            r"\BedrockTableCaption{Control-Register Selector Assignments}",
            r"\begin{BedrockLongTable}{@{}>{\raggedright\arraybackslash}p{0.75in}>{\raggedright\arraybackslash}p{1.05in}>{\raggedright\arraybackslash}p{3.55in}@{}}",
            r"\toprule",
            r"\rowcolor{BedrockHeaderFill}",
            r"\textbf{Selector} & \textbf{Register} & \textbf{Use}\\",
            r"\midrule",
            r"\endfirsthead",
            r"\multicolumn{3}{@{}l}{\scriptsize\itshape Table \theBedrockTable\ (continued)}\\",
            r"\toprule",
            r"\rowcolor{BedrockHeaderFill}",
            r"\textbf{Selector} & \textbf{Register} & \textbf{Use}\\",
            r"\midrule",
            r"\endhead",
            *rows,
            r"\bottomrule",
            r"\end{BedrockLongTable}",
        ]
    )


def render_code_reference_event_reference(classes):
    return "\n".join(
        [
            r"\begin{BedrockListedFormatDiagram}{Architectural Event Code}",
            r"\BedrockFormatRow{EVENT\_CODE[31:0]}{%",
            r"\BedrockFormatField{CLASS}{8}",
            r"\BedrockFormatField{SELECTOR}{24}",
            "}",
            r"\end{BedrockListedFormatDiagram}",
            "",
            r"\BedrockTableCaption{Architectural Event Classes}",
            r"\begin{BedrockTabular}{@{}>{\raggedright\arraybackslash}p{0.60in}>{\raggedright\arraybackslash}p{1.10in}>{\raggedright\arraybackslash}p{1.65in}>{\raggedright\arraybackslash}p{2.10in}@{}}",
            r"\toprule",
            r"\rowcolor{BedrockHeaderFill}",
            r"\textbf{Value} & \textbf{Class} & \textbf{Name} & \textbf{Selector policy}\\",
            r"\midrule",
            *classes,
            r"\bottomrule",
            r"\end{BedrockTabular}",
            "",
            r"Fixed selectors are assigned by this specification; platform and source selectors are supplied by the corresponding event source.",
        ]
    )


def render_cpuid_leaf(caption, explanation, rows, diagram_rows):
    return "\n".join(
        (
            explanation,
            "",
            rf"\BedrockTableCaption{{{caption} CPUID Query-Index Assignments}}",
            r"\begin{BedrockTabular}{@{}>{\raggedright\arraybackslash}p{1.55in}>{\raggedright\arraybackslash}p{4.10in}@{}}",
            r"\toprule",
            r"\rowcolor{BedrockHeaderFill}",
            r"\textbf{Index} & \textbf{Query}\\",
            r"\midrule",
            *rows,
            r"\bottomrule",
            r"\end{BedrockTabular}",
            "",
            rf"\begin{{BedrockListedFormatDiagram}}{{{caption} CPUID Result Formats}}",
            *diagram_rows,
            r"\end{BedrockListedFormatDiagram}",
        )
    )


def _identifier(value: str) -> str:
    escaped = tex_escape(value).replace(r"\_", r"\_\allowbreak{}")
    return rf"\texttt{{{escaped}}}"


def _code(row: EventCodeRow) -> str:
    return rf"\texttt{{0x{row.code:08X}}}"


def event_target_event_reference(event: ArchitecturalEvent) -> str:
    """Return the label owned by one public normative event row."""

    return f"event:{event.id.lower().replace('_', '-')}"


from engine.documents.fragments.cpuid import CpuidLeafProjection, ProjectedCpuidQuery
from engine.isa.cpuid import CpuidField
from engine.reference import Reference

def _index(first: int, last: int, stride: int) -> str:
    if first == last:
        return f"0x{first:04X}"
    result = f"0x{first:04X}--0x{last:04X}"
    return result if stride == 1 else f"{result}/{stride}"


def _diagram_labels(fields: tuple[CpuidField, ...]) -> dict[Reference[object], str]:
    labels: dict[Reference[object], str] = {}
    used: set[str] = set()
    for field in fields:
        candidates = [
            field.id,
            "".join(part[0] for part in field.id.split("_") if part),
            *(character for character in field.id if character != "_"),
        ]
        label = next(
            (
                candidate
                for candidate in candidates
                if candidate and len(candidate) <= field.bits and candidate not in used
            ),
            "?",
        )
        if label == "?":
            raise ValueError(
                f"cannot assign a CPUID diagram label to {field.reference!r}"
            )
        labels[field.reference] = label
        used.add(label)
    return labels


def _bit_range(field: CpuidField) -> str:
    return str(field.lsb) if field.bits == 1 else f"{field.msb}:{field.lsb}"


def _reserved_field(bits: int) -> str:
    label = "R" if bits < 8 else "reserved"
    return rf"\BedrockFormatReserved{{{label}}}{{{bits}}}"


def _query_diagram_label(
    query: ProjectedCpuidQuery,
    fields: tuple[CpuidField, ...],
    labels: dict[Reference[object], str],
) -> str:
    lines = [
        rf"index {_identifier(_index(query.first, query.last, query.stride))}: "
        f"{_identifier(query.id)}"
    ]
    entries = []
    for field in fields:
        label = labels[field.reference]
        name = (
            _identifier(field.id)
            if label == field.id
            else f"{_identifier(label)} = {_identifier(field.id)}"
        )
        entries.append(f"{name}[{_identifier(_bit_range(field))}]")
    lines.extend(
        ", ".join(entries[index : index + 2]) for index in range(0, len(entries), 2)
    )
    return r"\shortstack{" + r"\\".join(lines) + "}"


def _format_fields(
    fields: tuple[CpuidField, ...],
    labels: dict[Reference[object], str],
    target_labels,
) -> tuple[str, ...]:
    parts: list[str] = []
    cursor = 64
    for field in sorted(fields, key=lambda item: item.lsb, reverse=True):
        gap = cursor - field.msb - 1
        if gap:
            parts.append(_reserved_field(gap))
        target = (
            rf"\phantomsection\label{{{target_labels[field.reference]}}}"
            if field.reference in target_labels
            else ""
        )
        parts.append(
            rf"\BedrockFormatField{{{target}{tex_escape(labels[field.reference])}}}"
            rf"{{{field.bits}}}"
        )
        cursor = field.lsb
    if cursor:
        parts.append(_reserved_field(cursor))
    return tuple(parts)


def render_cpuid_projection(projection: CpuidLeafProjection, labels) -> str:
    leaf = projection.leaf
    queries = projection.queries
    diagram_labels = {query: _diagram_labels(query.fields) for query in queries}
    rows = [
        f"{_identifier(_index(query.first, query.last, query.stride))} & "
        f"{_identifier(query.id)}\\\\"
        for query in queries
    ]
    diagram_rows: list[str] = []
    for query in queries:
        label = _query_diagram_label(query, query.fields, diagram_labels[query])
        diagram_rows.extend(
            (
                rf"\BedrockFormatRowRange{{{label}}}{{63}}{{0}}{{%",
                *_format_fields(query.fields, diagram_labels[query], labels),
                "}",
            )
        )

    caption = tex_escape(leaf.name)
    class_value = f"0x{projection.class_value:08X}"
    leaf_value = f"0x{projection.leaf_value:04X}"
    selector_base = f"0x{projection.selector(0):016X}"
    explanation = (
        rf"Class {_identifier(class_value)}, leaf {_identifier(leaf_value)} uses "
        rf"the 64-bit query selector {_identifier(selector_base)} "
        rf"$\mathbin{{|}}\,\mathit{{index}}$, where $\mathit{{index}}$ is "
        r"the 16-bit query index below."
    )
    return render_cpuid_leaf(caption, explanation, tuple(rows), tuple(diagram_rows))



def render_event_row(row, labels):
    return _code(row) + rf"\phantomsection\label{{{labels[row.reference]}}} & {_identifier(row.event_id)}" + r"\\"


def render_event_classes(projection, labels):
    rows = tuple(
        rf"\texttt{{0x{item.value:02X}}}\phantomsection\label{{{labels[item.reference]}}} & "
        rf"{_identifier(item.id)} & {tex_escape(item.name)} & {tex_escape(item.selector.kind)} {item.selector.bits}-bit selector" + r"\\"
        for item in projection.classes
    )
    return render_code_reference_event_reference(rows)
