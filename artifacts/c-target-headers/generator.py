"""Construct explicitly selected public C headers from resolved interface inputs."""

from __future__ import annotations

from types import MappingProxyType

import os
from pathlib import Path
from typing import NamedTuple

from engine.artifacts.definition import GeneratedArtifact, GeneratedArtifactSet
from interfaces.c.model.project import InterfaceGroup, InterfaceIntrinsic, InterfaceType, InterfaceUtility
from engine.reference import Reference
from interfaces.c.model.projection import CInterfaceProjection, project_c_interface
from artifacts._shared.c_target_names import intrinsic_spelling, clang_builtin_spelling

_C_TYPES = MappingProxyType({
    "void": "void",
    "u8": "uint8_t",
    "u16": "uint16_t",
    "u32": "uint32_t",
    "u64": "uint64_t",
    "f32": "float",
    "f64": "double",
    "size": "size_t",
    "void_pointer": "void *",
    "const_void_pointer": "const void *",
    "u8_pointer": "uint8_t *",
    "u16_pointer": "uint16_t *",
    "u32_pointer": "uint32_t *",
    "u64_pointer": "uint64_t *",
    "f32_pointer": "float *",
    "f64_pointer": "double *",
})


class HeaderIntrinsicProjection(NamedTuple):
    intrinsic: InterfaceIntrinsic
    public_spelling: str
    builtin_spelling: str


class IntrinsicGroupHeaderProjection(NamedTuple):
    group: InterfaceGroup
    path: Path
    type_groups: tuple[InterfaceGroup, ...]
    utility_groups: tuple[InterfaceGroup, ...]
    types: tuple[InterfaceType, ...]
    utilities: tuple[InterfaceUtility, ...]
    includes: tuple[str, ...]
    dependencies: tuple[Path, ...]
    intrinsics: tuple[HeaderIntrinsicProjection, ...]


# Public header composition selects each source scope independently.
_HEADER_INPUTS = MappingProxyType({
    "core": ("base.intrinsics.core", ("base.types.core",), ()),
    "memory": ("base.intrinsics.memory", (), ()),
    "integer": ("base.intrinsics.integer", (), ()),
    "fpu": ("base.intrinsics.fpu", (), ()),
    "vector": ("base.intrinsics.vector", (), ()),
    "sysreg": ("base.intrinsics.sysreg", ("base.types.sysreg",), ("base.utilities.sysreg",)),
    "cache": ("base.intrinsics.cache", (), ()),
    "mmu": ("base.intrinsics.mmu", ("base.types.mmu",), ()),
})


