"""Distributed architectural control-register definitions and lookup."""

from __future__ import annotations

from engine.source.inventory import inspect_inventory

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from engine.diagnostics import Diagnostic
from engine.entity import Entity
from engine.isa.extensions import extension_owner_roots, load_extension_inventory
from engine.isa.registers import (
    decode_reset,
    load_register_layout,
    ConstantReset,
    LifecycleReset,
    RegisterField,
    RegisterLayout,
    ResetSpec,
    SourcedReset,
)
from engine.reference import Reference, ReferenceIndex, register_reference
from engine.source.inventory import DirectoryInventory
from engine.source.yaml import load_schema_yaml, load_yaml


@dataclass(frozen=True, slots=True)
class ControlRegister(Entity):
    """One register in the architectural RDCR/WRCR selector space."""

    reference: Reference["ControlRegister"]
    source: Path
    root: Path
    owner: str
    id: str
    selector: int
    summary: str
    reset: ResetSpec | None
    layout: RegisterLayout | None
    semantics: Path

    @property
    def width(self) -> int:
        return 64


@dataclass(frozen=True, slots=True)
class ControlRegisterNamespace(Entity):
    """Control registers owned by base or one declared extension."""

    reference: Reference["ControlRegisterNamespace"]
    owner: str
    root: Path
    inventory: DirectoryInventory
    generated_state_registers: tuple[str, ...]
    reset: ResetSpec | None
    registers: Mapping[str, ControlRegister]


    def __post_init__(self) -> None:
        object.__setattr__(self, "generated_state_registers", tuple(self.generated_state_registers))
        object.__setattr__(self, "registers", MappingProxyType(dict(self.registers)))

    @property
    def id(self) -> str:
        return "control_registers"

    @property
    def source(self) -> Path:
        return self.inventory.source


@dataclass(frozen=True, slots=True)
class ControlRegisterCatalog:
    """The distributed global control-register selector registry."""

    namespaces: Mapping[str, ControlRegisterNamespace]
    namespaces_by_reference: ReferenceIndex[ControlRegisterNamespace]
    registers: ReferenceIndex[ControlRegister]
    fields: ReferenceIndex[RegisterField]



    def __post_init__(self) -> None:
        namespaces = dict(self.namespaces)
        namespace_members: dict[Reference[ControlRegisterNamespace], ControlRegisterNamespace] = {}
        registers: dict[Reference[ControlRegister], ControlRegister] = {}
        fields: dict[Reference[RegisterField], RegisterField] = {}
        selectors: dict[int, ControlRegister] = {}
        for owner, namespace in namespaces.items():
            if owner != namespace.owner or namespace.reference != Reference(owner, (), "control_registers"):
                raise ValueError("control-register namespace key differs from its owner")
            register_reference(namespace_members, namespace.reference, namespace)
            for register_id, register in namespace.registers.items():
                if register_id != register.id or register.owner != owner or register.reference != Reference(owner, ("control_registers",), register_id):
                    raise ValueError(f"{register.source}: control-register identity differs from its namespace")
                register_reference(registers, register.reference, register)
                previous = selectors.get(register.selector)
                if previous is not None:
                    raise ValueError(
                        f"{register.source}: control-register selector "
                        f"0x{register.selector:04x} is also defined by {previous.source}"
                    )
                selectors[register.selector] = register
                if register.layout is not None:
                    for field in register.layout.fields:
                        if field.reference != Reference(owner, ("control_registers", register_id), field.id):
                            raise ValueError(f"{field.source}: control-register field identity differs from its owning layout")
                        register_reference(fields, field.reference, field)
        for name, expected in (("namespaces_by_reference", namespace_members), ("registers", registers), ("fields", fields)):
            supplied = getattr(self, name)
            index = supplied if isinstance(supplied, ReferenceIndex) else ReferenceIndex(supplied)
            if set(index) != set(expected) or any(index[reference] is not value for reference, value in expected.items()):
                raise ValueError(f"control-register {name} index differs from its canonical tree")
            object.__setattr__(self, name, index)
        object.__setattr__(self, "namespaces", MappingProxyType(namespaces))

    @property
    def base(self) -> ControlRegisterNamespace:
        return self.namespaces["base"]

    def namespace(self, owner: str) -> ControlRegisterNamespace:
        try:
            return self.namespaces[owner]
        except KeyError as error:
            raise ValueError(f"unknown control-register namespace {owner!r}") from error

    def selected(
        self, owners: set[str] | frozenset[str]
    ) -> tuple[ControlRegister, ...]:
        return tuple(
            register
            for owner, namespace in self.namespaces.items()
            if owner in owners
            for register in namespace.registers.values()
        )


def _load_namespace(
    owner: str,
    namespace_root: Path,
    isa_root: Path,
    references: dict,
) -> ControlRegisterNamespace:
    definitions_root = namespace_root / "control_registers/definitions"
    inventory, generated_state_registers, reset = _load_inventory(
        owner, definitions_root
    )
    registers: dict[str, ControlRegister] = {}
    for register_id in inventory.declared:
        register_root = definitions_root / register_id
        if register_id in registers or not register_root.is_dir():
            continue
        register = _load_register(
            owner,
            register_root,
            isa_root,
            reset,
        )
        register_reference(references["registers"], register.reference, register)
        if register.layout is not None:
            for field in register.layout.fields:
                register_reference(references["fields"], field.reference, field)
        registers[register_id] = register
    return ControlRegisterNamespace(
        Reference(owner, (), "control_registers"),
        owner,
        namespace_root,
        inventory,
        generated_state_registers,
        reset,
        MappingProxyType(registers),
    )


