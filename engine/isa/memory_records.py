"""Owner-local byte-addressed memory-record definitions and lookup."""

from __future__ import annotations

from engine.source.inventory import inspect_inventory, require_exact

from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from types import MappingProxyType
from typing import Any

from engine.isa.extensions import extension_owner_roots, load_extension_inventory
from engine.entity import Entity
from engine.reference import Reference, ReferenceIndex, register_reference
from engine.source.inventory import DirectoryInventory
from engine.source.yaml import load_schema_yaml, load_yaml


class MemoryRecordError(ValueError):
    """A memory-record source violates the byte-layout contract."""


@dataclass(frozen=True, slots=True)
class MemoryRecordParameter:
    """One finite architectural byte-size parameter."""

    id: str
    description: str
    values: tuple[int, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", tuple(self.values))



@dataclass(frozen=True, slots=True)
class ElementByteSize:
    """A fixed byte size or one declared parameter divided by a constant."""

    fixed: int | None = None
    parameter: str | None = None
    divisor: int = 1

    @property
    def is_fixed(self) -> bool:
        return self.fixed is not None

    def evaluate(self, parameter_value: int | None = None) -> int:
        if self.fixed is not None:
            return self.fixed
        if parameter_value is None:
            raise ValueError("a symbolic element size requires a parameter value")
        quotient, remainder = divmod(parameter_value, self.divisor)
        if remainder:
            raise ValueError("the parameter value does not produce whole bytes")
        return quotient

    def expression(self) -> "LinearByteExpression":
        if self.fixed is not None:
            return LinearByteExpression(self.fixed)
        assert self.parameter is not None
        return LinearByteExpression(0, self.parameter, Fraction(1, self.divisor))


@dataclass(frozen=True, slots=True)
class LinearByteExpression:
    """A byte expression with a constant and at most one linear parameter."""

    constant: int = 0
    parameter: str | None = None
    coefficient: Fraction = Fraction(0)

    def __post_init__(self) -> None:
        if self.constant < 0 or self.coefficient < 0:
            raise ValueError("byte expressions cannot be negative")
        if self.coefficient and self.parameter is None:
            raise ValueError("a nonzero coefficient requires a parameter")
        if not self.coefficient and self.parameter is not None:
            object.__setattr__(self, "parameter", None)

    def __add__(self, other: "LinearByteExpression") -> "LinearByteExpression":
        if self.parameter and other.parameter and self.parameter != other.parameter:
            raise ValueError("a record cannot mix byte-size parameters")
        return LinearByteExpression(
            self.constant + other.constant,
            self.parameter or other.parameter,
            self.coefficient + other.coefficient,
        )

    def __mul__(self, count: int) -> "LinearByteExpression":
        if count < 0:
            raise ValueError("a byte-expression multiplier cannot be negative")
        return LinearByteExpression(
            self.constant * count,
            self.parameter,
            self.coefficient * count,
        )

    def evaluate(self, parameter_value: int | None = None) -> int:
        value = Fraction(self.constant)
        if self.coefficient:
            if parameter_value is None:
                raise ValueError(
                    "a symbolic byte expression requires a parameter value"
                )
            value += self.coefficient * parameter_value
        if value.denominator != 1:
            raise ValueError("a byte expression did not produce a whole byte count")
        return value.numerator


@dataclass(frozen=True, slots=True)
class MemoryRecordBitField:
    id: str
    label: str
    diagram_label: str | None
    lsb: int
    bits: int

    @property
    def msb(self) -> int:
        return self.lsb + self.bits - 1

    def overlaps(self, other: "MemoryRecordBitField") -> bool:
        return self.lsb <= other.msb and other.lsb <= self.msb


@dataclass(frozen=True, slots=True)
class MemoryRecordBitFormat:
    bits: int
    fields: tuple[MemoryRecordBitField, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", tuple(self.fields))
        identifiers = tuple(member.id for member in self.fields)
        duplicates = sorted({key for key in identifiers if identifiers.count(key) > 1})
        if duplicates:
            raise MemoryRecordError(f"duplicate bit-format field IDs {duplicates}")



@dataclass(frozen=True, slots=True)
class MemoryRecordComponent:
    id: str
    label: str
    count: int
    element_bytes: ElementByteSize
    fixed_value: str | None
    bit_format: MemoryRecordBitFormat | None


@dataclass(frozen=True, slots=True)
class MemoryRecord(Entity):
    """One contiguous byte-addressed record in architected component order."""

    reference: Reference["MemoryRecord"]
    source: Path
    root: Path
    owner: str
    id: str
    name: str
    alignment_bytes: int
    parameter: MemoryRecordParameter | None
    components: tuple[MemoryRecordComponent, ...]


    def __post_init__(self) -> None:
        object.__setattr__(self, "components", tuple(self.components))
        identifiers = tuple(member.id for member in self.components)
        duplicates = sorted({key for key in identifiers if identifiers.count(key) > 1})
        if duplicates:
            raise MemoryRecordError(f"{self.source}: duplicate component IDs {duplicates}")

    @property
    def payload_expression(self) -> LinearByteExpression:
        result = LinearByteExpression()
        for component in self.components:
            result += component.element_bytes.expression() * component.count
        return result

    @property
    def parameter_values(self) -> tuple[int | None, ...]:
        if self.parameter is None:
            return (None,)
        return self.parameter.values

    def payload_bytes(self, parameter_value: int | None = None) -> int:
        return self.payload_expression.evaluate(parameter_value)

    def total_bytes(self, parameter_value: int | None = None) -> int:
        payload = self.payload_bytes(parameter_value)
        alignment = self.alignment_bytes
        return ((payload + alignment - 1) // alignment) * alignment

    def padding_bytes(self, parameter_value: int | None = None) -> int:
        return self.total_bytes(parameter_value) - self.payload_bytes(parameter_value)


@dataclass(frozen=True, slots=True)
class MemoryRecordNamespace:
    owner: str
    root: Path
    inventory: DirectoryInventory | None
    records: Mapping[str, MemoryRecord]

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", MappingProxyType(dict(self.records)))
        inventory = self.inventory
        if inventory is None:
            if self.records:
                raise MemoryRecordError(f"{self.root}: memory records require an inventory")
        elif inventory.owner != self.owner or inventory.duplicates or inventory.missing or inventory.undeclared or set(self.records) != set(inventory.declared):
            raise MemoryRecordError(f"{inventory.source}: declared, actual and loaded memory records must agree")



@dataclass(frozen=True, slots=True)
class MemoryRecordCatalog:
    """The union of optional owner-local memory-record namespaces."""

    namespaces: Mapping[str, MemoryRecordNamespace]
    references: ReferenceIndex[MemoryRecord]



    def __post_init__(self) -> None:
        object.__setattr__(self, "namespaces", MappingProxyType(dict(self.namespaces)))
        members = {}
        for owner, namespace in self.namespaces.items():
            if owner != namespace.owner:
                raise MemoryRecordError("memory-record namespace key differs from its owner")
            for key, member in namespace.records.items():
                if key != member.id or member.owner != owner or member.reference != Reference(owner, ("records",), key):
                    raise MemoryRecordError(f"{member.source}: memory-record identity differs from its namespace")
                register_reference(members, member.reference, member)
        index = self.references if isinstance(self.references, ReferenceIndex) else ReferenceIndex(self.references)
        if set(index) != set(members) or any(index[reference] is not member for reference, member in members.items()):
            raise MemoryRecordError("memory-record index differs from its canonical namespaces")
        object.__setattr__(self, "references", index)

    def namespace(self, owner: str) -> MemoryRecordNamespace:
        try:
            return self.namespaces[owner]
        except KeyError as error:
            raise ValueError(f"unknown memory-record namespace {owner!r}") from error

    def resolve(self, value: str | Reference[MemoryRecord]) -> MemoryRecord:
        reference: Reference[MemoryRecord] = Reference.parse(value)
        if reference.path != ("records",):
            raise ValueError(
                "memory-record references must have the form <owner>.records.<id>"
            )
        return self.references.resolve(reference)


def _load_namespace(
    owner: str,
    owner_root: Path,
    schema: Mapping[str, object],
    references: dict[Reference[MemoryRecord], MemoryRecord],
) -> MemoryRecordNamespace:
    records_root = owner_root / "records"
    if not records_root.exists():
        return MemoryRecordNamespace(owner, owner_root, None, MappingProxyType({}))
    inventory = require_exact(inspect_inventory(owner=owner, kind="memory-record", source=records_root / "records.yaml", root=records_root, key="records", name_pattern=r"[A-Z][A-Z0-9_]*", exact_keys=True))
    if inventory.declared != tuple(sorted(inventory.declared)):
        raise MemoryRecordError(
            f"{inventory.source}: record membership must be sorted; "
            "reader order is declared by (:memory-record:...:) placement"
        )
    records: dict[str, MemoryRecord] = {}
    for record_id in inventory.declared:
        member_root = records_root / record_id
        member_files = tuple(
            sorted(
                path.name
                for path in member_root.iterdir()
                if not path.name.startswith(".")
            )
        )
        if member_files != ("record.yaml",):
            raise MemoryRecordError(
                f"{member_root}: memory-record member files must be exactly "
                "('record.yaml',)"
            )
        source = member_root / "record.yaml"
        raw = load_schema_yaml(source, schema)
        record = _decode_record(owner, record_id, member_root, source, raw)
        register_reference(references, record.reference, record)
        records[record_id] = record
    return MemoryRecordNamespace(
        owner, owner_root, inventory, MappingProxyType(records)
    )


def _decode_record(
    owner: str,
    directory_id: str,
    root: Path,
    source: Path,
    raw: Mapping[str, Any],
) -> MemoryRecord:
    alignment = raw["alignment_bytes"]
    if alignment & (alignment - 1):
        raise MemoryRecordError(f"{source}: alignment_bytes must be a power of two")
    parameter = _decode_parameter(raw.get("parameter"), source)
    components = tuple(
        _decode_component(component, parameter, source, index)
        for index, component in enumerate(raw["components"])
    )
    return MemoryRecord(
        reference=Reference(owner, ("records",), directory_id),
        source=source,
        root=root,
        owner=owner,
        id=directory_id,
        name=raw["name"],
        alignment_bytes=alignment,
        parameter=parameter,
        components=components,
    )


def _decode_parameter(
    raw: Mapping[str, Any] | None, source: Path
) -> MemoryRecordParameter | None:
    if raw is None:
        return None
    values = tuple(raw["values"])
    if values != tuple(sorted(values)):
        raise MemoryRecordError(
            f"{source}: parameter values must be strictly increasing"
        )
    return MemoryRecordParameter(raw["id"], raw["description"], values)


def _decode_component(
    raw: Mapping[str, Any],
    parameter: MemoryRecordParameter | None,
    source: Path,
    index: int,
) -> MemoryRecordComponent:
    where = f"components[{index}]"
    size = _decode_element_size(raw["element_bytes"], parameter, source, where)
    count = raw.get("count", 1)
    bit_format = _decode_bit_format(raw.get("bit_format"), size, count, source, where)
    return MemoryRecordComponent(
        id=raw["id"],
        label=raw["label"],
        count=count,
        element_bytes=size,
        fixed_value=raw.get("fixed_value"),
        bit_format=bit_format,
    )


def _decode_element_size(
    raw: int | Mapping[str, Any],
    parameter: MemoryRecordParameter | None,
    source: Path,
    where: str,
) -> ElementByteSize:
    if isinstance(raw, int):
        return ElementByteSize(fixed=raw)
    parameter_id = raw["parameter"]
    if parameter is None or parameter.id != parameter_id:
        raise MemoryRecordError(
            f"{source}: {where}.element_bytes names undeclared parameter "
            f"{parameter_id!r}"
        )
    divisor = raw.get("divisor", 1)
    if any(value % divisor for value in parameter.values):
        raise MemoryRecordError(
            f"{source}: {where}.element_bytes must produce whole bytes for "
            f"every {parameter.id} value"
        )
    return ElementByteSize(parameter=parameter.id, divisor=divisor)


def _decode_bit_format(
    raw: Mapping[str, Any] | None,
    size: ElementByteSize,
    count: int,
    source: Path,
    where: str,
) -> MemoryRecordBitFormat | None:
    if raw is None:
        return None
    if count != 1 or not size.is_fixed:
        raise MemoryRecordError(
            f"{source}: {where}.bit_format requires one fixed-size component"
        )
    assert size.fixed is not None
    if raw["bits"] != size.fixed * 8:
        raise MemoryRecordError(
            f"{source}: {where}.bit_format bits must equal the component size"
        )
    fields = tuple(
        MemoryRecordBitField(
            id=field["id"],
            label=field["label"],
            diagram_label=field.get("diagram_label"),
            lsb=field["lsb"],
            bits=field["bits"],
        )
        for field in raw["fields"]
    )
    for field in fields:
        if field.msb >= raw["bits"]:
            raise MemoryRecordError(
                f"{source}: {where}.bit_format field {field.id!r} is out of bounds"
            )
    for index, field in enumerate(fields):
        if any(field.overlaps(other) for other in fields[index + 1 :]):
            raise MemoryRecordError(f"{source}: {where}.bit_format fields overlap")
    try:
        return MemoryRecordBitFormat(raw["bits"], fields)
    except MemoryRecordError as error:
        raise MemoryRecordError(f"{source}: {where}.bit_format: {error}") from error


def load_memory_records(
    isa_root: str | Path,
    *,
    extensions: DirectoryInventory,
) -> "MemoryRecordCatalog":
    root = Path(isa_root).resolve()
    owner_roots = extension_owner_roots(extensions)
    schema = load_yaml(root / "schemas/memory-record.yaml")
    references = {}
    namespaces: dict[str, MemoryRecordNamespace] = {}
    for owner, owner_root in owner_roots:
        namespace = _load_namespace(owner, owner_root, schema, references)
        namespaces[owner] = namespace
    return MemoryRecordCatalog(
        namespaces=MappingProxyType(namespaces),
        references=ReferenceIndex(references),
    )
