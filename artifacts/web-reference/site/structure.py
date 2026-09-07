#!/usr/bin/env python3
"""Discover page-owned document structure in expanded Bedrock LaTeX."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .navigation import ANCHOR_RE, SiteError
from engine.syntax.tex import mask_tex_code

DIVISION_RE = re.compile(r"\\(?P<kind>part|section)(?P<star>\*)?\s*\{")
LABEL_RE = re.compile(r"\\label\s*\{")
TITLE_PAGE_RE = re.compile(r"\\BedrockMakeTitlePage\s*\{")
INSTRUCTION_RE = re.compile(r"\\begin\{BedrockInstruction\}\s*\{")


@dataclass(frozen=True)
class DocumentTitle:
    title: str
    subtitle: str | None


@dataclass(frozen=True)
class PartSpec:
    key: str
    title: str
    start: int


@dataclass(frozen=True)
class SectionSpec:
    key: str
    title: str
    part: str | None
    labels: tuple[str, ...]
    start: int
    body_start: int
    end: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", tuple(self.labels))


@dataclass(frozen=True)
class LabelSpec:
    name: str
    start: int


@dataclass(frozen=True)
class InstructionSpec:
    mnemonic: str
    title: str
    label: str
    start: int


@dataclass(frozen=True)
class LatexStructure:
    title: DocumentTitle
    parts: tuple[PartSpec, ...]
    sections: tuple[SectionSpec, ...]
    labels: tuple[LabelSpec, ...]
    instructions: tuple[InstructionSpec, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "parts", tuple(self.parts))
        object.__setattr__(self, "sections", tuple(self.sections))
        object.__setattr__(self, "labels", tuple(self.labels))
        object.__setattr__(self, "instructions", tuple(self.instructions))


def _document_bounds(masked: str) -> tuple[int, int]:
    begin = masked.find(r"\begin{document}")
    end = masked.rfind(r"\end{document}")
    if begin < 0 or end < 0 or begin >= end:
        raise SiteError("expanded LaTeX must contain one complete document body")
    return begin + len(r"\begin{document}"), end


def _braced_value(text: str, open_brace: int, where: str) -> tuple[str, int]:
    if open_brace >= len(text) or text[open_brace] != "{":
        raise SiteError(f"{where}: expected opening brace")
    depth = 0
    index = open_brace
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace + 1 : index], index + 1
            if depth < 0:
                break
        index += 1
    raise SiteError(f"{where}: unterminated braced value")


def _plain_title(value: str, where: str) -> str:
    title = " ".join(value.split())
    if not title:
        raise SiteError(f"{where}: title must not be empty")
    if "\\" in title or "{" in title or "}" in title:
        raise SiteError(
            f"{where}: page titles must be plain text at the structural boundary: {title!r}"
        )
    return title


def _following_labels(
    original: str,
    masked: str,
    start: int,
    body_end: int,
    where: str,
) -> tuple[tuple[str, ...], int]:
    labels: list[str] = []
    cursor = start
    while cursor < body_end:
        while cursor < body_end and masked[cursor].isspace():
            cursor += 1
        match = LABEL_RE.match(masked, cursor)
        if match is None:
            break
        value, cursor = _braced_value(original, match.end() - 1, where)
        label = value.strip()
        if not label or any(char.isspace() for char in label):
            raise SiteError(f"{where}: invalid label {label!r}")
        labels.append(label)
    return tuple(labels), cursor


def _required_owned_label(labels: tuple[str, ...], prefix: str, where: str) -> str:
    owned = [label.removeprefix(prefix) for label in labels if label.startswith(prefix)]
    if len(owned) != 1:
        raise SiteError(
            f"{where}: expected exactly one {prefix!r} ownership label, found {owned}"
        )
    key = owned[0]
    if not ANCHOR_RE.fullmatch(key):
        raise SiteError(f"{where}: invalid stable page identifier {key!r}")
    return key


def _title_page(
    original: str, masked: str, body_start: int, body_end: int
) -> DocumentTitle:
    match = TITLE_PAGE_RE.search(masked, body_start, body_end)
    if match is None:
        raise SiteError("document body has no Bedrock title-page declaration")
    title_raw, cursor = _braced_value(original, match.end() - 1, "document title")
    while cursor < body_end and masked[cursor].isspace():
        cursor += 1
    if cursor >= body_end or masked[cursor] != "{":
        raise SiteError("document title: missing subtitle argument")
    subtitle_raw, _ = _braced_value(original, cursor, "document subtitle")
    return DocumentTitle(
        _plain_title(title_raw, "document title"),
        (
            _plain_title(subtitle_raw, "document subtitle")
            if subtitle_raw.split()
            else None
        ),
    )


def parse_latex_structure(text: str) -> LatexStructure:
    """Parse page-owning parts and sections from one expanded LaTeX document."""
    masked = mask_tex_code(text)
    body_start, body_end = _document_bounds(masked)
    title = _title_page(text, masked, body_start, body_end)

    raw_divisions: list[dict[str, object]] = []
    current_part: str | None = None
    for match in DIVISION_RE.finditer(masked, body_start, body_end):
        kind = match.group("kind")
        title_raw, after_title = _braced_value(
            text, match.end() - 1, f"{kind} at byte {match.start()}"
        )
        division_title = _plain_title(title_raw, f"{kind} at byte {match.start()}")
        labels, content_start = _following_labels(
            text,
            masked,
            after_title,
            body_end,
            f"{kind} {division_title!r}",
        )
        prefix = "part:" if kind == "part" else "page:"
        key = _required_owned_label(labels, prefix, f"{kind} {division_title!r}")
        if kind == "part":
            current_part = key
        raw_divisions.append(
            {
                "kind": kind,
                "key": key,
                "title": division_title,
                "part": current_part if kind == "section" else None,
                "labels": labels,
                "start": match.start(),
                "body_start": content_start,
            }
        )

    parts: list[PartSpec] = []
    sections: list[SectionSpec] = []
    seen_parts: set[str] = set()
    seen_sections: set[str] = set()
    for index, division in enumerate(raw_divisions):
        end = (
            int(raw_divisions[index + 1]["start"])
            if index + 1 < len(raw_divisions)
            else body_end
        )
        key = str(division["key"])
        if division["kind"] == "part":
            if key in seen_parts:
                raise SiteError(f"duplicate part page identifier {key!r}")
            seen_parts.add(key)
            parts.append(PartSpec(key, str(division["title"]), int(division["start"])))
            continue
        if key in seen_sections:
            raise SiteError(f"duplicate section page identifier {key!r}")
        seen_sections.add(key)
        sections.append(
            SectionSpec(
                key=key,
                title=str(division["title"]),
                part=str(division["part"]) if division["part"] is not None else None,
                labels=tuple(str(label) for label in division["labels"]),
                start=int(division["start"]),
                body_start=int(division["body_start"]),
                end=end,
            )
        )

    if not sections:
        raise SiteError("expanded LaTeX document has no page-owned sections")
    if parts and any(section.part is None for section in sections):
        raise SiteError("a document with parts has a section outside every part")

    labels: list[LabelSpec] = []
    seen_labels: set[str] = set()
    for match in LABEL_RE.finditer(masked, body_start, body_end):
        value, _ = _braced_value(
            text, match.end() - 1, f"label at byte {match.start()}"
        )
        name = value.strip()
        if not name or any(char.isspace() for char in name):
            raise SiteError(f"label at byte {match.start()}: invalid label {name!r}")
        if name in seen_labels:
            raise SiteError(f"duplicate source label {name!r}")
        seen_labels.add(name)
        labels.append(LabelSpec(name, match.start()))

    instructions: list[InstructionSpec] = []
    seen_instruction_labels: set[str] = set()
    for match in INSTRUCTION_RE.finditer(masked, body_start, body_end):
        mnemonic_raw, cursor = _braced_value(
            text, match.end() - 1, f"instruction at byte {match.start()} mnemonic"
        )
        arguments: list[str] = []
        for argument_name in ("title", "label"):
            while cursor < body_end and masked[cursor].isspace():
                cursor += 1
            if cursor >= body_end or masked[cursor] != "{":
                raise SiteError(
                    f"instruction at byte {match.start()}: missing {argument_name} argument"
                )
            value, cursor = _braced_value(
                text, cursor, f"instruction at byte {match.start()} {argument_name}"
            )
            arguments.append(value)
        mnemonic = _plain_title(
            mnemonic_raw, f"instruction at byte {match.start()} mnemonic"
        )
        instruction_title = _plain_title(
            arguments[0], f"instruction at byte {match.start()} title"
        )
        label = arguments[1].strip()
        if not label.startswith("instr:") or any(char.isspace() for char in label):
            raise SiteError(f"instruction {mnemonic}: invalid semantic label {label!r}")
        if label in seen_instruction_labels or label in seen_labels:
            raise SiteError(f"duplicate instruction source label {label!r}")
        seen_instruction_labels.add(label)
        instructions.append(
            InstructionSpec(mnemonic, instruction_title, label, match.start())
        )

    return LatexStructure(
        title,
        tuple(parts),
        tuple(sections),
        tuple(labels),
        tuple(instructions),
    )
