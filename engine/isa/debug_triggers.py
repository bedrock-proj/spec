"""Structured debug-trigger slot words and fields."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from engine.entity import Entity
from engine.reference import Reference, ReferenceIndex, register_reference
from engine.source.yaml import load_schema_yaml


@dataclass(frozen=True, slots=True)
class DebugTriggerField(Entity):
    reference: Reference["DebugTriggerField"]
    source: Path
    id: str
    lsb: int
    bits: int

    @property
    def msb(self) -> int:
        return self.lsb + self.bits - 1


@dataclass(frozen=True, slots=True)
class DebugTriggerWord(Entity):
    reference: Reference["DebugTriggerWord"]
    source: Path
    id: str
    bank: int
    bits: int
    fields: tuple[DebugTriggerField, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", tuple(self.fields))



@dataclass(frozen=True, slots=True)
class DebugTriggerSlot(Entity):
    reference: Reference["DebugTriggerSlot"]
    source: Path
    id: str
    words: Mapping[str, DebugTriggerWord]

    def __post_init__(self) -> None:
        object.__setattr__(self, "words", MappingProxyType(dict(self.words)))



@dataclass(frozen=True, slots=True)
class DebugTriggerCatalog:
    slot: DebugTriggerSlot
    slots: ReferenceIndex[DebugTriggerSlot]
    words: ReferenceIndex[DebugTriggerWord]
    fields: ReferenceIndex[DebugTriggerField]

    def __post_init__(self) -> None:
        words = {}
        fields = {}
        parent = self.slot.reference
        for key, word in self.slot.words.items():
            if key != word.id or word.reference != Reference(parent.owner, (*parent.path, parent.element), key):
                raise ValueError(f"{word.source}: debug word identity differs from its slot")
            register_reference(words, word.reference, word)
            for member in word.fields:
                if member.reference != Reference(word.reference.owner, (*word.reference.path, word.id), member.id):
                    raise ValueError(f"{member.source}: debug field identity differs from its word")
                register_reference(fields, member.reference, member)
        for name, expected in (("slots", {parent: self.slot}), ("words", words), ("fields", fields)):
            supplied = getattr(self, name)
            index = supplied if isinstance(supplied, ReferenceIndex) else ReferenceIndex(supplied)
            if set(index) != set(expected) or any(index[reference] is not member for reference, member in expected.items()):
                raise ValueError(f"{self.slot.source}: debug {name} index differs from its canonical tree")
            object.__setattr__(self, name, index)




def _validate_fields(word: DebugTriggerWord) -> None:
    for index, field in enumerate(word.fields):
        if field.msb >= word.bits:
            raise ValueError(
                f"{word.source}: debug-trigger field {field.id!r} exceeds "
                f"the {word.bits}-bit {word.id} word"
            )
        conflict = next(
            (
                other
                for other in word.fields[index + 1 :]
                if field.lsb <= other.msb and other.lsb <= field.msb
            ),
            None,
        )
        if conflict is not None:
            raise ValueError(
                f"{word.source}: debug-trigger fields {field.id!r} and "
                f"{conflict.id!r} overlap in {word.id}"
            )


def load_debug_triggers( isa_root: str | Path) -> "DebugTriggerCatalog":
    root = Path(isa_root).resolve()
    source = root / "debug/definitions/trigger_slot.yaml"
    raw = load_schema_yaml(source, root / "schemas/debug-trigger-slot.yaml")
    slot_reference: Reference[DebugTriggerSlot] = Reference(
        "base", ("debug", "triggers"), raw["id"]
    )
    words = {}
    fields = {}
    loaded_words: dict[str, DebugTriggerWord] = {}
    for raw_word in raw["words"]:
        word_reference: Reference[DebugTriggerWord] = Reference(
            slot_reference.owner,
            (*slot_reference.path, slot_reference.element),
            raw_word["id"],
        )
        loaded_fields = tuple(
            DebugTriggerField(
                Reference(
                    word_reference.owner,
                    (*word_reference.path, word_reference.element),
                    raw_field["id"],
                ),
                source,
                raw_field["id"],
                raw_field["lsb"],
                raw_field["bits"],
            )
            for raw_field in raw_word.get("fields", ())
        )
        word = DebugTriggerWord(
            word_reference,
            source,
            raw_word["id"],
            raw_word["bank"],
            raw["word_bits"],
            loaded_fields,
        )
        _validate_fields(word)
        register_reference(words, word.reference, word)
        for field in loaded_fields:
            register_reference(fields, field.reference, field)
        loaded_words[word.id] = word
    slot = DebugTriggerSlot(
        slot_reference, source, raw["id"], MappingProxyType(loaded_words)
    )
    slots = {}
    register_reference(slots, slot.reference, slot)
    return DebugTriggerCatalog(
        slot=slot,
        slots=ReferenceIndex(slots),
        words=ReferenceIndex(words),
        fields=ReferenceIndex(fields),
    )
