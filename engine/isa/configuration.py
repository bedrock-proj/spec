"""Values and operations owned by this specification boundary."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from engine.isa.catalog import SourceCatalog
from engine.isa.extensions import ExtensionDependencyCycleError


@dataclass(frozen=True, slots=True)
class IsaConfiguration:
    """An ordered, dependency-closed set of active extensions."""

    extension_ids: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, "extension_ids", tuple(self.extension_ids))

    @classmethod
    def resolve(
        cls,
        catalog: SourceCatalog,
        requested: Iterable[str] | None = None,
    ) -> "IsaConfiguration":
        requested_ids = (
            tuple(catalog.extensions)
            if requested is None
            else tuple(dict.fromkeys(requested))
        )
        enabled: set[str] = set()
        active: list[str] = []

        def enable(extension_id: str) -> None:
            extension = catalog.extension(extension_id)
            if extension_id in enabled:
                return
            if extension_id in active:
                raise ExtensionDependencyCycleError(extension.metadata.source, tuple((*active[active.index(extension_id):], extension_id)))
            active.append(extension_id)
            for required in extension.requires:
                if catalog.extension(required.metadata.id) is not required:
                    raise ValueError(f"{extension.metadata.source}: extension requirement is not canonical")
                enable(required.metadata.id)
            active.pop()
            enabled.add(extension_id)

        for extension_id in requested_ids:
            enable(extension_id)
        return cls(
            tuple(
                extension_id
                for extension_id in catalog.extensions
                if extension_id in enabled
            )
        )

    @property
    def owners(self) -> frozenset[str]:
        return frozenset(("base", *self.extension_ids))

    def enables(self, extension_id: str) -> bool:
        return extension_id in self.extension_ids
