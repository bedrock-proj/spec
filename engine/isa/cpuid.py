"""Distributed CPUID definition loading and logical lookup."""

from __future__ import annotations

from engine.source.inventory import inspect_inventory

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from engine.diagnostics import Diagnostic
from engine.entity import Entity
from engine.isa.extensions import extension_owner_roots, load_extension_inventory
from engine.reference import (
    Reference,
    ReferenceIndex,
    UnknownReferenceError,
    register_reference,
)
from engine.source.inventory import DirectoryInventory
from engine.source.yaml import load_schema_yaml



class UnknownCpuidFlagError(ValueError):
    def __init__(self, source: Path, reference: Reference[object]) -> None:
        self.source = source
        self.reference = reference
        super().__init__(f"{source}: unknown CPUID flag reference {reference!r}")



class CpuidFlagWidthError(ValueError):
    def __init__(self, source: Path, field: CpuidField) -> None:
        self.source = source
        self.field = field
        super().__init__(
            f"{source}: CPUID flag {field.id!r} names a {field.bits}-bit field"
        )



@dataclass(frozen=True, slots=True)
class CpuidIndexRange:
    """An inclusive arithmetic progression of CPUID query indexes."""

    first: int
    last: int
    stride: int = 1

    @property
    def count(self) -> int:
        if self.last < self.first:
            return 0
        return (self.last - self.first) // self.stride + 1

    def contains(self, value: int) -> bool:
        return (
            self.first <= value <= self.last and (value - self.first) % self.stride == 0
        )

    def overlaps(self, other: "CpuidIndexRange") -> bool:
        smaller, larger = (self, other) if self.count <= other.count else (other, self)
        return any(
            larger.contains(value)
            for value in range(smaller.first, smaller.last + 1, smaller.stride)
        )


@dataclass(frozen=True, slots=True)
class CpuidField(Entity):
    """One named field in a 64-bit CPUID result."""

    reference: Reference["CpuidField"]
    source: Path
    id: str
    lsb: int
    bits: int

    @property
    def msb(self) -> int:
        return self.lsb + self.bits - 1

    def overlaps(self, other: "CpuidField") -> bool:
        return self.lsb <= other.msb and other.lsb <= self.msb


