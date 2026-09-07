"""Render exhaustive instruction-entry dispatch."""

from dataclasses import dataclass
from engine.isa.instructions import Instruction


@dataclass(frozen=True, slots=True)
class SailDispatchEntry:
    instruction: Instruction
    entry: str


@dataclass(frozen=True, slots=True)
class SailDispatchProjection:
    """Selected operation-to-instruction-entry dispatch relation."""

    entries: tuple[SailDispatchEntry, ...]
    has_execution_provider: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))



def project_sail_dispatch(*, bundles, execution_provider) -> SailDispatchProjection:
    return SailDispatchProjection(
        tuple(
            (
                SailDispatchEntry(
                    semantics.instruction,
                    f"execute_{semantics.instruction.mnemonic}",
                )
                for semantics in bundles
            )
        ),
        execution_provider is not None,
    )


