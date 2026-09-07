"""ISA manual drawing construction from resolved engine projections."""

from __future__ import annotations

from engine.isa.encoding import AllowedOperandConstraint
from engine.isa.encoding import ExcludedOperandConstraint
from engine.isa.encoding import EncodingForm
from engine.isa.encoding import OperandConstraint

import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from collections.abc import Mapping
from engine.documents.sources import AuthoredSourceProjection
from engine.isa.instructions import Repcc
from artifacts._shared.latex import render_latex_source, render_semantic_text, target_label

from importlib import import_module

from engine.documents.projection import DocumentProjection
from engine.documents.projection import InstructionSetSummaryProjection
from engine.documents.projection import ProjectedInstructionSet
from engine.documents.projection import ProjectedTermGroup
from engine.documents.projection import ProjectedTopic

from engine.documents.instructions import InstructionFormatProjection
from artifacts._shared.latex import tex_escape
from artifacts._shared.latex import tex_code


def render_latex(projection: DocumentProjection, frame_sources, labels) -> str:
    parts = [render_latex_source(frame_sources[0], labels, render_fragment), render_latex_source(frame_sources[1], labels, render_fragment)]
    for projected in projection.blocks:
        if isinstance(projected, AuthoredSourceProjection):
            parts.append(render_latex_source(projected, labels, render_fragment))
        elif isinstance(projected, ProjectedTopic):
            parts.extend(
                [
                    f"% topic: {projected.topic.id}",
                    render_latex_source(projected.content, labels, render_fragment),
                ]
            )
        elif isinstance(projected, ProjectedTermGroup):
            parts.extend(
                [
                    f"% term-group: {projected.group.id}",
                    render_term_group(projected, labels),
                ]
            )
        elif isinstance(projected, ProjectedInstructionSet):
            group = projected.summary
            parts.extend(
                [
                    f"% instruction-set: {group.owner}",
                    r"\clearpage",
                    rf"\section{{{tex_escape(group.title)}}}",
                    rf"\label{{{instruction_group_label(group.owner)}}}",
                    _latex_document_render_instruction_summary(projected.summary, labels),
                    *(
                        item
                        for topic in projected.introduction
                        for item in (
                            f"% topic: {topic.topic.id}",
                            render_latex_source(topic.content, labels, render_fragment),
                        )
                    ),
                    *(render_instruction(entry, labels) for entry in projected.instructions),
                ]
            )
    parts.append(render_latex_source(frame_sources[2], labels, render_fragment))
    return "\n\n".join(parts) + "\n"


def _latex_document_render_instruction_summary(
    projection: InstructionSetSummaryProjection, labels,
) -> str:
    rows = (
        rf"\BedrockSummaryMnemonic{{{target_label(labels, row.reference)}}}"
        rf"{{{tex_escape(row.mnemonic)}}} & "
        rf"{tex_escape(row.description)}\\"
        for row in projection.rows
    )
    header = (
        r"\toprule",
        r"\rowcolor{BedrockHeaderFill}",
        r"\textbf{Mnemonic} & \textbf{Brief description}\\",
        r"\midrule",
    )
    return "\n".join(
        (
            r"\subsection{Summary}",
            rf"\BedrockTableCaption{{{tex_escape(projection.title + " Summary (Informative)")}}}",
            r"\begin{BedrockLongTable}{@{}>{\raggedright\arraybackslash}p{1.05in}>{\raggedright\arraybackslash}p{4.35in}@{}}",
            *header,
            r"\endfirsthead",
            r"\multicolumn{2}{l}{\scriptsize\itshape Table \theBedrockTable\ (continued)}\\",
            *header,
            r"\endhead",
            *rows,
            r"\bottomrule",
            r"\end{BedrockLongTable}",
        )
    )


_tables = import_module("artifacts.isa-reference.tables")

_ea = import_module("artifacts.isa-reference.diagrams.ea")
_registers = import_module("artifacts.isa-reference.diagrams.registers")
_memory = import_module("artifacts.isa-reference.diagrams.memory_records")
_vector = import_module("artifacts.isa-reference.diagrams.vector")


