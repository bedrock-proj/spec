"""Owner-local selection and semantic query layout for CPUID leaves."""
from __future__ import annotations
from dataclasses import dataclass
from engine.isa.model import DocumentTopic
from engine.isa.cpuid import CpuidField, CpuidLeaf, CpuidLeafOverlay, compose_selector
from engine.reference import Reference

@dataclass(frozen=True, slots=True)
class ProjectedCpuidQuery:
    """One public query row and its result-format fields."""

    id: str
    first: int
    last: int
    stride: int
    fields: tuple[CpuidField, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", tuple(self.fields))



@dataclass(frozen=True, slots=True)
class CpuidLeafProjection:
    """The owner-local public contract selected by one root CPUID leaf."""

    leaf: CpuidLeaf
    class_value: int
    leaf_value: int
    queries: tuple[ProjectedCpuidQuery, ...]


    def __post_init__(self) -> None:
        object.__setattr__(self, "queries", tuple(self.queries))

    def selector(self, index: int) -> int:
        """Compose one query selector for this projected leaf."""

        return compose_selector(self.class_value, self.leaf_value, index)


def project_cpuid_leaf( catalog, leaf: CpuidLeaf) -> "CpuidLeafProjection":
    if isinstance(leaf, CpuidLeafOverlay):
        raise ValueError(
            f"CPUID leaf projection requires a root leaf, not {leaf.reference!r}"
        )
    resolved = catalog.resolve_leaf(leaf)
    fields_by_query: dict[tuple[str, int, int, int], list[CpuidField]] = {}
    for namespace in catalog.namespaces.values():
        for cpuid_class in namespace.classes.values():
            for candidate in cpuid_class.leaves.values():
                candidate_root = catalog.resolve_leaf(candidate).root_leaf.reference
                if candidate_root != leaf.reference:
                    continue
                for query in candidate.queries:
                    key = (
                        query.id,
                        query.indexes.first,
                        query.indexes.last,
                        query.indexes.stride,
                    )
                    fields_by_query.setdefault(key, []).extend(query.fields)

    queries = tuple(
        ProjectedCpuidQuery(
            query_id,
            first,
            last,
            stride,
            tuple(sorted(fields, key=lambda field: field.lsb)),
        )
        for (query_id, first, last, stride), fields in sorted(
            fields_by_query.items(),
            key=lambda item: (item[0][1], item[0][2], item[0][3], item[0][0]),
        )
    )
    return CpuidLeafProjection(
        leaf,
        resolved.class_value,
        resolved.leaf_value,
        queries,
    )


def select_cpuid_leaf(reference, *, owner, catalog) -> CpuidLeafProjection:
    leaf = catalog.leaves.resolve(reference)
    if not isinstance(owner, DocumentTopic) or reference.owner != owner.reference.owner:
        raise ValueError(f"CPUID leaf {reference!r} does not belong to the selected source owner")
    return project_cpuid_leaf(catalog, leaf)