@dataclass(frozen=True, slots=True)
class CpuidLayout(Entity):
    """One reusable result layout owned by a CPUID leaf."""

    reference: Reference["CpuidLayout"]
    source: Path
    id: str
    fields: tuple[CpuidField, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", tuple(self.fields))



@dataclass(frozen=True, slots=True)
class CpuidCommonHeader(Entity):
    """The semantic fields shared by every CPUID index-zero header."""

    reference: Reference["CpuidCommonHeader"]
    source: Path
    id: str
    bits: int
    fields: tuple[CpuidField, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", tuple(self.fields))



@dataclass(frozen=True, slots=True)
class CpuidQuery(Entity):
    """One fixed or indexed CPUID query definition."""

    reference: Reference["CpuidQuery"]
    source: Path
    id: str
    indexes: CpuidIndexRange
    fields: tuple[CpuidField, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", tuple(self.fields))



@dataclass(frozen=True, slots=True)
class CpuidLeaf(Entity):
    """Common authored content of one CPUID leaf fragment."""

    reference: Reference["CpuidLeaf"]
    source: Path
    root: Path
    id: str
    name: str
    layouts: Mapping[str, CpuidLayout]
    queries: tuple[CpuidQuery, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "layouts", MappingProxyType(dict(self.layouts)))
        object.__setattr__(self, "queries", tuple(self.queries))



@dataclass(frozen=True, slots=True)
class CpuidLeafDefinition(CpuidLeaf):
    value: int


@dataclass(frozen=True, slots=True)
class CpuidLeafOverlay(CpuidLeaf):
    extends: Reference[CpuidLeaf]


@dataclass(frozen=True, slots=True)
class CpuidDiscoveryLeaf(CpuidLeaf):
    pass


@dataclass(frozen=True, slots=True)
class CpuidClass(Entity):
    """Common authored content of one CPUID class fragment."""

    reference: Reference["CpuidClass"]
    source: Path
    root: Path
    id: str
    name: str
    leaf_inventory: DirectoryInventory
    leaves: Mapping[str, CpuidLeaf]

    def __post_init__(self) -> None:
        object.__setattr__(self, "leaves", MappingProxyType(dict(self.leaves)))



@dataclass(frozen=True, slots=True)
class CpuidClassDefinition(CpuidClass):
    value: int


@dataclass(frozen=True, slots=True)
class CpuidClassOverlay(CpuidClass):
    extends: Reference[CpuidClass]


@dataclass(frozen=True, slots=True)
class CpuidNamespace:
    """All CPUID fragments authored by base or one extension."""

    owner: str
    root: Path
    class_inventory: DirectoryInventory
    classes: Mapping[str, CpuidClass]

    def __post_init__(self) -> None:
        object.__setattr__(self, "classes", MappingProxyType(dict(self.classes)))



class CpuidResolutionError(ValueError):
    """A source-located error while resolving a CPUID definition."""

    def __init__(self, source: Path, message: str) -> None:
        super().__init__(message)
        self.source = source


@dataclass(frozen=True, slots=True)
class ResolvedCpuidLeaf:
    """One leaf fragment joined with its root and numeric selector values."""

    leaf: CpuidLeaf
    root_leaf: CpuidLeaf
    class_value: int
    leaf_value: int


@dataclass(frozen=True, slots=True)
class CpuidCatalog:
    """The union of distributed base and extension CPUID definitions."""

    namespaces: Mapping[str, CpuidNamespace]
    common_header: CpuidCommonHeader
    classes: ReferenceIndex[CpuidClass]
    leaves: ReferenceIndex[CpuidLeaf]
    layouts: ReferenceIndex[CpuidLayout]
    queries: ReferenceIndex[CpuidQuery]
    fields: ReferenceIndex[CpuidField]
    layout_fields: ReferenceIndex[CpuidField]
    common_headers: ReferenceIndex[CpuidCommonHeader]
    common_header_fields: ReferenceIndex[CpuidField]



    def __post_init__(self) -> None:
        namespaces = dict(self.namespaces)
        classes: dict[Reference[CpuidClass], CpuidClass] = {}
        leaves: dict[Reference[CpuidLeaf], CpuidLeaf] = {}
        layouts: dict[Reference[CpuidLayout], CpuidLayout] = {}
        queries: dict[Reference[CpuidQuery], CpuidQuery] = {}
        fields: dict[Reference[CpuidField], CpuidField] = {}
        layout_fields: dict[Reference[CpuidField], CpuidField] = {}
        header_fields: dict[Reference[CpuidField], CpuidField] = {}
        for field in self.common_header.fields:
            parent = self.common_header.reference
            if field.reference != Reference(parent.owner, (*parent.path, parent.element), field.id):
                raise ValueError(f"{field.source}: CPUID common field identity differs from its header")
            register_reference(header_fields, field.reference, field)
        for owner, namespace in namespaces.items():
            if owner != namespace.owner:
                raise ValueError("CPUID namespace key differs from its owner")
            for class_id, cpuid_class in namespace.classes.items():
                if class_id != cpuid_class.id or cpuid_class.reference != Reference(owner, ("cpuid",), class_id):
                    raise ValueError(f"{cpuid_class.source}: CPUID class identity differs from its namespace")
                register_reference(classes, cpuid_class.reference, cpuid_class)
                for leaf_id, leaf in cpuid_class.leaves.items():
                    if leaf_id != leaf.id or leaf.reference != Reference(owner, ("cpuid", class_id), leaf_id):
                        raise ValueError(f"{leaf.source}: CPUID leaf identity differs from its class")
                    register_reference(leaves, leaf.reference, leaf)
                    for layout_id, layout in leaf.layouts.items():
                        if layout_id != layout.id or layout.reference != Reference(owner, ("cpuid", class_id, leaf_id), layout_id):
                            raise ValueError(f"{layout.source}: CPUID layout key differs from its identity")
                        register_reference(layouts, layout.reference, layout)
                        for field in layout.fields:
                            if field.reference != Reference(owner, ("cpuid", class_id, leaf_id, layout_id), field.id):
                                raise ValueError(f"{field.source}: CPUID field identity differs from its layout")
                            register_reference(layout_fields, field.reference, field)
                    for query in leaf.queries:
                        if query.reference != Reference(owner, ("cpuid", class_id, leaf_id), query.id):
                            raise ValueError(f"{query.source}: CPUID query identity differs from its leaf")
                        register_reference(queries, query.reference, query)
                        for field in query.fields:
                            if field.reference != Reference(owner, ("cpuid", class_id, leaf_id, query.id), field.id):
                                raise ValueError(f"{field.source}: CPUID field identity differs from its query")
                            register_reference(fields, field.reference, field)
        identities = {}
        for name, expected in (
            ("classes", classes), ("leaves", leaves), ("layouts", layouts),
            ("queries", queries), ("fields", fields), ("layout_fields", layout_fields),
            ("common_headers", {self.common_header.reference: self.common_header}),
            ("common_header_fields", header_fields),
        ):
            for reference, member in expected.items():
                if reference in identities:
                    previous = identities[reference]
                    raise ValueError(f"{member.source}: {type(member).__name__} repeats CPUID identity {reference!r}; also defined by {type(previous).__name__} at {previous.source}")
                identities[reference] = member
            supplied = getattr(self, name)
            index = supplied if isinstance(supplied, ReferenceIndex) else ReferenceIndex(supplied)
            if set(index) != set(expected) or any(index[reference] is not value for reference, value in expected.items()):
                raise ValueError(f"CPUID {name} index differs from its canonical tree")
            object.__setattr__(self, name, index)
        object.__setattr__(self, "namespaces", MappingProxyType(namespaces))

    @property
    def base(self) -> CpuidNamespace:
        return self.namespaces["base"]

    def namespace(self, owner: str) -> CpuidNamespace:
        try:
            return self.namespaces[owner]
        except KeyError as error:
            raise ValueError(f"unknown CPUID namespace {owner!r}") from error

    def root_class(self, cpuid_class: CpuidClass) -> CpuidClassDefinition:
        """Resolve a CPUID class overlay to its numeric class definition."""

        active: list[Reference[CpuidClass]] = []
        current = cpuid_class
        while isinstance(current, CpuidClassOverlay):
            if current.reference in active:
                raise CpuidResolutionError(
                    current.source, "circular CPUID class overlay"
                )
            active.append(current.reference)
            try:
                target = self.classes.resolve(current.extends)
            except UnknownReferenceError as error:
                raise CpuidResolutionError(
                    current.source, "unknown CPUID class overlay target"
                ) from error
            if current.id != target.id:
                raise CpuidResolutionError(
                    current.source,
                    f"class ID {current.id!r} does not match overlay target "
                    f"ID {target.id!r}",
                )
            current = target
        if not isinstance(current, CpuidClassDefinition):
            raise CpuidResolutionError(
                current.source, f"incomplete CPUID class definition {current.id!r}"
            )
        return current

    def _extension_discovery_leaf(self, cpuid_class: CpuidClass) -> CpuidLeaf | None:
        root_class = self.root_class(cpuid_class)
        if not isinstance(cpuid_class, CpuidClassOverlay) or root_class.value != 1:
            return None
        roots = tuple(
            leaf
            for leaf in cpuid_class.leaves.values()
            if isinstance(leaf, CpuidDiscoveryLeaf)
        )
        if len(roots) != 1:
            raise CpuidResolutionError(
                cpuid_class.leaf_inventory.source,
                "a class-1 extension contribution must own one discovery leaf",
            )
        return roots[0]

    def resolve_leaf(self, leaf: CpuidLeaf) -> ResolvedCpuidLeaf:
        """Resolve one leaf fragment through its definition and overlays."""

        active: list[Reference[CpuidLeaf]] = []

        def resolve(current: CpuidLeaf) -> ResolvedCpuidLeaf:
            if current.reference in active:
                raise CpuidResolutionError(
                    current.source, "circular CPUID leaf overlay"
                )
            if (
                current.reference.path[:1] != ("cpuid",)
                or len(current.reference.path) != 2
            ):
                raise CpuidResolutionError(
                    current.source,
                    f"invalid CPUID leaf reference {current.reference!r}",
                )
            cpuid_class = self.classes.resolve(
                Reference(
                    current.reference.owner,
                    ("cpuid",),
                    current.reference.path[1],
                )
            )
            root_class = self.root_class(cpuid_class)
            discovery = self._extension_discovery_leaf(cpuid_class)

            if isinstance(current, CpuidLeafOverlay):
                active.append(current.reference)
                try:
                    target = self.leaves.resolve(current.extends)
                except UnknownReferenceError as error:
                    raise CpuidResolutionError(
                        current.source, "unknown CPUID leaf overlay target"
                    ) from error
                resolved = resolve(target)
                active.pop()
                if current.id != target.id:
                    raise CpuidResolutionError(
                        current.source,
                        f"leaf ID {current.id!r} does not match overlay target "
                        f"ID {target.id!r}",
                    )
                if resolved.class_value != root_class.value:
                    raise CpuidResolutionError(
                        current.source,
                        f"CPUID leaf overlay {current.id!r} crosses numeric classes",
                    )
                return ResolvedCpuidLeaf(
                    current,
                    resolved.root_leaf,
                    resolved.class_value,
                    resolved.leaf_value,
                )

            if current is discovery:
                directories = tuple(
                    candidate
                    for candidate in cpuid_class.leaves.values()
                    if isinstance(candidate, CpuidLeafOverlay)
                    and (
                        (resolved := resolve(candidate)).class_value,
                        resolved.leaf_value,
                    )
                    == (1, 0)
                )
                if len(directories) != 1:
                    raise CpuidResolutionError(
                        cpuid_class.leaf_inventory.source,
                        "a class-1 extension contribution must reopen leaf 0 once",
                    )
                directory = directories[0]
                if len(directory.queries) != 1 or len(directory.queries[0].fields) != 1:
                    raise CpuidResolutionError(
                        directory.source,
                        "a class-1 extension directory contribution must own one bit",
                    )
                query = directory.queries[0]
                field = query.fields[0]
                if query.indexes.count != 1 or field.bits != 1:
                    raise CpuidResolutionError(
                        directory.source,
                        "an extension directory bit requires one fixed query index",
                    )
                try:
                    leaf_value = extension_discovery_leaf_value(
                        query.indexes.first, field.lsb
                    )
                except ValueError as error:
                    raise CpuidResolutionError(directory.source, str(error)) from error
                return ResolvedCpuidLeaf(current, current, root_class.value, leaf_value)

            if not isinstance(current, CpuidLeafDefinition):
                raise CpuidResolutionError(
                    current.source, f"incomplete CPUID leaf definition {current.id!r}"
                )
            return ResolvedCpuidLeaf(current, current, root_class.value, current.value)

        return resolve(leaf)

    def resolved_leaves(self) -> tuple[ResolvedCpuidLeaf, ...]:
        """Return all leaf fragments in owner and inventory order."""

        for cpuid_class in self.classes.values():
            self._extension_discovery_leaf(cpuid_class)
        return tuple(self.resolve_leaf(leaf) for leaf in self.leaves.values())

    def resolve_field(
        self, reference: Reference[CpuidField]
    ) -> tuple[ResolvedCpuidLeaf, CpuidQuery, CpuidField]:
        """Resolve a query field together with its canonical owning query and leaf."""

        field = self.fields.resolve(reference)
        for leaf in self.leaves.values():
            for query in leaf.queries:
                if any(member is field for member in query.fields):
                    return self.resolve_leaf(leaf), query, field
        raise CpuidResolutionError(
            field.source, f"CPUID field {field.id!r} has no owning query"
        )

    def resolve_flag(self, raw_reference: str | Reference[CpuidField], source: Path) -> CpuidField:
        """Resolve one fixed-index, one-bit CPUID availability field."""

        reference: Reference[CpuidField] = Reference.parse(raw_reference)

        try:
            _, query, field = self.resolve_field(reference)
        except UnknownReferenceError as error:
            raise UnknownCpuidFlagError(source, reference) from error
        if field.bits != 1:
            raise CpuidFlagWidthError(source, field)
        if query.indexes.count != 1:
            raise ValueError(
                f"{source}: CPUID flag {field.id!r} belongs to an indexed query range"
            )
        return field


def extension_discovery_leaf_value(directory_index: int, directory_bit: int) -> int:
    """Map one class-1 directory slot to its one-to-one discovery leaf."""

    if not 1 <= directory_index <= 1023:
        raise ValueError(
            f"extension directory index {directory_index!r} is outside 1..1023"
        )
    if not 0 <= directory_bit < 64:
        raise ValueError(f"extension directory bit {directory_bit!r} is outside 0..63")
    return 64 * (directory_index - 1) + directory_bit + 1


def compose_selector(class_value: int, leaf_value: int, index: int) -> int:
    """Compose the fixed architectural CPUID selector representation."""

    for name, value, limit in (
        ("class", class_value, 1 << 32),
        ("leaf", leaf_value, 1 << 16),
        ("index", index, 1 << 16),
    ):
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value < limit
        ):
            raise ValueError(f"CPUID {name} value {value!r} is out of range")
    return class_value << 32 | leaf_value << 16 | index


def _load_common_header(isa_root: Path, references: dict) -> CpuidCommonHeader:
    source = isa_root / "cpuid/common_header.yaml"
    raw = load_schema_yaml(source, isa_root / "schemas/cpuid-common-header.yaml")
    reference: Reference[CpuidCommonHeader] = Reference("base", ("cpuid",), raw["id"])
    fields = tuple(
        CpuidField(
            Reference(
                reference.owner,
                (*reference.path, reference.element),
                value["id"],
            ),
            source,
            value["id"],
            value["lsb"],
            value["bits"],
        )
        for value in raw["fields"]
    )
    header = CpuidCommonHeader(reference, source, raw["id"], raw["bits"], fields)
    for index, field in enumerate(fields):
        if field.msb >= header.bits:
            raise ValueError(f"{source}: common-header field {field.id!r} exceeds header width")
        for other in fields[index + 1 :]:
            if field.overlaps(other):
                raise ValueError(f"{source}: common-header fields {field.id!r} and {other.id!r} overlap")
    register_reference(references["common_headers"], header.reference, header)
    for field in fields:
        register_reference(references["common_header_fields"], field.reference, field)
    return header


def _load_namespace(
    owner: str,
    namespace_root: Path,
    isa_root: Path,
    references: dict,
) -> CpuidNamespace:
    classes_root = namespace_root / "cpuid/classes"
    inventory = _load_inventory(owner, classes_root, "classes")
    classes: dict[str, CpuidClass] = {}
    for class_id in inventory.declared:
        class_root = classes_root / class_id
        if class_id in classes or not class_root.is_dir():
            continue
        cpuid_class = _load_class(owner, class_root, isa_root, references)
        register_reference(references["classes"], cpuid_class.reference, cpuid_class)
        classes[class_id] = cpuid_class
    return CpuidNamespace(owner, namespace_root, inventory, MappingProxyType(classes))


def _load_class(
    owner: str,
    root: Path,
    isa_root: Path,
    references: dict,
) -> CpuidClass:
    source = root / "class.yaml"
    document = load_schema_yaml(source, isa_root / "schemas/cpuid-class.yaml")
    class_id = root.name
    reference: Reference[CpuidClass] = Reference(owner, ("cpuid",), class_id)
    leaves_root = root / "leaves"
    inventory = _load_inventory(owner, leaves_root, "leaves")
    leaves: dict[str, CpuidLeaf] = {}
    for leaf_id in inventory.declared:
        leaf_root = leaves_root / leaf_id
        if leaf_id in leaves or not leaf_root.is_dir():
            continue
        leaf = _load_leaf(owner, class_id, leaf_root, isa_root, references)
        register_reference(references["leaves"], leaf.reference, leaf)
        leaves[leaf_id] = leaf
    common = (
        reference,
        source,
        root,
        class_id,
        document["name"],
        inventory,
        MappingProxyType(leaves),
    )
    if "extends" in document:
        return CpuidClassOverlay(
            *common, Reference.parse(cast(str, document["extends"]))
        )
    return CpuidClassDefinition(*common, document["value"])


def _load_leaf(
    owner: str,
    class_id: str,
    root: Path,
    isa_root: Path,
    references: dict,
) -> CpuidLeaf:
    source = root / "leaf.yaml"
    document = load_schema_yaml(source, isa_root / "schemas/cpuid-leaf.yaml")
    leaf_id = root.name
    reference: Reference[CpuidLeaf] = Reference(owner, ("cpuid", class_id), leaf_id)
    raw_layouts = document.get("layouts", {})
    layouts: dict[str, CpuidLayout] = {}
    for layout_id, raw_layout in raw_layouts.items():
        layout_reference: Reference[CpuidLayout] = Reference(
            owner, ("cpuid", class_id, leaf_id), layout_id
        )
        layout_fields = tuple(
            CpuidField(
                reference=Reference(
                    owner,
                    ("cpuid", class_id, leaf_id, layout_id),
                    raw_field["id"],
                ),
                source=source,
                id=raw_field["id"],
                lsb=raw_field["lsb"],
                bits=raw_field["bits"],
            )
            for raw_field in raw_layout["fields"]
        )
        layout = CpuidLayout(layout_reference, source, layout_id, layout_fields)
        register_reference(references["layouts"], layout.reference, layout)
        for field in layout_fields:
            register_reference(references["layout_fields"], field.reference, field)
        layouts[layout_id] = layout
    queries: list[CpuidQuery] = []
    for raw_query in document["queries"]:
        query_id = raw_query["id"]
        query_reference: Reference[CpuidQuery] = Reference(
            owner, ("cpuid", class_id, leaf_id), query_id
        )
        raw_fields: list[Mapping[str, Any]] = []
        layout_id = raw_query.get("layout")
        if layout_id is not None:
            layout = raw_layouts.get(layout_id)
            if layout is None:
                raise ValueError(
                    f"{source}: query {query_id!r} uses unknown layout {layout_id!r}"
                )
            raw_fields.extend(layout["fields"])
        raw_fields.extend(raw_query.get("fields", ()))
        fields = tuple(
            CpuidField(
                reference=Reference(
                    owner, ("cpuid", class_id, leaf_id, query_id), raw_field["id"]
                ),
                source=source,
                id=raw_field["id"],
                lsb=raw_field["lsb"],
                bits=raw_field["bits"],
            )
            for raw_field in raw_fields
        )
        query = CpuidQuery(
            reference=query_reference,
            source=source,
            id=query_id,
            indexes=_parse_indexes(raw_query["index"]),
            fields=fields,
        )
        register_reference(references["queries"], query.reference, query)
        for field in fields:
            register_reference(references["fields"], field.reference, field)
        queries.append(query)
    common = (
        reference,
        source,
        root,
        leaf_id,
        document["name"],
        MappingProxyType(layouts),
        tuple(queries),
    )
    if "extends" in document:
        return CpuidLeafOverlay(
            *common, Reference.parse(cast(str, document["extends"]))
        )
    if "value" in document:
        return CpuidLeafDefinition(*common, document["value"])
    return CpuidDiscoveryLeaf(*common)


def _parse_indexes(raw: object) -> CpuidIndexRange:
    if isinstance(raw, int) and not isinstance(raw, bool):
        return CpuidIndexRange(raw, raw)
    if not isinstance(raw, Mapping):
        raise ValueError(f"invalid CPUID index specification {raw!r}")
    return CpuidIndexRange(raw["first"], raw["last"], raw.get("stride", 1))


def _load_inventory(owner: str, root: Path, key: str) -> DirectoryInventory:
    return inspect_inventory(
        owner=owner,
        kind={"classes": "class", "leaves": "leaf"}[key],
        source=root / f"{key}.yaml",
        root=root,
        key=key,
        allow_missing=True,
        name_pattern=r"[A-Z][A-Z0-9_]*",
    )




def check_cpuid(catalog: CpuidCatalog) -> Iterator[Diagnostic]:
    from engine.diagnostics import _error

    yield from _cpuid_validate_inventories(catalog)
    try:
        resolved = catalog.resolved_leaves()
    except CpuidResolutionError as error:
        yield _error("cpuid.resolution", error.source, str(error))
        return
    leaf_values = {
        item.leaf.reference: (item.class_value, item.leaf_value) for item in resolved
    }
    leaf_roots = {item.leaf.reference: item.root_leaf.reference for item in resolved}
    yield from _cpuid_validate_class_values(catalog)
    yield from _cpuid_validate_leaf_values(catalog, leaf_values)
    yield from _cpuid_validate_query_indexes(catalog, leaf_values, leaf_roots)


def _cpuid_validate_inventories(catalog: CpuidCatalog) -> Iterator[Diagnostic]:
    from engine.diagnostics import _error

    for namespace in catalog.namespaces.values():
        inventories = [namespace.class_inventory]
        inventories.extend(
            cpuid_class.leaf_inventory for cpuid_class in namespace.classes.values()
        )
        for inventory in inventories:
            kind = inventory.kind
            for missing in inventory.missing:
                yield _error(
                    f"cpuid.{kind}.missing-directory",
                    inventory.source,
                    f"declared CPUID {kind} {missing!r} has no directory",
                )
            for undeclared in inventory.undeclared:
                yield _error(
                    f"cpuid.{kind}.undeclared-directory",
                    inventory.root / undeclared,
                    f"CPUID {kind} directory {undeclared!r} is not in "
                    f"{inventory.source.name}",
                )
            for duplicate in inventory.duplicates:
                yield _error(
                    f"cpuid.{kind}.duplicate",
                    inventory.source,
                    f"CPUID {kind} {duplicate!r} is listed more than once",
                )


def _cpuid_validate_class_values(catalog: CpuidCatalog) -> Iterator[Diagnostic]:
    from engine.diagnostics import RelatedLocation, _error

    definitions = [
        cpuid_class
        for cpuid_class in catalog.classes.values()
        if isinstance(cpuid_class, CpuidClassDefinition)
    ]
    for index, left in enumerate(definitions):
        for right in definitions[index + 1 :]:
            if left.value != right.value:
                continue
            yield _error(
                "cpuid.class.value-overlap",
                left.source,
                f"class value 0x{left.value:08x} is also assigned to {right.id!r}",
                "value",
                related=(RelatedLocation(right.source, "conflicting class value"),),
            )


def _cpuid_validate_leaf_values(
    catalog: CpuidCatalog,
    leaf_values: Mapping[Reference[CpuidLeaf], tuple[int, int]],
) -> Iterator[Diagnostic]:
    from engine.diagnostics import RelatedLocation, _error

    definitions: list[tuple[tuple[int, int], CpuidLeaf]] = []
    for namespace in catalog.namespaces.values():
        for cpuid_class in namespace.classes.values():
            for leaf in cpuid_class.leaves.values():
                if isinstance(leaf, CpuidLeafOverlay):
                    continue
                selector = leaf_values.get(leaf.reference)
                if selector is not None:
                    definitions.append((selector, leaf))
    for index, (left_selector, left) in enumerate(definitions):
        for right_selector, right in definitions[index + 1 :]:
            if left_selector != right_selector:
                continue
            path = ("value",) if isinstance(left, CpuidLeafDefinition) else ()
            yield _error(
                "cpuid.leaf.value-overlap",
                left.source,
                f"leaf value 0x{left_selector[1]:04x} in class "
                f"0x{left_selector[0]:08x} "
                f"is also assigned to {right.id!r}",
                *path,
                related=(RelatedLocation(right.source, "conflicting leaf value"),),
            )


def _cpuid_validate_query_indexes(
    catalog: CpuidCatalog,
    leaf_values: Mapping[Reference[CpuidLeaf], tuple[int, int]],
    leaf_roots: Mapping[Reference[CpuidLeaf], Reference[CpuidLeaf]],
) -> Iterator[Diagnostic]:
    from engine.diagnostics import RelatedLocation, _error

    entries: list[tuple[tuple[int, int], Reference[CpuidLeaf], CpuidQuery]] = []
    for namespace in catalog.namespaces.values():
        for cpuid_class in namespace.classes.values():
            for leaf in cpuid_class.leaves.values():
                selector = leaf_values.get(leaf.reference)
                root = leaf_roots.get(leaf.reference)
                if selector is None or root is None:
                    continue
                for query in leaf.queries:
                    for field in query.fields:
                        if field.msb > 63:
                            yield _error(
                                "cpuid.field.range",
                                field.source,
                                f"field {field.id!r} occupies bits "
                                f"{field.msb}..{field.lsb} outside a 64-bit result",
                                "queries",
                            )
                    for field_index, left_field in enumerate(query.fields):
                        for right_field in query.fields[field_index + 1 :]:
                            if left_field.overlaps(right_field):
                                yield _error(
                                    "cpuid.field.overlap",
                                    left_field.source,
                                    f"fields {left_field.id!r} and {right_field.id!r} overlap",
                                    "queries",
                                )
                    entries.append((selector, root, query))

    for index, (left_selector, left_root, left) in enumerate(entries):
        for right_selector, right_root, right in entries[index + 1 :]:
            if left_selector != right_selector or not left.indexes.overlaps(
                right.indexes
            ):
                continue
            same_overlay_query = (
                left_root == right_root
                and left.id == right.id
                and left.indexes == right.indexes
            )
            if not same_overlay_query:
                yield _error(
                    "cpuid.query.index-overlap",
                    left.source,
                    f"query {left.id!r} overlaps {right.id!r} in "
                    f"class 0x{left_selector[0]:08x}, leaf 0x{left_selector[1]:04x}",
                    "queries",
                    related=(RelatedLocation(right.source, "conflicting query index"),),
                )
                continue
            for left_field in left.fields:
                for right_field in right.fields:
                    if left_field.overlaps(right_field):
                        yield _error(
                            "cpuid.field.overlay-overlap",
                            left_field.source,
                            f"field {left_field.id!r} overlaps "
                            f"{right_field.id!r} in a shared query",
                            "queries",
                            related=(
                                RelatedLocation(
                                    right_field.source,
                                    "conflicting result field",
                                ),
                            ),
                        )


def load_cpuid(
    isa_root: str | Path,
    *,
    extensions: DirectoryInventory,
) -> "CpuidCatalog":
    root = Path(isa_root).resolve()
    references = {
        "classes": {},
        "leaves": {},
        "layouts": {},
        "queries": {},
        "fields": {},
        "layout_fields": {},
        "common_headers": {},
        "common_header_fields": {},
    }
    common_header = _load_common_header(root, references)
    namespaces: dict[str, CpuidNamespace] = {}
    for owner, namespace_root in extension_owner_roots(extensions):
        namespace = _load_namespace(owner, namespace_root, root, references)
        namespaces[owner] = namespace
    return CpuidCatalog(
        namespaces=MappingProxyType(namespaces),
        common_header=common_header,
        classes=ReferenceIndex(references["classes"]),
        leaves=ReferenceIndex(references["leaves"]),
        layouts=ReferenceIndex(references["layouts"]),
        queries=ReferenceIndex(references["queries"]),
        fields=ReferenceIndex(references["fields"]),
        layout_fields=ReferenceIndex(references["layout_fields"]),
        common_headers=ReferenceIndex(references["common_headers"]),
        common_header_fields=ReferenceIndex(references["common_header_fields"]),
    )
