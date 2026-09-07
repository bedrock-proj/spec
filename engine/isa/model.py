"""Typed manifests for executable Sail units and owner-local TeX topics."""

from __future__ import annotations

from engine.syntax.tex import mask_tex_code

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from engine.entity import Entity
from engine.isa.extensions import (
    extension_owner_roots,
)
from engine.source.inventory import DirectoryInventory
from engine.reference import Reference, ReferenceIndex
from engine.source.yaml import load_schema_yaml, load_yaml


class ModelError(ValueError):
    """Base class for a rejected model ownership or dependency relation."""


class MissingModelManifestError(ModelError):
    def __init__(self, source: Path) -> None:
        self.source = source
        super().__init__(f"required model manifest does not exist: {source}")


class ModelSourceOwnershipConflictError(ModelError):
    def __init__(
        self,
        manifest: Path,
        source: Path,
        first_owner: str,
        second_owner: str,
    ) -> None:
        self.manifest = manifest
        self.source = source
        self.first_owner = first_owner
        self.second_owner = second_owner
        super().__init__(
            f"{manifest}: source {source} is owned by both "
            f"{first_owner} and {second_owner}"
        )


class ModelSourceOutsideOwnerError(ModelError):
    def __init__(self, manifest: Path, owner: str, source: Path, root: Path) -> None:
        self.manifest = manifest
        self.owner = owner
        self.source = source
        self.root = root
        super().__init__(f"{manifest}: {owner} source {source} is outside {root}")


class InvalidTopicStructureError(ModelError):
    def __init__(
        self,
        manifest: Path,
        topic_id: str,
        document: Path,
        heading_count: int,
    ) -> None:
        self.manifest = manifest
        self.topic_id = topic_id
        self.document = document
        self.heading_count = heading_count
        super().__init__(
            f"{manifest}: document topic {topic_id!r} has {heading_count} headings "
            f"in {document}"
        )


class SailDependencyCycleError(ModelError):
    def __init__(self, source: Path, cycle: tuple[Reference["SailUnit"], ...]) -> None:
        self.source = source
        self.cycle = cycle
        super().__init__(f"{source}: circular Sail dependency: {cycle!r}")


class UnknownSailDependencyError(ModelError):
    def __init__(
        self,
        source: Path,
        requiring: Reference["SailUnit"],
        required: Reference["SailUnit"],
    ) -> None:
        self.source = source
        self.requiring = requiring
        self.required = required
        super().__init__(f"{source}: {requiring!r} requires unknown unit {required!r}")


@dataclass(frozen=True, slots=True)
class SailUnit:
    """One dependency-ordered executable semantics unit."""

    owner: str
    id: str
    reference: Reference["SailUnit"]
    source: Path
    sources: tuple[Path, ...]
    requires: tuple[Reference["SailUnit"], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "requires", tuple(self.requires))



@dataclass(frozen=True, slots=True)
class ExecutionProvider:
    """One owner-local implementation of the injectable execution boundaries."""

    owner: str
    source: Path
    provider: Path


@dataclass(frozen=True, slots=True)
class DocumentTopic(Entity):
    """One owner-local authored TeX source available to public composers."""

    owner: str
    id: str
    reference: Reference["DocumentTopic"]
    source: Path
    document: Path


