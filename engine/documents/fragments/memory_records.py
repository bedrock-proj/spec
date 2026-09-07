"""Selected memory-record components, offsets, and padding."""
from __future__ import annotations
from dataclasses import dataclass
from engine.isa.model import DocumentTopic
from engine.isa.memory_records import LinearByteExpression, MemoryRecord, MemoryRecordComponent

@dataclass(frozen=True, slots=True)
class MemoryRecordComponentProjection:
    """One component projected at its derived byte offset."""

    component: MemoryRecordComponent
    offset: LinearByteExpression
    size: LinearByteExpression


@dataclass(frozen=True, slots=True)
class MemoryRecordPaddingProjection:
    """Derived trailing storage needed to meet record alignment."""

    offset: LinearByteExpression
    values: tuple[int, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", tuple(self.values))



@dataclass(frozen=True, slots=True)
class MemoryRecordProjection:
    """The public layout selected by one memory-record directive."""

    record: MemoryRecord
    components: tuple[MemoryRecordComponentProjection, ...]
    padding: MemoryRecordPaddingProjection | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "components", tuple(self.components))



def project_record_layout( record: MemoryRecord) -> "MemoryRecordProjection":
    cursor = LinearByteExpression()
    components: list[MemoryRecordComponentProjection] = []
    for component in record.components:
        size = component.element_bytes.expression() * component.count
        components.append(MemoryRecordComponentProjection(component, cursor, size))
        cursor += size

    padding_values = tuple(
        sorted(
            {
                record.padding_bytes(parameter_value)
                for parameter_value in record.parameter_values
            }
        )
    )
    padding = None
    if any(padding_values):
        padding = MemoryRecordPaddingProjection(
            cursor,
            padding_values,
        )
    return MemoryRecordProjection(record, tuple(components), padding)


def select_memory_record(reference, *, owner, catalog) -> MemoryRecordProjection:
    record = catalog.resolve(reference)
    if not isinstance(owner, DocumentTopic) or reference.owner != owner.reference.owner:
        raise ValueError(f"memory record {reference!r} does not belong to the selected source owner")
    return project_record_layout(record)