def _load_inventory(
    owner: str, root: Path
) -> tuple[DirectoryInventory, tuple[str, ...], ResetSpec | None]:
    source = root / "control_registers.yaml"
    membership = inspect_inventory(
        owner=owner,
        kind="control-register",
        source=source,
        root=root,
        key="control_registers",
        allow_missing=True,
        name_pattern=r"[A-Z][A-Z0-9_]*",
    )
    if not source.is_file():
        return membership, (), None
    raw = load_yaml(source)
    values = membership.declared
    generated_state_registers = raw.get("generated_state_registers", list(values))
    if not isinstance(generated_state_registers, list) or any(
        not isinstance(value, str) for value in generated_state_registers
    ):
        raise ValueError(
            f"{source}: expected a generated_state_registers list of strings"
        )
    unknown_state_registers = tuple(
        register_id
        for register_id in generated_state_registers
        if register_id not in values
    )
    if unknown_state_registers:
        raise ValueError(
            f"{source}: generated-state control registers are not declared members: "
            f"{unknown_state_registers}"
        )
    duplicate_state_registers = tuple(
        dict.fromkeys(
            register_id
            for register_id in generated_state_registers
            if generated_state_registers.count(register_id) > 1
        )
    )
    if duplicate_state_registers:
        raise ValueError(
            f"{source}: generated-state control registers are listed more than once: "
            f"{duplicate_state_registers}"
        )
    return membership, tuple(generated_state_registers), decode_reset(raw.get("reset"))


def _load_register(
    owner: str,
    root: Path,
    isa_root: Path,
    default_reset: ResetSpec | None,
) -> ControlRegister:
    source = root / "register.yaml"
    raw = load_schema_yaml(source, isa_root / "schemas/control-register.yaml")
    register_id = root.name
    reference: Reference[ControlRegister] = Reference(
        owner, ("control_registers",), register_id
    )
    layout = load_register_layout(root / "layout.yaml", isa_root / "schemas/register-layout.yaml", reference)
    if layout is not None and layout.bits != 64:
        raise ValueError(f"{layout.source}: control-register layout must be 64 bits")
    return ControlRegister(
        reference,
        source,
        root,
        owner,
        register_id,
        raw["selector"],
        raw["summary"],
        decode_reset(raw["reset"]) if "reset" in raw else default_reset,
        layout,
        root / "semantics.sail",
    )








def check_control_registers(catalog: ControlRegisterCatalog) -> Iterator[Diagnostic]:
    from engine.diagnostics import _error

    for namespace in catalog.namespaces.values():
        inventory = namespace.inventory
        for missing in inventory.missing:
            yield _error(
                "control-register.missing-directory",
                inventory.source,
                f"declared control register {missing!r} has no directory",
            )
        for undeclared in inventory.undeclared:
            yield _error(
                "control-register.undeclared-directory",
                inventory.root / undeclared,
                f"control-register directory {undeclared!r} is not declared",
            )
        for duplicate in inventory.duplicates:
            yield _error(
                "control-register.duplicate",
                inventory.source,
                f"control register {duplicate!r} is listed more than once",
            )
    for register in catalog.registers.values():
        yield from _control_registers_validate_register(register)


def _control_registers_validate_register(
    register: ControlRegister,
) -> Iterator[Diagnostic]:
    from engine.diagnostics import _error
    from engine.isa.registers import ConstantReset

    if not register.semantics.is_file():
        yield _error(
            "control-register.semantics.missing",
            register.semantics,
            f"control register {register.id!r} has no required semantics artifact",
        )
    reset = register.reset
    if isinstance(reset, ConstantReset) and reset.value >= 1 << 64:
        yield _error(
            "control-register.reset.range",
            register.source,
            f"reset value does not fit control register {register.id!r}",
            "reset",
        )
    layout = register.layout
    if layout is None:
        return
    for field in layout.fields:
        if field.msb >= 64:
            yield _error(
                "control-register.layout.field-range",
                layout.source,
                f"field {field.id!r} occupies bits {field.msb}..{field.lsb} "
                "outside a 64-bit control register",
                "fields",
            )
    for index, left in enumerate(layout.fields):
        for right in layout.fields[index + 1 :]:
            if left.overlaps(right):
                yield _error(
                    "control-register.layout.field-overlap",
                    layout.source,
                    f"fields {left.id!r} and {right.id!r} overlap",
                    "fields",
                )


def load_control_registers(
    isa_root: str | Path,
    *,
    extensions: DirectoryInventory,
) -> "ControlRegisterCatalog":
    root = Path(isa_root).resolve()
    references = {"namespaces_by_reference": {}, "registers": {}, "fields": {}}
    namespaces: dict[str, ControlRegisterNamespace] = {}
    for owner, namespace_root in extension_owner_roots(extensions):
        namespace = _load_namespace(owner, namespace_root, root, references)
        register_reference(
            references["namespaces_by_reference"], namespace.reference, namespace
        )
        namespaces[owner] = namespace
    return ControlRegisterCatalog(
        namespaces=MappingProxyType(namespaces),
        namespaces_by_reference=ReferenceIndex(references["namespaces_by_reference"]),
        registers=ReferenceIndex(references["registers"]),
        fields=ReferenceIndex(references["fields"]),
    )


def control_register_selector_members(namespace: ControlRegisterNamespace) -> tuple[tuple[ControlRegister, int], ...]:
    return tuple(sorted(((register, register.selector) for register in namespace.registers.values()), key=lambda item: item[1]))
