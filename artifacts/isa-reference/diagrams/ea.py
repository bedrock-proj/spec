"""Construct EA diagrams from resolved encodings and ordered evaluation steps."""
from __future__ import annotations
import argparse
import re
import sys
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path
from engine.documents.fragments.ea import EAModeDiagramProjection, project_mode
from engine.isa.ea import (
    EACapture, EARegisterUpdate, EACalculate, ResolvedEAField, ImmediateEAMode,
    MemoryEAMode, FixedEASegment, EALiteral, EAIdentifier, EANegation, EABinary,
    select_ea_modes,
)
from artifacts._shared.latex import tex_escape, tex_code

_TITLE_MINOR_WORDS = frozenset({"a", "an", "and", "as", "at", "but", "by", "for", "in", "nor", "of", "on", "or", "per", "the", "to", "via", "with"})

@dataclass(frozen=True, slots=True)
class _FlowBoxNode:
    node_id: str
    row: int
    text: str
    row_label: str
    show_bits: bool


def _flow_row_y(row):
    return 0.90 - row * 0.72


def _number(value):
    rendered = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if rendered == "-0" else rendered


def render_flow_layout(caption, nodes, edges, memory_tail):
    height = 0.70 + max(0, len(nodes) - 1 + int(memory_tail is not None)) * 0.72
    lines = [rf"\BedrockEAFlowStart{{{_number(height)}in}}{{{tex_escape(caption)}}}%"]
    for node in nodes:
        lines.append(rf"  \BedrockEAFlowLabeledBox{{{node.row_label}}}{{{node.node_id}}}{{{_number(_flow_row_y(node.row))}}}{{4.62}}{{2.2}}{{{tex_escape(node.text)}}}{{{int(node.show_bits)}}}%")
    lines.extend(rf"  \draw[bedrockFlowArrow] ({source}.south) -- ({target}.north);%" for source, target in edges)
    if memory_tail is not None:
        pointer, row = memory_tail
        lines.append(rf"  \BedrockEAFlowMemoryTail{{{pointer}}}{{{_number(_flow_row_y(row))}}}{{4.62}}{{2.2}}%")
    lines.append(r"  \BedrockEAFlowEnd")
    return "\n".join(lines)


def _expression_text(expression):
    match expression:
        case EALiteral(value): return str(value)
        case EAIdentifier(name): return name
        case EANegation(operand): return f"(-{_expression_text(operand)})"
        case EABinary(operator, left, right): return f"({_expression_text(left)} {operator} {_expression_text(right)})"
    raise TypeError(f"unsupported EA expression {expression!r}")


def _binding_text(binding):
    if isinstance(binding, ResolvedEAField):
        return binding.field.role, f"{binding.definition.id}({binding.field.symbol})"
    return binding.payload.role, binding.definition.id


def _render_calculation(mode, steps):
    if not steps:
        return None
    nodes, edges = [], []
    for index, step in enumerate(steps):
        if isinstance(step, EACapture):
            role, value = _binding_text(step.binding)
            text, label = f"{role} := {value}", "CAPTURE TERM"
        elif isinstance(step, EARegisterUpdate):
            _, value = _binding_text(step.binding)
            operator = "-" if step.update.update_type == "predecrement" else "+"
            text, label = f"{value} := {value} {operator} {step.update.difference}", "TEMPORARY STATE"
        elif isinstance(step, EACalculate):
            text, label = _expression_text(step.expression), "CALCULATE"
        else:
            raise TypeError(f"unsupported EA evaluation step: {type(step).__name__}")
        node = _FlowBoxNode(f"step{index}", index, text, label, False)
        if nodes:
            edges.append((nodes[-1].node_id, node.node_id))
        nodes.append(node)
    immediate = isinstance(mode, ImmediateEAMode)
    result = _FlowBoxNode("result", len(nodes), "IMMEDIATE VALUE" if immediate else "EFFECTIVE ADDRESS", "OPERAND" if immediate else "OPERAND POINTER", True)
    edges.append((nodes[-1].node_id, result.node_id))
    nodes.append(result)
    return render_flow_layout(_title_case(f"{mode.name} calculation"), nodes, edges, None if immediate else (result.node_id, len(nodes)))


def _encoding_label(resolved):
    parts = [f"{update.target} {update.update_type}" for update in resolved.updates]
    parts.extend(payload.definition.id for payload in resolved.payloads)
    return " + ".join(parts) if parts else "plain"


