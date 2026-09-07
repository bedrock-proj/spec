"""Shared typed loading and schema validation for YAML authoring sources."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml
from jsonschema import Draft202012Validator


def freeze_source(value: Any) -> Any:
    """Detach acyclic YAML containers into immutable mappings, sequences, and sets.

    Repeated container references retain their identity inside the snapshot.
    Recursive source containers are rejected; scalar values are unchanged.
    """
    containers = (Mapping, list, tuple, set, frozenset)
    memo = {}
    active = set()
    pending = [(value, False)]

    def freeze(item):
        return memo[id(item)] if isinstance(item, containers) else item

    while pending:
        item, expanded = pending.pop()
        if not isinstance(item, containers):
            continue
        identity = id(item)
        if expanded:
            if isinstance(item, Mapping):
                result = MappingProxyType({freeze(key): freeze(child) for key, child in item.items()})
            elif isinstance(item, (set, frozenset)):
                result = frozenset(freeze(child) for child in item)
            else:
                result = tuple(freeze(child) for child in item)
            memo[identity] = result
            active.remove(identity)
            continue
        if identity in active:
            raise ValueError("recursive source container")
        if identity in memo:
            continue
        active.add(identity)
        pending.append((item, True))
        children = (
            tuple(child for pair in item.items() for child in pair)
            if isinstance(item, Mapping) else tuple(item)
        )
        pending.extend((child, False) for child in reversed(children))

    return freeze(value)


class _UniqueKeyLoader(yaml.SafeLoader):
    def construct_document(self, node):
        pending = [node]
        visited = set()
        while pending:
            current = pending.pop()
            identity = id(current)
            if identity in visited:
                continue
            visited.add(identity)
            if isinstance(current, yaml.MappingNode):
                keys = {}
                for key_node, value_node in current.value:
                    key = (
                        ("yaml-merge-key",)
                        if key_node.tag == "tag:yaml.org,2002:merge"
                        else key_node.value
                        if key_node.tag == "tag:yaml.org,2002:value"
                        else self.construct_object(key_node, deep=True)
                    )
                    try:
                        previous = keys.get(key)
                        keys[key] = key_node.start_mark
                    except TypeError as error:
                        raise yaml.constructor.ConstructorError(
                            "while constructing a mapping", current.start_mark,
                            "found an unhashable key", key_node.start_mark,
                        ) from error
                    if previous is not None:
                        raise yaml.constructor.ConstructorError(
                            "first occurrence of mapping key", previous,
                            f"duplicate mapping key {key!r}", key_node.start_mark,
                        )
                    pending.extend((key_node, value_node))
            elif isinstance(current, yaml.SequenceNode):
                pending.extend(current.value)
        return super().construct_document(node)


def load_yaml(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        with source.open("r", encoding="utf-8") as stream:
            document = yaml.load(stream, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as error:
        raise ValueError(f"{source}: invalid YAML: {error}") from error
    if not isinstance(document, dict):
        raise ValueError(f"{source}: expected a YAML mapping")
    return document


def load_schema_yaml(
    source: str | Path,
    schema: str | Path | Mapping[str, object],
) -> dict[str, Any]:
    source_path = Path(source)
    document = load_yaml(source_path)
    schema_document = (
        load_yaml(schema) if isinstance(schema, (str, Path)) else dict(schema)
    )
    errors = sorted(
        Draft202012Validator(schema_document).iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path)
        where = f" at {location}" if location else ""
        raise ValueError(f"{source_path}{where}: {error.message}") from error
    return document