def project_abi(project: CInterfaceProjection, outputs) -> tuple[IntrinsicGroupHeaderProjection, ...]:
    collections = {collection.id: collection for collection in project.collections}
    unknown = set(outputs) - set(_HEADER_INPUTS) - set(collections)
    if unknown:
        raise ValueError(f"unknown C header output selections: {sorted(unknown)}")
    indexes = tuple({group.reference: group for group in groups} for groups in (project.intrinsic_groups, project.type_groups, project.utility_groups))
    selected, placements, intrinsic_headers = {}, {}, {}
    for role in outputs:
        if role not in _HEADER_INPUTS:
            continue
        intrinsic_ref, type_refs, utility_refs = _HEADER_INPUTS[role]
        try:
            group = indexes[0][Reference.parse(intrinsic_ref)]
            type_groups = tuple(indexes[1][Reference.parse(value)] for value in type_refs)
            utility_groups = tuple(indexes[2][Reference.parse(value)] for value in utility_refs)
        except KeyError as error:
            raise ValueError(f"C header {role!r} selects an unknown scoped group: {error.args[0]!r}") from error
        types = tuple(value for member in type_groups for value in project.types_by_group[member.reference])
        utilities = tuple(value for member in utility_groups for value in project.utilities_by_group[member.reference])
        for value in types:
            if value.reference in placements:
                raise ValueError(f"C type {value.reference!r} has more than one selected header placement")
            if value.kind not in {"enum", "struct"}:
                raise ValueError(f"{value.source}: C headers cannot represent type kind {value.kind!r}")
            placements[value.reference] = role
        if group.reference in intrinsic_headers:
            raise ValueError(f"intrinsic group {group.reference!r} has more than one selected header placement")
        intrinsic_headers[group.reference] = role
        selected[role] = group, type_groups, utility_groups, types, utilities
    for role in outputs:
        if role in collections:
            for group in project.collection_groups[role]:
                if group.reference not in intrinsic_headers:
                    raise ValueError(f"C collection {role!r} requires unselected group {group.reference!r}")
    dependencies = {role: set() for role in selected}
    def require_type(role, definition):
        if not isinstance(definition, InterfaceType):
            return
        target = placements.get(definition.reference)
        if target is None:
            raise ValueError(f"C header {role!r} requires unplaced type {definition.reference!r}")
        if target != role:
            dependencies[role].add(target)
    for source, target in project.type_dependencies:
        if source.reference in placements:
            require_type(placements[source.reference], target)
    for role, (group, _, _, _, _) in selected.items():
        for intrinsic in project.intrinsics_by_group[group.reference]:
            result, parameters = project.signatures[intrinsic.reference]
            for kind in (result, *(parameter[1] for parameter in parameters)):
                require_type(role, kind)
    active, done = set(), set()
    def visit(role):
        if role in active:
            raise ValueError(f"C header dependencies form a cycle at {role!r}")
        if role in done:
            return
        active.add(role)
        for dependency in sorted(dependencies[role]):
            visit(dependency)
        active.remove(role)
        done.add(role)
    for role in selected:
        visit(role)
    result = []
    for role, (group, type_groups, utility_groups, types, utilities) in selected.items():
        type_refs = {value.reference for value in types}
        ordered_types = tuple(value for value in project.types if value.reference in type_refs)
        includes = tuple(dict.fromkeys(include for member in (group, *type_groups, *utility_groups) for include in project.group_includes[member.reference]))
        intrinsics = tuple(HeaderIntrinsicProjection(value, intrinsic_spelling(value.id), clang_builtin_spelling(value.id)) for value in sorted(project.intrinsics_by_group[group.reference], key=lambda value: value.id))
        result.append(IntrinsicGroupHeaderProjection(group, outputs[role], type_groups, utility_groups, ordered_types, tuple(sorted(utilities, key=lambda value: value.id)), includes, tuple(outputs[value] for value in sorted(dependencies[role])), intrinsics))
    return tuple(result)


def _inputs(context) -> CInterfaceProjection:
    interface = context.workspace.require_provider("interfaces.c")
    isa = context.workspace.require_provider("isa")
    c_abi = context.workspace.require_provider("abi.c")
    return context.shared_result((project_c_interface, id(interface), id(isa), id(c_abi)), lambda: project_c_interface(interface, isa, c_abi))


def generate(definition, context) -> GeneratedArtifactSet:
    project = _inputs(context)
    selected = project_abi(project, definition.outputs)
    paths = {group.group.reference: group.path for group in selected}
    artifacts = [GeneratedArtifact(group.path, _render_group(project, group)) for group in selected]
    for collection in project.collections:
        if collection.id in definition.outputs:
            path = definition.outputs[collection.id]
            artifacts.append(GeneratedArtifact(path, _render_collection(collection.id, project.collection_groups[collection.id], path, paths)))
    return GeneratedArtifactSet(tuple(artifacts), artifact_id=definition.id)


def validate(definition, context) -> None:
    project = _inputs(context)
    selected = project_abi(project, definition.outputs)
    for group in selected:
        for item in group.intrinsics:
            result, parameters = project.signatures[item.intrinsic.reference]
            _c_type(result)
            for _, kind, _ in parameters:
                _c_type(kind)


def _render_group(project, group) -> str:
    guard = f"__BEDROCK{group.group.id.upper()}INTRIN_H"
    sections = [f"#ifndef {guard}", f"#define {guard}", ""]
    sections.extend(f"#include <{include}>" for include in group.includes)
    sections.extend(f'#include "{Path(os.path.relpath(path, group.path.parent)).as_posix()}"' for path in group.dependencies)
    sections.append("")
    for utility in group.utilities:
        parameters, body = project.utility_definitions[utility.reference]
        sections.extend((_render_utility(utility.id, parameters, body), ""))
    for definition in group.types:
        sections.extend((_render_type(definition, project), ""))
    for item in group.intrinsics:
        sections.extend((_render_intrinsic(item, project), ""))
    sections.extend((f"#endif /* {guard} */", ""))
    return "\n".join(sections)


