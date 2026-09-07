#!/usr/bin/env python3
"""Extract reader-visible TeX diagrams and render them as site SVG assets."""

from __future__ import annotations

from types import MappingProxyType

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from engine.syntax.tex import mask_tex_code

from .navigation import SiteError
from .structure import LatexStructure

DOCUMENT_BEGIN = r"\begin{document}"
DOCUMENT_END = r"\end{document}"
VISUAL_ENVIRONMENTS = MappingProxyType({
    "BedrockTikzDiagram": (4, False),
    "BedrockListedTikzDiagram": (4, False),
    "BedrockVectorExample": (5, False),
    "BedrockBitDiagram": (1, False),
    "BedrockListedBitDiagram": (1, True),
    "BedrockFormatDiagram": (1, False),
    "BedrockListedFormatDiagram": (1, True),
    "BedrockByteOrderDiagram": (6, False),
    "BedrockListedByteOrderDiagram": (6, False),
    "BedrockStructLayout": (4, False),
    "BedrockListedStructLayout": (4, False),
    "BedrockStackFrameDiagram": (1, False),
    "BedrockListedStackFrameDiagram": (1, False),
})
VISUAL_BEGIN_RE = re.compile(
    r"\\begin\{(?P<environment>"
    + "|".join((*VISUAL_ENVIRONMENTS, "center", "tikzpicture"))
    + r")\}"
)
FIGURE_CAPTION_RE = re.compile(r"\\BedrockFigureCaption\s*\{")
LABEL_RE = re.compile(r"\\label\s*\{")
PDFINFO_PAGES_RE = re.compile(r"^Pages:\s+(\d+)\s*$", flags=re.MULTILINE)


class SiteVisualError(SiteError):
    """A TeX visual cannot be represented as a generated SVG asset."""


@dataclass(frozen=True)
class VisualSpec:
    id: str
    title: str
    caption_tex: str
    source: str
    marker: str
    asset: PurePosixPath
    start: int
    end: int
    labels: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", tuple(self.labels))


@dataclass(frozen=True)
class VisualizedLatex:
    text: str
    visuals: tuple[VisualSpec, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "visuals", tuple(self.visuals))

    @property
    def titles(self) -> dict[str, str]:
        return {visual.marker: visual.title for visual in self.visuals}


def _balanced_value(
    text: str,
    open_index: int,
    opening: str,
    closing: str,
    where: str,
) -> tuple[str, int]:
    if open_index >= len(text) or text[open_index] != opening:
        raise SiteVisualError(f"{where}: expected {opening!r}")
    depth = 0
    index = open_index
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == opening:
            depth += 1
        elif text[index] == closing:
            depth -= 1
            if depth == 0:
                return text[open_index + 1 : index], index + 1
        index += 1
    raise SiteVisualError(f"{where}: unterminated {opening!r} value")


def _next_argument(text: str, cursor: int, where: str) -> tuple[str, int]:
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    return _balanced_value(text, cursor, "{", "}", where)


def _environment_end(text: str, environment: str, start: int) -> int:
    token_re = re.compile(rf"\\(?P<kind>begin|end)\{{{re.escape(environment)}\}}")
    depth = 0
    for match in token_re.finditer(mask_tex_code(text), start):
        if match.group("kind") == "begin":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return match.end()
    raise SiteVisualError(f"{environment} environment at byte {start} is unterminated")


def _plain_title(value: str) -> str:
    text = value
    # Public syntax is normally wrapped in \texttt{}.  Unwrap it with balanced
    # TeX arguments so metasyntax grouping braces remain visible rather than
    # being mistaken for the wrapper's closing delimiter.
    while True:
        command = text.find(r"\texttt{")
        if command < 0:
            break
        body, end = _balanced_value(
            text, command + len(r"\texttt"), "{", "}", "plain title texttt"
        )
        text = text[:command] + body + text[end:]
    replacements = {
        r"\_": "_",
        r"\{": "{",
        r"\}": "}",
        r"\<": "<",
        r"\textless{}": "<",
        r"\textgreater{}": ">",
        r"\textbar{}": "|",
        r"\textemdash{}": "—",
        "~": " ",
    }
    for source, destination in replacements.items():
        text = text.replace(source, destination)
    previous = None
    while text != previous:
        previous = text
        text = re.sub(r"\\[A-Za-z@]+\*?\s*\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\[A-Za-z@]+\*?", "", text)
    text = " ".join(text.split())
    return text or "Diagram"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "diagram"


def _owner_at(structure: LatexStructure, position: int) -> str:
    owner = "document"
    for section in structure.sections:
        if section.start <= position < section.end:
            owner = section.key
            for instruction in structure.instructions:
                if section.start <= instruction.start <= position:
                    owner = instruction.label.removeprefix("instr:")
                elif instruction.start > position:
                    break
            break
    return owner


def _optional_label(text: str, cursor: int, where: str) -> tuple[str | None, int]:
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    if cursor >= len(text) or text[cursor] != "[":
        return None, cursor
    label, cursor = _balanced_value(text, cursor, "[", "]", where)
    normalized = label.strip()
    return normalized or None, cursor


