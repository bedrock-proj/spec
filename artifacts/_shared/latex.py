"""Values and operations owned by this specification boundary."""

from __future__ import annotations

from engine.documents.sources import AuthoredSourceProjection
from engine.documents.sources import SemanticSourceProjection
from engine.documents.sources import StyleSourceProjection
from engine.documents.targets import PublicTargetCatalog

from collections import Counter
from dataclasses import asdict
from dataclasses import dataclass
from enum import StrEnum
from collections.abc import Mapping
from types import MappingProxyType
import json
import re


def tex_escape(value: object) -> str:
    """Escape untrusted plain text before insertion into TeX."""

    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
        "|": r"\textbar{}",
        "<": r"\textless{}",
        ">": r"\textgreater{}",
        "'": r"\textquotesingle{}",
    }
    return "".join(replacements.get(character, character) for character in str(value))


def tex_code(value: object) -> str:
    return r"\texttt{" + tex_escape(value).replace("--", r"{-}{-}") + "}"


def tex_reference_code(value: object) -> str:
    """Render a code entity with safe breakpoints at qualified-name separators."""

    return (
        tex_code(value)
        .replace(r"\_", r"\_\allowbreak{}")
        .replace(".", r".\allowbreak{}")
    )


def _label_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


@dataclass(frozen=True, slots=True)
class TexValidationIssue:
    """One machine-readable document-validation failure."""

    code: "TexValidationCode"
    actual: int | None = None
    expected: int | None = None
    values: tuple[str, ...] = ()
    counts: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", tuple(self.values))
        object.__setattr__(self, "counts", tuple(tuple(pair) for pair in self.counts))


class TexValidationCode(StrEnum):
    DOCUMENT_ENVIRONMENT_COUNT = "document_environment_count"
    UNRESOLVED_PLACEHOLDERS = "unresolved_placeholders"
    DUPLICATE_PUBLIC_TARGETS = "duplicate_public_targets"
    UNRESOLVED_PUBLIC_TARGETS = "unresolved_public_targets"


@dataclass(frozen=True, slots=True)
class TexValidationReport:
    passed: bool
    issues: tuple[TexValidationIssue, ...]
    quantitative: Mapping[str, int]
    qualitative_review: Mapping[str, str]

    def __post_init__(self):
        object.__setattr__(self, "issues", tuple(self.issues))
        object.__setattr__(self, "quantitative", MappingProxyType(dict(self.quantitative)))
        object.__setattr__(self, "qualitative_review", MappingProxyType(dict(self.qualitative_review)))

    def render_json(self) -> str:
        return json.dumps({"passed": self.passed, "issues": [asdict(issue) for issue in self.issues], "quantitative": dict(self.quantitative), "qualitative_review": dict(self.qualitative_review)}, indent=2, sort_keys=True) + "\n"


def validate_tex(
    tex: str,
) -> TexValidationReport:
    from engine.syntax.tex import mask_tex_code

    byte_count = len(tex.encode("utf-8"))
    tex = mask_tex_code(tex)
    issues: list[TexValidationIssue] = []
    document_begins = tex.count(r"\begin{document}")
    document_ends = tex.count(r"\end{document}")
    if document_begins != 1 or document_ends != 1:
        issues.append(
            TexValidationIssue(
                TexValidationCode.DOCUMENT_ENVIRONMENT_COUNT,
                counts=(("begin", document_begins), ("end", document_ends)),
            )
        )
    placeholders = sorted(set(PLACEHOLDER_RE.findall(tex)))
    if placeholders:
        issues.append(
            TexValidationIssue(
                TexValidationCode.UNRESOLVED_PLACEHOLDERS,
                values=tuple(placeholders),
            )
        )
    targets = (
        LABEL_RE.findall(tex)
        + INSTRUCTION_TARGET_RE.findall(tex)
        + LISTED_DIAGRAM_TARGET_RE.findall(tex)
    )
    duplicate_targets = sorted(
        target for target, count in Counter(targets).items() if count > 1
    )
    if duplicate_targets:
        issues.append(
            TexValidationIssue(
                TexValidationCode.DUPLICATE_PUBLIC_TARGETS,
                values=tuple(duplicate_targets),
            )
        )
    references = set(
        HYPER_REFERENCE_RE.findall(tex)
        + STANDARD_REFERENCE_RE.findall(tex)
        + SUMMARY_REFERENCE_RE.findall(tex)
    )
    missing_targets = sorted(references.difference(targets))
    if missing_targets:
        issues.append(
            TexValidationIssue(
                TexValidationCode.UNRESOLVED_PUBLIC_TARGETS,
                values=tuple(missing_targets),
            )
        )
    return TexValidationReport(
        passed=not issues,
        issues=tuple(issues),
        quantitative={
            "bytes": byte_count,
            "public_targets": len(set(targets)),
            "public_references": len(references),
        },
        qualitative_review={},
    )


