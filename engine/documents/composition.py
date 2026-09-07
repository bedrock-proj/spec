"""Canonical block selection and authored public document order."""
from __future__ import annotations
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from engine.isa.catalog import InstructionBundle
from engine.isa.model import DocumentTopic
from engine.isa.terminology import TermGroup
from engine.documents.sources import AuthoredSourceProjection
from engine.reference import Reference

@dataclass(frozen=True, slots=True)
class InstructionSetBlock:
    owner: str
    title: str
    introduction: tuple[DocumentTopic, ...]
    instructions: tuple[InstructionBundle, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "introduction", tuple(self.introduction))
        object.__setattr__(self, "instructions", tuple(self.instructions))


DocumentBlock = DocumentTopic | InstructionSetBlock | TermGroup | AuthoredSourceProjection

@dataclass(frozen=True, slots=True)
class DocumentComposition:
    blocks: tuple[DocumentBlock, ...]

    def __post_init__(self):
        object.__setattr__(self, "blocks", tuple(self.blocks))


def load_composition(body, *, isa, sources) -> DocumentComposition:
    if not isinstance(body, Sequence) or isinstance(body, str) or not body:
        raise ValueError("document body must be a non-empty sequence")
    blocks, topics, groups, owners, authored = [], set(), set(), set(), set()
    def topic(reference, owner=None):
        value = isa.model.document_topics.resolve(Reference.parse(reference))
        if value.reference in topics:
            raise ValueError(f"duplicate public topic placement: {value.reference!r}")
        if owner is not None and value.owner != owner:
            raise ValueError(f"topic {value.reference!r} does not belong to instruction set {owner!r}")
        topics.add(value.reference)
        return value
    for index, raw in enumerate(body):
        if not isinstance(raw, Mapping) or len(raw) != 1:
            raise ValueError(f"document body[{index}] must contain exactly one block")
        if "source" in raw:
            value = sources[raw["source"]]
            if value.source in authored:
                raise ValueError(f"duplicate authored source placement: {value.source}")
            authored.add(value.source)
            blocks.append(value)
        elif "topic" in raw:
            blocks.append(topic(raw["topic"]))
        elif "term-group" in raw:
            group = isa.terminology.groups.resolve(Reference.parse(raw["term-group"]))
            if group.reference in groups:
                raise ValueError(f"duplicate terminology group placement: {group.reference!r}")
            groups.add(group.reference)
            blocks.append(group)
        elif "instruction-set" in raw:
            item = raw["instruction-set"]
            if not isinstance(item, Mapping) or set(item) - {"owner", "title", "introduction"}:
                raise ValueError(f"document body[{index}] has an invalid instruction-set block")
            owner, title = item["owner"], item["title"]
            if not isinstance(title, str) or not title:
                raise ValueError("instruction-set title must be non-empty")
            if owner in owners:
                raise ValueError(f"duplicate instruction-set placement: {owner!r}")
            owners.add(owner)
            members = isa.catalog.base.instructions if owner == "base" else isa.catalog.extensions[owner].instruction_set.instructions
            introduction = tuple(topic(reference, owner) for reference in item.get("introduction", ()))
            blocks.append(InstructionSetBlock(owner, title, introduction, members))
        else:
            raise ValueError(f"document body[{index}] has an unknown block")
    return DocumentComposition(tuple(blocks))