def render_instruction(entry, labels) -> str:
    instruction = entry.bundle.instruction
    parts = [
        r"\clearpage",
        rf"\begin{{BedrockInstruction}}{{{tex_escape(instruction.mnemonic)}}}"
        rf"{{{tex_escape(instruction.name)}}}{{{instruction_label(instruction.mnemonic)}}}",
        *(
            rf"\BedrockOperationField{{{tex_escape(label)}}}{{{value}}}"
            for label, value in render_instruction_metadata(entry.metadata)
        ),
        r"\BedrockInstructionDescriptionHeading{Detailed Semantics}",
        render_latex_source(entry.description, labels, render_fragment),
        _instruction_entry_forms(entry.formats),
        r"\end{BedrockInstruction}",
    ]
    return "\n".join(part for part in parts if part)


def _instruction_entry_forms(
    formats: tuple[InstructionFormatProjection, ...],
) -> str:
    blocks = [r"\begin{BedrockInstructionForms}"]
    for index, projected in enumerate(formats):
        form = projected.form
        blocks.extend(
            [
                r"\begin{BedrockFormBlock}{2.75in}",
                *([r"\BedrockInstructionFormsHeading"] if index == 0 else []),
                rf"\textbf{{{tex_code(form.syntax.code)}}}\par",
                r"\BedrockInstructionFormatHeading",
                _instruction_entry_instruction_diagram(projected),
            ]
        )
        if form.additional_cpuid_flags:
            blocks.append(
                r"\par\Needspace{0.36in}\noindent"
                r"\textbf{Additional CPUID flags:}\enspace "
                + ", ".join(tex_code(field.id) for field in form.additional_cpuid_flags)
                + r"\par"
            )
        blocks.extend(_instruction_entry_restrictions(form))
        blocks.append(r"\end{BedrockFormBlock}")
    blocks.append(r"\end{BedrockInstructionForms}")
    return "\n".join(blocks)


