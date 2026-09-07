"""Values and operations owned by this specification boundary."""

from __future__ import annotations

from engine.entity import Entity
from engine.documents.dependencies import DependencyGraph
import json
from pathlib import Path
import re


def _relative(path: Path, root: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(root.resolve()))
    except ValueError:
        return str(resolved)


def _entity_type_name(entity: Entity) -> str:
    """Return a diagnostic type name without a parallel kind discriminator."""

    return re.sub(r"(?<!^)(?=[A-Z])", "-", type(entity).__name__).lower()


def render_dependency_graph(graph: DependencyGraph, selected_presentations, root: Path) -> str:
    grouped: dict[
        tuple[Reference[object] | Path, Reference[object], str], list[DependencyEdge]
    ] = {}
    for edge in graph.edges:
        grouped.setdefault((edge.source, edge.target, edge.kind), []).append(edge)
    incoming: dict[Reference[object] | Path, int] = {}
    outgoing: dict[Reference[object] | Path, int] = {}
    referenced = {edge.source for edge in graph.edges} | {
        edge.target for edge in graph.edges
    }
    def order(reference):
        if isinstance(reference, Path):
            return (0, reference.as_posix())
        return (1, reference.owner, *reference.path, reference.element)

    ordered_references = sorted(referenced, key=order)
    node_ids = {
        reference: f"node-{index}"
        for index, reference in enumerate(ordered_references)
    }
    edges = []
    for (source, target, kind), occurrences in sorted(
        grouped.items(), key=lambda item: (order(item[0][0]), order(item[0][1]), item[0][2])
    ):
        incoming[target] = incoming.get(target, 0) + len(occurrences)
        outgoing[source] = outgoing.get(source, 0) + len(occurrences)
        edges.append(
            {
                "source": node_ids[source],
                "target": node_ids[target],
                "kind": kind,
                "occurrences": len(occurrences),
                "locations": [
                    {
                        "source": _relative(edge.source_path, root),
                        "offset": edge.offset,
                    }
                    for edge in occurrences
                ],
            }
        )
    nodes = []
    for reference in ordered_references:
        if isinstance(reference, Path):
            kind = "document-source"
            display = _relative(reference, root)
        else:
            entity, presentation = selected_presentations[reference]
            kind = _entity_type_name(entity)
            display = presentation.display
        nodes.append(
            {
                "id": node_ids[reference],
                "kind": kind,
                "display": display,
                "incoming": incoming.get(reference, 0),
                "outgoing": outgoing.get(reference, 0),
                "degree": incoming.get(reference, 0) + outgoing.get(reference, 0),
            }
        )
    return (
        json.dumps({"nodes": nodes, "edges": edges}, indent=2, sort_keys=True)
        + "\n"
    )