def _labels_in_snippet(snippet: str) -> tuple[str, ...]:
    labels: list[str] = []
    for match in LABEL_RE.finditer(mask_tex_code(snippet)):
        value, _ = _balanced_value(snippet, match.end() - 1, "{", "}", "visual label")
        normalized = value.strip()
        if normalized and normalized not in labels:
            labels.append(normalized)
    return tuple(labels)


def _environment_visual(
    text: str,
    match: re.Match[str],
) -> tuple[int, str, str, tuple[str, ...], str] | None:
    environment = match.group("environment")
    if environment is None:
        return None
    end = _environment_end(text, environment, match.start())
    snippet = text[match.start() : end]
    if environment == "center":
        if r"\begin{tikzpicture}" not in mask_tex_code(snippet):
            return None
        caption = FIGURE_CAPTION_RE.search(mask_tex_code(snippet))
        if caption is None:
            title = "Diagram"
        else:
            title, _ = _balanced_value(
                snippet,
                caption.end() - 1,
                "{",
                "}",
                "direct TikZ caption",
            )
        return end, title, snippet, _labels_in_snippet(snippet), title
    if environment == "tikzpicture":
        return end, "Diagram", snippet, _labels_in_snippet(snippet), "Diagram"

    argument_count, has_optional_label = VISUAL_ENVIRONMENTS[environment]
    cursor = match.end()
    arguments: list[str] = []
    for index in range(argument_count):
        value, cursor = _next_argument(
            text,
            cursor,
            f"{environment} argument {index + 1}",
        )
        arguments.append(value)
    label = None
    if has_optional_label:
        label, _ = _optional_label(text, cursor, f"{environment} label")
    labels = list(_labels_in_snippet(snippet))
    if label is not None and label not in labels:
        labels.insert(0, label)
    if environment == "BedrockVectorExample":
        # The PDF caption and the image's text alternative are separately
        # authored.  Existing generic consumers continue to receive title=alt.
        return end, arguments[4], snippet, tuple(labels), arguments[3]
    title = arguments[3] if environment.endswith("TikzDiagram") else arguments[0]
    return end, title, snippet, tuple(labels), title


def _replacement(visual: VisualSpec, title_tex: str) -> str:
    labels = "".join(f"\\phantomsection\\label{{{label}}}\n" for label in visual.labels)
    # Keep site-only reader text intact across Pandoc's LaTeX and GFM writers.
    # Escaped ampersands become literal entity spellings in the Markdown, while
    # the Unicode dash avoids Pandoc dropping the TeX textemdash command.
    title_tex = (
        title_tex.replace(r"\textless{}", r"\&lt;")
        .replace(r"\textgreater{}", r"\&gt;")
        .replace(r"\textemdash{}", "—")
    )
    return (
        "\n\n"
        + labels
        + "\\begin{center}\n"
        + f"\\includegraphics{{{visual.marker}}}\n"
        + f"\\par\\smallskip\\textbf{{{title_tex}}}\n"
        + "\\end{center}\n\n"
    )


def extract_visuals(
    document: str,
    text: str,
    structure: LatexStructure,
) -> VisualizedLatex:
    """Replace document-body visual constructs with stable image markers."""
    code = mask_tex_code(text)
    body_start = code.find(DOCUMENT_BEGIN)
    body_end = code.rfind(DOCUMENT_END)
    if body_start < 0 or body_end < body_start:
        raise SiteVisualError(f"{document}: expanded LaTeX has no document body")
    cursor = body_start + len(DOCUMENT_BEGIN)
    replacements: list[tuple[int, int, str]] = []
    visuals: list[VisualSpec] = []
    owner_counts: dict[str, int] = {}
    markers: set[str] = set()

    while True:
        match = VISUAL_BEGIN_RE.search(code, cursor, body_end)
        if match is None:
            break
        parsed = _environment_visual(text, match)
        if parsed is None:
            cursor = _environment_end(
                text, match.group("environment") or "", match.start()
            )
            continue
        end, title_tex, snippet, labels, caption_tex = parsed
        owner = _owner_at(structure, match.start())
        ordinal = owner_counts.get(owner, 0) + 1
        owner_counts[owner] = ordinal
        visual_id = f"{_slug(owner)}-{ordinal:02d}"
        asset = PurePosixPath("assets") / "visuals" / document / f"{visual_id}.svg"
        marker = (
            PurePosixPath("_site_visual") / document / f"{visual_id}.svg"
        ).as_posix()
        if marker in markers:
            raise SiteVisualError(f"{document}: duplicate visual marker {marker}")
        markers.add(marker)
        visual = VisualSpec(
            id=visual_id,
            title=_plain_title(title_tex),
            caption_tex=caption_tex,
            source=snippet,
            marker=marker,
            asset=asset,
            start=match.start(),
            end=end,
            labels=labels,
        )
        visuals.append(visual)
        replacements.append((match.start(), end, _replacement(visual, caption_tex)))
        cursor = end

    transformed = text
    for start, end, replacement in reversed(replacements):
        transformed = transformed[:start] + replacement + transformed[end:]
    return VisualizedLatex(transformed, tuple(visuals))