def instruction_label(mnemonic: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", mnemonic.lower()).strip("-")
    return f"instr:{slug}"


def _instruction_entry_ragged(lines) -> str:
    rendered = "".join(rf"\noindent {line}\par " for line in lines)
    return rf"\begin{{BedrockRaggedBlock}}{rendered}\end{{BedrockRaggedBlock}}"


def _instruction_entry_restrictions(form: "EncodingForm") -> list[str]:
    if not form.constraints and not form.overlaps:
        return []
    restrictions = [r"\BedrockInstructionRestrictionsHeading"]
    restrictions.extend(
        _instruction_entry_operand_constraint(constraint)
        for constraint in form.constraints
    )
    restrictions.extend(
        rf"\BedrockInstructionOperandOverlap"
        rf"{{{tex_escape(overlap.operands[0])}}}"
        rf"{{{tex_escape(overlap.operands[1])}}}"
        rf"{{{tex_escape(overlap.type)}}}"
        for overlap in form.overlaps
    )
    return restrictions


def _instruction_entry_operand_constraint(constraint: "OperandConstraint") -> str:
    if isinstance(constraint, AllowedOperandConstraint):
        relation = "allowed"
    elif isinstance(constraint, ExcludedOperandConstraint):
        relation = "excluded"
    else:
        raise TypeError(f"unsupported operand constraint {type(constraint).__name__}")
    values = ", ".join(str(value) for value in constraint.values)
    return (
        rf"\BedrockInstructionOperandConstraint"
        rf"{{{tex_escape(constraint.role)}}}"
        rf"{{{relation}}}"
        rf"{{{tex_escape(values)}}}"
        rf"{{{tex_escape(constraint.reason)}}}"
    )


def _instruction_entry_instruction_diagram(
    projected: InstructionFormatProjection,
) -> str:
    form = projected.form
    fields: list[str] = []
    for byte_index, byte in enumerate(projected.bytes):
        if byte_index:
            fields.append(r"\BedrockBitGap{1}")
        for segment in byte.segments:
            macro = "BedrockBitFixed" if segment.fixed else "BedrockBitVariable"
            fields.append(
                rf"\{macro}{{{tex_escape(segment.label)}}}{{{segment.width}}}"
            )
    return "\n".join(
        [
            rf"\begin{{BedrockBitDiagram}}{{Format: Instruction format for "
            rf"{tex_escape(form.syntax.code)}}}",
            rf"\BedrockBitFieldRow{{}}{{\BedrockByteRowLabels{{0}}"
            rf"{{{len(projected.bytes)}}}}}{{%",
            *fields,
            "}",
            r"\end{BedrockBitDiagram}",
        ]
    )


@dataclass(frozen=True, slots=True)
class DocumentFrame:
    preamble: Path
    title_page: Path
    postamble: Path
    sources: Mapping[str, Path]


def load_document_frame(definition, repository) -> DocumentFrame:
    repository = Path(repository)
    artifact_root = definition.source.parent
    def source(raw, boundary):
        if not isinstance(raw, str):
            raise ValueError(f"{definition.source}: document source must be a path string")
        return boundary / raw
    body_sources = {item["source"]: source(item["source"], artifact_root) for item in definition.data["body"] if "source" in item}
    return DocumentFrame(source(definition.data["preamble"], repository), source(definition.data["title-page"], repository), source(definition.data["postamble"], repository), MappingProxyType(body_sources))


def instruction_group_label(owner: str) -> str:
    return "page:instruction-group-" + re.sub(r"[^a-z0-9]+", "-", owner.lower()).strip("-")


def render_instruction_metadata(metadata):
    fields = [
        ("Operation", tex_escape(metadata.summary)),
        ("Assembler Syntax", _instruction_entry_ragged(tex_code(syntax) for syntax in metadata.syntax)),
        ("Privilege", "Supervisor only" if metadata.privileged else "Unprivileged"),
    ]
    if metadata.required_cpuid_flags:
        fields.append(("Required CPUID flags", _instruction_entry_ragged(tex_code(field.id) for field in metadata.required_cpuid_flags)))
    if metadata.repeat is not None:
        text = "REP eligible"
        if isinstance(metadata.repeat, Repcc):
            text += "; REPcc observes " + tex_code(metadata.repeat.observed_value)
        fields.append(("Repeat eligibility", text))
    return tuple(fields)


def render_term_group(projected, labels):
    group = projected.group
    parts = [rf"\subsection{{{tex_escape(group.title)}}}\label{{{target_label(labels, group.reference)}}}"]
    for term, definition in projected.terms:
        display = term.forms.canonical
        if term.abbreviation is not None:
            display += f" ({term.abbreviation.canonical})"
        if term.article is not None:
            subject = rf"{term.article.capitalize()} \emph{{{tex_escape(display)}}}"
        else:
            subject = rf"\emph{{{tex_escape(display[:1].upper() + display[1:])}}}"
        anchor = rf"\phantomsection\label{{{target_label(labels, term.reference)}}}"
        parts.append(anchor + f"\n{subject} is {render_semantic_text(definition, labels)}")
    return "\n\n".join(parts)


def render_fragment(selection, labels):
    from engine.documents.fragments.cpuid import CpuidLeafProjection
    from engine.documents.fragments.ea import EAModeDiagramProjection
    from engine.documents.fragments.events import EventCodeRow, EventClassesProjection
    from engine.documents.fragments.registers import RegisterFigureProjection, ControlRegistersProjection
    from engine.documents.fragments.disclosures import DisclosuresProjection
    from engine.documents.fragments.memory_records import MemoryRecordProjection
    from engine.documents.fragments.structured_fields import StructuredFieldTarget
    from engine.isa.vector_examples import VectorDiagram
    if isinstance(selection, CpuidLeafProjection):
        return _tables.render_cpuid_projection(selection, labels)
    if isinstance(selection, EAModeDiagramProjection):
        return _ea.render_mode(selection)
    if isinstance(selection, RegisterFigureProjection):
        return _registers.render_register_figure(selection)
    if isinstance(selection, MemoryRecordProjection):
        return _memory.render_memory_record(selection)
    if isinstance(selection, VectorDiagram):
        return _vector.render_vector_diagram(selection)
    if isinstance(selection, StructuredFieldTarget):
        return rf"\phantomsection\label{{{target_label(labels, selection.entity.reference)}}}"
    if isinstance(selection, EventCodeRow):
        return _tables.render_event_row(selection, labels)
    if isinstance(selection, EventClassesProjection):
        return _tables.render_event_classes(selection, labels)
    if isinstance(selection, ControlRegistersProjection):
        return _tables.render_control_register_reference(selection)
    if isinstance(selection, DisclosuresProjection):
        return _tables.render_implementation_disclosure(selection)
    raise TypeError(f"unsupported source fragment: {type(selection).__name__}")