def _render_collection(collection_id, groups, path, paths) -> str:
    guard = "__BEDROCK_" + collection_id.upper() + "_INTRIN_H"
    lines = [f"#ifndef {guard}", f"#define {guard}", ""]
    lines.extend(f'#include "{Path(os.path.relpath(paths[group.reference], path.parent)).as_posix()}"' for group in groups)
    lines.extend(("", f"#endif /* {guard} */", ""))
    return "\n".join(lines)


def _render_utility(identifier, parameters, body) -> str:
    suffix = f"({', '.join(parameters)})" if parameters else ""
    return f"#define __BEDROCK_{identifier.upper()}{suffix} {body}"


def _render_type(definition, project) -> str:
    spelling, tag = f"__bedrock_{definition.id}_t", f"__bedrock_{definition.id}"
    if definition.kind == "enum":
        prefix = project.enum_prefixes[definition.reference]
        body = ",\n".join(f"  {prefix}{member.id} = {value:#x}" for member, value in project.enum_members[definition.reference])
        return f"typedef enum {tag} {{\n{body}\n}} {spelling};"
    if definition.kind == "struct":
        fields = []
        for name, kind, count, reserved, offset, size, alignment in project.record_fields[definition.reference]:
            member = "__" + name if reserved else name
            suffix = f"[{count}]" if count is not None else ""
            fields.append(f"  {_c_type(kind)} {member}{suffix};")
        return f"typedef struct {tag} {{\n" + "\n".join(fields) + f"\n}} {spelling};"
    raise ValueError(f"{definition.source}: unsupported C type kind {definition.kind!r}")


def _render_intrinsic(item, project) -> str:
    intrinsic = item.intrinsic
    result_type, parameters = project.signatures[intrinsic.reference]
    names = tuple(name for name, _, _ in parameters)
    public, builtin = item.public_spelling, item.builtin_spelling
    wrapper = project.wrappers[intrinsic.reference]
    if wrapper == "macro":
        return f"#define {public}({', '.join(names)}) " + "\\\n  " + f"{builtin}({', '.join(names)})"
    if wrapper in {"aggregate-result", "aggregate-result-macro"}:
        return _render_aggregate_wrapper(public, builtin, result_type, parameters, wrapper == "aggregate-result-macro")
    result = _c_type(result_type)
    declaration = _parameter_declaration(parameters)
    call = f"{builtin}({', '.join(names)})"
    statement = f"return {call};" if result != "void" else f"{call};"
    return f"static __inline__ {result}\n{public}({declaration})\n{{\n  {statement}\n}}"


def _render_aggregate_wrapper(public, builtin, result_type, parameters, macro) -> str:
    names = tuple(name for name, _, _ in parameters)
    result = _c_type(result_type)
    if macro:
        call = ", ".join((*names, "&__result.value", "&__result.flags"))
        lines = (f"#define {public}({', '.join(names)})", "  __extension__ ({", f"    {result} __result = {{0}};", f"    {builtin}({call});", "    __result;", "  })")
        return (" \\" + "\n").join(lines)
    call = ", ".join((*names, "&result.value", "&result.flags"))
    return f"static __inline__ {result}\n{public}({_parameter_declaration(parameters)})\n{{\n  {result} result = {{0}};\n  {builtin}({call});\n  return result;\n}}"


def _parameter_declaration(parameters) -> str:
    return ", ".join(f"{_c_type(kind)} {name}" for name, kind, _ in parameters) if parameters else "void"


def _c_type(kind) -> str:
    if isinstance(kind, InterfaceType):
        return f"__bedrock_{kind.id}_t"
    try:
        return _C_TYPES[kind]
    except KeyError as error:
        raise ValueError(f"C target cannot represent semantic type {kind!r}") from error
