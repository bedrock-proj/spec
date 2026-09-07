"""Canonical declared and active relationships for Sail source composition."""

from __future__ import annotations

from dataclasses import dataclass
from engine.reference import Reference
from engine.isa.catalog import InstructionBundle
from engine.isa.instructions import Instruction
from engine.isa.cpuid import CpuidField


@dataclass(frozen=True, slots=True)
class SailTypeContribution:
    owner: str
    instruction_set: str | None
    fault_kinds: tuple[str, ...]
    effect_kinds: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fault_kinds", tuple(self.fault_kinds))
        object.__setattr__(self, "effect_kinds", tuple(self.effect_kinds))



@dataclass(frozen=True, slots=True)
class SailOperationProjection:
    reference: Reference[InstructionBundle]
    instruction: Instruction


@dataclass(frozen=True, slots=True)
class SailEventProjection:
    reference: Reference
    event_id: str
    class_value: int
    selector: int | None
    frame: str
    family: str | None


@dataclass(frozen=True, slots=True)
class SailControlRegisterProjection:
    reference: Reference
    owner: str
    id: str
    selector: int
    generated_state: bool


@dataclass(frozen=True, slots=True)
class SailRegistryProjection:
    cpuid_flags: tuple[CpuidField, ...]
    type_contributions: tuple[SailTypeContribution, ...]
    event_families: tuple[str, ...]
    events: tuple[SailEventProjection, ...]
    control_registers: tuple[SailControlRegisterProjection, ...]
    operations: tuple[SailOperationProjection, ...]
    active_operations: frozenset[Reference]
    active_events: frozenset[Reference]
    active_control_registers: frozenset[Reference]

    def __post_init__(self) -> None:
        object.__setattr__(self, "cpuid_flags", tuple(self.cpuid_flags))
        object.__setattr__(self, "type_contributions", tuple(self.type_contributions))
        object.__setattr__(self, "event_families", tuple(self.event_families))
        object.__setattr__(self, "events", tuple(self.events))
        object.__setattr__(self, "control_registers", tuple(self.control_registers))
        object.__setattr__(self, "operations", tuple(self.operations))



def project_sail_registry(*, bundles, configuration, active_owners, control_registers, events, generated_state_registers, model_namespaces) -> SailRegistryProjection:
    flags = {field.reference: field for bundle in bundles for form in bundle.encodings.forms for field in bundle.required_cpuid_flags_for(form)}
    contributions = tuple(SailTypeContribution(owner, model_namespaces[owner].instruction_set, model_namespaces[owner].fault_kinds, model_namespaces[owner].effect_kinds) for owner in configuration.extension_ids)
    for bundle in bundles:
        if bundle.owner != "base" and model_namespaces[bundle.owner].instruction_set is None:
            raise ValueError(f"{model_namespaces[bundle.owner].source}: an instruction owner must declare its Sail instruction set")
    order = {owner: index for index, owner in enumerate(("base", *configuration.extension_ids))}
    controls = tuple(sorted(control_registers, key=lambda register: (order[register.owner], register.selector)))
    return SailRegistryProjection(
        tuple(flags.values()), contributions,
        tuple(dict.fromkeys(item.event.family for item in events if item.event.family is not None)),
        tuple(SailEventProjection(item.event.reference, item.event.id, item.code.class_value, item.code.event_selector, item.event.frame, item.event.family) for item in events),
        tuple(SailControlRegisterProjection(item.reference, item.owner, item.id, item.selector, (item.owner, item.id) in generated_state_registers) for item in controls),
        tuple(SailOperationProjection(bundle.reference, bundle.instruction) for bundle in bundles),
        frozenset(bundle.reference for bundle in bundles if bundle.owner in active_owners),
        frozenset(item.event.reference for item in events if item.owner in active_owners),
        frozenset(item.reference for item in controls if item.owner in active_owners),
    )
