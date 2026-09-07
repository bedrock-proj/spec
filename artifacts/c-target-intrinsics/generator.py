"""Render selected C interface documentation rows."""
from __future__ import annotations

from engine.documents.abi import IntrinsicGroupsProjection, IntrinsicGroupProjection, IntrinsicTypesProjection
from interfaces.c.model.project import InterfaceType
from interfaces.c.model.projection import project_c_interface
from artifacts._shared.documents import authored_tex_generate, authored_document_source, build_document
from artifacts._shared.c_target_names import intrinsic_group_header


def _inputs(context):
    interface = context.workspace.require_provider("interfaces.c")
    isa = context.workspace.require_provider("isa")
    c_abi = context.workspace.require_provider("abi.c")
    project = context.shared_result((project_c_interface, id(interface), id(isa), id(c_abi)), lambda: project_c_interface(interface, isa, c_abi))
    return {"isa": isa, "interfaces.c": project}


def render_source(definition, context):
    return authored_document_source(definition, context, _inputs(context), render_fragment=render_fragment)


def validate(definition, context):
    render_source(definition, context)


def generate(definition, context):
    return authored_tex_generate(definition, render_source(definition, context))


def build(definition, context, *, compile_pdf, latexmk="latexmk"):
    return build_document(definition, context, compile_pdf=compile_pdf, latexmk=latexmk)


def render_fragment(projection, labels):
    if isinstance(projection, IntrinsicGroupsProjection): return _render_header_families(projection)
    if isinstance(projection, IntrinsicGroupProjection): return _render_intrinsic_group(projection)
    if isinstance(projection, IntrinsicTypesProjection): return _render_types(projection)
    raise TypeError(f"unsupported C interface fragment {type(projection).__name__}")


def _render_header_families(projection) -> str:
    rows = []
    for group, collections, exposure in projection.rows:
        family = group.title.removesuffix(" Intrinsics")
        umbrella = ", ".join(collection.id for collection in collections)
        rows.append(f"{family} & " + _code("<" + intrinsic_group_header(group.id) + ">") + f" & {umbrella} & {exposure}" + r"\\")
    return _longtable("Target Intrinsic Header Families", ("Family", "Header", "Umbrella", "Exposure"), rows, "p{1.15in}p{1.65in}p{0.65in}p{2.15in}")


def _render_intrinsic_group(projection) -> str:
    rows = []
    for intrinsic, signature, bundle, operands, description in projection.rows:
        result, parameters = signature
        parameter_types = ",".join(_document_type(kind) for _, kind, _ in parameters) or "void"
        operation = bundle.instruction.mnemonic
        if "size" in operands:
            operation += f".{operands['size']}"
        elif "source" in operands:
            operation += f" {operands['source']}"
        signature_text = _document_type(result) + "(" + parameter_types + ")"
        rows.append(" & ".join((_code(intrinsic.id), _code(signature_text), _code(operation), description)) + r"\\")
    return _longtable(projection.group.title, ("Name", "C interface", "Lowering", "Availability, constraint, and effect"), rows, "p{1.4in}p{1.4in}p{0.8in}p{2.0in}")


def _render_types(projection) -> str:
    rows = [_code(f"__bedrock_{definition.id}_t") + " & " + description + r"\\" for definition, description in projection.rows]
    return _longtable("Target Intrinsic Shared Types", ("Type", "ABI contract"), rows, "p{2.05in}p{3.45in}")


def _longtable(
    caption: str,
    headings: tuple[str, ...],
    rows: list[str],
    columns: str,
) -> str:
    heading = " & ".join(f"\\textbf{{{item}}}" for item in headings)
    return "\n".join(
        (
            f"\\BedrockTableCaption{{{caption}}}",
            r"\begingroup\footnotesize",
            r"\setlength{\tabcolsep}{2pt}",
            f"\\begin{{longtable}}{{@{{}}{columns}@{{}}}}",
            r"\toprule",
            heading + r"\\",
            r"\midrule",
            r"\endhead",
            *rows,
            r"\bottomrule",
            r"\end{longtable}",
            r"\endgroup",
        )
    )



def _document_type(kind) -> str:
    if isinstance(kind, InterfaceType):
        return kind.id
    names = {"u8": "u8", "u16": "u16", "u32": "u32", "u64": "u64", "f32": "float", "f64": "double", "size": "size_t", "void": "void", "void_pointer": "void *", "const_void_pointer": "const void *"}
    if kind in names:
        return names[kind]
    if kind.endswith("_pointer") and kind.removesuffix("_pointer") in names:
        return names[kind.removesuffix("_pointer")] + " *"
    raise ValueError(f"C interface document cannot represent type {kind!r}")


def _code(value: str) -> str:
    escaped = value.replace("_", r"\_").replace("<", r"\textless{}").replace(">", r"\textgreater{}")
    return rf"\texttt{{{escaped}}}"
