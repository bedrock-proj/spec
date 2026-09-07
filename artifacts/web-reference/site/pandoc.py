#!/usr/bin/env python3
"""Convert expanded Bedrock LaTeX into page-owned site Markdown ASTs."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from collections.abc import Mapping
from types import MappingProxyType
from engine.source.yaml import freeze_source

from .model import scoped_target, section_page_key
from .navigation import PageRegistry, SiteError
from .structure import LatexStructure

MARKER_PREFIXES = ("part:", "page:", "instr:")
PANDOC_MINIMUM_VERSION = (3, 8)
PANDOC_GFM_WRITER = "gfm+tex_math_dollars-tex_math_gfm"
PANDOC_VERSION_RE = re.compile(r"^pandoc (?P<version>[0-9]+(?:\.[0-9]+)*)$")
ALLOWED_ANCHOR_RE = re.compile(
    r'<a href="#(?P<anchor>[a-z0-9][a-z0-9-]*)" id="(?P=anchor)"></a>'
)
HTML_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s+[^<>]*)?\s*/?>")
FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
INLINE_CODE_RE = re.compile(r"(`+)(?:[^`]|`(?!\1))*\1")
DESCRIPTION_BEGIN_RE = re.compile(r"\\begin\{description\}")
DESCRIPTION_END = r"\end{description}"
DESCRIPTION_ITEM_RE = re.compile(r"\\item\s*\[")
INSTRUCTION_BEGIN_RE = re.compile(r"\\begin\{BedrockInstruction\}\s*\{")
LONGTABLE_CONTINUATION_RE = re.compile(r"\\endfirsthead.*?\\endhead", flags=re.DOTALL)
SUPPORTED_TABLE_ENVIRONMENTS = (
    "BedrockDenseLongTable",
    "BedrockFlagEffects",
    "BedrockLongTable",
    "BedrockTabular",
    "longtable",
    "tabularx",
    "tabular",
)
TABLE_ENVIRONMENT_RE = re.compile(
    r"\\begin\{(?P<environment>"
    + "|".join(re.escape(environment) for environment in SUPPORTED_TABLE_ENVIRONMENTS)
    + r")\}"
    r".*?\\end\{(?P=environment)\}",
    flags=re.DOTALL,
)
TABLE_COMMAND_RE = re.compile(r"\\BedrockField\s*\{")
LEADING_TABLE_NUMBER_RE = re.compile(
    r"(?P<prefix>(?:^|&|\\\\)\s*)"
    r"(?P<number>[0-9]+(?:\.\.[0-9]+|--[0-9]+)?)"
    r"(?=(?:-|&|\s|\\))",
    flags=re.MULTILINE,
)
SITE_TABLE_ENVIRONMENT = MappingProxyType({
    "BedrockDenseLongTable": "longtable",
    "BedrockLongTable": "longtable",
    "BedrockTabular": "tabular",
})


class SiteMarkdownError(SiteError):
    """A source AST cannot be represented by the strict site backend."""


@dataclass(frozen=True)
class PandocAst:
    api_version: tuple[int, ...]
    meta: Mapping[str, Any]
    blocks: tuple[Mapping[str, Any], ...]

    def __post_init__(self):
        meta, blocks = freeze_source((self.meta, self.blocks))
        object.__setattr__(self, "api_version", tuple(self.api_version))
        object.__setattr__(self, "meta", meta)
        object.__setattr__(self, "blocks", blocks)


@dataclass(frozen=True)
class PageAst:
    key: str
    base_heading_level: int
    blocks: tuple[Mapping[str, Any], ...]

    def __post_init__(self):
        object.__setattr__(self, "blocks", freeze_source(self.blocks))


@dataclass(frozen=True)
class RenderedPage:
    markdown: str
    emitted_targets: frozenset[str]
    emitted_visuals: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "emitted_targets", frozenset(self.emitted_targets))
        object.__setattr__(self, "emitted_visuals", frozenset(self.emitted_visuals))


def _editable_ast(value):
    memo = {}
    def detach(item):
        if not isinstance(item, (Mapping, tuple, list)):
            return item
        identity = id(item)
        if identity in memo:
            return memo[identity]
        if isinstance(item, Mapping):
            result = {}
            memo[identity] = result
            result.update((key, detach(child)) for key, child in item.items())
        else:
            result = []
            memo[identity] = result
            result.extend(detach(child) for child in item)
        return result
    return detach(value)


def _balanced_value(
    text: str,
    open_index: int,
    opening: str,
    closing: str,
    where: str,
) -> tuple[str, int]:
    if open_index >= len(text) or text[open_index] != opening:
        raise SiteMarkdownError(f"{where}: expected {opening!r}")
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
    raise SiteMarkdownError(f"{where}: unterminated {opening!r} value")


def _replace_command(
    text: str,
    command: str,
    argument_count: int,
    render: Any,
) -> str:
    pattern = re.compile(rf"\\{re.escape(command)}\s*\{{")
    cursor = 0
    output: list[str] = []
    while True:
        match = pattern.search(text, cursor)
        if match is None:
            output.append(text[cursor:])
            break
        output.append(text[cursor : match.start()])
        argument_cursor = match.end() - 1
        arguments: list[str] = []
        for index in range(argument_count):
            while argument_cursor < len(text) and text[argument_cursor].isspace():
                argument_cursor += 1
            value, argument_cursor = _balanced_value(
                text,
                argument_cursor,
                "{",
                "}",
                f"{command} argument {index + 1}",
            )
            arguments.append(value)
        output.append(render(*arguments))
        cursor = argument_cursor
    return "".join(output)


def _replace_instruction_environments(text: str) -> str:
    cursor = 0
    output: list[str] = []
    while True:
        match = INSTRUCTION_BEGIN_RE.search(text, cursor)
        if match is None:
            output.append(text[cursor:])
            break
        output.append(text[cursor : match.start()])
        argument_cursor = match.end() - 1
        arguments: list[str] = []
        for name in ("mnemonic", "title", "label"):
            while argument_cursor < len(text) and text[argument_cursor].isspace():
                argument_cursor += 1
            value, argument_cursor = _balanced_value(
                text,
                argument_cursor,
                "{",
                "}",
                f"BedrockInstruction {name}",
            )
            arguments.append(value)
        output.append(rf"\phantomsection\label{{{arguments[2]}}}\par ")
        cursor = argument_cursor
    return "".join(output).replace(r"\end{BedrockInstruction}", r"\par ")


def _normalize_reader_macros(text: str) -> str:
    text = _replace_instruction_environments(text)
    text = text.replace(
        r"\BedrockInstructionRestrictionsHeading",
        r"\par ",
    )
    text = _replace_command(
        text,
        "BedrockField",
        2,
        lambda label, value: (
            rf"\begin{{tabular}}{{ll}}\textbf{{{label}}} & {value}"
            rf"\end{{tabular}}"
        ),
    )
    text = _replace_command(
        text,
        "BedrockInstructionField",
        2,
        lambda label, value: rf"\subsection*{{{label}}}\par {value}\par ",
    )
    text = _replace_command(
        text,
        "BedrockOperationField",
        2,
        lambda label, value: rf"\subsection*{{{label}}}\par {value}\par ",
    )
    text = _replace_command(
        text,
        "BedrockInstructionStatus",
        2,
        lambda label, value: rf"\subsection*{{{label}}}\par {value}\par ",
    )
    text = _replace_command(
        text,
        "BedrockInstructionMetadata",
        4,
        lambda kind, family, privilege, length: (
            rf"\par\textit{{Class: {kind}; family: {family}; privilege: {privilege}; "
            rf"length: {length}}}\par "
        ),
    )
    text = _replace_command(
        text,
        "BedrockInstructionOperandConstraint",
        4,
        lambda role, relation, values, reason: (
            rf"\par\texttt{{{role}}} --- \texttt{{{relation}}}: "
            rf"\texttt{{{values}}} [\texttt{{{reason}}}]\par "
        ),
    )
    text = _replace_command(
        text,
        "BedrockInstructionOperandOverlap",
        3,
        lambda left, right, rule: (
            rf"\par\texttt{{{left}}}, \texttt{{{right}}} --- "
            rf"\texttt{{{rule}}}\par "
        ),
    )
    text = _replace_command(
        text,
        "BedrockTableCaption",
        1,
        lambda title: rf"\par\textbf{{{title}}}\par ",
    )
    text = _replace_command(
        text,
        "BedrockFigureCaption",
        1,
        lambda title: rf"\par\textbf{{{title}}}\par ",
    )
    text = re.sub(
        r"(?<!\\newcommand\{)\\BedrockInstructionFormsHeading\b",
        r"\\subsection*{Encodings}",
        text,
    )
    text = LONGTABLE_CONTINUATION_RE.sub("", text)
    text = TABLE_ENVIRONMENT_RE.sub(
        lambda match: LEADING_TABLE_NUMBER_RE.sub(
            lambda number: (
                number.group("prefix") + r"\mbox{" + number.group("number") + "}"
            ),
            match.group(0),
        ),
        text,
    )
    return text


def normalize_latex_for_site(text: str) -> str:
    """Normalize semantic TeX constructs that Pandoc would otherwise discard."""
    # Pandoc drops \textbar{} inside \texttt{}, which changes the authored
    # public metasyntax.  This is a reader-boundary spelling normalization.
    text = text.replace(r"\textbar{}", "|")
    text = _normalize_reader_macros(text)
    # The PDF projection keeps the semantic table wrappers.  Lower them to the
    # standard environments understood by the site reader.
    for source, target in SITE_TABLE_ENVIRONMENT.items():
        text = text.replace(rf"\begin{{{source}}}", rf"\begin{{{target}}}")
        text = text.replace(rf"\end{{{source}}}", rf"\end{{{target}}}")
    cursor = 0
    output: list[str] = []
    while True:
        match = DESCRIPTION_BEGIN_RE.search(text, cursor)
        if match is None:
            output.append(text[cursor:])
            break
        output.append(text[cursor : match.start()])
        content_start = match.end()
        if content_start < len(text) and text[content_start] == "[":
            _, content_start = _balanced_value(
                text,
                content_start,
                "[",
                "]",
                "description options",
            )
        content_end = text.find(DESCRIPTION_END, content_start)
        if content_end < 0:
            raise SiteMarkdownError("unterminated description environment")
        content = text[content_start:content_end]
        item_replacements: list[tuple[int, int, str]] = []
        for item in DESCRIPTION_ITEM_RE.finditer(content):
            label, item_end = _balanced_value(
                content,
                item.end() - 1,
                "[",
                "]",
                "description item",
            )
            item_replacements.append(
                (item.start(), item_end, rf"\par\textbf{{{label}}}\par ")
            )
        for start, end, replacement in reversed(item_replacements):
            content = content[:start] + replacement + content[end:]
        output.extend(["\n", content, "\n"])
        cursor = content_end + len(DESCRIPTION_END)
    return "".join(output)


def require_supported_pandoc(
    *,
    repository: Path,
    pandoc: str = "pandoc",
    environment: dict[str, str] | None = None,
) -> tuple[int, ...]:
    """Require the Pandoc reader/writer contract used by the site backend."""
    environment = dict(os.environ if environment is None else environment)
    result = subprocess.run(
        [pandoc, "--version"],
        text=True,
        capture_output=True,
        cwd=repository,
        env=environment,
        check=False,
    )
    first_line = result.stdout.splitlines()[0].strip() if result.stdout else ""
    match = PANDOC_VERSION_RE.fullmatch(first_line)
    if result.returncode != 0 or match is None:
        detail = (
            result.stderr.strip() or first_line or f"exit status {result.returncode}"
        )
        raise SiteMarkdownError(f"could not determine Pandoc version: {detail}")
    version = tuple(int(component) for component in match.group("version").split("."))
    if version < PANDOC_MINIMUM_VERSION:
        required = ".".join(str(component) for component in PANDOC_MINIMUM_VERSION)
        actual = ".".join(str(component) for component in version)
        raise SiteMarkdownError(
            f"Pandoc {required} or newer is required for site table conversion; "
            f"found {actual}"
        )
    return version


def _supported_table_count(text: str, where: Path) -> int:
    begin = r"\begin{document}"
    end = r"\end{document}"
    start = text.find(begin)
    finish = text.rfind(end)
    if start < 0 or finish < start:
        raise SiteMarkdownError(f"{where}: expanded LaTeX document body is missing")
    body = text[start + len(begin) : finish]
    environment_count = 0
    for name in SUPPORTED_TABLE_ENVIRONMENTS:
        begins = body.count(rf"\begin{{{name}}}")
        ends = body.count(rf"\end{{{name}}}")
        if begins != ends:
            raise SiteMarkdownError(
                f"{where}: unbalanced {name} table environment; "
                f"{begins} begin markers and {ends} end markers"
            )
        environment_count += begins
    return environment_count + len(TABLE_COMMAND_RE.findall(body))


def _unsupported_html_tags(markdown: str) -> list[str]:
    tags: set[str] = set()
    fence_character: str | None = None
    fence_length = 0
    for line in markdown.splitlines():
        fence = FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            if fence_character is None:
                fence_character = marker[0]
                fence_length = len(marker)
            elif marker[0] == fence_character and len(marker) >= fence_length:
                fence_character = None
                fence_length = 0
            continue
        if fence_character is not None:
            continue
        visible = INLINE_CODE_RE.sub("", line)
        visible = ALLOWED_ANCHOR_RE.sub("", visible)
        tags.update(HTML_TAG_RE.findall(visible))
    return sorted(tags)


def read_pandoc_ast(
    expanded_source: Path,
    *,
    repository: Path,
    pandoc: str = "pandoc",
    environment: dict[str, str] | None = None,
) -> PandocAst:
    """Read one expanded LaTeX document as Pandoc's semantic JSON AST."""
    environment = dict(os.environ if environment is None else environment)
    normalized = normalize_latex_for_site(expanded_source.read_text(encoding="utf-8"))
    # Current document artifacts expand their style implementation into the
    # preamble.  Pandoc must consume the reader body, not executable command
    # definitions containing nested TikZ environments.
    begin = normalized.find(r"\begin{document}")
    end = normalized.rfind(r"\end{document}")
    if begin < 0 or end < begin:
        raise SiteMarkdownError(f"{expanded_source}: document body is missing")
    normalized = (
        r"\begin{document}"
        + normalized[begin + len(r"\begin{document}") : end]
        + r"\end{document}"
    )
    expected_tables = _supported_table_count(normalized, expanded_source)
    result = subprocess.run(
        [pandoc, "--from=latex", "--to=json"],
        input=normalized,
        text=True,
        capture_output=True,
        cwd=repository,
        env=environment,
        check=False,
    )
    if result.returncode != 0 or result.stderr.strip():
        detail = result.stderr.strip() or f"exit status {result.returncode}"
        raise SiteMarkdownError(f"Pandoc failed to read {expanded_source}: {detail}")
    parsed = json.loads(result.stdout)
    raw_nodes: list[str] = []
    table_nodes = 0

    def inspect(value: Any) -> None:
        nonlocal table_nodes
        if isinstance(value, dict):
            if value.get("t") in {"RawBlock", "RawInline"}:
                raw_nodes.append(repr(value.get("c")))
            if value.get("t") == "Table":
                table_nodes += 1
            for child in value.values():
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)

    inspect(parsed.get("blocks", []))
    if raw_nodes:
        sample = ", ".join(raw_nodes[:3])
        raise SiteMarkdownError(
            f"{expanded_source}: Pandoc produced {len(raw_nodes)} unresolved raw nodes: {sample}"
        )
    if table_nodes != expected_tables:
        raise SiteMarkdownError(
            f"{expanded_source}: Pandoc table structure mismatch; "
            f"{expected_tables} supported LaTeX table producers emitted "
            f"{table_nodes} Table nodes"
        )
    return PandocAst(
        tuple(parsed["pandoc-api-version"]),
        dict(parsed.get("meta", {})),
        tuple(parsed.get("blocks", [])),
    )


