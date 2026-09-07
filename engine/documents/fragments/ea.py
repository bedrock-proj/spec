"""Canonical EA selections for encoding and evaluation diagrams."""
from __future__ import annotations
from dataclasses import dataclass
from engine.isa.model import DocumentTopic
from engine.isa.ea import EAMode, EAEncoding, ResolvedEAEncoding, EACapture, EARegisterUpdate, EACalculate, resolve_ea_encoding, ea_evaluation_steps, ea_syntax

@dataclass(frozen=True, slots=True)
class EAModeDiagramProjection:
    mode: EAMode
    encodings: tuple[ResolvedEAEncoding, ...]
    evaluation: tuple[tuple[EACapture | EARegisterUpdate | EACalculate, ...], ...]
    syntax: tuple[str | None, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "encodings", tuple(self.encodings))
        object.__setattr__(self, "evaluation", tuple(tuple(item0) for item0 in self.evaluation))
        object.__setattr__(self, "syntax", tuple(self.syntax))



def project_mode(mode: EAMode, encoding: EAEncoding | None = None, *, field_types, payload_types) -> EAModeDiagramProjection:
    selected = mode.encodings if encoding is None else (encoding,)
    resolved = tuple(resolve_ea_encoding(mode, item, field_types=field_types, payload_types=payload_types) for item in selected)
    return EAModeDiagramProjection(mode, resolved, tuple(ea_evaluation_steps(item) for item in resolved), tuple(ea_syntax(item) for item in resolved))


def select_ea_diagram(reference, *, owner, modes, field_types, payload_types) -> EAModeDiagramProjection:
    mode = modes.resolve(reference)
    if not isinstance(owner, DocumentTopic) or reference.owner != owner.reference.owner:
        raise ValueError(f"EA mode {reference!r} does not belong to the selected source owner")
    return project_mode(mode, field_types=field_types, payload_types=payload_types)
