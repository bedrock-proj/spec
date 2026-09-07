"""Explicit implementation-disclosure rows."""
from dataclasses import dataclass
from engine.isa.disclosures import ImplementationDisclosure

@dataclass(frozen=True, slots=True)
class DisclosuresProjection:
    disclosures: tuple[ImplementationDisclosure, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "disclosures", tuple(self.disclosures))



def select_disclosures(identifiers, *, catalog) -> DisclosuresProjection:
    by_id = {item.id: item for item in catalog.disclosures}
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("duplicate implementation-disclosure selection")
    unknown = set(identifiers) - set(by_id)
    if unknown:
        raise ValueError(f"unknown implementation disclosures: {sorted(unknown)}")
    return DisclosuresProjection(tuple(by_id[key] for key in identifiers))
