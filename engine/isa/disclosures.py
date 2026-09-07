"""Typed implementation-defined disclosure registry."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from engine.isa.extensions import extension_owner_roots, load_extension_inventory
from engine.source.inventory import DirectoryInventory
from engine.source.yaml import load_schema_yaml


@dataclass(frozen=True, slots=True)
class ImplementationDisclosure:
    owner: str
    source: Path
    id: str
    item: str
    defining_rules: tuple[str, ...]
    publication: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "defining_rules", tuple(self.defining_rules))



@dataclass(frozen=True, slots=True)
class ImplementationDisclosureCatalog:
    sources: tuple[Path, ...]
    disclosures: tuple[ImplementationDisclosure, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "disclosures", tuple(self.disclosures))
        if len(set(self.sources)) != len(self.sources):
            raise ValueError("disclosure source snapshot repeats a source")
        seen = {}
        owners = {}
        for member in self.disclosures:
            if member.source not in self.sources:
                raise ValueError(f"{member.source}: disclosure source is not part of its catalog")
            if member.id in seen:
                raise ValueError(f"{member.source}: duplicate disclosure {member.id!r}; also declared by {seen[member.id].source}")
            if owners.setdefault(member.source, member.owner) != member.owner:
                raise ValueError(f"{member.source}: disclosure source has more than one owner")
            seen[member.id] = member



def load_disclosures(
    isa_root: str | Path,
    *,
    extensions: DirectoryInventory,
) -> "ImplementationDisclosureCatalog":
    root = Path(isa_root).resolve()
    schema_source = root / "schemas/implementation-disclosures.yaml"
    disclosures = []
    sources = []
    for owner, namespace_root in extension_owner_roots(extensions):
        source = namespace_root / "implementation_disclosures.yaml"
        if not source.is_file():
            continue
        sources.append(source)
        document = load_schema_yaml(source, schema_source)
        for raw in document["disclosures"]:
            disclosures.append(
                ImplementationDisclosure(
                    owner=owner,
                    source=source,
                    id=raw["id"],
                    item=raw["item"],
                    defining_rules=tuple(raw["defining_rules"]),
                    publication=raw["publication"],
                )
            )
    return ImplementationDisclosureCatalog(tuple(sources), tuple(disclosures))
