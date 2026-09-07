"""ELF ABI document serialization from selected semantic rows."""
from __future__ import annotations
from engine.documents.abi import ElfRelocationsProjection, ElfDebugRegistersProjection, ElfEntryStateProjection
from abi.elf.model.projection import project_elf_abi
from artifacts._shared.documents import authored_tex_generate, build_document, authored_document_source


def _inputs(context):
    isa = context.workspace.require_provider("isa")
    project = context.workspace.require_provider("abi.elf")
    projection = context.shared_result((project_elf_abi, id(project), id(isa)), lambda: project_elf_abi(project, isa))
    return {"isa": isa, "abi.elf": projection}


def render_source(definition, context):
    return authored_document_source(definition, context, _inputs(context), render_fragment=render_fragment)


def validate(definition, context):
    render_source(definition, context)


def generate(definition, context):
    return authored_tex_generate(definition, render_source(definition, context))


def build(definition, context, *, compile_pdf, latexmk="latexmk"):
    return build_document(definition, context, compile_pdf=compile_pdf, latexmk=latexmk)


def render_fragment(projection, labels):
    if isinstance(projection, ElfRelocationsProjection): return _relocation_rows(projection)
    if isinstance(projection, ElfDebugRegistersProjection): return _debug_register_table(projection)
    if isinstance(projection, ElfEntryStateProjection): return _entry_state_table(projection)
    raise TypeError(f"unsupported ELF ABI fragment {type(projection).__name__}")


def _relocation_rows(projection) -> str:
    rows = []
    for relocation in sorted(projection.relocations, key=lambda item: item.value):
        result = relocation.result
        if result.kind.value == "none":
            size = "0"
        elif result.kind.value == "bytes":
            size = "variable-size"
        elif result.kind.value == "pair":
            size = f"{result.width_bits}-bit pair"
        else:
            sign = "signed" if result.signed else "unsigned"
            size = f"{result.width_bits}-bit {sign}"
        rows.append(
            f"{relocation.value} & {_code(relocation.id)} & {_code(size)} & "
            f"{_code(relocation.calculation.code)}\\\\"
        )
    return "\n".join(rows)


def _code(value: str) -> str:
    escaped = value.replace("_", r"\_")
    return rf"\texttt{{{escaped}}}"


def _debug_register_table(projection) -> str:
    rows: list[str] = []
    for assignment in sorted(
        projection.assignments, key=lambda item: item.first
    ):
        number = (
            f"{assignment.first} and greater"
            if assignment.last is None
            else str(assignment.first)
            if assignment.first == assignment.last
            else f"{assignment.first}..{assignment.last}"
        )
        registers = (
            "---"
            if not assignment.registers
            else _register_display(assignment.registers)
        )
        status = assignment.status
        if assignment.condition is not None:
            status = f"{status}; {assignment.condition}"
        rows.append(
            f"{number} & {_code(registers) if registers != '---' else registers} & {status}\\\\"
        )
    return "\n".join(
        (
            r"\BedrockTableCaption{Bedrock DWARF Register Numbers}",
            r"\begin{BedrockLongTable}{@{}>{\raggedright\arraybackslash}p{1.10in}>{\raggedright\arraybackslash}p{1.75in}>{\raggedright\arraybackslash}p{2.55in}@{}}",
            r"\toprule",
            r"\rowcolor{BedrockHeaderFill}",
            r"\textbf{Numbers} & \textbf{Registers} & \textbf{Status}\\",
            r"\midrule",
            r"\endfirsthead",
            r"\multicolumn{3}{l}{\scriptsize\itshape Table \theBedrockTable\ (continued)}\\",
            r"\toprule",
            r"\rowcolor{BedrockHeaderFill}",
            r"\textbf{Numbers} & \textbf{Registers} & \textbf{Status}\\",
            r"\midrule",
            r"\endhead",
            *rows,
            r"\bottomrule",
            r"\end{BedrockLongTable}",
        )
    )
def _register_display(registers) -> str:
    names = [item.id for item in registers]
    if len(names) == 1:
        return names[0]
    return f"{names[0]}..{names[-1]}"


def _entry_state_table(projection) -> str:
    state = projection.state
    segments = "/".join(
        state.segment_contexts[role].id
        for role in ("code", "data", "stack")
    )
    permissions = "/".join(state.stack_permissions)
    cleared = ", ".join(item.id for item in state.cleared)
    readiness = ", ".join(item.replace("_", " ") for item in state.readiness)
    entry_pc = f"{state.entry_point.id} = {state.entry_point_source}"
    entry_stack = (
        f"{state.stack.id}, {state.stack_alignment_bytes}-byte aligned, "
        f"{permissions}"
    )
    rows = (
        f"Entry PC & {_code(entry_pc)}\\\\",
        f"Entry stack & {_code(entry_stack)}\\\\",
        f"Segment contexts & {_code(segments)}\\\\",
        f"TLS base & {_code(state.tls_base.id if state.tls_base else 'absent')}\\\\",
        f"Readiness & {readiness}\\\\",
        f"Cleared state & {_code(cleared)}\\\\",
        f"Stack payload owner & {_code(state.payload_owner)}\\\\",
    )
    return "\n".join(
        (
            r"\BedrockTableCaption{Language-Independent ELF Program Entry State}",
            r"\begin{BedrockLongTable}{@{}p{1.55in}p{3.85in}@{}}",
            r"\toprule",
            r"\textbf{Property} & \textbf{Contract}\\",
            r"\midrule",
            r"\endhead",
            *rows,
            r"\bottomrule",
            r"\end{BedrockLongTable}",
        )
    )
