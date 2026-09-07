"""Structured architectural-event frame and payload fields."""

from __future__ import annotations

from engine.source.inventory import inspect_inventory, require_exact

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from engine.entity import Entity
from engine.reference import Reference, ReferenceIndex, register_reference
from engine.source.inventory import DirectoryInventory
from engine.source.yaml import load_schema_yaml


@dataclass(frozen=True, slots=True)
class EventFrameField(Entity):
    reference: Reference["EventFrameField"]
    source: Path
    id: str
    lsb: int
    bits: int

    @property
    def msb(self) -> int:
        return self.lsb + self.bits - 1


@dataclass(frozen=True, slots=True)
class EventFrameSlot(Entity):
    reference: Reference["EventFrameSlot"]
    source: Path
    id: str
    offset: int
    bits: int
    fields: tuple[EventFrameField, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", tuple(self.fields))



@dataclass(frozen=True, slots=True)
class EventFrame(Entity):
    reference: Reference["EventFrame"]
    source: Path
    id: str
    slots: Mapping[str, EventFrameSlot]

    def __post_init__(self) -> None:
        object.__setattr__(self, "slots", MappingProxyType(dict(self.slots)))



@dataclass(frozen=True, slots=True)
class EventFrameCatalog:
    frame: EventFrame
    frames: ReferenceIndex[EventFrame]
    slots: ReferenceIndex[EventFrameSlot]
    fields: ReferenceIndex[EventFrameField]

    def __post_init__(self) -> None:
        slots = {}
        fields = {}
        parent = self.frame.reference
        for slot_id, slot in self.frame.slots.items():
            if slot_id != slot.id or slot.reference != Reference(parent.owner, (*parent.path, parent.element), slot_id):
                raise ValueError(f"{slot.source}: event slot identity differs from its frame")
            register_reference(slots, slot.reference, slot)
            for member in slot.fields:
                if member.reference != Reference(slot.reference.owner, (*slot.reference.path, slot.id), member.id):
                    raise ValueError(f"{member.source}: event field identity differs from its slot")
                register_reference(fields, member.reference, member)
        for name, expected in (("frames", {parent: self.frame}), ("slots", slots), ("fields", fields)):
            supplied = getattr(self, name)
            index = supplied if isinstance(supplied, ReferenceIndex) else ReferenceIndex(supplied)
            if set(index) != set(expected) or any(index[reference] is not member for reference, member in expected.items()):
                raise ValueError(f"{self.frame.source}: event-frame {name} index differs from its canonical tree")
            object.__setattr__(self, name, index)



@dataclass(frozen=True, slots=True)
class ResolvedEventFrameLayout:
    frame_type: str
    payload_slots: tuple[EventFrameSlot, ...]
    allocated_slot_count: int
    byte_length: int


    def __post_init__(self) -> None:
        object.__setattr__(self, "payload_slots", tuple(self.payload_slots))

    @property
    def last_payload_slot(self) -> EventFrameSlot | None:
        return self.payload_slots[-1] if self.payload_slots else None


@dataclass(frozen=True, slots=True)
class ResolvedEventFrame:
    frame: EventFrame
    header_slots: tuple[EventFrameSlot, ...]
    control_fields: Mapping[str, EventFrameField]
    event_code: EventFrameField
    reserved_masks: Mapping[Reference[EventFrameSlot], int]
    layouts: Mapping[str, ResolvedEventFrameLayout]

    def __post_init__(self) -> None:
        object.__setattr__(self, "header_slots", tuple(self.header_slots))
        object.__setattr__(self, "control_fields", MappingProxyType(dict(self.control_fields)))
        object.__setattr__(self, "reserved_masks", MappingProxyType(dict(self.reserved_masks)))
        object.__setattr__(self, "layouts", MappingProxyType(dict(self.layouts)))
        slots = {slot.reference: slot for slot in self.frame.slots.values()}
        fields = {field.reference: field for slot in slots.values() for field in slot.fields}
        for slot in self.header_slots:
            if slots.get(slot.reference) is not slot:
                raise ValueError(f"{slot.source}: resolved header must use the frame's canonical slots")
        for key, member in self.control_fields.items():
            if key != member.id or fields.get(member.reference) is not member:
                raise ValueError(f"{member.source}: resolved control field must use a canonical frame member")
        if fields.get(self.event_code.reference) is not self.event_code:
            raise ValueError(f"{self.event_code.source}: event code must use a canonical frame field")
        if not set(self.reserved_masks) <= set(slots):
            raise ValueError(f"{self.frame.source}: reserved mask names a slot outside the frame")
        for kind, layout in self.layouts.items():
            if kind != layout.frame_type:
                raise ValueError(f"{self.frame.source}: resolved layout key differs from its frame type")
            for slot in layout.payload_slots:
                if slots.get(slot.reference) is not slot:
                    raise ValueError(f"{slot.source}: resolved payload must use the frame's canonical slots")



def resolve_event_frame_layouts(frame: EventFrame) -> ResolvedEventFrame:
    """Resolve the four architectural frame allocations and their real payloads."""
    header_ids = ("FRAME_CONTROL", "EVENT_INFO", "SAVED_PC", "SAVED_SP", "SAVED_CS", "SAVED_DS", "SAVED_SS", "PADDING")
    payload_ids = ("ERROR_CODE", "FAULT_EA", "FAULT_LINEAR", "EVENT_AUX")
    selected = {}
    for index, identifier in enumerate((*header_ids, *payload_ids)):
        if identifier not in frame.slots:
            raise ValueError(f"{frame.source}: architectural frame lacks slot {identifier!r}")
        slot = frame.slots[identifier]
        if slot.bits != 64 or slot.offset != index * 8:
            raise ValueError(f"{slot.source}: {identifier} must occupy architectural slot {index}")
        selected[identifier] = slot
    control = selected["FRAME_CONTROL"]
    fields = {field.id: field for field in control.fields}
    expected = {"FRAME_SIZE": (0, 8), "FRAME_TYPE": (8, 4), "SAVED_DFA": (12, 1), "FLAGS": (32, 4), "STATUS": (36, 16)}
    if set(fields) != set(expected) or any((fields[key].lsb, fields[key].bits) != value for key, value in expected.items()):
        raise ValueError(f"{control.source}: invalid architectural frame-control fields")
    info = selected["EVENT_INFO"]
    if len(info.fields) != 1 or (info.fields[0].id, info.fields[0].lsb, info.fields[0].bits) != ("EVENT_CODE", 0, 32):
        raise ValueError(f"{info.source}: invalid architectural event-code field")
    variants = (("basic", (), 8), ("error", payload_ids[:1], 10), ("page", payload_ids[:3], 12), ("auxiliary", payload_ids, 12))
    layouts = {}
    for kind, members, count in variants:
        slots = tuple(selected[identifier] for identifier in members)
        byte_length = count * control.bits // 8
        if any(slot.offset + slot.bits // 8 > byte_length for slot in slots):
            raise ValueError(f"{frame.source}: {kind} payload exceeds its frame allocation")
        layouts[kind] = ResolvedEventFrameLayout(kind, slots, count, byte_length)
    reserved = {slot.reference: ((1 << slot.bits) - 1) ^ sum(((1 << field.bits) - 1) << field.lsb for field in slot.fields) for slot in (control, info)}
    return ResolvedEventFrame(frame, tuple(selected[key] for key in header_ids), MappingProxyType(fields), info.fields[0], MappingProxyType(reserved), MappingProxyType(layouts))



@dataclass(frozen=True, slots=True)
class EventPayloadField(Entity):
    reference: Reference["EventPayloadField"]
    source: Path
    id: str
    lsb: int
    bits: int

    @property
    def msb(self) -> int:
        return self.lsb + self.bits - 1


@dataclass(frozen=True, slots=True)
class EventPayloadFormat(Entity):
    reference: Reference["EventPayloadFormat"]
    source: Path
    id: str
    bits: int
    fields: tuple[EventPayloadField, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", tuple(self.fields))



@dataclass(frozen=True, slots=True)
class EventPayloadCatalog:
    inventory: DirectoryInventory
    formats: Mapping[str, EventPayloadFormat]
    formats_by_reference: ReferenceIndex[EventPayloadFormat]
    fields: ReferenceIndex[EventPayloadField]

    def __post_init__(self) -> None:
        object.__setattr__(self, "formats", MappingProxyType(dict(self.formats)))
        if self.inventory.duplicates or self.inventory.missing or self.inventory.undeclared or set(self.formats) != set(self.inventory.declared):
            raise ValueError(f"{self.inventory.source}: declared, actual and loaded payload membership must agree")
        formats = {}
        fields = {}
        for key, payload in self.formats.items():
            if key != payload.id or payload.reference != Reference(self.inventory.owner, ("events", "payloads"), key):
                raise ValueError(f"{payload.source}: payload identity differs from its collection")
            register_reference(formats, payload.reference, payload)
            for member in payload.fields:
                if member.reference != Reference(payload.reference.owner, (*payload.reference.path, payload.id), member.id):
                    raise ValueError(f"{member.source}: payload field identity differs from its format")
                register_reference(fields, member.reference, member)
        for name, expected in (("formats_by_reference", formats), ("fields", fields)):
            supplied = getattr(self, name)
            index = supplied if isinstance(supplied, ReferenceIndex) else ReferenceIndex(supplied)
            if set(index) != set(expected) or any(index[reference] is not member for reference, member in expected.items()):
                raise ValueError(f"{self.inventory.source}: payload {name} index differs from its canonical tree")
            object.__setattr__(self, name, index)




def _validate_fields(source: Path, owner: str, bits: int, fields) -> None:
    for index, field in enumerate(fields):
        if field.msb >= bits:
            raise ValueError(
                f"{source}: field {field.id!r} exceeds the {bits}-bit {owner}"
            )
        conflict = next(
            (
                other
                for other in fields[index + 1 :]
                if field.lsb <= other.msb and other.lsb <= field.msb
            ),
            None,
        )
        if conflict is not None:
            raise ValueError(
                f"{source}: fields {field.id!r} and {conflict.id!r} overlap in {owner}"
            )


def load_event_frame( isa_root: str | Path) -> "EventFrameCatalog":
    root = Path(isa_root).resolve()
    source = root / "events/definitions/event_frame.yaml"
    raw = load_schema_yaml(source, root / "schemas/event-frame.yaml")
    frame_reference: Reference[EventFrame] = Reference(
        "base", ("events", "frame"), raw["id"]
    )
    slot_index = {}
    field_index = {}
    loaded_slots: dict[str, EventFrameSlot] = {}
    for raw_slot in raw["slots"]:
        slot_reference: Reference[EventFrameSlot] = Reference(
            frame_reference.owner,
            (*frame_reference.path, frame_reference.element),
            raw_slot["id"],
        )
        fields = tuple(
            EventFrameField(
                Reference(
                    slot_reference.owner,
                    (*slot_reference.path, slot_reference.element),
                    value["id"],
                ),
                source,
                value["id"],
                value["lsb"],
                value["bits"],
            )
            for value in raw_slot.get("fields", ())
        )
        slot = EventFrameSlot(
            slot_reference,
            source,
            raw_slot["id"],
            raw_slot["offset"],
            raw["slot_bits"],
            fields,
        )
        _validate_fields(slot.source, slot.id, slot.bits, slot.fields)
        for previous in loaded_slots.values():
            if (
                slot.offset * 8 < previous.offset * 8 + previous.bits
                and previous.offset * 8 < slot.offset * 8 + slot.bits
            ):
                raise ValueError(
                    f"{source}: event-frame slots {slot.id!r} and "
                    f"{previous.id!r} overlap"
                )
        register_reference(slot_index, slot.reference, slot)
        for field in fields:
            register_reference(field_index, field.reference, field)
        loaded_slots[slot.id] = slot
    frame = EventFrame(
        frame_reference, source, raw["id"], MappingProxyType(loaded_slots)
    )
    frames = {}
    register_reference(frames, frame.reference, frame)
    return EventFrameCatalog(
        frame=frame,
        frames=ReferenceIndex(frames),
        slots=ReferenceIndex(slot_index),
        fields=ReferenceIndex(field_index),
    )


def load_event_payloads( isa_root: str | Path) -> "EventPayloadCatalog":
    root = Path(isa_root).resolve()
    payload_root = root / "events/payloads"
    inventory = require_exact(inspect_inventory(owner="base", kind="event-payload", source=payload_root / "payloads.yaml", root=payload_root, key="payloads", name_pattern=r"[A-Z][A-Z0-9_]*", exact_keys=True))
    format_index = {}
    field_index = {}
    formats: dict[str, EventPayloadFormat] = {}
    for format_id in inventory.declared:
        source = payload_root / format_id / "payload.yaml"
        raw = load_schema_yaml(source, root / "schemas/event-payload.yaml")
        format_reference: Reference[EventPayloadFormat] = Reference(
            "base", ("events", "payloads"), format_id
        )
        fields = tuple(
            EventPayloadField(
                Reference(
                    format_reference.owner,
                    (*format_reference.path, format_reference.element),
                    value["id"],
                ),
                source,
                value["id"],
                value["lsb"],
                value["bits"],
            )
            for value in raw["fields"]
        )
        payload = EventPayloadFormat(
            format_reference, source, format_id, raw["bits"], fields
        )
        _validate_fields(source, format_id, payload.bits, fields)
        register_reference(format_index, payload.reference, payload)
        for field in fields:
            register_reference(field_index, field.reference, field)
        formats[format_id] = payload
    return EventPayloadCatalog(
        inventory=inventory,
        formats=MappingProxyType(formats),
        formats_by_reference=ReferenceIndex(format_index),
        fields=ReferenceIndex(field_index),
    )