def _attribute(node: dict[str, Any]) -> list[Any] | None:
    tag = node.get("t")
    content = node.get("c")
    if tag == "Header":
        return content[1]
    if tag in {"Code", "CodeBlock", "Div", "Image", "Link", "Span", "Table"}:
        return content[0]
    return None


def _structural_markers(block: dict[str, Any]) -> list[tuple[str, int | None]]:
    markers: list[tuple[str, int | None]] = []

    def inspect(value: Any) -> None:
        if isinstance(value, dict):
            tag = value.get("t")
            if tag in {"Header", "Span"}:
                attribute = _attribute(value)
                identifier = attribute[0] if attribute else ""
                if identifier.startswith(MARKER_PREFIXES):
                    level = int(value["c"][0]) if tag == "Header" else None
                    markers.append((identifier, level))
            if tag == "Link":
                return
            for child in value.values():
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)

    inspect(block)
    return markers


def _remove_marker(value: Any, marker: str) -> Any:
    if isinstance(value, dict):
        if value.get("t") == "Span":
            attribute = _attribute(value)
            if attribute and attribute[0] == marker:
                return None
        result: dict[str, Any] = {}
        for key, child in value.items():
            cleaned = _remove_marker(child, marker)
            if cleaned is not None:
                result[key] = cleaned
        return result
    if isinstance(value, list):
        cleaned_items = []
        for child in value:
            cleaned = _remove_marker(child, marker)
            if cleaned is not None:
                cleaned_items.append(cleaned)
        return cleaned_items
    return value


