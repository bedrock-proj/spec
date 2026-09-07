"""Values and operations owned by this specification boundary."""

from __future__ import annotations

from dataclasses import dataclass

from engine.entity import Entity, EntityPresentation
from engine.isa.terminology import Term, TermGroup
from engine.syntax.semantic_text import (
    TermForm, SemanticText, TextOrigin, LiteralText, EntityReferenceText, TermReferenceText,
)


@dataclass(frozen=True, slots=True)
class ResolvedTextReference:
    occurrence: EntityReferenceText | TermReferenceText
    entity: Entity
    presentation: EntityPresentation


@dataclass(frozen=True, slots=True)
class ResolvedSemanticText:
    raw: str
    origin: TextOrigin
    parts: tuple[LiteralText | ResolvedTextReference, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "parts", tuple(self.parts))



def resolve_text_reference(part, *, entities, terms) -> ResolvedTextReference:
    entity = entities.resolve(part.reference)
    if isinstance(part, TermReferenceText):
        term = terms.resolve(part.reference)
        if entity is not term:
            raise ValueError("semantic term reference does not preserve canonical identity")
        presentation = EntityPresentation(resolve_term_form(term, part.form))
    elif isinstance(part, EntityReferenceText):
        presentation = entities.presentation(part.reference)
    else:
        raise TypeError(f"unsupported semantic reference: {type(part).__name__}")
    return ResolvedTextReference(part, entity, presentation)


def resolve_semantic_text(text: SemanticText, *, entities, terms) -> ResolvedSemanticText:
    return ResolvedSemanticText(
        text.raw, text.origin,
        tuple(part if isinstance(part, LiteralText) else resolve_text_reference(part, entities=entities, terms=terms) for part in text.parts),
    )


def project_term_group(group: TermGroup, *, entities, terms):
    return tuple((term, resolve_semantic_text(term.definition, entities=entities, terms=terms)) for term in group.terms.values())


def selected_term_targets(group: TermGroup):
    return (group.reference, *(term.reference for term in group.terms.values()))


def resolve_term_form(term: Term, form: TermForm) -> str:
    if form is TermForm.CANONICAL:
        return term.forms.canonical
    if form is TermForm.PLURAL and term.forms.plural is not None:
        return term.forms.plural
    if form is TermForm.ADJECTIVE and term.forms.adjective is not None:
        return term.forms.adjective
    if term.abbreviation is not None:
        if form is TermForm.SHORT:
            return term.abbreviation.canonical
        if form is TermForm.FIRST:
            return f"{term.forms.canonical} ({term.abbreviation.canonical})"
    raise ValueError(
        f"term {term.forms.canonical!r} does not define form {form.value!r}"
    )
