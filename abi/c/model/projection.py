"""Resolved C ABI relations shared by document and compiler artifacts."""

from __future__ import annotations

from dataclasses import dataclass

from abi.c.model.project import (
    CAbiProject, CType, RuntimeHelper, MemoryOrderMapping, AtomicLowering,
    ResolvedCallingConvention, ResolvedValueClass, Promotion, resolve_calling_convention,
)
from engine.isa.catalog import InstructionBundle
from engine.isa.registers import Register
from engine.reference import QualifiedReference


@dataclass(frozen=True, slots=True)
class CAbiProjection:
    types: tuple[CType, ...]
    calling_convention: ResolvedCallingConvention
    value_classes: tuple[ResolvedValueClass, ...]
    promotions: tuple[Promotion, ...]
    runtime_helpers: tuple[tuple[RuntimeHelper, CType, tuple[CType, ...]], ...]
    memory_orders: tuple[tuple[MemoryOrderMapping, tuple[str | InstructionBundle, ...] | None, tuple[str | InstructionBundle, ...] | None, tuple[str | InstructionBundle, ...]], ...]
    atomic_lowerings: tuple[tuple[AtomicLowering, tuple[InstructionBundle, ...]], ...]
    preservation: tuple[tuple[str, tuple[Register, ...]], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "types", tuple(self.types))
        object.__setattr__(self, "value_classes", tuple(self.value_classes))
        object.__setattr__(self, "promotions", tuple(self.promotions))
        object.__setattr__(self, "runtime_helpers", tuple((item0[0], item0[1], tuple(item0[2])) for item0 in self.runtime_helpers))
        object.__setattr__(self, "memory_orders", tuple((item0[0], (None if item0[1] is None else tuple(item0[1])), (None if item0[2] is None else tuple(item0[2])), tuple(item0[3])) for item0 in self.memory_orders))
        object.__setattr__(self, "atomic_lowerings", tuple((item0[0], tuple(item0[1])) for item0 in self.atomic_lowerings))
        object.__setattr__(self, "preservation", tuple((item0[0], tuple(item0[1])) for item0 in self.preservation))



def project_c_abi(project: CAbiProject, isa) -> CAbiProjection:
    def instruction(reference):
        if reference.domain != "isa":
            raise ValueError(f"C ABI instruction must belong to ISA: {reference!r}")
        return isa.catalog.instructions.resolve(reference.local)

    def sequence(items):
        return None if items is None else tuple(instruction(item) if isinstance(item, QualifiedReference) else item for item in items)

    rules = resolve_calling_convention(project, isa.registers)
    values = {value.definition.reference: value for value in rules.value_classes.values()}
    return CAbiProjection(
        tuple(project.types.values()), rules,
        tuple(values[reference] for reference in rules.definition.value_classes),
        tuple(project.promotions.resolve(reference) for reference in rules.definition.promotions),
        tuple((helper, project.types.resolve(helper.result), tuple(project.types.resolve(parameter) for parameter in helper.parameters)) for helper in project.runtime_helpers.values()),
        tuple((order, sequence(order.load), sequence(order.store), sequence(order.thread_fence)) for order in project.memory_orders.values()),
        tuple((lowering, tuple(instruction(reference) for reference in lowering.instructions)) for lowering in project.atomic_lowerings.values()),
        tuple((preservation.disposition, tuple(isa.registers.registers.resolve(reference.local) for reference in preservation.registers)) for preservation in project.calling_convention.preservation),
    )
