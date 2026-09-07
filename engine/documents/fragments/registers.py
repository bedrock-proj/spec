"""Explicit owner-local register groups and selector rows."""
from __future__ import annotations
from dataclasses import dataclass
from engine.isa.model import DocumentTopic
from engine.isa.registers import RegisterGroup, RegisterNamespace
from engine.isa.control_registers import ControlRegister

@dataclass(frozen=True, slots=True)
class RegisterFigureProjection:
    """One owner-local ordered register-group selection."""

    namespace: RegisterNamespace
    groups: tuple[RegisterGroup, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "groups", tuple(self.groups))



@dataclass(frozen=True, slots=True)
class ControlRegistersProjection:
    registers: tuple[ControlRegister, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "registers", tuple(self.registers))



def select_register_figure(namespace, group_ids, *, owner, catalog):
    if not isinstance(owner, DocumentTopic) or namespace != owner.reference.owner:
        raise ValueError("register figure namespace does not match the source owner")
    selected = catalog.namespace(namespace)
    if not group_ids or len(set(group_ids)) != len(group_ids):
        raise ValueError("register figure requires unique selected groups")
    unknown = set(group_ids) - set(selected.groups)
    if unknown:
        raise ValueError(f"unknown register figure groups: {sorted(unknown)}")
    return RegisterFigureProjection(selected, tuple(selected.groups[key] for key in group_ids))


def select_control_registers(references, *, catalog) -> ControlRegistersProjection:
    if len(set(references)) != len(references):
        raise ValueError("duplicate control-register selection")
    return ControlRegistersProjection(tuple(sorted((catalog.registers.resolve(reference) for reference in references), key=lambda register: register.selector)))
