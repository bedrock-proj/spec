"""Place resolved Sail contributions in the declared source project."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from engine.isa.model import SailUnit
from engine.sail.composition import SailProgram


@dataclass(frozen=True, slots=True)
class SailProjectModule:
    """One owned module in the generated Sail project."""

    name: str
    requirements: tuple[str, ...]
    sources: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "requirements", tuple(self.requirements))
        object.__setattr__(self, "sources", tuple(self.sources))


@dataclass(frozen=True, slots=True)
class SailProject:
    """Semantic Sail project structure before textual serialization."""

    modules: tuple[SailProjectModule, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "modules", tuple(self.modules))
        names = tuple(module.name for module in self.modules)
        duplicates = tuple(
            dict.fromkeys(name for name in names if names.count(name) > 1)
        )
        if duplicates:
            raise ValueError(
                f"duplicate Sail project module names: {list(duplicates)}"
            )


def project_sail_project(
    program: SailProgram,
    output_root: str | Path,
    outputs: dict[str, Path],
    *, source_root: Path, source_alias: Path,
) -> SailProject:
    root = Path(output_root).resolve()
    source_root = Path(source_root).resolve()
    source_alias = Path(source_alias).absolute()
    project_directory = root / outputs["project"].parent

    def authored_path(source):
        relative = source.relative_to(source_root)
        return Path(
            os.path.relpath(source_alias / relative, project_directory)
        ).as_posix()

    def generated_path(role):
        return Path(os.path.relpath(root / outputs[role], project_directory)).as_posix()

    sail_units = program.sail_units
    bundles = program.implementation_bundles
    registry_sources = (
        *(
            authored_path(source)
            for source in program.registry_type_sources
        ),
        generated_path("registry"),
    )
    modules = [SailProjectModule("registry", (), registry_sources)]
    boundary = next(
        (unit for unit in sail_units if unit.owner == "base" and unit.id == "boundary"),
        None,
    )
    ordinary_units = tuple((unit for unit in sail_units if unit is not boundary))
    for unit in ordinary_units:
        requirements = ["registry"]
        requirements.extend(
            (
                _module_name(required)
                for required in program.unit_dependencies[unit.reference]
            )
        )
        sources = tuple(
            (
                authored_path(source)
                for source in program.unit_sources[unit.reference]
            )
        )
        if unit.owner == "base" and unit.id == "decode":
            sources = (generated_path("catalog"), *sources)
        modules.append(
            SailProjectModule(_module_name(unit), tuple(requirements), sources)
        )
    operation_sources = tuple(
        dict.fromkeys(
            (
                authored_path(semantics.semantics)
                for semantics in bundles
            )
        )
    ) + (generated_path("dispatch"),)
    provider = program.execution_provider
    if provider is not None:
        operation_sources += (
            authored_path(provider.provider),
        )
    operation_requirements = [
        "registry",
        *(_module_name(unit) for unit in ordinary_units),
    ]
    modules.append(
        SailProjectModule(
            "operation_entries", tuple(operation_requirements), operation_sources
        )
    )
    if boundary is not None:
        requirements = ["registry", "operation_entries"]
        requirements.extend(
            (
                _module_name(required)
                for required in program.unit_dependencies[boundary.reference]
            )
        )
        sources = tuple(
            (
                authored_path(source)
                for source in program.unit_sources[boundary.reference]
            )
        )
        modules.append(
            SailProjectModule(_module_name(boundary), tuple(requirements), sources)
        )
    return SailProject(tuple(modules))


def render_sail_project(project: SailProject) -> str:
    lines: list[str] = []
    for module in project.modules:
        lines.append(f"{module.name} {{")
        if module.requirements:
            lines.append(f"  requires {', '.join(module.requirements)}")
        lines.extend(_render_sources(module.sources))
        lines.extend(("}", ""))
    return "\n".join(lines)


def _quoted_path(path: str) -> str:
    escaped = []
    for character in path:
        if character in ('"', "\\"):
            escaped.append("\\" + character)
        elif ord(character) < 32 or ord(character) == 127:
            escaped.append(f"\\{ord(character):03d}")
        else:
            escaped.append(character)
    return '"' + "".join(escaped) + '"'


def _render_sources(sources: tuple[str, ...]) -> list[str]:
    if len(sources) == 1:
        return [f"  files {_quoted_path(sources[0])}"]
    return [
        "  files",
        *(
            f"    {_quoted_path(source)}{',' if index + 1 < len(sources) else ''}"
            for index, source in enumerate(sources)
        ),
    ]


def _module_name(unit: SailUnit) -> str:
    name = f"{unit.owner}_{unit.id}"
    return "model_" + re.sub(r"[^A-Za-z0-9_]", "_", name)
