"""Explicit selection of canonical structured fields as public targets."""
from __future__ import annotations
from dataclasses import dataclass
from engine.entity import Entity
from engine.isa.cpuid import CpuidField
from engine.isa.debug_triggers import DebugTriggerField, DebugTriggerWord
from engine.isa.event_structures import EventFrameField, EventFrameSlot, EventPayloadField
from engine.isa.instruction_headers import InstructionHeaderField
from engine.isa.page_tables import PageTableEntryField
from engine.isa.registers import RegisterField

@dataclass(frozen=True, slots=True)
class StructuredFieldTarget:
    entity: Entity


def select_structured_field(name, reference, *, entities) -> StructuredFieldTarget:
    types = {
        "debug-trigger-target": (DebugTriggerWord, DebugTriggerField),
        "event-structure-target": (EventFrameSlot, EventFrameField, EventPayloadField),
        "instruction-header-field-target": (InstructionHeaderField,),
        "cpuid-field-target": (CpuidField,),
        "pte-field-target": (PageTableEntryField,),
        "register-field-target": (RegisterField,),
    }
    try:
        expected = types[name]
    except KeyError as error:
        raise ValueError(f"unknown structured-field directive {name!r}") from error
    entity = entities.resolve(reference)
    if not isinstance(entity, expected):
        raise ValueError(f"{name} resolves to {type(entity).__name__}: {reference!r}")
    return StructuredFieldTarget(entity)