def strip_tex_comments(text: str) -> str:
    """Remove comment contents, retaining the percent that suppresses newlines."""
    result = []
    index = 0
    while index < len(text):
        character = text[index]
        result.append(character)
        index += 1
        if character == "\\" and index < len(text):
            result.append(text[index])
            index += 1
        elif character == "%":
            while index < len(text) and text[index] != "\n":
                index += 1
    return "".join(result)


PLACEHOLDER_RE = re.compile(r"@[A-Z0-9_]+@")

LABEL_RE = re.compile(r"\\label\{([^{}]+)\}")

HYPER_REFERENCE_RE = re.compile(r"\\hyperref\[([^\]]+)\]")

STANDARD_REFERENCE_RE = re.compile(r"\\(?:auto|page)?ref\{([^{}]+)\}")

INSTRUCTION_TARGET_RE = re.compile(
    r"^\\begin\{BedrockInstruction\}.*\{([^{}\n]+)\}\s*$", re.MULTILINE
)

LISTED_DIAGRAM_TARGET_RE = re.compile(
    r"^\\begin\{BedrockListed(?:Bit|Format)Diagram\}.*\[([^\]\n]+)\]\s*$",
    re.MULTILINE,
)

SUMMARY_REFERENCE_RE = re.compile(r"\\BedrockSummaryMnemonic\{([^{}]+)\}")


from engine.documents.sources import SourceInputProjection, SourceReferenceProjection
from engine.documents.terminology import ResolvedSemanticText
from engine.syntax.semantic_text import LiteralText
from engine.entity import EntityDisplayStyle


def create_target_labels(targets: PublicTargetCatalog, explicit_labels):
    unknown = set(explicit_labels) - set(targets.selected)
    if unknown:
        raise ValueError(f"labels supplied for unselected public targets: {unknown}")
    labels, reverse = {}, {}
    for reference in targets.selected:
        label = explicit_labels[reference] if reference in explicit_labels else "entity:" + _label_slug(".".join((reference.owner, *reference.path, reference.element)))
        if not isinstance(label, str) or not label or any(char in label for char in "{}\n\r"):
            raise ValueError(f"invalid public target label: {label!r}")
        if label in reverse:
            raise ValueError(f"public label {label!r} is shared by {reference!r} and {reverse[label]!r}")
        reverse[label] = reference
        labels[reference] = label
    return MappingProxyType(labels)


def target_label(labels, reference) -> str:
    try:
        return labels[reference]
    except KeyError as error:
        raise ValueError(f"no label for selected public reference {reference!r}") from error


def _render_reference(reference, labels) -> str:
    presentation = reference.presentation
    display = tex_reference_code(presentation.display) if presentation.display_style is EntityDisplayStyle.CODE else tex_escape(presentation.display)
    return rf"\hyperref[{target_label(labels, reference.entity.reference)}]{{{display}}}"


def render_semantic_text(text: ResolvedSemanticText, labels, *, escape_literals=True) -> str:
    return "".join(
        (tex_escape(part.value) if escape_literals else part.value)
        if isinstance(part, LiteralText) else _render_reference(part, labels)
        for part in text.parts
    )


def render_source_projection(projection: AuthoredSourceProjection, labels, render_fragment) -> str:
    parts, cursor = [], 0
    for part in projection.parts:
        parts.append(projection.raw[cursor:part.span.start])
        if isinstance(part, SourceInputProjection):
            content = render_source_projection(part.source, labels, render_fragment)
            if isinstance(part.source, StyleSourceProjection):
                content = re.sub(r"(?m)^\\endinput\s*$", "", content).rstrip()
            parts.append(f"% begin input: {part.requested}\n{content}\n% end input: {part.requested}")
        elif isinstance(part, SourceReferenceProjection):
            parts.append(_render_reference(part.reference, labels))
        else:
            parts.append(render_fragment(part.selection, labels))
        cursor = part.span.end
    parts.append(projection.raw[cursor:])
    return "".join(parts)


def render_latex_source(projection: AuthoredSourceProjection, labels, render_fragment) -> str:
    return render_source_projection(projection, labels, render_fragment).rstrip()
