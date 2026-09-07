"""Renderer-independent strings containing typed semantic references."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from engine.reference import Reference, ReferenceError



_ESCAPE_START_RE = re.compile(r"\(:([a-z][a-z0-9_-]*):")


class SemanticTextError(ValueError):
    """Raised when a semantic-text escape is malformed."""

    def __init__(
        self,
        reason: "SemanticTextErrorReason",
        origin: "TextOrigin",
        offset: int,
        detail: str,
    ) -> None:
        self.reason = reason
        self.origin = origin
        self.offset = offset
        self.detail = detail
        location = ".".join(map(str, origin.path))
        where = f" at {location}" if location else ""
        super().__init__(f"{origin.source}{where}, offset {offset}: {detail}")


class SemanticTextErrorReason(StrEnum):
    NON_STRING = "non_string"
    UNTERMINATED_ESCAPE = "unterminated_escape"
    UNKNOWN_ESCAPE_KIND = "unknown_escape_kind"
    INVALID_ESCAPE_PAYLOAD = "invalid_escape_payload"
    MODIFIED_ENTITY_REFERENCE = "modified_entity_reference"
    INVALID_REFERENCE = "invalid_reference"
    UNKNOWN_TERM_FORM = "unknown_term_form"


class TermForm(StrEnum):
    """A registered presentation of one terminology entry."""

    CANONICAL = "canonical"
    PLURAL = "plural"
    ADJECTIVE = "adjective"
    SHORT = "short"
    FIRST = "first"


@dataclass(frozen=True, slots=True)
class TextOrigin:
    """The authored location from which a semantic string was loaded."""

    source: Path
    path: tuple[str | int, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", tuple(self.path))



@dataclass(frozen=True, slots=True)
class LiteralText:
    value: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class EntityReferenceText:
    reference: Reference
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class TermReferenceText:
    reference: Reference
    form: TermForm
    start: int
    end: int


SemanticTextPart = LiteralText | EntityReferenceText | TermReferenceText


@dataclass(frozen=True, slots=True)
class SemanticText:
    """An immutable parsed string using ``(:ref:...:)`` style escapes."""

    raw: str
    origin: TextOrigin
    parts: tuple[SemanticTextPart, ...]


    def __post_init__(self) -> None:
        object.__setattr__(self, "parts", tuple(self.parts))

    @classmethod
    def parse(cls, raw: str, *, origin: TextOrigin) -> "SemanticText":
        if not isinstance(raw, str):
            raise SemanticTextError(
                SemanticTextErrorReason.NON_STRING,
                origin,
                0,
                "semantic text must be a string",
            )
        parts: list[SemanticTextPart] = []
        cursor = 0
        while match := _ESCAPE_START_RE.search(raw, cursor):
            if match.start() > cursor:
                parts.append(
                    LiteralText(raw[cursor : match.start()], cursor, match.start())
                )
            end = raw.find(":)", match.end())
            if end < 0:
                raise SemanticTextError(
                    SemanticTextErrorReason.UNTERMINATED_ESCAPE,
                    origin,
                    match.start(),
                    "unterminated semantic escape",
                )
            kind = match.group(1)
            payload = raw[match.end() : end]
            escape_end = end + 2
            parts.append(
                parse_escape(kind, payload, origin, match.start(), escape_end)
            )
            cursor = escape_end
        if cursor < len(raw) or not parts:
            parts.append(LiteralText(raw[cursor:], cursor, len(raw)))
        return cls(raw, origin, tuple(parts))

    @property
    def dependencies(self) -> tuple[Reference, ...]:
        return tuple(
            part.reference
            for part in self.parts
            if isinstance(part, (EntityReferenceText, TermReferenceText))
        )


def parse_escape(
    kind: str,
    payload: str,
    origin: TextOrigin,
    start: int,
    end: int,
) -> SemanticTextPart:
    if kind not in {"ref", "term"}:
        raise SemanticTextError(
            SemanticTextErrorReason.UNKNOWN_ESCAPE_KIND,
            origin,
            start,
            f"unknown semantic escape kind {kind!r}",
        )
    pieces = payload.split("|")
    if not pieces[0] or len(pieces) > 2:
        raise SemanticTextError(
            SemanticTextErrorReason.INVALID_ESCAPE_PAYLOAD,
            origin,
            start,
            f"invalid {kind} escape payload",
        )
    if kind == "ref":
        if len(pieces) != 1:
            raise SemanticTextError(
                SemanticTextErrorReason.MODIFIED_ENTITY_REFERENCE,
                origin,
                start,
                "ref escapes do not accept a modifier",
            )
        try:
            entity_reference: Reference[Entity] = Reference.parse(pieces[0])
        except ReferenceError as error:
            raise SemanticTextError(
                SemanticTextErrorReason.INVALID_REFERENCE,
                origin,
                start,
                str(error),
            ) from error
        return EntityReferenceText(entity_reference, start, end)
    try:
        term_reference: Reference = Reference.parse(pieces[0])
    except ReferenceError as error:
        raise SemanticTextError(
            SemanticTextErrorReason.INVALID_REFERENCE,
            origin,
            start,
            str(error),
        ) from error
    raw_form = pieces[1] if len(pieces) == 2 else TermForm.CANONICAL.value
    try:
        form = TermForm(raw_form)
    except ValueError as error:
        raise SemanticTextError(
            SemanticTextErrorReason.UNKNOWN_TERM_FORM,
            origin,
            start,
            f"unknown term form {raw_form!r}",
        ) from error
    return TermReferenceText(term_reference, form, start, end)