def render_encoding_diagram(projection):
    lines = [rf"\begin{{BedrockFormatDiagram}}{{{tex_escape(_title_case(projection.mode.name + ' encodings'))}}}"]
    for resolved in projection.encodings:
        lines.append(rf"\BedrockFormatRow{{{tex_escape(_encoding_label(resolved))}}}{{%")
        for key, values in groupby(resolved.pattern.code, key=lambda character: "fixed" if character in "01" else character):
            values = tuple(values)
            code = "".join(values) if key == "fixed" else key
            macro = "Fixed" if key == "fixed" else "FieldCode"
            lines.append(rf"  \BedrockFormat{macro}{{{code}}}{{{len(values)}}}")
        lines.append("}")
    lines.append(r"\end{BedrockFormatDiagram}")
    return "\n".join(lines)


def render_description_block(projection):
    mode = projection.mode
    lines = [rf"\BedrockEAProfileTitle{{{tex_escape(_title_case(mode.catalog.name + ' ' + mode.name))}}}", r"\begin{BedrockEAProfile}"]
    for syntax in dict.fromkeys(projection.syntax):
        if syntax is not None:
            lines.append(rf"\BedrockEAProfileSyntax{{{tex_escape(syntax).replace('--', '{-}{-}')}}}")
    label = "EA encoding" if mode.catalog.mode_type == "compact" else "Descriptor"
    patterns = "; ".join(tex_code(" ".join(chunk.code for chunk in item.patterns)) for item in projection.encodings)
    lines.append(rf"\BedrockEAProfileLine{{{label}}}{{{patterns}.}}")
    if isinstance(mode, MemoryEAMode):
        if mode.segment is None:
            segment = "Uses the operation default data segment."
        elif isinstance(mode.segment, FixedEASegment):
            segment = f"Fixed to {tex_escape(mode.segment.register)}."
        else:
            segment = "SEG(s) selects the encoded segment register."
        lines.append(rf"\BedrockEAProfileLine{{Segment}}{{{segment}}}")
    payloads = tuple(dict.fromkeys((item.payload.role, item.definition.id) for resolved in projection.encodings for item in resolved.payloads))
    payload_text = "Appends " + ", ".join(f"{tex_code(name)} ({tex_escape(role)})" for role, name in payloads) + " as selected by the encoding." if payloads else "No payload bytes are appended by this encoding."
    lines.append(rf"\BedrockEAProfileLine{{Payload}}{{{payload_text}}}")
    for resolved in projection.encodings:
        for update in resolved.updates:
            amount = tex_code(update.difference)
            target = tex_escape(update.target)
            text = f"Postincrement captures the current temporary {target} register, then adds {amount}." if update.update_type == "postincrement" else f"Predecrement subtracts {amount} from the temporary {target} register before capture."
            lines.append(rf"\BedrockEAProfileLine{{Update}}{{{text}}}")
    lines.append(r"\end{BedrockEAProfile}")
    return "\n".join(lines)


def render_mode(projection: EAModeDiagramProjection):
    sections = []
    for resolved, steps, syntax in zip(projection.encodings, projection.evaluation, projection.syntax, strict=True):
        selected = EAModeDiagramProjection(projection.mode, (resolved,), (steps,), (syntax,))
        height = 2.28 + max(0, len(steps)) * 0.72
        sections.extend((rf"\par\Needspace{{{height:.2f}in}}%", r"\begin{BedrockFormBlock}{2.75in}", render_description_block(selected), r"\end{BedrockFormBlock}", render_encoding_diagram(selected)))
        flow = _render_calculation(projection.mode, steps)
        if flow is not None:
            sections.append(flow)
    return "\n\n".join(sections)


def render_modes(projections):
    return "\n\n\\clearpage\n\n".join(render_mode(projection) for projection in projections) + "\n"


def _title_case(value):
    matches = tuple(re.finditer(r"[A-Za-z0-9]+", str(value)))
    text, parts, cursor = str(value), [], 0
    for index, match in enumerate(matches):
        parts.append(text[cursor:match.start()])
        word = match.group()
        if word.isupper() or any(char.isdigit() or char.isupper() for char in word[1:]):
            rendered = word
        elif word.lower() in _TITLE_MINOR_WORDS and index not in {0, len(matches)-1}:
            rendered = word.lower()
        else:
            rendered = word.capitalize()
        parts.append(rendered)
        cursor = match.end()
    parts.append(text[cursor:])
    return "".join(parts)


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("modes", nargs="*", type=Path, metavar="MODE_YAML")
    parser.add_argument("--isa-root", type=Path, default=Path(__file__).resolve().parents[3] / "isa")
    parser.add_argument("-o", "--output", type=Path)
    return parser


def main(argv=None):
    from engine.isa.project import load_isa
    args = _parser().parse_args(argv)
    isa = load_isa(args.isa_root)
    modes = select_ea_modes(isa.catalog.ea_modes, args.modes)
    projections = tuple(project_mode(mode, field_types=isa.types.field_types, payload_types=isa.types.payload_types) for mode in modes)
    rendered = render_modes(projections)
    if args.output is None:
        sys.stdout.write(rendered)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
