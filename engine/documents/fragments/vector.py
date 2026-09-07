"""Explicit selection from an instruction-owned diagram collection."""
from engine.isa.catalog import InstructionBundle


def select_vector_diagram(reference, *, owner, source):
    if not isinstance(owner, InstructionBundle):
        raise ValueError(f"{source}: vector diagram requires an instruction description owner")
    if reference not in owner.diagrams.diagrams:
        raise ValueError(f"{source}: vector diagram {reference!r} is not owned by this instruction")
    return owner.diagrams.diagrams.resolve(reference)
