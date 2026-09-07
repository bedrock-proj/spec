"""Domain-neutral workspace exposed to artifact generators."""

from __future__ import annotations

from abi.c.model.project import load_c_abi
from abi.elf.model.project import load_elf_abi
from interfaces.c.model.project import load_c_interface

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, TypeVar

from engine.entity import EntityCatalog, EntityDependency
from engine.observability import log_phase
from engine.reference import QualifiedReference, Reference

_T = TypeVar("_T")
_LOGGER = logging.getLogger(__name__)


class SpecificationProvider(Protocol):
    """One domain exposed through the workspace's uniform entity contract."""

    entities: EntityCatalog

    def resolve(self, reference: Reference[_T]) -> _T: ...

    def entity_dependencies(self) -> tuple[EntityDependency, ...]: ...


@dataclass(frozen=True, slots=True)
class SpecWorkspace:
    """Repository root and named domain providers available to generators."""

    root: Path
    providers: Mapping[str, SpecificationProvider]



    def __post_init__(self) -> None:
        object.__setattr__(self, "providers", MappingProxyType(dict(self.providers)))

    def require_provider(self, name: str) -> SpecificationProvider:
        try:
            return self.providers[name]
        except KeyError as error:
            available = ", ".join(sorted(self.providers)) or "none"
            raise ValueError(
                f"workspace does not provide {name!r}; available providers: {available}"
            ) from error

    def resolve(
        self,
        reference: str | QualifiedReference[_T],
    ) -> _T:
        """Resolve a qualified reference through its owning provider."""

        qualified = QualifiedReference.parse(reference)
        provider = self.require_provider(qualified.domain)
        return provider.resolve(qualified.local)


def load_workspace(root: str | Path) -> "SpecWorkspace":
    """Load the repository's declared, closed-world provider composition."""

    from engine.isa.project import load_isa

    repository = Path(root).resolve()
    with log_phase(_LOGGER, "workspace.load", root=repository) as phase:
        with log_phase(_LOGGER, "workspace.provider.load", provider="isa"):
            isa = load_isa(repository / "isa")
        with log_phase(_LOGGER, "workspace.provider.load", provider="abi.elf"):
            elf = load_elf_abi(repository / "abi/elf", isa)
        with log_phase(_LOGGER, "workspace.provider.load", provider="abi.c"):
            c_abi = load_c_abi(repository / "abi/c", isa)
        with log_phase(_LOGGER, "workspace.provider.load", provider="interfaces.c"):
            interface = load_c_interface(repository / "interfaces/c", isa, c_abi)
        workspace = create_workspace(
            repository,
            {
                "isa": isa,
                "abi.elf": elf,
                "abi.c": c_abi,
                "interfaces.c": interface,
            },
        )
        phase["providers"] = len(workspace.providers)
        return workspace


def create_workspace( root: str | Path, providers: Mapping[str, SpecificationProvider]
) -> "SpecWorkspace":
    return SpecWorkspace(
        Path(root).resolve(),
        MappingProxyType(dict(providers)),
    )
