"""Relational validation for the complete ISA authoring model."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from pathlib import Path

from engine.diagnostics import Diagnostic, DiagnosticBag, _error
from engine.isa.control_registers import check_control_registers
from engine.isa.cpuid import check_cpuid
from engine.isa.encoding_reservations import check_encoding_reservations
from engine.isa.events import check_events
from engine.isa.catalog import check_bundle
from engine.isa.catalog import check_catalog
from engine.isa.project import IsaProject
from engine.isa.registers import check_registers
from engine.isa.types import check_register_types
from engine.isa.project import _check_reset_sources
from engine.artifacts.registry import load_artifact_registry
from engine.artifacts.generate import artifact_context, validate_artifacts
from engine.isa.terminology import check_terminology
from engine.observability import log_phase
from engine.sail.validation import check_sail_bundle
from engine.workspace import SpecWorkspace

_LOGGER = logging.getLogger(__name__)


def check_isa(project: IsaProject, targets: Iterable[str | Path] = ()) -> DiagnosticBag:
    requested = tuple(targets)
    with log_phase(_LOGGER, "check", targets=len(requested)) as phase:
        selected = project.catalog.select(requested)
        diagnostics: list[Diagnostic] = []
        for bundle in selected:
            diagnostics.extend(check_bundle(bundle, field_types=project.types.field_types, payload_types=project.types.payload_types, ea_modes=project.catalog.ea_modes, registers=project.registers, reservations=project.encoding_reservations))
            diagnostics.extend(check_sail_bundle(bundle))
        diagnostics.extend(check_catalog(project.catalog, selected, complete=not requested, field_types=project.types.field_types, payload_types=project.types.payload_types, registers=project.registers))
        if not requested:
            diagnostics.extend(
                check_encoding_reservations(project.encoding_reservations.inventory, project.encoding_reservations.reservations)
            )
            diagnostics.extend(check_cpuid(project.cpuid))
            diagnostics.extend(check_events(project.events))
            diagnostics.extend(check_registers(project.registers))
            diagnostics.extend(check_register_types(project.registers, project.types))
            diagnostics.extend(_check_reset_sources(project.registers, project.control_registers))
            diagnostics.extend(check_control_registers(project.control_registers))
            diagnostics.extend(check_terminology(project.terminology))
        phase["complete"] = not requested
        phase["diagnostics"] = len(diagnostics)
        return DiagnosticBag(tuple(diagnostics))


def check_workspace(
    workspace: SpecWorkspace, targets: Iterable[str | Path] = ()
) -> DiagnosticBag:
    requested = tuple(targets)
    provider = workspace.require_provider("isa")
    if not isinstance(provider, IsaProject):
        raise TypeError("workspace isa provider must be an IsaProject")
    isa_diagnostics = check_isa(provider, requested)
    if requested:
        return isa_diagnostics
    diagnostics = list(isa_diagnostics)
    diagnostics.extend(_validate_dependencies(workspace))
    try:
        registry = load_artifact_registry(workspace)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        diagnostics.append(_error("artifact.registry", workspace.root / "artifacts", str(error)))
    else:
        context = artifact_context(registry, workspace, workspace.root / "output")
        diagnostics.extend(_error("artifact.projection", definition.source, str(error)) for definition, error in validate_artifacts(registry, context))
    return DiagnosticBag(tuple(diagnostics))


def _validate_dependencies(workspace: SpecWorkspace) -> Iterator[Diagnostic]:
    for provider_name, provider in workspace.providers.items():
        for dependency in provider.entity_dependencies():
            try:
                provider.entities.resolve(dependency.source)
                workspace.resolve(dependency.target)
            except (KeyError, TypeError, ValueError) as error:
                yield _error(
                    "workspace.dependency",
                    workspace.root,
                    f"{provider_name}: {error}",
                )
