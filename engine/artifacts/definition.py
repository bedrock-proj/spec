"""Common contracts and discovery for generated specification artifacts."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from engine.source.yaml import freeze_source, load_schema_yaml

_LOGGER = logging.getLogger(__name__)
_ARTIFACT_ID = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*")
_OWNERSHIP_DIRECTORY = ".artifact-ownership"


@dataclass(frozen=True, slots=True)
class GeneratedArtifact:
    relative_path: Path
    content: str | bytes

    def __post_init__(self) -> None:
        if (
            not self.relative_path.parts
            or self.relative_path == Path(".")
            or self.relative_path.is_absolute()
            or ".." in self.relative_path.parts
        ):
            raise ValueError(
                f"generated artifact path escapes output root: {self.relative_path}"
            )


@dataclass(frozen=True, slots=True)
class GeneratedArtifactSet:
    artifacts: tuple[GeneratedArtifact, ...]
    artifact_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        if not self.artifact_id:
            raise ValueError("generated artifact set requires a non-empty artifact id")
        validate_artifact_paths(tuple(artifact.relative_path for artifact in self.artifacts))

    def artifact(self, relative_path: str | Path) -> GeneratedArtifact:
        wanted = Path(relative_path)
        matches = [item for item in self.artifacts if item.relative_path == wanted]
        if len(matches) != 1:
            raise ValueError(
                f"expected one generated artifact {wanted}, found {len(matches)}"
            )
        return matches[0]


def validate_artifact_paths(paths: tuple[Path, ...]) -> None:
    path_set: set[Path] = set()
    for path in paths:
        if not path.parts or path == Path("."):
            raise ValueError("generated artifact path must name a file")
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"generated artifact path escapes output root: {path}")
        if path.parts[0] == _OWNERSHIP_DIRECTORY:
            raise ValueError(
                f"generated artifact path uses reserved ownership directory: {path}"
            )
        if path in path_set:
            raise ValueError(f"duplicate generated artifact path: {path}")
        path_set.add(path)
    for path in paths:
        for parent in path.parents:
            if parent == Path("."):
                break
            if parent in path_set:
                raise ValueError(
                    "generated artifact paths overlap as file and directory: "
                    f"{parent}, {path}"
                )


@dataclass(frozen=True, slots=True)
class ArtifactDefinition:
    """One declarative artifact definition owned below ``artifacts``."""

    id: str
    source: Path
    data: Mapping[str, object]
    summary: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", freeze_source(self.data))

    @property
    def dependencies(self) -> tuple[str, ...]:
        raw = self.data.get("depends-on", ())
        return tuple(str(item) for item in raw)

    @property
    def inputs(self) -> tuple[str, ...]:
        raw = self.data.get("inputs", ())
        return tuple(str(item) for item in raw)

    @property
    def outputs(self) -> Mapping[str, Path]:
        return self._output_mapping("outputs")

    @property
    def derived_outputs(self) -> Mapping[str, Path]:
        return self._output_mapping("derived-outputs", required=False)

    def _output_mapping(self, key: str, *, required: bool = True) -> Mapping[str, Path]:
        raw = self.data[key] if required else self.data.get(key, {})
        if not isinstance(raw, Mapping):
            raise ValueError(f"{self.source}: {key} must be a mapping")
        outputs = {str(name): Path(str(path)) for name, path in raw.items()}
        for name, path in outputs.items():
            if (
                not path.parts
                or path == Path(".")
                or path.is_absolute()
                or ".." in path.parts
            ):
                raise ValueError(
                    f"{self.source}: {key} root {name!r} escapes the output tree"
                )
        return MappingProxyType(outputs)

    @property
    def output_roots(self) -> tuple[Path, ...]:
        return (*self.outputs.values(), *self.derived_outputs.values())

    def validate_generated(self, artifacts: GeneratedArtifactSet) -> None:
        """Require the generated set to populate exactly its declared roots."""

        if artifacts.artifact_id != self.id:
            raise ValueError(
                f"{self.source}: generated owner {artifacts.artifact_id!r} does not "
                f"match artifact id {self.id!r}"
            )
        roots = tuple(self.outputs.values())
        self._validate_owned_paths(artifacts, roots)
        populated = {
            root
            for root in roots
            if any(
                artifact.relative_path == root
                or artifact.relative_path.is_relative_to(root)
                for artifact in artifacts.artifacts
            )
        }
        missing = tuple(root for root in roots if root not in populated)
        if missing:
            raise ValueError(
                f"{self.source}: generated set does not populate output roots "
                f"{[path.as_posix() for path in missing]}"
            )

    def validate_owned(self, artifacts: GeneratedArtifactSet) -> None:
        """Require every published path to belong to this declared artifact."""

        if artifacts.artifact_id != self.id:
            raise ValueError(
                f"{self.source}: generated owner {artifacts.artifact_id!r} does not "
                f"match artifact id {self.id!r}"
            )
        self._validate_owned_paths(artifacts, self.output_roots)

    def _validate_owned_paths(
        self, artifacts: GeneratedArtifactSet, roots: tuple[Path, ...]
    ) -> None:
        for artifact in artifacts.artifacts:
            owners = tuple(
                root
                for root in roots
                if artifact.relative_path == root
                or artifact.relative_path.is_relative_to(root)
            )
            if len(owners) != 1:
                raise ValueError(
                    f"{self.source}: generated path {artifact.relative_path} is "
                    f"owned by {len(owners)} declared output roots"
                )


def load_artifact_definition(source: str | Path) -> ArtifactDefinition:
    source = Path(source).resolve()
    raw = load_schema_yaml(source, source.parent.parent / "schema.yaml")
    artifact_id = source.parent.name
    if source.name != "artifact.yaml":
        raise ValueError(f"{source}: expected a file named artifact.yaml")
    if _ARTIFACT_ID.fullmatch(artifact_id) is None:
        raise ValueError(f"{source}: invalid artifact directory {artifact_id!r}")
    return ArtifactDefinition(
        artifact_id,
        source,
        MappingProxyType(raw),
        str(raw["summary"]) if "summary" in raw else None,
    )