def _is_empty_block(block: dict[str, Any]) -> bool:
    return block.get("t") in {"Para", "Plain"} and not block.get("c")


def split_document_ast(
    document: str,
    structure: LatexStructure,
    ast: PandocAst,
) -> Mapping[str, PageAst]:
    """Split one document AST at explicit page and instruction boundaries."""
    pages: dict[str, list[dict[str, Any]]] = {}
    levels: dict[str, int] = {}
    current_page: str | None = None
    current_section_level: int | None = None

    for block in _editable_ast(ast.blocks):
        markers = _structural_markers(block)
        if len(markers) > 1:
            raise SiteMarkdownError(
                f"{document}: one AST block contains multiple structural markers: {markers}"
            )
        if markers:
            marker, marker_level = markers[0]
            if marker.startswith("part:"):
                if marker_level is None:
                    raise SiteMarkdownError(f"{document}: part marker is not a heading")
                current_page = None
                current_section_level = None
                continue
            if marker.startswith("page:"):
                if marker_level is None:
                    raise SiteMarkdownError(f"{document}: page marker is not a heading")
                key = section_page_key(document, marker.removeprefix("page:"))
                current_page = key
                current_section_level = marker_level
                if key in pages:
                    raise SiteMarkdownError(
                        f"{document}: duplicate AST page marker {marker!r}"
                    )
                pages[key] = []
                levels[key] = marker_level
                continue
            if marker.startswith("instr:"):
                if current_section_level is None:
                    raise SiteMarkdownError(
                        f"{document}: instruction marker {marker!r} is outside a section"
                    )
                key = f"{document}:instruction:{marker.removeprefix('instr:')}"
                current_page = key
                if key in pages:
                    raise SiteMarkdownError(
                        f"{document}: duplicate instruction AST marker {marker!r}"
                    )
                pages[key] = []
                levels[key] = current_section_level
                cleaned = _remove_marker(block, marker)
                if cleaned is not None and not _is_empty_block(cleaned):
                    pages[key].append(cleaned)
                continue
        if current_page is not None:
            pages[current_page].append(block)

    expected = {
        section_page_key(document, section.key) for section in structure.sections
    } | {
        f"{document}:instruction:{instruction.label.removeprefix('instr:')}"
        for instruction in structure.instructions
    }
    actual = set(pages)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise SiteMarkdownError(
            f"{document}: AST page ownership mismatch; missing={missing}, extra={extra}"
        )
    return MappingProxyType({
        key: PageAst(key, levels[key], tuple(blocks)) for key, blocks in pages.items()
    })


