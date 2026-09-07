"""Resolved document selection, source occurrences, and public targets."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from engine.documents.composition import DocumentComposition, InstructionSetBlock
from engine.documents.dependencies import DependencyGraph, dependency_edges
from engine.documents.sources import AuthoredSourceProjection, source_dependencies, source_target_blocks, source_occurrences
from engine.documents.targets import PublicTargetCatalog
from engine.documents.terminology import ResolvedSemanticText, project_term_group, selected_term_targets
from engine.documents.instructions import InstructionFormatProjection, InstructionMetadata, project_instruction_formats, project_instruction_metadata
from engine.isa.catalog import InstructionBundle
from engine.isa.model import DocumentTopic
from engine.isa.terminology import Term, TermGroup
from engine.isa.vector_examples import VectorDiagram
from engine.documents.fragment_expansion import SourceFragmentProjection
from engine.reference import Reference

@dataclass(frozen=True, slots=True)
class ProjectedTopic:
    topic: DocumentTopic
    content: AuthoredSourceProjection

@dataclass(frozen=True, slots=True)
class ProjectedTermGroup:
    group: TermGroup
    terms: tuple[tuple[Term, ResolvedSemanticText], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "terms", tuple(tuple(item0) for item0 in self.terms))


@dataclass(frozen=True, slots=True)
class ProjectedInstructionEntry:
    bundle: InstructionBundle
    formats: tuple[InstructionFormatProjection, ...]
    metadata: InstructionMetadata
    description: AuthoredSourceProjection

    def __post_init__(self) -> None:
        object.__setattr__(self, "formats", tuple(self.formats))


@dataclass(frozen=True, slots=True)
class InstructionSummaryRow:
    reference: Reference[InstructionBundle]
    mnemonic: str
    description: str
    source: Path

@dataclass(frozen=True, slots=True)
class InstructionSetSummaryProjection:
    owner: str
    title: str
    rows: tuple[InstructionSummaryRow, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "rows", tuple(self.rows))


@dataclass(frozen=True, slots=True)
class ProjectedInstructionSet:
    summary: InstructionSetSummaryProjection
    introduction: tuple[ProjectedTopic, ...]
    instructions: tuple[ProjectedInstructionEntry, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "introduction", tuple(self.introduction))
        object.__setattr__(self, "instructions", tuple(self.instructions))


ProjectedDocumentBlock = ProjectedTopic | ProjectedTermGroup | ProjectedInstructionSet | AuthoredSourceProjection

@dataclass(frozen=True, slots=True)
class DocumentProjection:
    composition: DocumentComposition
    blocks: tuple[ProjectedDocumentBlock, ...]
    public_targets: PublicTargetCatalog
    dependencies: DependencyGraph


    def __post_init__(self) -> None:
        object.__setattr__(self, "blocks", tuple(self.blocks))

    @property
    def instruction_groups(self) -> tuple[InstructionSetSummaryProjection, ...]:
        return tuple(block.summary for block in self.blocks if isinstance(block, ProjectedInstructionSet))


def project_document(composition: DocumentComposition, isa, source_projections, *, additional_sources=()) -> DocumentProjection:
    dependencies, targets, projected = [], [], []
    selected_sources = list(additional_sources)
    def topic(value):
        source = source_projections[value.reference]
        selected_sources.append(source)
        return ProjectedTopic(value, source)
    for block in composition.blocks:
        if isinstance(block, AuthoredSourceProjection):
            projected.append(block)
            selected_sources.append(block)
        elif isinstance(block, DocumentTopic):
            projected.append(topic(block))
        elif isinstance(block, TermGroup):
            terms = project_term_group(block, entities=isa.entities, terms=isa.terminology.terms)
            projected.append(ProjectedTermGroup(block, terms))
            targets.append(selected_term_targets(block))
            for term, _ in terms:
                dependencies.extend(dependency_edges(term.reference, term.definition))
        elif isinstance(block, InstructionSetBlock):
            targets.append(tuple(bundle.reference for bundle in block.instructions))
            summary = InstructionSetSummaryProjection(block.owner, block.title, tuple(InstructionSummaryRow(bundle.reference, bundle.instruction.mnemonic, bundle.instruction.summary, bundle.instruction.source) for bundle in block.instructions))
            entries = tuple(ProjectedInstructionEntry(bundle, project_instruction_formats(bundle), project_instruction_metadata(bundle), source_projections[bundle.reference]) for bundle in block.instructions)
            projected.append(ProjectedInstructionSet(summary, tuple(topic(value) for value in block.introduction), entries))
            selected_sources.extend(entry.description for entry in entries)
        else:
            raise TypeError(f"unsupported document block: {type(block).__name__}")
    diagrams = {}
    for source in selected_sources:
        dependencies.extend(source_dependencies(source))
        targets.extend(source_target_blocks(source))
        for _, occurrence, input_chain in source_occurrences(source):
            if isinstance(occurrence, SourceFragmentProjection) and isinstance(occurrence.selection, VectorDiagram):
                reference = occurrence.selection.reference
                if reference in diagrams:
                    raise ValueError(f"duplicate vector diagram placement {reference!r}: {diagrams[reference]!r} and {(occurrence.span, input_chain)!r}")
                diagrams[reference] = (occurrence.span, input_chain)
    public_targets = PublicTargetCatalog.create(isa.entities, targets, (edge.target for edge in dependencies))
    return DocumentProjection(composition, tuple(projected), public_targets, DependencyGraph(tuple(dependencies)))
