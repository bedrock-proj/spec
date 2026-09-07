"""Current-model page ownership and semantic-link registry for the web site."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from engine.documents.projection import InstructionSetSummaryProjection
from importlib import import_module

_manual = import_module("artifacts.isa-reference.document")
from .navigation import (
    LinkTarget,
    NavigationGroup,
    PageRegistry,
    PageSpec,
    stable_anchor,
)
from .structure import LabelSpec, LatexStructure, SectionSpec
from .visual import VisualizedLatex

ROOT_PAGE_KEY = "site:home"
ISA_DOCUMENT_ID = "isa"
INSTRUCTION_PART_ID = "instruction-set-reference"


@dataclass(frozen=True, slots=True)
class DocumentSiteSpec:
    id: str
    navigation_title: str
    download: PurePosixPath
    structure: LatexStructure
    source_text: str
    visualized: VisualizedLatex


@dataclass(frozen=True, slots=True)
class SiteModel:
    registry: PageRegistry
    groups: tuple[NavigationGroup, ...]
    documents: tuple[DocumentSiteSpec, ...]
    instruction_groups: tuple[InstructionSetSummaryProjection, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "groups", tuple(self.groups))
        object.__setattr__(self, "documents", tuple(self.documents))
        object.__setattr__(self, "instruction_groups", tuple(self.instruction_groups))

    def navigation(self) -> list[dict[str, object]]:
        return self.registry.navigation(ROOT_PAGE_KEY, self.groups)


def scoped_target(document: str, label: str) -> str:
    return f"{document}:{label}"


def document_page_key(document: str) -> str:
    return f"document:{document}"


def section_page_key(document: str, section: str) -> str:
    return f"{document}:section:{section}"


def part_page_key(document: str, part: str) -> str:
    return f"{document}:part:{part}"


def _labels_in_range(
    structure: LatexStructure, start: int, end: int
) -> tuple[LabelSpec, ...]:
    return tuple(label for label in structure.labels if start <= label.start < end)


def _register_targets(
    pages: list[PageSpec],
    targets: list[tuple[str, LinkTarget]],
    document: str,
    page: str,
    labels: tuple[LabelSpec, ...],
    owner: str,
    *,
    owner_is_label: bool = True,
) -> None:
    names = [label.name for label in labels]
    expected = 1 if owner_is_label else 0
    if names.count(owner) != expected:
        raise ValueError(
            f"{page}: expected owning label {owner!r} count {expected}, "
            f"found {names.count(owner)}"
        )
    if not owner_is_label:
        _add_target(targets, scoped_target(document, owner), page)
    for label in labels:
        _add_target(
            targets,
            scoped_target(document, label.name),
            page,
            anchor=None if label.name == owner else stable_anchor(label.name),
        )


def _landing(
    pages: list[PageSpec],
    targets: list[tuple[str, LinkTarget]],
    document: DocumentSiteSpec,
) -> str:
    key = document_page_key(document.id)
    _add_page(
        pages,
        targets,
        PageSpec(
            key,
            document.structure.title.title,
            PurePosixPath(document.id) / "index.md",
            group=document.id,
            source=document.id,
        ),
        target_names=(key,),
    )
    return key


def _flat_sections(
    pages: list[PageSpec],
    targets: list[tuple[str, LinkTarget]],
    document: DocumentSiteSpec,
    landing: str,
) -> None:
    if document.structure.parts:
        raise ValueError(f"{document.id}: only the ISA document may contain parts")
    for section in document.structure.sections:
        page = section_page_key(document.id, section.key)
        _add_page(
            pages,
            targets,
            PageSpec(
                page,
                section.title,
                PurePosixPath(document.id) / f"{section.key}.md",
                group=document.id,
                parent=landing,
                source=document.id,
            ),
        )
        _register_targets(
            pages,
            targets,
            document.id,
            page,
            _labels_in_range(document.structure, section.start, section.end),
            f"page:{section.key}",
        )


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        raise ValueError(f"cannot derive site slug from {value!r}")
    return slug


def _instruction_pages(
    pages: list[PageSpec],
    targets: list[tuple[str, LinkTarget]],
    instruction_groups: tuple[InstructionSetSummaryProjection, ...],
    document: DocumentSiteSpec,
    part_page: str,
    sections: dict[str, SectionSpec],
) -> set[str]:
    consumed: set[str] = set()
    root = f"{document.id}:instructions"
    _add_page(
        pages,
        targets,
        PageSpec(
            root,
            "Instructions",
            PurePosixPath(document.id) / "instructions/index.md",
            group=document.id,
            parent=part_page,
            source=document.id,
        ),
        target_names=(scoped_target(document.id, "page:instructions"),),
    )

    reading_key = "reading-an-instruction-description"
    reading = sections[reading_key]
    reading_page = section_page_key(document.id, reading.key)
    _add_page(
        pages,
        targets,
        PageSpec(
            reading_page,
            reading.title,
            PurePosixPath(document.id) / "instructions" / f"{reading.key}.md",
            group=document.id,
            parent=root,
            source=document.id,
        ),
    )
    _register_targets(
        pages,
        targets,
        document.id,
        reading_page,
        _labels_in_range(document.structure, reading.start, reading.end),
        f"page:{reading.key}",
    )
    consumed.add(reading.key)

    positions = {item.label: item for item in document.structure.instructions}
    for group in instruction_groups:
        group_id = _slug(group.owner)
        section = sections[_manual.instruction_group_label(group.owner).removeprefix("page:")]
        ordered = []
        for row in group.rows:
            label = _manual.instruction_label(row.mnemonic)
            position = positions.get(label)
            if position is None or not section.start <= position.start < section.end:
                raise ValueError(
                    f"{document.id}: instruction {row.mnemonic} "
                    f"is not owned by section {section.key}"
                )
            ordered.append(position)
        if any(a.start >= b.start for a, b in zip(ordered, ordered[1:])):
            raise ValueError(
                f"{document.id}: instruction order differs in {section.key}"
            )

        group_page = section_page_key(document.id, section.key)
        _add_page(
            pages,
            targets,
            PageSpec(
                group_page,
                group.title,
                PurePosixPath(document.id)
                / "instructions"
                / "groups"
                / group_id
                / "index.md",
                group=document.id,
                parent=root,
                source=document.id,
            ),
        )
        first = ordered[0].start if ordered else section.end
        _register_targets(
            pages,
            targets,
            document.id,
            group_page,
            _labels_in_range(document.structure, section.start, first),
            f"page:{section.key}",
        )
        consumed.add(section.key)
        for index, row in enumerate(group.rows):
            mnemonic = row.mnemonic
            slug = _manual.instruction_label(row.mnemonic).removeprefix("instr:")
            page = f"{document.id}:instruction:{slug}"
            _add_page(
                pages,
                targets,
                PageSpec(
                    page,
                    mnemonic,
                    PurePosixPath(document.id) / "instructions" / f"{slug}.md",
                    group=document.id,
                    parent=group_page,
                    source=str(row.source),
                ),
            )
            end = ordered[index + 1].start if index + 1 < len(ordered) else section.end
            _register_targets(
                pages,
                targets,
                document.id,
                page,
                _labels_in_range(document.structure, ordered[index].start, end),
                _manual.instruction_label(row.mnemonic),
                owner_is_label=False,
            )
    return consumed


def _isa_pages(
    pages: list[PageSpec],
    targets: list[tuple[str, LinkTarget]],
    instruction_groups: tuple[InstructionSetSummaryProjection, ...],
    document: DocumentSiteSpec,
    landing: str,
) -> None:
    if not document.structure.parts:
        raise ValueError("isa: expected part-owned navigation groups")
    sections = {section.key: section for section in document.structure.sections}
    consumed: set[str] = set()
    instruction_sections: set[str] = set()
    for part in document.structure.parts:
        part_page = part_page_key(document.id, part.key)
        _add_page(
            pages,
            targets,
            PageSpec(
                part_page,
                part.title,
                PurePosixPath(document.id) / part.key / "index.md",
                group=document.id,
                parent=landing,
                source=document.id,
            ),
            target_names=(scoped_target(document.id, f"part:{part.key}"),),
        )
        for section in document.structure.sections:
            if section.part != part.key:
                continue
            if section.key == "reading-an-instruction-description":
                if part.key != INSTRUCTION_PART_ID:
                    raise ValueError("instruction reference belongs to the wrong part")
                instruction_sections = _instruction_pages(
                    pages,
                    targets,
                    instruction_groups,
                    document,
                    part_page,
                    sections,
                )
                consumed.update(instruction_sections)
                continue
            if section.key in instruction_sections:
                continue
            page = section_page_key(document.id, section.key)
            _add_page(
                pages,
                targets,
                PageSpec(
                    page,
                    section.title,
                    PurePosixPath(document.id) / f"{section.key}.md",
                    group=document.id,
                    parent=part_page,
                    source=document.id,
                ),
            )
            _register_targets(
                pages,
                targets,
                document.id,
                page,
                _labels_in_range(document.structure, section.start, section.end),
                f"page:{section.key}",
            )
            consumed.add(section.key)
    expected = set(sections)
    if consumed != expected:
        raise ValueError(
            f"isa: section ownership mismatch; "
            f"missing={sorted(expected - consumed)}, extra={sorted(consumed - expected)}"
        )


def project_navigation(
    documents: tuple[DocumentSiteSpec, ...],
    instruction_groups: tuple[InstructionSetSummaryProjection, ...],
) -> SiteModel:
    expected = ("isa", "elf-abi", "c-abi", "target-intrinsics")
    if tuple(item.id for item in documents) != expected:
        raise ValueError(f"site document order must be {expected}")
    pages: list[PageSpec] = []
    targets: list[tuple[str, LinkTarget]] = []
    _add_page(
        pages,
        targets,
        PageSpec(
            ROOT_PAGE_KEY,
            "Bedrock Architecture",
            PurePosixPath("index.md"),
            source="site",
        ),
        target_names=(ROOT_PAGE_KEY,),
    )
    groups = []
    for document in documents:
        groups.append(NavigationGroup(document.id, document.navigation_title))
        landing = _landing(pages, targets, document)
        if document.id == ISA_DOCUMENT_ID:
            _isa_pages(pages, targets, instruction_groups, document, landing)
        else:
            _flat_sections(pages, targets, document, landing)
    return SiteModel(
        PageRegistry(pages, targets), tuple(groups), documents, instruction_groups
    )


def _add_page(pages, targets, page: PageSpec, *, target_names=()) -> None:
    pages.append(page)
    targets.extend((name, LinkTarget(page.key)) for name in target_names)


def _add_target(targets, name: str, page: str, *, anchor: str | None = None) -> None:
    targets.append((name, LinkTarget(page, anchor)))