def _anchor_html(anchor: str) -> dict[str, Any]:
    return {
        "t": "RawInline",
        "c": ["html", f'<a href="#{anchor}" id="{anchor}"></a>'],
    }


def transform_page(document, page, registry, visual_titles):
    targets = registry.targets
    emitted: set[str] = set()
    emitted_visuals: set[str] = set()

    def _target_for_identifier(identifier: str) -> tuple[str, str | None] | None:
        if not identifier:
            return None
        target_name = scoped_target(document, identifier)
        target = targets.get(target_name)
        if target is None or target.page != page.key:
            return None
        return target_name, target.anchor


    def _rewrite_attribute(node: dict[str, Any]) -> str | None:
        attribute = _attribute(node)
        if attribute is None:
            return None
        identifier = attribute[0]
        target = _target_for_identifier(identifier)
        attribute[:] = ["", [], []]
        if target is None:
            return None
        target_name, anchor = target
        emitted.add(target_name)
        return anchor


    def _rewrite_link(node: dict[str, Any]) -> None:
        destination = node["c"][2][0]
        if not destination.startswith("#"):
            return
        label = destination[1:]
        target_name = scoped_target(document, label)
        if target_name in targets:
            node["c"][2][0] = registry.relative_link(page.key, target_name)


    def _rewrite_image(node: dict[str, Any]) -> None:
        destination = node["c"][2][0]
        title = visual_titles.get(destination)
        if title is None:
            return
        asset = PurePosixPath(destination).relative_to("_site_visual")
        # Entity spellings keep canonical angle operands reader-exact instead
        # of making Pandoc's GFM writer emit visible backslash escapes.
        image_text = title.replace("<", "&lt;").replace(">", "&gt;")
        node["c"][1] = _plain_inlines(image_text)
        node["c"][2][0] = registry.relative_asset(
            page.key,
            PurePosixPath("assets") / "visuals" / asset,
        )
        emitted_visuals.add(destination)


    def _rewrite_tree(value: Any) -> None:
        if isinstance(value, dict):
            tag = value.get("t")
            if tag in {"RawBlock", "RawInline"}:
                if (
                    tag == "RawInline"
                    and value.get("c", [None, None])[0] == "html"
                    and ALLOWED_ANCHOR_RE.fullmatch(value["c"][1])
                ):
                    return
                raise SiteMarkdownError(
                    f"{page.key}: source AST contains unresolved {tag}"
                )
            if tag == "Link":
                _rewrite_link(value)
            if tag == "Image":
                _rewrite_image(value)
            anchor = _rewrite_attribute(value)
            if anchor is not None:
                if tag in {"Header", "Span"}:
                    content_index = 2 if tag == "Header" else 1
                    value["c"][content_index].extend(
                        [{"t": "Space"}, _anchor_html(anchor)]
                    )
                else:
                    raise SiteMarkdownError(
                        f"{page.key}: labeled {tag} requires a block anchor insertion"
                    )
            for child in value.values():
                _rewrite_tree(child)
        elif isinstance(value, list):
            for child in value:
                _rewrite_tree(child)


    def _flatten_layout_divs(value: Any) -> None:
        if isinstance(value, dict):
            for child in value.values():
                _flatten_layout_divs(child)
            return
        if not isinstance(value, list):
            return
        flattened: list[Any] = []
        for child in value:
            if isinstance(child, dict) and child.get("t") == "Div":
                anchor = _rewrite_attribute(child)
                if anchor is not None:
                    flattened.append({"t": "Para", "c": [_anchor_html(anchor)]})
                div_blocks = child["c"][1]
                _flatten_layout_divs(div_blocks)
                flattened.extend(div_blocks)
                continue
            _flatten_layout_divs(child)
            flattened.append(child)
        value[:] = flattened


    blocks = _editable_ast(page.blocks)
    _flatten_layout_divs(blocks)
    output: list[dict[str, Any]] = []
    for block in blocks:
        if block.get("t") == "Table":
            _normalize_native_table(block)
            anchor = _rewrite_attribute(block)
            if anchor is not None:
                output.append({"t": "Para", "c": [_anchor_html(anchor)]})
            _rewrite_tree(block)
            output.append(block)
            continue
        _rewrite_tree(block)
        output.append(block)

    shift = page.base_heading_level - 1
    for block in output:
        if block.get("t") != "Header":
            continue
        original = int(block["c"][0])
        if original <= page.base_heading_level:
            raise SiteMarkdownError(
                f"{page.key}: content heading level {original} crosses its page boundary"
            )
        block["c"][0] = original - shift
    return freeze_source(output), frozenset(emitted), frozenset(emitted_visuals)



