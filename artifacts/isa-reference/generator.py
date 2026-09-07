"""ISA manual construction from one resolved document selection."""
from __future__ import annotations
from importlib import import_module
from pathlib import Path
from collections.abc import Mapping
from engine.artifacts.definition import GeneratedArtifact, GeneratedArtifactSet
from engine.documents.composition import load_composition, InstructionSetBlock
from engine.documents.projection import project_document
from engine.documents.sources import project_source
from engine.isa.model import DocumentTopic
from artifacts._shared.latex import create_target_labels, validate_tex
from artifacts._shared.documents import build_document

_document = import_module("artifacts.isa-reference.document")
_dependencies = import_module("artifacts.isa-reference.dependencies")


def _inputs(definition, context):
    isa = context.workspace.require_provider("isa")
    def selected():
        frame = _document.load_document_frame(definition, context.workspace.root)
        catalogs = {"isa": isa}
        sources = {}
        def source(path, boundary):
            projected = project_source(path, definition.source, context.workspace.root, catalogs, shared_result=context.shared_result)
            if projected.source.suffix != ".tex" or not projected.source.is_relative_to(boundary):
                raise ValueError(f"{definition.source}: invalid document source {path}")
            return projected
        frame_sources = tuple(source(path, context.workspace.root) for path in (frame.preamble, frame.title_page, frame.postamble))
        authored_keys = tuple(raw["source"] for raw in definition.data["body"] if isinstance(raw, Mapping) and "source" in raw)
        authored = {key: source(frame.sources[key], definition.source.parent) for key in authored_keys}
        composition = load_composition(definition.data["body"], isa=isa, sources=authored)
        for block in composition.blocks:
            if isinstance(block, DocumentTopic):
                sources[block.reference] = project_source(block.document, block, context.workspace.root, catalogs, shared_result=context.shared_result)
            elif isinstance(block, InstructionSetBlock):
                for topic in block.introduction:
                    sources[topic.reference] = project_source(topic.document, topic, context.workspace.root, catalogs, shared_result=context.shared_result)
                for bundle in block.instructions:
                    sources[bundle.reference] = project_source(bundle.description, bundle, context.workspace.root, catalogs, shared_result=context.shared_result)
        projection = project_document(composition, isa, sources, additional_sources=frame_sources)
        explicit = {row.reference: _document.instruction_label(row.mnemonic) for group in projection.instruction_groups for row in group.rows}
        labels = create_target_labels(projection.public_targets, explicit)
        return projection, frame_sources, labels
    return context.shared_result((_inputs, id(definition), id(isa)), selected)


def project(definition, context):
    return _inputs(definition, context)[0]


def render_source(definition, context):
    def rendered():
        projection, frame, labels = _inputs(definition, context)
        text = _document.render_latex(projection, frame, labels)
        report = validate_tex(text)
        if not report.passed:
            raise ValueError(f"{definition.source}: invalid public document: {report.issues!r}")
        return text
    return context.shared_result((render_source, id(definition)), rendered)


def validate(definition, context):
    render_source(definition, context)


def generate(definition, context) -> GeneratedArtifactSet:
    isa = context.workspace.require_provider("isa")
    projection = project(definition, context)
    presentations = {reference: (isa.entities.resolve(reference), isa.entities.presentation(reference)) for edge in projection.dependencies.edges for reference in (edge.source, edge.target) if not isinstance(reference, Path)}
    return GeneratedArtifactSet((
        GeneratedArtifact(definition.outputs["document"], render_source(definition, context)),
        GeneratedArtifact(definition.outputs["dependencies"], _dependencies.render_dependency_graph(projection.dependencies, presentations, isa.root)),
    ), definition.id)


def build(definition, context, *, compile_pdf, latexmk="latexmk"):
    return build_document(definition, context, compile_pdf=compile_pdf, latexmk=latexmk)
