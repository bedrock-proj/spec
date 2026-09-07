"""C ABI document serialization from selected semantic rows."""
from __future__ import annotations
from engine.documents.abi import CReturnRulesProjection, CMemoryOrdersProjection, CAtomicLoweringsProjection
from abi.c.model.projection import project_c_abi
from artifacts._shared.documents import authored_tex_generate, build_document, authored_document_source


def _inputs(context):
    isa = context.workspace.require_provider("isa")
    project = context.workspace.require_provider("abi.c")
    projection = context.shared_result((project_c_abi, id(project), id(isa)), lambda: project_c_abi(project, isa))
    return {"isa": isa, "abi.c": projection}


def render_source(definition, context):
    return authored_document_source(definition, context, _inputs(context), render_fragment=render_fragment)


def validate(definition, context):
    render_source(definition, context)


def generate(definition, context):
    return authored_tex_generate(definition, render_source(definition, context))


def build(definition, context, *, compile_pdf, latexmk="latexmk"):
    return build_document(definition, context, compile_pdf=compile_pdf, latexmk=latexmk)


def render_fragment(projection, labels):
    if isinstance(projection, CReturnRulesProjection): return _return_register_table(projection)
    if isinstance(projection, CMemoryOrdersProjection): return _memory_order_table(projection)
    if isinstance(projection, CAtomicLoweringsProjection): return _atomic_lowering_table(projection)
    raise TypeError(f"unsupported C ABI fragment {type(projection).__name__}")


def _return_register_table(projection) -> str:
    rows = []
    for value in projection.rows:
        value_class = value.definition
        registers = value.result_registers
        component_roles = value.result_component_roles
        policy = value_class.result
        names = tuple(register.id for register in registers)
        if policy.mode == "sret":
            rule = f"sret pointer in {projection.sret_register.id}; result pointer in {names[0]}"
        elif policy.mode == "size_dependent":
            direct = ":".join(reversed(names))
            rule = f"up to {policy.direct_maximum_bytes} bytes in {direct}; larger values use sret"
        else:
            kinds_by_roles = {}
            for kind in value_class.kinds:
                kinds_by_roles.setdefault(component_roles[kind], []).append(kind)
            rules = []
            for roles, kinds in kinds_by_roles.items():
                meaning = (
                    "; ".join(f"{role} component in {name}" for role, name in zip(roles, names, strict=True))
                    if roles else ":".join(reversed(names))
                )
                rules.append(
                    f"{', '.join(kinds)}: {meaning}"
                    if len(kinds_by_roles) > 1 else meaning
                )
            rule = "; ".join(rules)
        kinds = ", ".join(value_class.kinds)
        rows.append(f"{_code(kinds)} & {_code(rule)}\\\\")
    return "\n".join(
        (
            r"\BedrockTableCaption{C Return Register Quick Reference}",
            r"\begingroup\footnotesize",
            r"\setlength{\tabcolsep}{2pt}",
            r"\begin{longtable}{@{}p{2.15in}p{3.35in}@{}}",
            r"\toprule",
            r"\textbf{Result kinds} & \textbf{Register rule}\\",
            r"\midrule",
            r"\endhead",
            *rows,
            r"\bottomrule",
            r"\end{longtable}",
            r"\endgroup",
        )
    )


def _memory_order_table(projection) -> str:
    rows = []
    for mapping, load, store, thread_fence in projection.rows:
        rows.append(
            " & ".join(
                (
                    _code(mapping.id.lower()),
                    _code(mapping.instruction_order),
                    _sequence(load, "load"),
                    _sequence(store, "store"),
                    _sequence(thread_fence, "access"),
                )
            )
            + r"\\"
        )
    return "\n".join(
        (
            r"\BedrockTableCaption{C Atomic Memory Order Mapping}",
            r"\begingroup\scriptsize",
            r"\setlength{\tabcolsep}{2pt}",
            r"\begin{longtable}{@{}p{0.7in}p{0.75in}p{1.35in}p{1.35in}p{1.0in}@{}}",
            r"\toprule",
            r"\textbf{C order} & \textbf{Instruction} & \textbf{Load} & \textbf{Store} & \textbf{Fence}\\",
            r"\midrule",
            r"\endhead",
            *rows,
            r"\bottomrule",
            r"\end{longtable}",
            r"\endgroup",
        )
    )


def _sequence(sequence, access_name: str) -> str:
    if sequence is None:
        return "---"
    if len(sequence) == 0:
        return "zero instructions"
    names = [
        access_name
        if item == "access"
        else item.instruction.mnemonic
        for item in sequence
    ]
    return _code("; ".join(names))


def _code(value: str) -> str:
    escaped = value.replace("_", r"\_")
    return rf"\texttt{{{escaped}}}"


def _atomic_lowering_table(projection) -> str:
    rows = []
    for lowering, resolved_instructions in projection.rows:
        operations = ", ".join(lowering.c_operations)
        instructions = ", ".join(
            item.instruction.mnemonic
            for item in resolved_instructions
        )
        if lowering.strategy == "aligned_access":
            rule = f"aligned {instructions} access plus the order sequence"
        elif lowering.strategy == "compare_exchange_loop":
            rule = f"loop using {instructions}"
        else:
            rule = instructions
        rows.append(f"{_code(operations)} & {_code(rule)}\\\\")
    caption = "C Atomic Lowering"
    return "\n".join(
        (
            rf"\BedrockTableCaption{{{caption}}}",
            r"\begin{BedrockLongTable}{@{}p{2.15in}p{3.25in}@{}}",
            r"\toprule",
            r"\textbf{C operation} & \textbf{Bedrock lowering}\\",
            r"\midrule",
            r"\endhead",
            *rows,
            r"\bottomrule",
            r"\end{BedrockLongTable}",
        )
    )
