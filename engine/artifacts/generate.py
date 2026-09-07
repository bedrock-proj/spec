"""Invocation-owned artifact projection and private shared results."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from engine.artifacts.definition import GeneratedArtifactSet
from engine.artifacts.registry import ArtifactGeneratorRegistry
from engine.workspace import SpecWorkspace

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class ArtifactGenerationContext:
    workspace: SpecWorkspace
    output_root: Path
    registry: ArtifactGeneratorRegistry
    generate: Callable[[str, Path | None], GeneratedArtifactSet]
    shared_result: Callable[[object, Callable[[], _T]], _T]


def artifact_context(registry, workspace, output_root):
    generated = {}
    shared = {}
    generating = []
    resolving = []

    def shared_result(key, factory):
        if key in shared:
            return shared[key]
        if key in resolving:
            raise ValueError(f"circular shared artifact projection: {key!r}")
        resolving.append(key)
        try:
            value = factory()
            shared[key] = value
            return value
        finally:
            resolving.pop()

    def generate(artifact_id, destination=None):
        root = Path(output_root if destination is None else destination).resolve()
        definition = registry.definition(artifact_id)
        producer = registry.entrypoint(artifact_id, "generate")
        key = (id(definition), producer, root)
        if key in generated:
            return generated[key]
        if key in generating:
            raise ValueError(f"circular artifact generation: {artifact_id}")
        generating.append(key)
        try:
            def dependency(name, destination=None):
                if name not in definition.dependencies:
                    raise ValueError(
                        f"{definition.source}: undeclared artifact dependency {name!r}"
                    )
                return generate(name, root if destination is None else destination)

            context = ArtifactGenerationContext(workspace, root, registry, dependency, shared_result)
            result = producer(definition, context)
            definition.validate_generated(result)
            generated[key] = result
            return result
        finally:
            generating.pop()

    return ArtifactGenerationContext(
        workspace, Path(output_root).resolve(), registry, generate, shared_result
    )


def generate_artifact(registry, artifact_id, workspace, output_root):
    return artifact_context(registry, workspace, output_root).generate(artifact_id, None)


def validate_artifacts(registry, context):
    for artifact_id in registry.artifact_ids:
        definition = registry.definition(artifact_id)
        try:
            registry.entrypoint(artifact_id, "validate")(definition, context)
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
            yield definition, error
