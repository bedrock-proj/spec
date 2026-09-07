"""Values and operations owned by this specification boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from engine.isa.control_registers import ControlRegisterCatalog
from engine.isa.cpuid import CpuidCatalog
from engine.isa.events import EventCatalog
from engine.isa.registers import ConstantReset
from engine.isa.registers import RegisterCatalog
from engine.isa.registers import RegisterWidth
from types import MappingProxyType
from engine.isa.event_structures import ResolvedEventFrame, ResolvedEventFrameLayout


def _mask(lsb: int, bits: int) -> int:
    return ((1 << bits) - 1) << lsb


@dataclass(frozen=True)
class CpuidFieldProjection:
    id: str
    lsb: int
    bits: int
    mask: int


@dataclass(frozen=True)
class CpuidQueryProjection:
    owner: str
    class_id: str
    class_value: int
    leaf_id: str
    leaf_value: int
    query_id: str
    first_index: int
    last_index: int
    stride: int
    fields: tuple[CpuidFieldProjection, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", tuple(self.fields))



@dataclass(frozen=True)
class FixedEventRouteProjection:
    owner: str
    event_id: str
    code: int
    frame: ResolvedEventFrameLayout
    payloads: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "payloads", tuple(self.payloads))



@dataclass(frozen=True)
class DynamicEventRouteProjection:
    class_value: int
    frame: ResolvedEventFrameLayout


@dataclass(frozen=True)
class EventCodecProjection:
    frame: ResolvedEventFrame
    payloads: frozenset[str]
    fixed_routes: tuple[FixedEventRouteProjection, ...]
    dynamic_routes: tuple[DynamicEventRouteProjection, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fixed_routes", tuple(self.fixed_routes))
        object.__setattr__(self, "dynamic_routes", tuple(self.dynamic_routes))



@dataclass(frozen=True)
class RegisterFieldProjection:
    id: str
    lsb: int
    mask: int


@dataclass(frozen=True)
class RegisterContractProjection:
    register_id: str
    encoding: int
    width: RegisterWidth
    writable_mask: int
    reset_value: int | None
    fields: tuple[RegisterFieldProjection, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", tuple(self.fields))



@dataclass(frozen=True)
class RegisterContractsProjection:
    groups: Mapping[tuple[str, str], tuple[RegisterContractProjection, ...]]
    control_registers: Mapping[str, tuple[RegisterContractProjection, ...]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "groups", MappingProxyType({key0: tuple(value0) for key0, value0 in self.groups.items()}))
        object.__setattr__(self, "control_registers", MappingProxyType({key0: tuple(value0) for key0, value0 in self.control_registers.items()}))



@dataclass(frozen=True)
class VectorGeometryProjection:
    vector_register_count: int
    predicate_register_count: int


def cpuid_project(catalog: CpuidCatalog) -> tuple[CpuidQueryProjection, ...]:
    queries = []
    for owner, namespace in catalog.namespaces.items():
        for cpuid_class in namespace.classes.values():
            for leaf in cpuid_class.leaves.values():
                resolved = catalog.resolve_leaf(leaf)
                for query in leaf.queries:
                    queries.append(
                        CpuidQueryProjection(
                            owner=owner,
                            class_id=cpuid_class.id,
                            class_value=resolved.class_value,
                            leaf_id=leaf.id,
                            leaf_value=resolved.leaf_value,
                            query_id=query.id,
                            first_index=query.indexes.first,
                            last_index=query.indexes.last,
                            stride=query.indexes.stride,
                            fields=tuple(
                                (
                                    CpuidFieldProjection(
                                        field.id,
                                        field.lsb,
                                        field.bits,
                                        _mask(field.lsb, field.bits),
                                    )
                                    for field in query.fields
                                )
                            ),
                        )
                    )
    return tuple(queries)


def event_codec_project(catalog: EventCatalog, frame: ResolvedEventFrame) -> EventCodecProjection:
    resolved = catalog.resolved_events()
    payloads = frozenset(name for item in resolved for name in item.event.payload)
    class_frames: dict[int, ResolvedEventFrameLayout] = {}
    fixed_routes = []
    for item in resolved:
        if item.event.frame not in frame.layouts:
            raise ValueError(f"{item.event.source}: unknown event frame {item.event.frame!r}")
        layout = frame.layouts[item.event.frame]
        if item.code.selector.kind != "fixed":
            previous = class_frames.setdefault(item.code.class_value, layout)
            if previous is not layout:
                raise ValueError(f"{item.event.source}: dynamic event class selects conflicting frames")
        if item.code.value is None:
            continue
        fixed_routes.append(
            FixedEventRouteProjection(
                item.owner,
                item.event.id,
                item.code.value,
                layout,
                tuple(item.event.payload),
            )
        )
    return EventCodecProjection(
        frame,
        payloads,
        tuple(fixed_routes),
        tuple(
            (
                DynamicEventRouteProjection(class_value, frame)
                for class_value, frame in sorted(class_frames.items())
            )
        ),
    )


def vector_geometry_project(catalog: RegisterCatalog) -> VectorGeometryProjection:
    vector_namespace = catalog.namespaces.get("VECTOR")
    if vector_namespace is None:
        raise ValueError("VECTOR register namespace is required")
    return VectorGeometryProjection(
        len(vector_namespace.groups["VECTOR"].registers),
        len(vector_namespace.groups["PREDICATE"].registers),
    )


def register_contracts_project(
    register_catalog: RegisterCatalog, control_registers: ControlRegisterCatalog
) -> RegisterContractsProjection:
    def project(register, encoding: int, width: RegisterWidth):
        fields = register.layout.fields if register.layout else ()
        return RegisterContractProjection(
            register_id=register.id,
            encoding=encoding,
            width=width,
            writable_mask=(
                sum(_mask(field.lsb, field.bits) for field in fields)
                if register.layout is not None
                else (1 << 64) - 1
            ),
            reset_value=(
                register.reset.value
                if isinstance(register.reset, ConstantReset)
                else None
            ),
            fields=tuple(
                RegisterFieldProjection(
                    field.id, field.lsb, _mask(field.lsb, field.bits)
                )
                for field in fields
            ),
        )

    return RegisterContractsProjection(
        MappingProxyType(
            {
                (owner, group.id): tuple(
                    project(register, register.encoding, register.width)
                    for register in group.registers.values()
                    if register.encoding is not None
                )
                for owner, namespace in register_catalog.namespaces.items()
                for group in namespace.groups.values()
            }
        ),
        MappingProxyType(
            {
                owner: tuple(
                    project(register, register.selector, 64)
                    for register in namespace.registers.values()
                )
                for owner, namespace in control_registers.namespaces.items()
                if namespace.registers
            }
        ),
    )
