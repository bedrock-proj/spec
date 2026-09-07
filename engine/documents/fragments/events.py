"""Explicit event rows and event-class selections."""
from __future__ import annotations
from dataclasses import dataclass
from engine.isa.events import ArchitecturalEvent, EventClass, resolve_event_classes
from engine.reference import Reference

@dataclass(frozen=True, slots=True)
class EventCodeRow:
    """One explicitly selected fixed event-code assignment."""

    reference: Reference[ArchitecturalEvent]
    code: int
    event_id: str


def project_row_event_reference(catalog, reference: Reference[object]) -> EventCodeRow:
    """Select the public row owned by one fixed architectural event."""

    resolved = next(
        (
            item
            for item in catalog.resolved_events()
            if item.event.reference == reference
        ),
        None,
    )
    if resolved is None:
        raise KeyError(reference)
    if resolved.code.value is None:
        raise ValueError(f"event {reference!r} has no fixed event-code assignment")
    return EventCodeRow(
        resolved.event.reference,
        resolved.code.value,
        resolved.event.id,
    )


@dataclass(frozen=True, slots=True)
class EventClassesProjection:
    classes: tuple[EventClass, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "classes", tuple(self.classes))



def select_event_classes(references, *, catalog) -> EventClassesProjection:
    classes = tuple(catalog.classes.resolve(reference) for reference in references)
    if len(set(references)) != len(references):
        raise ValueError("duplicate event-class selection")
    roots, diagnostics = resolve_event_classes(catalog)
    if diagnostics:
        raise ValueError(diagnostics)
    if any(roots.resolve(item.reference) is not item for item in classes):
        raise ValueError("event-class table must select root classes")
    return EventClassesProjection(classes)