def _plain_inlines(value: str) -> list[dict[str, Any]]:
    inlines: list[dict[str, Any]] = []
    for index, word in enumerate(value.split()):
        if index:
            inlines.append({"t": "Space"})
        inlines.append({"t": "Str", "c": word})
    return inlines


def _table_rows(table: dict[str, Any]) -> list[list[Any]]:
    content = table["c"]
    rows = list(content[3][1])
    for body in content[4]:
        rows.extend(body[2])
        rows.extend(body[3])
    rows.extend(content[5][1])
    return [row[1] for row in rows]


def _join_inline_groups(groups: Iterable[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for group in groups:
        if not group:
            continue
        if result:
            result.extend([{"t": "Str", "c": ";"}, {"t": "Space"}])
        result.extend(group)
    return result


def _table_as_inlines(table: dict[str, Any]) -> list[dict[str, Any]]:
    groups: list[list[dict[str, Any]]] = []
    for row in _table_rows(table):
        for cell in row:
            groups.append(_cell_as_inlines(cell[4]))
    return _join_inline_groups(groups)


def _cell_as_inlines(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[list[dict[str, Any]]] = []
    for block in blocks:
        tag = block.get("t")
        if tag in {"Para", "Plain"}:
            groups.append(block["c"])
        elif tag == "Table":
            groups.append(_table_as_inlines(block))
        elif tag == "CodeBlock":
            groups.append(
                [
                    {
                        "t": "Code",
                        "c": [["", [], []], " ".join(block["c"][1].split())],
                    }
                ]
            )
        else:
            raise SiteMarkdownError(
                f"table cell contains unsupported block {tag}; decompose it before writing"
            )
    return _join_inline_groups(groups)


def _normalize_native_table(table: dict[str, Any]) -> None:
    """Flatten TeX makecell-style nested tables into one native pipe-table cell."""
    for row in _table_rows(table):
        for cell in row:
            blocks = cell[4]
            if len(blocks) == 1 and blocks[0].get("t") in {"Para", "Plain"}:
                blocks[0]["t"] = "Plain"
                continue
            cell[4] = [{"t": "Plain", "c": _cell_as_inlines(blocks)}]


def render_page_ast(
    page: PageAst,
    *,
    repository: Path,
    document: str,
    title: str,
    registry: PageRegistry,
    visual_titles: dict[str, str],
    api_version: Iterable[int],
    pandoc: str = "pandoc",
    environment: dict[str, str] | None = None,
) -> RenderedPage:
    """Rewrite semantic links and serialize one page as strict site Markdown."""
    body, emitted, emitted_visuals = transform_page(document, page, registry, visual_titles)
    blocks = [
        {"t": "Header", "c": [1, ["", [], []], _plain_inlines(title)]},
        *body,
    ]
    payload = {
        "pandoc-api-version": list(api_version),
        "meta": {},
        "blocks": blocks,
    }
    environment = dict(os.environ if environment is None else environment)
    result = subprocess.run(
        [
            pandoc,
            "--from=json",
            f"--to={PANDOC_GFM_WRITER}",
            "--wrap=none",
            "--markdown-headings=atx",
        ],
        input=json.dumps(_editable_ast(payload)),
        text=True,
        capture_output=True,
        cwd=repository,
        env=environment,
        check=False,
    )
    if result.returncode != 0 or result.stderr.strip():
        detail = result.stderr.strip() or f"exit status {result.returncode}"
        raise SiteMarkdownError(f"Pandoc failed to write {page.key}: {detail}")
    markdown = result.stdout.rstrip() + "\n"
    raw_tags = _unsupported_html_tags(markdown)
    if raw_tags:
        raise SiteMarkdownError(
            f"{page.key}: site Markdown contains unsupported raw HTML: "
            + ", ".join(raw_tags[:5])
        )
    return RenderedPage(markdown, emitted, emitted_visuals)