@dataclass(frozen=True, slots=True)
class ModelNamespace:
    """The Sail units and document topics owned by base or one extension."""

    owner: str
    source: Path
    root: Path
    instruction_set: str | None
    fault_kinds: tuple[str, ...]
    effect_kinds: tuple[str, ...]
    sail_units: tuple[SailUnit, ...]
    execution_provider: ExecutionProvider | None
    document_topics: tuple[DocumentTopic, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fault_kinds", tuple(self.fault_kinds))
        object.__setattr__(self, "effect_kinds", tuple(self.effect_kinds))
        object.__setattr__(self, "sail_units", tuple(self.sail_units))
        object.__setattr__(self, "document_topics", tuple(self.document_topics))
        owned_sources = {}
        for collection in (self.sail_units, self.document_topics):
            identifiers = set()
            for member in collection:
                if member.owner != self.owner or member.source != self.source:
                    raise ModelError(f"{self.source}: model member must belong to its namespace manifest")
                if member.reference in identifiers:
                    raise ModelError(f"{self.source}: duplicate model member {member.reference!r}")
                identifiers.add(member.reference)
                paths = member.sources if isinstance(member, SailUnit) else (member.document,)
                for path in paths:
                    if path in owned_sources:
                        raise ModelSourceOwnershipConflictError(self.source, path, owned_sources[path], f"{self.owner}.{member.id}")
                    owned_sources[path] = f"{self.owner}.{member.id}"
        provider = self.execution_provider
        if provider is not None:
            if provider.owner != self.owner or provider.source != self.source:
                raise ModelError(f"{self.source}: execution provider must belong to its namespace manifest")
            if provider.provider in owned_sources:
                raise ModelSourceOwnershipConflictError(self.source, provider.provider, owned_sources[provider.provider], f"{self.owner}.execution_provider")



def load_model_namespace(
    schema: Mapping[str, object], owner: str, root: str | Path
) -> ModelNamespace:
    namespace_root = Path(root).resolve()
    source = namespace_root / "model.yaml"
    if not source.is_file():
        raise MissingModelManifestError(source)

    manifest = load_schema_yaml(source, schema)

    sail = manifest.get("sail", {})
    instruction_set = (
        str(sail["instruction_set"])
        if isinstance(sail, Mapping) and "instruction_set" in sail
        else None
    )
    fault_kinds = (
        tuple(str(item) for item in sail.get("fault_kinds", ()))
        if isinstance(sail, Mapping)
        else ()
    )
    sail_units = _load_sail_units(owner, namespace_root, source, manifest)
    provider = _load_execution_provider(
        owner, namespace_root, source, manifest
    )
    topics = _load_document_topics(owner, namespace_root, source, manifest)
    return ModelNamespace(
        owner,
        source,
        namespace_root,
        instruction_set,
        fault_kinds,
        tuple(str(item) for item in sail.get("effect_kinds", ())),
        sail_units,
        provider,
        topics,
    )


def _load_sail_units(
    owner: str,
    root: Path,
    manifest_path: Path,
    manifest: Mapping[str, object],
) -> tuple[SailUnit, ...]:
    section = manifest.get("sail", {})
    raw_units = section.get("units", ()) if isinstance(section, Mapping) else ()
    units: list[SailUnit] = []
    for raw in raw_units:
        unit_id = raw["id"]
        sources = tuple(
            _owned_source(owner, root, item, ".sail", manifest_path)
            for item in raw["sources"]
        )
        units.append(
            SailUnit(
                owner=owner,
                id=unit_id,
                reference=Reference.parse(f"{owner}.{unit_id}"),
                source=manifest_path,
                sources=sources,
                requires=tuple(
                    Reference.parse(reference) for reference in raw.get("requires", ())
                ),
            )
        )
    return tuple(units)


def _load_execution_provider(
    owner: str,
    root: Path,
    manifest_path: Path,
    manifest: Mapping[str, object],
) -> ExecutionProvider | None:
    section = manifest.get("sail", {})
    raw_provider = (
        section.get("execution_provider") if isinstance(section, Mapping) else None
    )
    if raw_provider is None:
        return None
    provider = _owned_source(owner, root, raw_provider, ".sail", manifest_path)
    return ExecutionProvider(owner, manifest_path, provider)


def _load_document_topics(
    owner: str,
    root: Path,
    manifest_path: Path,
    manifest: Mapping[str, object],
) -> tuple[DocumentTopic, ...]:
    section = manifest.get("documents", {})
    raw_topics = section.get("topics", ()) if isinstance(section, Mapping) else ()
    topics: list[DocumentTopic] = []
    for raw in raw_topics:
        topic_id = raw["id"]
        document = _owned_source(owner, root, raw["source"], ".tex", manifest_path)
        _require_one_topic_heading(document, manifest_path, topic_id)
        topics.append(
            DocumentTopic(
                owner=owner,
                id=topic_id,
                reference=Reference.parse(f"{owner}.{topic_id}"),
                source=manifest_path,
                document=document,
            )
        )
    return tuple(topics)


@dataclass(frozen=True, slots=True)
class ModelCatalog:
    """Independent Sail dependency and owner-local document catalogs."""

    base: ModelNamespace
    extensions: Mapping[str, ModelNamespace]
    sail_units: ReferenceIndex[SailUnit]
    sail_order: tuple[Reference[SailUnit], ...] = field(init=False)
    document_topics: ReferenceIndex[DocumentTopic]

    def __post_init__(self):
        object.__setattr__(self, "extensions", MappingProxyType(dict(self.extensions)))
        if self.base.owner != "base" or "base" in self.extensions or any(key != value.owner for key, value in self.extensions.items()):
            raise ModelError("model namespace keys differ from their owners")
        for name in ("sail_units", "document_topics"):
            members = {}
            for namespace in (self.base, *self.extensions.values()):
                for member in getattr(namespace, name):
                    if member.owner != namespace.owner or member.reference != Reference.parse(f"{namespace.owner}.{member.id}"):
                        raise ModelError(f"{member.source}: model member identity differs from its namespace")
                    if member.reference in members:
                        raise ModelError(f"{member.source}: duplicate model member {member.reference!r}")
                    members[member.reference] = member
            supplied = getattr(self, name)
            index = supplied if isinstance(supplied, ReferenceIndex) else ReferenceIndex(supplied)
            if set(index) != set(members) or any(index[reference] is not member for reference, member in members.items()):
                raise ModelError(f"model {name} index differs from its canonical namespaces")
            object.__setattr__(self, name, index)
        object.__setattr__(self, "sail_order", _resolve_sail_units(self.sail_units))


def load_model(
    isa_root: str | Path,
    extension_catalog: DirectoryInventory,
) -> ModelCatalog:
    root = Path(isa_root).resolve()
    extension_roots = {
        extension_id: extension_root
        for extension_id, extension_root in extension_owner_roots(extension_catalog)[1:]
        if extension_root.is_dir()
    }
    schema = load_yaml(root / "schemas/model.yaml")
    base = load_model_namespace(schema, "base", root)
    namespaces = {
        extension_id: load_model_namespace(schema, extension_id, extension_root)
        for extension_id, extension_root in extension_roots.items()
    }
    return resolve_model(base, namespaces)


def resolve_model(
    base: ModelNamespace,
    extensions: Mapping[str, ModelNamespace],
) -> ModelCatalog:
    sail_units: dict[Reference[SailUnit], SailUnit] = {}
    topics: dict[Reference[DocumentTopic], DocumentTopic] = {}
    for namespace in (base, *extensions.values()):
        for unit in namespace.sail_units:
            if unit.reference in sail_units:
                raise ValueError(f"duplicate Sail unit {unit.owner}.{unit.id}")
            sail_units[unit.reference] = unit
        for topic in namespace.document_topics:
            if topic.reference in topics:
                raise ValueError(f"duplicate document topic {topic.owner}.{topic.id}")
            topics[topic.reference] = topic

    return ModelCatalog(
        base=base,
        extensions=MappingProxyType(dict(extensions)),
        sail_units=ReferenceIndex(sail_units),
        document_topics=ReferenceIndex(topics),
    )


def _resolve_sail_units(
    units: Mapping[Reference[SailUnit], SailUnit],
) -> tuple[Reference[SailUnit], ...]:
    resolved: list[Reference[SailUnit]] = []
    complete: set[Reference[SailUnit]] = set()
    active: list[Reference[SailUnit]] = []

    def resolve(reference: Reference[SailUnit]) -> None:
        if reference in complete:
            return
        if reference in active:
            start = active.index(reference)
            cycle = (*active[start:], reference)
            raise SailDependencyCycleError(units[reference].source, cycle)
        unit = units.get(reference)
        if unit is None:
            requiring = active[-1] if active else reference
            source = (
                units[requiring].source if requiring in units else Path("model.yaml")
            )
            raise UnknownSailDependencyError(source, requiring, reference)
        active.append(reference)
        for required in unit.requires:
            resolve(required)
        active.pop()
        complete.add(reference)
        resolved.append(reference)

    for reference in units:
        resolve(reference)
    return tuple(resolved)


def _owned_source(
    owner: str, root: Path, raw: object, suffix: str, manifest: Path
) -> Path:
    relative = Path(str(raw))
    path = (root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root) or path.suffix != suffix:
        raise ModelSourceOutsideOwnerError(manifest, owner, path, root)
    if owner == "base" and path.is_relative_to((root / "extensions").resolve()):
        raise ModelSourceOutsideOwnerError(manifest, owner, path, root)
    if not path.is_file():
        raise ValueError(f"{manifest}: required {owner} source does not exist: {path}")
    return path


_TOPIC_HEADING = re.compile(
    r"^[ \t]*\\(?:part|chapter|section|subsection|subsubsection)\*?\{",
    re.MULTILINE,
)


def _require_one_topic_heading(document: Path, manifest: Path, topic_id: str) -> None:
    headings = _TOPIC_HEADING.findall(mask_tex_code(document.read_text(encoding="utf-8")))
    if len(headings) != 1:
        raise InvalidTopicStructureError(manifest, topic_id, document, len(headings))




def resolve_sail_sources(selected_units, control_registers):
    """Resolve the model's source contributions before artifact module placement."""
    state_sources = {
        (namespace.root / "control_registers/semantics/control_state.sail").resolve(): tuple(register.semantics for register in namespace.registers.values())
        for namespace in control_registers.namespaces.values() if namespace.registers
    }
    unit_sources = MappingProxyType({
        unit.reference: tuple(owned for source in unit.sources for owned in (source, *state_sources.get(source.resolve(), ())))
        for unit in selected_units
    })
    registry_types = (control_registers.namespaces["base"].root / "control_registers/semantics/types.sail",)
    return registry_types, unit_sources
