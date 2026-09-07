"""Hierarchical typed project for the Bedrock C ABI."""

from __future__ import annotations

from engine.source.inventory import inspect_inventory, require_exact

from engine.entity import create_entity_catalog

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
import re
from types import MappingProxyType
from typing import TYPE_CHECKING, TypeVar, cast

from engine.entity import Entity, EntityCatalog, EntityDisplayStyle
from engine.entity import EntityDependency
from engine.source.inventory import DirectoryInventory
from engine.reference import (
    QualifiedReference,
    Reference,
    ReferenceIndex,
    register_reference,
)
from engine.source.yaml import load_schema_yaml

if TYPE_CHECKING:
    from engine.isa.catalog import InstructionBundle
    from engine.isa.registers import Register


_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class CType(Entity):
    reference: Reference["CType"]
    source: Path
    root: Path
    id: str
    spelling: str
    call_kind: str
    size_bits: int | str
    alignment_bytes: int
    representation: str | None

    def __post_init__(self) -> None:
        if isinstance(self.size_bits, int):
            if type(self.size_bits) is not int or self.size_bits < 8 or self.size_bits % 8:
                raise ValueError(
                    f"{self.source}: fixed C ABI type size must be a positive whole number of bytes"
                )
        elif not isinstance(self.size_bits, str) or not self.size_bits:
            raise ValueError(f"{self.source}: C ABI type size must be fixed bits or a symbolic expression")
        if type(self.alignment_bytes) is not int or self.alignment_bytes < 1:
            raise ValueError(f"{self.source}: C ABI type alignment must be a positive byte count")


@dataclass(frozen=True, slots=True)
class RegisterClass(Entity):
    reference: Reference["RegisterClass"]
    source: Path
    root: Path
    id: str
    arguments: tuple[QualifiedReference[Register], ...]
    results: tuple[QualifiedReference[Register], ...]
    assignment_order: str
    tuple_alignment: int
    exhaustion: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", tuple(self.arguments))
        object.__setattr__(self, "results", tuple(self.results))
        if self.assignment_order != "ascending":
            raise ValueError(f"{self.source}: C ABI register assignment must be ascending")
        if self.exhaustion not in {"permanent", "indirect"}:
            raise ValueError(f"{self.source}: unknown register exhaustion policy {self.exhaustion!r}")
        if type(self.tuple_alignment) is not int or self.tuple_alignment < 1:
            raise ValueError(f"{self.source}: tuple alignment must be a positive integer")
        for direction in ("arguments", "results"):
            references = getattr(self, direction)
            if not references or len(set(references)) != len(references):
                raise ValueError(f"{self.source}: {direction} must contain distinct registers")



@dataclass(frozen=True, slots=True)
class LocationPolicy:
    mode: str
    register_class: Reference["RegisterClass"]
    units: int
    alignment_units: int
    direct_maximum_bytes: int | None

    def __post_init__(self) -> None:
        if self.mode not in {"value", "copy_address", "size_dependent", "sret"}:
            raise ValueError(f"unknown C ABI location mode {self.mode!r}")
        if not isinstance(self.register_class, Reference):
            raise ValueError("C ABI location policy requires a register-class reference")
        for name in ("units", "alignment_units"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"C ABI location {name} must be a positive integer")
        if self.direct_maximum_bytes is not None and (
            type(self.direct_maximum_bytes) is not int or self.direct_maximum_bytes < 1
        ):
            raise ValueError("C ABI direct size threshold must be a positive integer")
        if self.mode == "size_dependent" and self.direct_maximum_bytes is None:
            raise ValueError("size-dependent C ABI location requires a direct size threshold")


@dataclass(frozen=True, slots=True)
class ValueClass(Entity):
    reference: Reference["ValueClass"]
    source: Path
    root: Path
    id: str
    kinds: tuple[str, ...]
    argument: LocationPolicy
    result: LocationPolicy

    def __post_init__(self) -> None:
        object.__setattr__(self, "kinds", tuple(self.kinds))
        if not self.kinds or len(set(self.kinds)) != len(self.kinds):
            raise ValueError(f"{self.source}: value class must declare distinct nonempty call kinds")



@dataclass(frozen=True, slots=True)
class Promotion(Entity):
    reference: Reference["Promotion"]
    source: Path
    root: Path
    id: str
    source_kinds: tuple[str, ...]
    target_kind: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_kinds", tuple(self.source_kinds))



@dataclass(frozen=True, slots=True)
class StackConvention:
    pointer: QualifiedReference[Register]
    growth: str
    entry_alignment_bytes: int
    first_argument_offset_bytes: int
    argument_slot_bytes: int
    sret_register: QualifiedReference[Register]
    red_zone_bytes: int

    def __post_init__(self) -> None:
        if self.growth != "descending":
            raise ValueError("C ABI stack must grow downward")
        for name, minimum in (
            ("entry_alignment_bytes", 1), ("first_argument_offset_bytes", 0),
            ("argument_slot_bytes", 1), ("red_zone_bytes", 0),
        ):
            value = getattr(self, name)
            if type(value) is not int or value < minimum:
                raise ValueError(f"C ABI stack {name} must be an integer >= {minimum}")


