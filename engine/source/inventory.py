"""Closed-world directory inventories shared by specification domains."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from engine.source.yaml import load_yaml


@dataclass(frozen=True, slots=True)
class DirectoryInventory:
    """One ordered YAML inventory paired with its member directories."""

    owner: str
    kind: str
    source: Path
    root: Path
    declared: tuple[str, ...]
    actual: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "declared", tuple(self.declared))
        object.__setattr__(self, "actual", tuple(self.actual))

    @property
    def missing(self) -> tuple[str, ...]:
        """Declared members without a corresponding directory."""

        return tuple(sorted(set(self.declared) - set(self.actual)))

    @property
    def undeclared(self) -> tuple[str, ...]:
        """Member directories absent from the declaration."""

        return tuple(sorted(set(self.actual) - set(self.declared)))

    @property
    def duplicates(self) -> tuple[str, ...]:
        """Names declared more than once."""

        return tuple(
            sorted({value for value in self.declared if self.declared.count(value) > 1})
        )





def inspect_inventory(
    *,
    owner: str,
    kind: str,
    source: str | Path,
    root: str | Path,
    key: str,
    allow_missing: bool = False,
    exact_keys: bool = False,
    validate_names: bool = False,
    name_pattern: str | None = None,
) -> DirectoryInventory:
    """Inspect membership without rejecting declared-versus-actual drift."""

    source_path = Path(source).resolve()
    root_path = Path(root).resolve()
    if not source_path.is_file():
        if not allow_missing:
            raise ValueError(f"required inventory does not exist: {source_path}")
        declared: tuple[str, ...] = ()
    else:
        document = load_yaml(source_path)
        if exact_keys and set(document) != {key}:
            raise ValueError(
                f"{source_path}: inventory keys must be exactly {key!r}"
            )
        values = document.get(key)
        if not isinstance(values, list) or any(
            not isinstance(value, str) for value in values
        ):
            raise ValueError(f"{source_path}: expected a {key} list of names")
        pattern = (
            name_pattern
            if name_pattern is not None
            else r"[A-Za-z][A-Za-z0-9_-]*"
            if validate_names
            else None
        )
        invalid = tuple(
            value
            for value in values
            if pattern is not None and re.fullmatch(pattern, value) is None
        )
        if invalid:
            raise ValueError(
                f"{source_path}: invalid {key} names {invalid}; "
                f"expected pattern {pattern!r}"
            )
        declared = tuple(values)
    actual = (
        tuple(
            sorted(
                path.name
                for path in root_path.iterdir()
                if path.is_dir() and not path.name.startswith(".")
            )
        )
        if root_path.is_dir()
        else ()
    )
    return DirectoryInventory(owner, kind, source_path, root_path, declared, actual)


def require_exact(inventory: DirectoryInventory) -> DirectoryInventory:
    """Reject invalid closed-world membership before publishing a catalog."""

    if inventory.duplicates:
        raise ValueError(
            f"{inventory.source}: duplicate {inventory.kind} entries "
            f"{list(inventory.duplicates)}"
        )
    if not inventory.root.is_dir():
        raise ValueError(
            f"{inventory.source}: member directory is missing: {inventory.root}"
        )
    if inventory.missing or inventory.undeclared:
        raise ValueError(
            f"{inventory.source}: declared {inventory.kind} {inventory.declared}; "
            f"member directories are {inventory.actual}"
        )
    return inventory
