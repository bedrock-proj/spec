"""Declared extension metadata and closed-world extension inventory."""

from __future__ import annotations

from engine.source.inventory import inspect_inventory, require_exact

from engine.reference import Reference

from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar
from engine.entity import Entity
from collections.abc import Callable, Mapping
from types import MappingProxyType

from engine.source.inventory import DirectoryInventory
from engine.source.yaml import load_schema_yaml


@dataclass(frozen=True, slots=True)
class ExtensionMetadata:
    """Schema-decoded metadata from one ``extension.yaml`` file."""

    id: str
    name: str
    requires: tuple[str, ...]
    required_cpuid_flags: tuple[str, ...]
    source: Path
    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "requires", tuple(self.requires))
        object.__setattr__(self, "required_cpuid_flags", tuple(self.required_cpuid_flags))




def extension_owner_roots(inventory) -> tuple[tuple[str, Path], ...]:
    """Return base and declared extension roots in declaration order."""

    return (
        ("base", inventory.root.parent),
        *(
            (extension_id, inventory.root / extension_id)
            for extension_id in inventory.declared
        ),
    )


def load_extension_inventory(isa_root: str | Path) -> "DirectoryInventory":
    root = Path(isa_root) / "extensions"
    return require_exact(inspect_inventory(
        owner="isa",
        kind="extension",
        source=root / "extensions.yaml",
        root=root,
        key="extensions",
        name_pattern=r"[A-Z][A-Z0-9_]*",
    ))


_Requirement = TypeVar("_Requirement", bound=Entity)


class ExtensionDependencyCycleError(ValueError):
    def __init__(self, source: Path, cycle: tuple[str, ...]) -> None:
        self.source = source
        self.cycle = cycle
        super().__init__(
            f"{source}: circular extension dependency: {' -> '.join(cycle)}"
        )


class RequiredExtensionUnavailableError(ValueError):
    def __init__(self, source: Path | str, extension_id: str) -> None:
        self.source = source
        self.extension_id = extension_id
        super().__init__(
            f"{source}: required extension {extension_id!r} is not available"
        )






class RepeatedCpuidRequirementError(ValueError):
    def __init__(self, source: Path, field: Entity) -> None:
        self.source = source
        self.field = field
        super().__init__(
            f"{source}: CPUID flag {field.reference.element!r} repeats an inherited requirement"
        )


def load_extension_metadata( path: str | Path, isa_root: str | Path) -> "ExtensionMetadata":
    source = Path(path)
    root = Path(isa_root)
    document = load_schema_yaml(source, root / "schemas/extension.yaml")
    extension_id = source.parent.name
    return ExtensionMetadata(
        id=extension_id,
        name=document["name"],
        requires=tuple(document.get("requires", ())),
        required_cpuid_flags=tuple(document.get("required_cpuid_flags", ())),
        source=source,
        root=source.parent,
    )



def resolve_extension_requirements(
    metadata_by_id: Mapping[str, ExtensionMetadata],
    *,
    resolve_flag: Callable[[str, Path], _Requirement],
) -> Mapping[str, tuple[_Requirement, ...]]:
    """Resolve requirements once, in prerequisite-before-dependent order."""
    resolved: dict[str, tuple[_Requirement, ...]] = {}
    active: list[str] = []

    def resolve(extension_id: str) -> tuple[_Requirement, ...]:
        if extension_id in resolved:
            return resolved[extension_id]
        if extension_id in active:
            start = active.index(extension_id)
            raise ExtensionDependencyCycleError(
                metadata_by_id[active[-1]].source, (*active[start:], extension_id)
            )
        extension = metadata_by_id.get(extension_id)
        if extension is None:
            source = metadata_by_id[active[-1]].source if active else extension_id
            raise RequiredExtensionUnavailableError(source, extension_id)
        active.append(extension_id)
        try:
            fields: list[_Requirement] = []
            seen: set[Reference[_Requirement]] = set()
            for required_id in extension.requires:
                for field in resolve(required_id):
                    if field.reference not in seen:
                        fields.append(field)
                        seen.add(field.reference)
            for raw_reference in extension.required_cpuid_flags:
                field = resolve_flag(raw_reference, extension.source)
                if field.reference in seen:
                    raise RepeatedCpuidRequirementError(extension.source, field)
                fields.append(field)
                seen.add(field.reference)
            resolved[extension_id] = tuple(fields)
            return resolved[extension_id]
        finally:
            active.pop()

    for extension_id in metadata_by_id:
        resolve(extension_id)
    return MappingProxyType(resolved)