@dataclass(frozen=True, slots=True)
class PreservationSet:
    registers: tuple[QualifiedReference[Register], ...]
    disposition: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "registers", tuple(self.registers))



@dataclass(frozen=True, slots=True)
class CallingConvention:
    source: Path
    root: Path
    stack: StackConvention
    register_class_inventory: DirectoryInventory
    value_class_inventory: DirectoryInventory
    promotion_inventory: DirectoryInventory
    register_classes: tuple[Reference[RegisterClass], ...]
    value_classes: tuple[Reference[ValueClass], ...]
    promotions: tuple[Reference[Promotion], ...]
    preservation: tuple[PreservationSet, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "register_classes", tuple(self.register_classes))
        object.__setattr__(self, "value_classes", tuple(self.value_classes))
        object.__setattr__(self, "promotions", tuple(self.promotions))
        object.__setattr__(self, "preservation", tuple(self.preservation))
        for name in ("register_classes", "value_classes", "promotions"):
            references = getattr(self, name)
            if len(set(references)) != len(references):
                raise ValueError(f"{self.source}: duplicate {name} reference")



@dataclass(frozen=True, slots=True)
class RuntimeHelper(Entity):
    reference: Reference["RuntimeHelper"]
    source: Path
    root: Path
    id: str
    symbol: str
    result: Reference[CType]
    parameters: tuple[Reference[CType], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", tuple(self.parameters))



@dataclass(frozen=True, slots=True)
class MemoryOrderMapping(Entity):
    reference: Reference["MemoryOrderMapping"]
    source: Path
    root: Path
    id: str
    instruction_order: str
    load: tuple[str | QualifiedReference[InstructionBundle], ...] | None
    store: tuple[str | QualifiedReference[InstructionBundle], ...] | None
    thread_fence: tuple[str | QualifiedReference[InstructionBundle], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "load", (None if self.load is None else tuple(self.load)))
        object.__setattr__(self, "store", (None if self.store is None else tuple(self.store)))
        object.__setattr__(self, "thread_fence", tuple(self.thread_fence))



@dataclass(frozen=True, slots=True)
class AtomicLowering(Entity):
    reference: Reference["AtomicLowering"]
    source: Path
    root: Path
    id: str
    c_operations: tuple[str, ...]
    strategy: str
    instructions: tuple[QualifiedReference[InstructionBundle], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "c_operations", tuple(self.c_operations))
        object.__setattr__(self, "instructions", tuple(self.instructions))



@dataclass(frozen=True, slots=True)
class ResolvedRegisterClass:
    definition: RegisterClass
    arguments: tuple[Register, ...]
    results: tuple[Register, ...]

    def __post_init__(self) -> None:
        from engine.isa.registers import Register

        object.__setattr__(self, "arguments", tuple(self.arguments))
        object.__setattr__(self, "results", tuple(self.results))
        for direction in ("arguments", "results"):
            values = getattr(self, direction)
            references = getattr(self.definition, direction)
            if any(not isinstance(value, Register) for value in values):
                raise ValueError(f"{self.definition.source}: {direction} must resolve to registers")
            if tuple(QualifiedReference("isa", value.reference) for value in values) != references:
                raise ValueError(f"{self.definition.source}: resolved {direction} do not match their declared register order")



@dataclass(frozen=True, slots=True)
class ResolvedValueClass:
    definition: ValueClass
    argument_register_class: ResolvedRegisterClass
    result_register_class: ResolvedRegisterClass
    result_registers: tuple[Register, ...] = field(init=False)
    result_component_roles: Mapping[str, tuple[str, ...]] = field(init=False)

    def __post_init__(self) -> None:
        from engine.isa.registers import VariableRegisterWidth

        for direction, policy, register_class in (
            ("argument", self.definition.argument, self.argument_register_class),
            ("result", self.definition.result, self.result_register_class),
        ):
            if not isinstance(register_class, ResolvedRegisterClass) or register_class.definition.reference != policy.register_class:
                raise ValueError(
                    f"{self.definition.source}: resolved {direction} class does not match its policy reference"
                )
        if self.definition.argument.mode not in {"value", "copy_address"}:
            raise ValueError(f"{self.definition.source}: argument policy must pass a value or copy address")
        result_class = self.result_register_class
        policy = self.definition.result
        units = policy.units
        if units > len(result_class.results):
            raise ValueError(
                f"{self.definition.source}: result requires {units} registers but "
                f"{result_class.definition.id} provides {len(result_class.results)}"
            )
        result_registers = result_class.results[:units]
        if policy.mode == "size_dependent":
            if any(kind != "aggregate" for kind in self.definition.kinds):
                raise ValueError(f"{self.definition.source}: size-dependent result requires an aggregate size input")
            assert policy.direct_maximum_bytes is not None
            capacity = sum(
                (
                    register.width.minimum
                    if isinstance(register.width, VariableRegisterWidth)
                    else register.width
                )
                // 8
                for register in result_registers
            )
            if policy.direct_maximum_bytes > capacity:
                raise ValueError(
                    f"{self.definition.source}: direct aggregate result threshold "
                    f"{policy.direct_maximum_bytes} exceeds the selected result-register "
                    f"capacity of {capacity} bytes"
                )
        component_roles = {
            kind: (("real", "imaginary") if kind in {
                "complex_f32", "complex_f64", "complex_long_double"
            } else ())
            for kind in self.definition.kinds
        }
        for kind, roles in component_roles.items():
            if roles and (policy.mode != "value" or len(roles) != units):
                raise ValueError(
                    f"{self.definition.source}: {kind} requires one direct result register "
                    "for each of its real and imaginary components"
                )
        object.__setattr__(self, "result_registers", result_registers)
        object.__setattr__(self, "result_component_roles", MappingProxyType(component_roles))


@dataclass(frozen=True, slots=True)
class ResolvedCallingConvention:
    definition: CallingConvention
    stack_pointer: Register
    sret_register: Register
    register_classes: Mapping[Reference[RegisterClass], ResolvedRegisterClass]
    value_classes: Mapping[str, ResolvedValueClass]
    promotions: Mapping[str, str]


    def __post_init__(self) -> None:
        from engine.isa.registers import Register

        object.__setattr__(self, "register_classes", MappingProxyType(dict(self.register_classes)))
        object.__setattr__(self, "value_classes", MappingProxyType(dict(self.value_classes)))
        object.__setattr__(self, "promotions", MappingProxyType(dict(self.promotions)))
        if set(self.register_classes) != set(self.definition.register_classes):
            raise ValueError(f"{self.definition.source}: resolved register-class membership differs from the calling convention")
        for reference, register_class in self.register_classes.items():
            if reference != register_class.definition.reference:
                raise ValueError(f"{self.definition.source}: resolved register-class key differs from its definition")
        for name, reference in (("stack_pointer", self.definition.stack.pointer), ("sret_register", self.definition.stack.sret_register)):
            register = getattr(self, name)
            if not isinstance(register, Register) or QualifiedReference("isa", register.reference) != reference:
                raise ValueError(f"{self.definition.source}: {name} must resolve to its declared ISA register")
        canonical_registers = {}
        assigned_arguments = {}
        for register_class in self.register_classes.values():
            for register in register_class.arguments:
                if register.reference in assigned_arguments:
                    raise ValueError(f"{register_class.definition.source}: argument register {register.id} is assigned more than once")
                assigned_arguments[register.reference] = register_class
            for register in (*register_class.arguments, *register_class.results):
                previous = canonical_registers.setdefault(register.reference, register)
                if previous is not register:
                    raise ValueError(f"{register_class.definition.source}: register {register.id} has multiple canonical instances")
        for register in (self.stack_pointer, self.sret_register):
            if canonical_registers.setdefault(register.reference, register) is not register:
                raise ValueError(f"{self.definition.source}: register {register.id} has multiple canonical instances")
        sret_class = assigned_arguments.get(self.sret_register.reference)
        if sret_class is None or sret_class.arguments[0] is not self.sret_register:
            raise ValueError(f"{self.definition.source}: hidden result pointer must occupy the first argument register of its class")
        pointer_value = self.value_classes.get("pointer")
        if (
            pointer_value is None
            or pointer_value.definition.argument.mode != "value"
            or pointer_value.definition.argument.units != 1
            or pointer_value.argument_register_class is not sret_class
        ):
            raise ValueError(
                f"{self.definition.source}: hidden result pointer must use the canonical "
                "single-register object-pointer argument class"
            )
        value_definitions = {}
        for kind, value in self.value_classes.items():
            if kind not in value.definition.kinds:
                raise ValueError(f"{value.definition.source}: kind {kind!r} is not declared by its resolved value class")
            if value_definitions.setdefault(value.definition.reference, value) is not value:
                raise ValueError(f"{value.definition.source}: kind aliases must share one canonical resolved value class")
            if any(self.value_classes.get(alias) is not value for alias in value.definition.kinds):
                raise ValueError(f"{value.definition.source}: every declared kind must share its resolved value class")
            for register_class in (value.argument_register_class, value.result_register_class):
                if self.register_classes.get(register_class.definition.reference) is not register_class:
                    raise ValueError(f"{value.definition.source}: policy must use the canonical resolved register class")
        if set(value_definitions) != set(self.definition.value_classes):
            raise ValueError(f"{self.definition.source}: resolved value-class membership differs from the calling convention")
        for kind, target in self.promotions.items():
            if kind not in self.value_classes or target not in self.value_classes:
                raise ValueError(f"{self.definition.source}: promotion must connect declared call kinds")
        for value in value_definitions.values():
            if value.definition.result.mode in {"sret", "size_dependent"}:
                if not value.result_registers or value.result_registers[0] is not self.sret_register:
                    raise ValueError(
                        f"{value.definition.source}: indirect result must return "
                        "its address in the canonical sret register"
                    )
                if value.definition.result.mode == "sret" and len(value.result_registers) != 1:
                    raise ValueError(
                        f"{value.definition.source}: sret result must return exactly one address register"
                    )

    def value_class(self, kind: str) -> ResolvedValueClass:
        try:
            return self.value_classes[kind]
        except KeyError as error:
            raise ValueError(f"unsupported call kind {kind!r}") from error


@dataclass(frozen=True, slots=True)
class CAbiNamespace:
    owner: str
    root: Path
    type_inventory: DirectoryInventory
    register_class_inventory: DirectoryInventory
    value_class_inventory: DirectoryInventory
    promotion_inventory: DirectoryInventory
    runtime_helper_inventory: DirectoryInventory
    memory_order_inventory: DirectoryInventory
    atomic_lowering_inventory: DirectoryInventory
    types: Mapping[str, CType]
    register_classes: Mapping[str, RegisterClass]
    value_classes: Mapping[str, ValueClass]
    promotions: Mapping[str, Promotion]
    calling_convention: CallingConvention
    runtime_helpers: Mapping[str, RuntimeHelper]
    memory_orders: Mapping[str, MemoryOrderMapping]
    atomic_lowerings: Mapping[str, AtomicLowering]

    def __post_init__(self) -> None:
        for name, kind, member_type, inventory, filename in (
            ("types", "type", CType, self.type_inventory, "type.yaml"),
            ("register_classes", "register-class", RegisterClass, self.register_class_inventory, "register_class.yaml"),
            ("value_classes", "value-class", ValueClass, self.value_class_inventory, "value_class.yaml"),
            ("promotions", "promotion", Promotion, self.promotion_inventory, "promotion.yaml"),
            ("runtime_helpers", "runtime-helper", RuntimeHelper, self.runtime_helper_inventory, "runtime_helper.yaml"),
            ("memory_orders", "memory-order", MemoryOrderMapping, self.memory_order_inventory, "memory_order.yaml"),
            ("atomic_lowerings", "atomic-lowering", AtomicLowering, self.atomic_lowering_inventory, "lowering.yaml"),
        ):
            members = dict(getattr(self, name))
            if (inventory.owner != self.owner or inventory.kind != kind
                    or inventory.root != self.root / name
                    or inventory.source != inventory.root / f"{name}.yaml"):
                raise ValueError(f"{inventory.source}: inventory does not belong to its C ABI collection")
            if inventory.duplicates or inventory.missing or inventory.undeclared or set(members) != set(inventory.declared):
                raise ValueError(f"{inventory.source}: declared, actual and loaded {kind} membership must agree")
            for key, member in members.items():
                if not isinstance(key, str) or re.fullmatch(r"[A-Z][A-Z0-9_]*", key) is None:
                    raise ValueError(f"{inventory.source}: invalid {kind} name {key!r}")
                if not isinstance(member, member_type):
                    raise ValueError(f"{inventory.source}: {key!r} must be a {member_type.__name__}")
                if member.id != key or member.reference != Reference(self.owner, (name,), key):
                    raise ValueError(f"{member.source}: member identity does not match its {kind} collection key")
                if member.root != inventory.root / key or member.source != member.root / filename:
                    raise ValueError(f"{member.source}: member must belong to its declared {kind} directory")
            object.__setattr__(self, name, MappingProxyType(members))
        convention = self.calling_convention
        if convention.root != self.root or convention.source != self.root / "calling_convention.yaml":
            raise ValueError(f"{convention.source}: calling convention must belong to its C ABI namespace")
        for name, inventory_name in (
            ("register_classes", "register_class_inventory"),
            ("value_classes", "value_class_inventory"),
            ("promotions", "promotion_inventory"),
        ):
            inventory = getattr(self, inventory_name)
            if getattr(convention, inventory_name) is not inventory:
                raise ValueError(f"{convention.source}: {name} must use the namespace's canonical inventory")
            references = tuple(getattr(self, name)[key].reference for key in inventory.declared)
            if getattr(convention, name) != references:
                raise ValueError(f"{convention.source}: {name} must preserve declared collection membership and order")



@dataclass(frozen=True, slots=True)
class CAbiProject:
    root: Path
    namespaces: Mapping[str, CAbiNamespace]
    types: ReferenceIndex[CType]
    calling_convention: CallingConvention
    register_classes: ReferenceIndex[RegisterClass]
    value_classes: ReferenceIndex[ValueClass]
    promotions: ReferenceIndex[Promotion]
    runtime_helpers: ReferenceIndex[RuntimeHelper]
    memory_orders: ReferenceIndex[MemoryOrderMapping]
    atomic_lowerings: ReferenceIndex[AtomicLowering]
    entities: EntityCatalog



    def __post_init__(self) -> None:
        object.__setattr__(self, "namespaces", MappingProxyType(dict(self.namespaces)))
        for key, namespace in self.namespaces.items():
            if key != namespace.owner:
                raise ValueError(f"{namespace.root}: namespace owner does not match its index key")
        if sum(namespace.calling_convention is self.calling_convention for namespace in self.namespaces.values()) != 1:
            raise ValueError(f"{self.root}: project calling convention must be canonical in its owning namespace")
        entities = {}
        for name in ("types", "register_classes", "value_classes", "promotions", "runtime_helpers", "memory_orders", "atomic_lowerings"):
            supplied = getattr(self, name)
            index = supplied if isinstance(supplied, ReferenceIndex) else ReferenceIndex(supplied)
            expected = {}
            for namespace in self.namespaces.values():
                for member in getattr(namespace, name).values():
                    if member.reference in entities:
                        raise ValueError(f"{member.source}: duplicate C ABI entity reference {member.reference!r}")
                    expected[member.reference] = entities[member.reference] = member
            if set(index) != set(expected) or any(index[reference] is not member for reference, member in expected.items()):
                raise ValueError(f"{self.root}: {name} index must contain the canonical namespace members")
            object.__setattr__(self, name, index)
        if set(self.entities.references) != set(entities) or any(self.entities.resolve(reference) is not member for reference, member in entities.items()):
            raise ValueError(f"{self.root}: entity catalog must contain the canonical C ABI members")

    def resolve(self, reference: Reference[_T]) -> _T:
        return cast(
            _T,
            self.entities.resolve(cast(Reference[Entity], reference)),
        )

    def entity_dependencies(self) -> tuple[EntityDependency, ...]:
        """Return the C ABI relationships intentionally exposed to tooling."""

        result: list[EntityDependency] = []

        def add(
            source: Reference[object],
            target: QualifiedReference[object],
            kind: str,
        ) -> None:
            result.append(EntityDependency(source, target, kind))

        def local(reference: Reference[object]) -> QualifiedReference[object]:
            return QualifiedReference("abi.c", reference)

        for definition in self.register_classes.values():
            source = cast(Reference[object], definition.reference)
            for target in (*definition.arguments, *definition.results):
                add(
                    source,
                    cast(QualifiedReference[object], target),
                    "register-class-register",
                )
        for definition in self.value_classes.values():
            source = cast(Reference[object], definition.reference)
            for policy in (definition.argument, definition.result):
                add(
                    source,
                    local(cast(Reference[object], policy.register_class)),
                    "value-class-register-class",
                )
        for definition in self.runtime_helpers.values():
            source = cast(Reference[object], definition.reference)
            for target in (definition.result, *definition.parameters):
                add(
                    source,
                    local(cast(Reference[object], target)),
                    "runtime-helper-type",
                )
        for definition in self.memory_orders.values():
            source = cast(Reference[object], definition.reference)
            sequences = (
                definition.load or (),
                definition.store or (),
                definition.thread_fence,
            )
            for sequence in sequences:
                for target in sequence:
                    if isinstance(target, QualifiedReference):
                        add(
                            source,
                            cast(QualifiedReference[object], target),
                            "memory-order-instruction",
                        )
        for definition in self.atomic_lowerings.values():
            source = cast(Reference[object], definition.reference)
            for target in definition.instructions:
                add(
                    source,
                    cast(QualifiedReference[object], target),
                    "atomic-lowering-instruction",
                )
        return tuple(result)






def _load_namespace(owner: str, root: Path) -> CAbiNamespace:
    schemas = root / "schemas"
    type_inventory = _inventory(owner, root, "type", "types")
    register_inventory = _inventory(owner, root, "register-class", "register_classes")
    value_inventory = _inventory(owner, root, "value-class", "value_classes")
    promotion_inventory = _inventory(owner, root, "promotion", "promotions")
    helper_inventory = _inventory(owner, root, "runtime-helper", "runtime_helpers")
    order_inventory = _inventory(owner, root, "memory-order", "memory_orders")
    atomic_inventory = _inventory(owner, root, "atomic-lowering", "atomic_lowerings")

    register_classes = {}
    value_classes = {}
    promotions = {}
    types: dict[str, CType] = {}
    for entity_id in type_inventory.declared:
        entity_root = type_inventory.root / entity_id
        source = entity_root / "type.yaml"
        raw = load_schema_yaml(source, schemas / "type.yaml")
        c_type_reference: Reference[CType] = Reference(owner, ("types",), entity_id)
        c_type = CType(
            c_type_reference,
            source,
            entity_root,
            entity_id,
            raw["spelling"],
            raw["call_kind"],
            raw["size_bits"],
            raw["alignment_bytes"],
            raw.get("representation"),
        )
        types[entity_id] = c_type

    for child_id in register_inventory.declared:
        child_root = register_inventory.root / child_id
        child_source = child_root / "register_class.yaml"
        child = load_schema_yaml(child_source, schemas / "register-class.yaml")
        register_class_reference: Reference[RegisterClass] = Reference(
            owner, ("register_classes",), child_id
        )
        register_reference(
            register_classes,
            register_class_reference,
            RegisterClass(
                register_class_reference,
                child_source,
                child_root,
                child_id,
                tuple((QualifiedReference.parse(item) for item in child["arguments"])),
                tuple((QualifiedReference.parse(item) for item in child["results"])),
                child["assignment_order"],
                child.get("tuple_alignment", 1),
                child["exhaustion"],
            ),
        )
    for child_id in value_inventory.declared:
        child_root = value_inventory.root / child_id
        child_source = child_root / "value_class.yaml"
        child = load_schema_yaml(child_source, schemas / "value-class.yaml")
        value_class_reference: Reference[ValueClass] = Reference(
            owner, ("value_classes",), child_id
        )
        register_reference(
            value_classes,
            value_class_reference,
            ValueClass(
                value_class_reference,
                child_source,
                child_root,
                child_id,
                tuple(child["kinds"]),
                _location_policy(child["argument"]),
                _location_policy(child["result"]),
            ),
        )
    for child_id in promotion_inventory.declared:
        child_root = promotion_inventory.root / child_id
        child_source = child_root / "promotion.yaml"
        child = load_schema_yaml(child_source, schemas / "promotion.yaml")
        promotion_reference: Reference[Promotion] = Reference(
            owner, ("promotions",), child_id
        )
        register_reference(
            promotions,
            promotion_reference,
            Promotion(
                promotion_reference,
                child_source,
                child_root,
                child_id,
                tuple(child["source_kinds"]),
                child["target_kind"],
            ),
        )

    source = root / "calling_convention.yaml"
    raw = load_schema_yaml(source, schemas / "calling-convention.yaml")
    stack = raw["stack"]
    convention = CallingConvention(
        source,
        root,
        StackConvention(
            QualifiedReference.parse(stack["pointer"]),
            stack["growth"],
            stack["entry_alignment_bytes"],
            stack["first_argument_offset_bytes"],
            stack["argument_slot_bytes"],
            QualifiedReference.parse(stack["sret_register"]),
            stack.get("red_zone_bytes", 0),
        ),
        register_inventory,
        value_inventory,
        promotion_inventory,
        tuple(
            Reference(owner, ("register_classes",), item)
            for item in register_inventory.declared
        ),
        tuple(
            Reference(owner, ("value_classes",), item)
            for item in value_inventory.declared
        ),
        tuple(
            Reference(owner, ("promotions",), item)
            for item in promotion_inventory.declared
        ),
        tuple(
            PreservationSet(
                tuple(
                    QualifiedReference.parse(register) for register in item["registers"]
                ),
                item["disposition"],
            )
            for item in raw["preservation"]
        ),
    )

    helpers: dict[str, RuntimeHelper] = {}
    for entity_id in helper_inventory.declared:
        entity_root = helper_inventory.root / entity_id
        source = entity_root / "runtime_helper.yaml"
        raw = load_schema_yaml(source, schemas / "runtime-helper.yaml")
        helper_reference: Reference[RuntimeHelper] = Reference(
            owner, ("runtime_helpers",), entity_id
        )
        helper = RuntimeHelper(
            helper_reference,
            source,
            entity_root,
            entity_id,
            raw["symbol"],
            Reference.parse(cast(str, raw["result"])),
            tuple(
                Reference.parse(cast(str, parameter))
                for parameter in cast(list[object], raw["parameters"])
            ),
        )
        helpers[entity_id] = helper

    orders: dict[str, MemoryOrderMapping] = {}
    for entity_id in order_inventory.declared:
        entity_root = order_inventory.root / entity_id
        source = entity_root / "memory_order.yaml"
        raw = load_schema_yaml(source, schemas / "memory-order.yaml")
        memory_order_reference: Reference[MemoryOrderMapping] = Reference(
            owner, ("memory_orders",), entity_id
        )
        memory_order = MemoryOrderMapping(
            memory_order_reference,
            source,
            entity_root,
            entity_id,
            str(raw["instruction_order"]),
            _memory_order_sequence(raw.get("load")),
            _memory_order_sequence(raw.get("store")),
            _memory_order_sequence(raw["thread_fence"]) or (),
        )
        orders[entity_id] = memory_order

    atomic_lowerings: dict[str, AtomicLowering] = {}
    for entity_id in atomic_inventory.declared:
        entity_root = atomic_inventory.root / entity_id
        source = entity_root / "lowering.yaml"
        raw = load_schema_yaml(source, schemas / "atomic-lowering.yaml")
        atomic_lowering_reference: Reference[AtomicLowering] = Reference(
            owner, ("atomic_lowerings",), entity_id
        )
        atomic_lowering = AtomicLowering(
            atomic_lowering_reference,
            source,
            entity_root,
            entity_id,
            tuple(raw["c_operations"]),
            str(raw["strategy"]),
            tuple(
                QualifiedReference.parse(cast(str, instruction))
                for instruction in cast(list[object], raw["instructions"])
            ),
        )
        atomic_lowerings[entity_id] = atomic_lowering

    return CAbiNamespace(
        owner,
        root,
        type_inventory,
        register_inventory,
        value_inventory,
        promotion_inventory,
        helper_inventory,
        order_inventory,
        atomic_inventory,
        MappingProxyType(types),
        MappingProxyType({item.id: item for item in register_classes.values()}),
        MappingProxyType({item.id: item for item in value_classes.values()}),
        MappingProxyType({item.id: item for item in promotions.values()}),
        convention,
        MappingProxyType(helpers),
        MappingProxyType(orders),
        MappingProxyType(atomic_lowerings),
    )


def _inventory(owner: str, root: Path, kind: str, plural: str) -> DirectoryInventory:
    inventory = require_exact(inspect_inventory(owner=owner, kind=kind, source=root / plural / f"{plural}.yaml", root=root / plural, key=plural, exact_keys=True, name_pattern=r"[A-Za-z][A-Za-z0-9_-]*"))
    invalid = tuple(
        entity_id
        for entity_id in inventory.actual
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", entity_id) is None
    )
    if invalid:
        raise ValueError(
            f"{inventory.source}: invalid {kind} directory names {invalid}"
        )
    return inventory


def _location_policy(raw: Mapping[str, object]) -> LocationPolicy:
    return LocationPolicy(
        str(raw["mode"]),
        Reference.parse(cast(str, raw["register_class"])),
        int(cast(int | str, raw["units"])),
        int(cast(int | str, raw.get("alignment_units", 1))),
        (
            int(cast(int | str, raw["direct_maximum_bytes"]))
            if "direct_maximum_bytes" in raw
            else None
        ),
    )


def _memory_order_sequence(
    raw: object,
) -> tuple[str | QualifiedReference[InstructionBundle], ...] | None:
    if raw is None:
        return None
    return tuple(
        item if item == "access" else QualifiedReference.parse(item)
        for item in cast(list[str], raw)
    )


def _build_entities(namespace: CAbiNamespace) -> EntityCatalog:
    entries = []
    for members in (namespace.types, namespace.register_classes, namespace.value_classes, namespace.promotions, namespace.runtime_helpers, namespace.memory_orders, namespace.atomic_lowerings):
        for value in members.values():
            display = value.symbol if isinstance(value, RuntimeHelper) else value.id
            entries.append((value, display, EntityDisplayStyle.CODE))
    return create_entity_catalog(entries)


def load_c_abi(root: str | Path, isa) -> CAbiProject:
    domain_root = Path(root).resolve()
    base = _load_namespace("base", domain_root)
    project = CAbiProject(
        domain_root, MappingProxyType({"base": base}),
        ReferenceIndex({item.reference: item for item in base.types.values()}),
        base.calling_convention,
        ReferenceIndex({item.reference: item for item in base.register_classes.values()}),
        ReferenceIndex({item.reference: item for item in base.value_classes.values()}),
        ReferenceIndex({item.reference: item for item in base.promotions.values()}),
        ReferenceIndex({item.reference: item for item in base.runtime_helpers.values()}),
        ReferenceIndex({item.reference: item for item in base.memory_orders.values()}),
        ReferenceIndex({item.reference: item for item in base.atomic_lowerings.values()}),
        _build_entities(base),
    )
    check_c_abi(project, isa)
    return project


def _isa_target(isa, reference):
    if reference.domain != "isa":
        raise ValueError(f"C ABI reference must name an ISA entity: {reference!r}")
    return isa.resolve(reference.local)


def _register_target(registers, reference):
    if reference.domain != "isa":
        raise ValueError(f"C ABI register must belong to ISA: {reference!r}")
    return registers.registers.resolve(reference.local)


def check_c_abi(project, isa):
    """Resolve every local and cross-domain relationship."""
    rules = resolve_calling_convention(project, isa.registers)
    pointer_types = tuple(
        c_type for c_type in project.types.values() if c_type.call_kind == "pointer"
    )
    if not pointer_types or any(
        not isinstance(c_type.size_bits, int) for c_type in pointer_types
    ):
        raise ValueError(
            f"{project.root}: hidden result pointer requires a fixed object-pointer size"
        )
    sret_width = rules.sret_register.width
    from engine.isa.registers import VariableRegisterWidth

    minimum_sret_bits = (
        sret_width.minimum if isinstance(sret_width, VariableRegisterWidth) else sret_width
    )
    required_pointer_bits = max(cast(int, c_type.size_bits) for c_type in pointer_types)
    if minimum_sret_bits < required_pointer_bits:
        raise ValueError(
            f"{project.calling_convention.source}: hidden result register provides "
            f"{minimum_sret_bits} bits for a {required_pointer_bits}-bit object pointer"
        )
    for c_type in project.types.values():
        if c_type.call_kind not in rules.value_classes:
            raise ValueError(
                f"{c_type.source}: call kind {c_type.call_kind!r} has no value class"
            )
    for helper in project.runtime_helpers.values():
        project.types.resolve(helper.result)
        for parameter in helper.parameters:
            project.types.resolve(parameter)
    for mapping in project.memory_orders.values():
        for sequence in (mapping.load, mapping.store, mapping.thread_fence):
            for operation in sequence or ():
                if isinstance(operation, QualifiedReference):
                    _isa_target(isa, operation)
    operation_owners: dict[str, AtomicLowering] = {}
    for lowering in project.atomic_lowerings.values():
        for operation in lowering.c_operations:
            operation_owner = operation_owners.get(operation)
            if operation_owner is not None:
                raise ValueError(
                    f"{lowering.source}: C operation {operation!r} is also "
                    f"lowered by {operation_owner.id}"
                )
            operation_owners[operation] = lowering
        for instruction in lowering.instructions:
            _isa_target(isa, instruction)


def resolve_calling_convention(project, registers):
    convention = project.calling_convention
    resolved_registers: dict[Reference[RegisterClass], ResolvedRegisterClass] = {}
    for register_class_reference in convention.register_classes:
        register_class = project.register_classes.resolve(register_class_reference)
        resolved_registers[register_class_reference] = ResolvedRegisterClass(
            register_class,
            tuple(
                _register_target(registers, register) for register in register_class.arguments
            ),
            tuple(
                _register_target(registers, register) for register in register_class.results
            ),
        )
    resolved_values: dict[str, ResolvedValueClass] = {}
    for value_class_reference in convention.value_classes:
        value_class = project.value_classes.resolve(value_class_reference)
        try:
            argument_class = resolved_registers[value_class.argument.register_class]
            result_class = resolved_registers[value_class.result.register_class]
        except KeyError as error:
            raise ValueError(
                f"{value_class.source}: policy register class {error.args[0]!r} "
                "is not part of the calling convention"
            ) from error
        resolved_value_class = ResolvedValueClass(
            value_class, argument_class, result_class,
        )
        for kind in value_class.kinds:
            if kind in resolved_values:
                previous = resolved_values[kind].definition
                raise ValueError(
                    f"{value_class.source}: call kind {kind!r} is also classified by {previous.id}"
                )
            resolved_values[kind] = resolved_value_class
    promotions: dict[str, str] = {}
    for promotion_reference in convention.promotions:
        promotion = project.promotions.resolve(promotion_reference)
        if promotion.target_kind not in resolved_values:
            raise ValueError(
                f"{promotion.source}: target kind {promotion.target_kind!r} has no value class"
            )
        for kind in promotion.source_kinds:
            if kind not in resolved_values:
                raise ValueError(f"{promotion.source}: source kind {kind!r} has no value class")
            if kind in promotions:
                raise ValueError(f"{promotion.source}: source kind {kind!r} is promoted more than once")
            promotions[kind] = promotion.target_kind
    preserved = {}
    for preservation in convention.preservation:
        for reference in preservation.registers:
            register = _register_target(registers, reference)
            if reference in preserved:
                raise ValueError(
                    f"{convention.source}: register {register.id} has both "
                    f"{preserved[reference]!r} and {preservation.disposition!r} dispositions"
                )
            preserved[reference] = preservation.disposition
    return ResolvedCallingConvention(
        convention,
        _register_target(registers, convention.stack.pointer),
        _register_target(registers, convention.stack.sret_register),
        MappingProxyType(resolved_registers),
        MappingProxyType(resolved_values),
        MappingProxyType(promotions),
    )
