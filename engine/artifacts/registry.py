"""Closed artifact definition inventory and module entrypoints."""

from __future__ import annotations

from engine.artifacts.definition import load_artifact_definition

import importlib.util
import logging
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import FunctionType, MappingProxyType

from engine.artifacts.definition import ArtifactDefinition
from engine.observability import log_phase
from engine.workspace import SpecWorkspace

_LOGGER = logging.getLogger(__name__)
_ENTRYPOINTS = frozenset((
    "generate", "validate", "build", "project", "render_source",
    "project_program", "render_program",
))


@dataclass(frozen=True, slots=True, eq=False)
class ArtifactGeneratorRegistry:
    """Validated definition and generator bindings, fixed after discovery."""

    definitions: Mapping[str, ArtifactDefinition]
    entrypoints: Mapping[str, Mapping[str, Callable[..., object]]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "definitions", MappingProxyType(dict(self.definitions)))
        object.__setattr__(self, "entrypoints", MappingProxyType({
            name: MappingProxyType(dict(entries))
            for name, entries in self.entrypoints.items()
        }))
        if any(key != definition.id for key, definition in self.definitions.items()):
            raise ValueError("artifact definition key does not match its identity")
        _validate_dependencies(self.definitions)
        _validate_output_ownership(self.definitions)
        if set(self.definitions) != set(self.entrypoints):
            raise ValueError("artifact definitions and entrypoints must have identical membership")
        for name, entries in self.entrypoints.items():
            source = self.definitions[name].source
            if not {"generate", "validate"} <= entries.keys():
                raise ValueError(f"{source}: artifact requires generate and validate entrypoints")
            if not entries.keys() <= _ENTRYPOINTS:
                raise ValueError(f"{source}: unknown artifact entrypoints {sorted(entries.keys() - _ENTRYPOINTS)}")
            for operation, function in entries.items():
                if not isinstance(function, FunctionType):
                    raise ValueError(f"{source}: artifact entrypoint {operation!r} must be a function")




    @property
    def artifact_ids(self) -> tuple[str, ...]:
        return tuple(self.definitions)

    def definition(self, artifact_id: str) -> ArtifactDefinition:
        try:
            return self.definitions[artifact_id]
        except KeyError as error:
            raise ValueError(f"unknown artifact {artifact_id!r}") from error


    def entrypoint(self, artifact_id: str, operation: str) -> Callable[..., object]:
        definition = self.definition(artifact_id)
        try:
            return self.entrypoints[artifact_id][operation]
        except KeyError as error:
            raise ValueError(f"{definition.source}: artifact has no {operation!r} entrypoint") from error


def _load_generator(definition: ArtifactDefinition) -> Mapping[str, Callable[..., object]]:
    raw = definition.data.get("generator")
    if not isinstance(raw, str) or not raw:
        raise ValueError(
            f"{definition.source}: implemented artifact requires generator"
        )
    source = (definition.source.parent / raw).resolve()
    if not source.is_relative_to(definition.source.parent) or not source.is_file():
        raise ValueError(f"{definition.source}: invalid generator path {raw!r}")
    module_identity = object()
    module_name = f"_bedrock_artifact_{definition.id.replace('-', '_')}_{id(module_identity)}"
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        raise ValueError(f"{definition.source}: cannot load generator {source}")
    module = importlib.util.module_from_spec(spec)
    code = compile(source.read_bytes(), str(source), "exec", dont_inherit=True)
    registered = module_name in sys.modules
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        exec(code, vars(module))
        return {name: vars(module)[name] for name in _ENTRYPOINTS if name in vars(module)}
    finally:
        if registered:
            sys.modules[module_name] = previous
        else:
            sys.modules.pop(module_name, None)


def _validate_dependencies(definitions: Mapping[str, ArtifactDefinition]) -> None:
    active: list[str] = []
    complete: set[str] = set()

    def visit(artifact_id: str) -> None:
        if artifact_id in complete:
            return
        if artifact_id in active:
            start = active.index(artifact_id)
            cycle = (*active[start:], artifact_id)
            raise ValueError("circular artifact dependency: " + " -> ".join(cycle))
        active.append(artifact_id)
        generator = definitions[artifact_id]
        for dependency in generator.dependencies:
            if dependency not in definitions:
                raise ValueError(
                    f"{generator.source}: unknown artifact dependency "
                    f"{dependency!r}"
                )
            visit(dependency)
        active.pop()
        complete.add(artifact_id)

    for artifact_id in definitions:
        visit(artifact_id)


def _validate_output_ownership(definitions: Mapping[str, ArtifactDefinition]) -> None:
    owners: list[tuple[Path, str]] = []
    for artifact_id, generator in definitions.items():
        for output in generator.output_roots:
            for previous, previous_id in owners:
                if (
                    output == previous
                    or output.is_relative_to(previous)
                    or previous.is_relative_to(output)
                ):
                    raise ValueError(
                        f"artifact output root {output} owned by {artifact_id} "
                        f"overlaps {previous} owned by {previous_id}"
                    )
            owners.append((output, artifact_id))



def load_artifact_registry(workspace: SpecWorkspace) -> ArtifactGeneratorRegistry:
    with log_phase(_LOGGER, "artifact.registry.load", level=logging.DEBUG) as phase:
        artifact_root = workspace.root / "artifacts"
        definitions = {}
        for source in sorted(artifact_root.glob("*/artifact.yaml")):
            definition = load_artifact_definition(source)
            if definition.id in definitions:
                raise ValueError(f"duplicate artifact id {definition.id!r}")
            missing = sorted(set(definition.inputs) - set(workspace.providers))
            if missing:
                raise ValueError(f"{source}: unavailable artifact inputs {missing}")
            definitions[definition.id] = definition
        _validate_dependencies(definitions)
        _validate_output_ownership(definitions)
        entrypoints = {name: _load_generator(value) for name, value in definitions.items()}
        phase["artifacts"] = len(definitions)
        return ArtifactGeneratorRegistry(definitions, entrypoints)
