"""Executable full-Sail-model artifact projection."""

from __future__ import annotations

from pathlib import Path
from importlib import import_module

_project = import_module("artifacts.sail-model.project")
_dispatch = import_module("artifacts.sail-model.dispatch")
_catalog = import_module("artifacts.sail-model.catalog")
_registry = import_module("artifacts.sail-model.registry")
from engine.sail.composition import compose_sail
from engine.sail.validation import require_sail_entries

from engine.isa.configuration import IsaConfiguration
from engine.artifacts.definition import GeneratedArtifact, GeneratedArtifactSet
from engine.artifacts.generate import ArtifactGenerationContext

_OUTPUT_ROLES = frozenset(("registry", "catalog", "dispatch", "project"))


def project_program(definition, context: ArtifactGenerationContext):
    """Return the configured program projected by this artifact."""
    isa = context.workspace.require_provider("isa")
    raw_extensions = definition.data.get("extensions")
    extensions = None if raw_extensions is None else tuple(str(item) for item in raw_extensions)
    configuration = IsaConfiguration.resolve(isa.catalog, extensions)
    key = (project_program, id(isa), configuration)

    def project():
        program = compose_sail(isa.catalog, isa.model, isa.types, isa.registers, isa.control_registers, isa.events, configuration)
        require_sail_entries(program)
        return program

    return context.shared_result(key, project)


def generate(definition, context: ArtifactGenerationContext) -> GeneratedArtifactSet:
    program = project_program(definition, context)
    return render_program(definition, program, context.output_root, source_root=context.workspace.root, source_alias=context.workspace.root)


def render_program(definition, program, output_root, *, source_root, source_alias) -> GeneratedArtifactSet:
    outputs = definition.outputs
    if set(outputs) != _OUTPUT_ROLES:
        raise ValueError(
            f"{definition.source}: sail-model outputs must be exactly "
            f"{sorted(_OUTPUT_ROLES)}, found {sorted(outputs)}"
        )
    registry_path = outputs["registry"]
    catalog_path = outputs["catalog"]
    dispatch_path = outputs["dispatch"]
    project_path = outputs["project"]
    return GeneratedArtifactSet(
        (
            GeneratedArtifact(
                registry_path,
                _registry.render_sail_registry(program.registry, _catalog.catalog_id_declarations(program)),
            ),
            GeneratedArtifact(catalog_path, _catalog.render_sail_catalog(program)),
            GeneratedArtifact(dispatch_path, _dispatch.render_sail_dispatch(program.dispatch)),
            GeneratedArtifact(
                project_path,
                _project.render_sail_project(
                    _project.project_sail_project(program, output_root, outputs, source_root=source_root, source_alias=source_alias)
                ),
            ),
        ),
        artifact_id=definition.id,
    )


def validate(definition, context: ArtifactGenerationContext) -> None:
    program = project_program(definition, context)
    generated = render_program(
        definition,
        program,
        context.output_root,
        source_root=context.workspace.root,
        source_alias=context.workspace.root,
    )
    definition.validate_generated(generated)
