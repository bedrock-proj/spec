"""Strict finite-example diagrams for the vector instruction reference.

The sources own closed finite views, not an operation language. Some variants
author displayed cells and connections explicitly. ``predicate-width-conversion`` authors
the widths, complete-result policy, and one closed contiguous mapping from which
the finite cells and exactly eight transfers are derived.
"""

from __future__ import annotations

from engine.source.inventory import inspect_inventory, require_exact

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any
from collections.abc import Mapping

from engine.entity import Entity
from engine.reference import Reference, ReferenceIndex, register_reference
from engine.source.inventory import DirectoryInventory
from engine.source.yaml import freeze_source, load_schema_yaml, load_yaml

VISIBLE_BYTES = 16
LANE_ORDER = "right-to-left"
CELL_EFFECTS = frozenset(
    {"copy", "preserve", "zero", "set", "clear", "sign-fill", "ignored"}
)
TRANSFER_EFFECTS = frozenset({"copy", "preserve", "sign-fill"})
CELL_APPEARANCES = frozenset(
    {
        "old",
        "source",
        "zero",
        "discarded",
        "predicate-on",
        "predicate-off",
        "dont-care",
    }
)
WIDTH_CONTAINER_BITS = frozenset({16, 32, 64})
WIDTH_CONNECTION_EFFECTS = frozenset({"copy", "sign-fill", "zero"})
WIDTH_CONNECTION_DISPLAYS = frozenset({"transfer", "expansion-guide"})
PREDICATE_WIDTH_TERMS = frozenset({"source-lanes", "destination-lanes"})
PREDICATE_LANE_EDGE_DISPLAYS = frozenset({"transfer", "control"})


class VectorDiagramError(ValueError):
    """A vector example source does not meet the finite-view contract."""


@dataclass(frozen=True, slots=True)
class VectorExample:
    """Common validated structure shared by finite vector examples."""

    scalable: bool
    rows: tuple[Mapping[str, Any], ...]
    edges: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        values = freeze_source({field.name: getattr(self, field.name) for field in fields(self)})
        for name, value in values.items():
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class VectorLaneTransferExample(VectorExample):
    pass


@dataclass(frozen=True, slots=True)
class IntegerWidthConversionExample(VectorExample):
    pass


@dataclass(frozen=True, slots=True)
class StatefulPredicateRangeExample(VectorExample):
    states: tuple[Mapping[str, str], ...]
    start: int
    end: int | str


@dataclass(frozen=True, slots=True)
class CountedPredicateRangeExample(VectorExample):
    count: Mapping[str, str]
    start: int
    end: int | str


PredicateRangeExample = StatefulPredicateRangeExample | CountedPredicateRangeExample


@dataclass(frozen=True, slots=True)
class PredicateWidthConversionExample(VectorExample):
    pass


@dataclass(frozen=True, slots=True)
class PredicateLaneTransferExample(VectorExample):
    pass


@dataclass(frozen=True, slots=True)
class ScalarVectorTransferExample(VectorExample):
    scalars: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class PredicatedVectorLoadExample(VectorExample):
    base: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PredicatedVectorReductionExample(VectorExample):
    selected: tuple[int, ...]
    fold_label: str
    terms: tuple[str, ...]
    continuation: bool
    result_label: str
    result_value: str


@dataclass(frozen=True, slots=True)
class FloatingPointWidthConversionExample(VectorExample):
    pass


@dataclass(frozen=True, slots=True)
class VectorDiagram(Entity):
    """One instruction-owned finite VECTOR example and its reader metadata."""

    reference: Reference["VectorDiagram"]
    source: Path
    id: str
    caption: str
    alt_text: str
    example: VectorExample



@dataclass(frozen=True, slots=True)
class VectorDiagramCatalog:
    """An optional closed collection of diagrams owned by one instruction."""

    owner: str
    instruction: Reference[object]
    root: Path
    inventory: DirectoryInventory | None
    diagrams: ReferenceIndex[VectorDiagram]



    def __post_init__(self) -> None:
        object.__setattr__(self, "diagrams", (self.diagrams if isinstance(self.diagrams, ReferenceIndex) else ReferenceIndex(self.diagrams)))
        if self.instruction.owner != self.owner or self.instruction.path != ("instructions",):
            raise VectorDiagramError(f"{self.root}: diagram collection must belong to one instruction")
        inventory = self.inventory
        if inventory is None:
            if self.diagrams:
                raise VectorDiagramError(f"{self.root}: diagram members require an inventory")
        elif inventory.owner != self.owner or inventory.kind != "vector-diagram" or inventory.root != self.root or inventory.source != self.root / "diagrams.yaml" or inventory.duplicates or inventory.missing or inventory.undeclared or set(inventory.declared) != {member.id for member in self.diagrams.values()}:
            raise VectorDiagramError(f"{inventory.source}: diagram inventory and loaded membership must agree")
        for reference, member in self.diagrams.items():
            expected = Reference(self.owner, ("instructions", self.instruction.element, "diagrams"), member.id)
            if reference != expected or member.reference != expected or member.source != self.root / member.id / "diagram.yaml":
                raise VectorDiagramError(f"{member.source}: diagram identity or source differs from its instruction-owned collection")

    @property
    def declared(self) -> tuple[str, ...]:
        return () if self.inventory is None else self.inventory.declared



def _fail(path: Path, message: str) -> None:
    raise VectorDiagramError(f"{path}: {message}")


def _mapping(value: Any, path: Path, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(path, f"{where} must be a mapping")
    return value


def _string(value: Any, path: Path, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(path, f"{where} must be a non-empty string")
    return value


def _list(value: Any, path: Path, where: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        _fail(path, f"{where} must be a non-empty list")
    return value


def _check_keys(
    item: dict[str, Any],
    path: Path,
    where: str,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    allowed = required | (optional or set())
    unknown = set(item) - allowed
    missing = required - set(item)
    if unknown or missing:
        _fail(
            path,
            f"{where} keys must be within {sorted(allowed)}; "
            f"missing={sorted(missing)}, extra={sorted(unknown)}",
        )


def _cell(
    value: Any,
    path: Path,
    where: str,
    *,
    additional_appearances: frozenset[str] = frozenset(),
) -> dict[str, str]:
    item = _mapping(value, path, where)
    _check_keys(item, path, where, {"value", "effect"}, {"appearance"})
    value_text = _string(item["value"], path, where + ".value")
    effect = _string(item["effect"], path, where + ".effect")
    if effect not in CELL_EFFECTS:
        _fail(path, f"{where}.effect is not a displayed result classification")
    decoded = {"value": value_text, "effect": effect}
    if "appearance" in item:
        appearance = _string(item["appearance"], path, where + ".appearance")
        if appearance not in CELL_APPEARANCES | additional_appearances:
            _fail(path, f"{where}.appearance is not a registered cell appearance")
        decoded["appearance"] = appearance
    return decoded


def _view(raw: dict[str, Any], path: Path) -> bool:
    view = _mapping(raw.get("view"), path, "view")
    _check_keys(view, path, "view", {"visible_bytes", "lane_order", "scalable"})
    if view["visible_bytes"] != VISIBLE_BYTES:
        _fail(
            path,
            f"view.visible_bytes must be the fixed {VISIBLE_BYTES}-byte example view",
        )
    if view["lane_order"] != LANE_ORDER:
        _fail(path, "view must use right-to-left lanes")
    if not isinstance(view["scalable"], bool):
        _fail(path, "view.scalable must be a boolean")
    return view["scalable"]


def _integer(value: Any, path: Path, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(path, f"{where} must be an integer")
    return value


def _decode_lane_map(raw: dict[str, Any], path: Path, scalable: bool) -> VectorExample:
    _check_keys(raw, path, "root", {"kind", "view", "rows", "edges"})
    rows: list[dict[str, Any]] = []
    row_ids: set[str] = set()
    result_rows: list[dict[str, Any]] = []
    for index, value in enumerate(_list(raw["rows"], path, "rows")):
        row = _mapping(value, path, f"rows[{index}]")
        _check_keys(
            row,
            path,
            f"rows[{index}]",
            {"id", "label", "role", "element_bits", "cells"},
        )
        row_id = _string(row["id"], path, f"rows[{index}].id")
        if row_id in row_ids:
            _fail(path, f"duplicate row ID {row_id!r}")
        row_ids.add(row_id)
        if row["role"] not in {
            "source",
            "destination-before",
            "destination-after",
            "predicate",
        }:
            _fail(path, f"rows[{index}].role is invalid")
        element_bits = _integer(
            row["element_bits"], path, f"rows[{index}].element_bits"
        )
        if element_bits <= 0:
            _fail(path, f"rows[{index}].element_bits must be positive")
        cells = tuple(
            _cell(cell, path, f"rows[{index}].cells[{cell_index}]")
            for cell_index, cell in enumerate(
                _list(row["cells"], path, f"rows[{index}].cells")
            )
        )
        if VISIBLE_BYTES * 8 % element_bits:
            _fail(path, f"rows[{index}].element_bits does not divide the fixed view")
        if len(cells) != VISIBLE_BYTES * 8 // element_bits:
            _fail(path, f"rows[{index}] does not cover every visible lane")
        decoded = {
            "id": row_id,
            "label": _string(row["label"], path, f"rows[{index}].label"),
            "role": row["role"],
            "element_bits": element_bits,
            "cells": cells,
        }
        rows.append(decoded)
        if row["role"] == "destination-after":
            result_rows.append(decoded)
    if len(result_rows) != 1:
        _fail(path, "vector-lane-transfer requires exactly one destination-after row")
    rows_by_id = {row["id"]: row for row in rows}
    edges = _decode_edges(raw["edges"], path, rows_by_id, result_rows[0])
    if any(
        cell["effect"] not in TRANSFER_EFFECTS | {"zero"}
        for cell in result_rows[0]["cells"]
    ):
        _fail(
            path,
            "result cells must use a renderable transfer or terminal classification",
        )
    return VectorLaneTransferExample(scalable, tuple(rows), edges)


def _decode_edges(
    value: Any, path: Path, rows: dict[str, dict[str, Any]], result_row: dict[str, Any]
) -> tuple[dict[str, Any], ...]:
    edges: list[dict[str, Any]] = []
    targets: set[int] = set()
    for index, item_value in enumerate(_list(value, path, "edges")):
        item = _mapping(item_value, path, f"edges[{index}]")
        _check_keys(
            item,
            path,
            f"edges[{index}]",
            {"from_row", "from_cell", "to_row", "to_cell", "effect"},
        )
        source = _string(item["from_row"], path, f"edges[{index}].from_row")
        target_row = _string(item["to_row"], path, f"edges[{index}].to_row")
        source_cell = _integer(item["from_cell"], path, f"edges[{index}].from_cell")
        target_cell = _integer(item["to_cell"], path, f"edges[{index}].to_cell")
        if source not in rows or target_row != result_row["id"]:
            _fail(path, f"edges[{index}] has an invalid cell reference")
        if not 0 <= source_cell < len(
            rows[source]["cells"]
        ) or not 0 <= target_cell < len(result_row["cells"]):
            _fail(path, f"edges[{index}] cell index is out of bounds")
        if target_cell in targets:
            _fail(path, f"edges[{index}] does not give one valid result target")
        effect = _string(item["effect"], path, f"edges[{index}].effect")
        if effect not in TRANSFER_EFFECTS:
            _fail(
                path,
                f"edges[{index}].effect must be a renderable transfer classification",
            )
        if effect != result_row["cells"][target_cell]["effect"]:
            _fail(
                path, f"edges[{index}].effect must equal its result-cell classification"
            )
        targets.add(target_cell)
        edges.append(item)
    return tuple(edges)


def _decode_width_map(raw: dict[str, Any], path: Path, scalable: bool) -> VectorExample:
    if not scalable:
        _fail(path, "integer-width-conversion requires scalable continuation")
    return _decode_detailed_width_map(raw, path, scalable)


def _decode_width_container(
    value: Any,
    path: Path,
    where: str,
    container_bits: int,
    *,
    additional_appearances: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], ...]:
    container = _mapping(value, path, where)
    _check_keys(container, path, where, {"cells"})
    cells: list[dict[str, Any]] = []
    total_bits = 0
    for index, cell_value in enumerate(
        _list(container["cells"], path, where + ".cells")
    ):
        cell_where = f"{where}.cells[{index}]"
        item = _mapping(cell_value, path, cell_where)
        _check_keys(item, path, cell_where, {"value", "effect", "appearance", "bits"})
        decoded = _cell(
            {key: item[key] for key in ("value", "effect", "appearance")},
            path,
            cell_where,
            additional_appearances=additional_appearances,
        )
        bits = _integer(item["bits"], path, cell_where + ".bits")
        if bits <= 0:
            _fail(path, f"{cell_where}.bits must be positive")
        total_bits += bits
        cells.append({**decoded, "bits": bits})
    if total_bits != container_bits:
        _fail(path, f"{where} cells must cover exactly {container_bits} bits")
    return tuple(cells)


def _decode_detailed_width_edges(
    value: Any,
    path: Path,
    rows: dict[str, dict[str, Any]],
    result_row: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    edges: list[dict[str, Any]] = []
    targets: set[tuple[int, int]] = set()
    for index, item_value in enumerate(_list(value, path, "edges")):
        item = _mapping(item_value, path, f"edges[{index}]")
        _check_keys(
            item,
            path,
            f"edges[{index}]",
            {
                "from_row",
                "from_container",
                "from_cell",
                "to_row",
                "to_container",
                "to_cell",
                "effect",
                "display",
            },
        )
        source_id = _string(item["from_row"], path, f"edges[{index}].from_row")
        target_id = _string(item["to_row"], path, f"edges[{index}].to_row")
        if source_id not in rows or target_id != result_row["id"]:
            _fail(path, f"edges[{index}] has an invalid row reference")
        source_row = rows[source_id]
        if source_row.get("role") != "source":
            _fail(path, f"edges[{index}] source must be the source row")
        source_container = _integer(
            item["from_container"], path, f"edges[{index}].from_container"
        )
        source_cell = _integer(item["from_cell"], path, f"edges[{index}].from_cell")
        target_container = _integer(
            item["to_container"], path, f"edges[{index}].to_container"
        )
        target_cell = _integer(item["to_cell"], path, f"edges[{index}].to_cell")
        if not 0 <= source_container < len(source_row["containers"]):
            _fail(path, f"edges[{index}] source container is out of bounds")
        if not 0 <= target_container < len(result_row["containers"]):
            _fail(path, f"edges[{index}] target container is out of bounds")
        if not 0 <= source_cell < len(source_row["containers"][source_container]):
            _fail(path, f"edges[{index}] source cell is out of bounds")
        if (
            source_row["containers"][source_container][source_cell]["effect"]
            == "ignored"
        ):
            _fail(path, f"edges[{index}] source cell must not be ignored")
        if not 0 <= target_cell < len(result_row["containers"][target_container]):
            _fail(path, f"edges[{index}] target cell is out of bounds")
        target = (target_container, target_cell)
        if target in targets:
            _fail(path, f"edges[{index}] does not give one valid result target")
        effect = _string(item["effect"], path, f"edges[{index}].effect")
        display = _string(item["display"], path, f"edges[{index}].display")
        if effect not in WIDTH_CONNECTION_EFFECTS:
            _fail(
                path,
                f"edges[{index}].effect is not a integer-width-conversion connection",
            )
        if display not in WIDTH_CONNECTION_DISPLAYS:
            _fail(
                path,
                f"edges[{index}].display is not a integer-width-conversion presentation",
            )
        if display == "transfer" and effect != "copy":
            _fail(path, f"edges[{index}] transfer presentation requires copy")
        if display == "expansion-guide" and effect not in {"sign-fill", "zero"}:
            _fail(path, f"edges[{index}] expansion guide requires sign-fill or zero")
        if effect != result_row["containers"][target_container][target_cell]["effect"]:
            _fail(
                path, f"edges[{index}].effect must equal its result-cell classification"
            )
        targets.add(target)
        edges.append(item)
    return tuple(edges)


def _decode_detailed_width_map(
    raw: dict[str, Any], path: Path, scalable: bool
) -> VectorExample:
    _check_keys(
        raw,
        path,
        "detailed integer-width-conversion root",
        {"kind", "view", "container_bits", "rows", "edges"},
    )
    container_bits = _integer(raw["container_bits"], path, "container_bits")
    if container_bits not in WIDTH_CONTAINER_BITS:
        _fail(path, "container_bits must be one of 16, 32, or 64")
    container_count = VISIBLE_BYTES * 8 // container_bits
    rows: list[dict[str, Any]] = []
    row_ids: set[str] = set()
    roles: list[str] = []
    for index, value in enumerate(_list(raw["rows"], path, "rows")):
        row = _mapping(value, path, f"rows[{index}]")
        role = row.get("role")
        if role == "predicate":
            _check_keys(
                row,
                path,
                f"rows[{index}]",
                {"id", "label", "role", "element_bits", "cells"},
            )
        else:
            _check_keys(
                row,
                path,
                f"rows[{index}]",
                {"id", "label", "role", "containers"},
            )
        row_id = _string(row["id"], path, f"rows[{index}].id")
        if row_id in row_ids:
            _fail(path, f"duplicate row ID {row_id!r}")
        row_ids.add(row_id)
        label = _string(row["label"], path, f"rows[{index}].label")
        if role == "predicate":
            if row["element_bits"] != 8:
                _fail(
                    path,
                    "detailed integer-width-conversion predicate row uses byte positions",
                )
            cells = tuple(
                _cell(cell, path, f"rows[{index}].cells[{cell_index}]")
                for cell_index, cell in enumerate(
                    _list(row["cells"], path, f"rows[{index}].cells")
                )
            )
            if len(cells) != VISIBLE_BYTES:
                _fail(
                    path,
                    "detailed integer-width-conversion predicate row must cover 16 bytes",
                )
            if any(cell["effect"] not in {"set", "clear", "ignored"} for cell in cells):
                _fail(
                    path,
                    "detailed integer-width-conversion predicate cells must be set, clear, or ignored",
                )
            decoded = {
                "id": row_id,
                "label": label,
                "role": role,
                "element_bits": 8,
                "cells": cells,
            }
        else:
            if role not in {"source", "destination-after"}:
                _fail(
                    path,
                    f"rows[{index}].role is invalid for a detailed integer-width-conversion",
                )
            containers = tuple(
                _decode_width_container(
                    container,
                    path,
                    f"rows[{index}].containers[{container_index}]",
                    container_bits,
                )
                for container_index, container in enumerate(
                    _list(row["containers"], path, f"rows[{index}].containers")
                )
            )
            if len(containers) != container_count:
                _fail(
                    path,
                    f"rows[{index}] must declare all {container_count} visible containers",
                )
            decoded = {
                "id": row_id,
                "label": label,
                "role": role,
                "container_bits": container_bits,
                "containers": containers,
            }
        roles.append(role)
        rows.append(decoded)
    if sorted(roles) != ["destination-after", "predicate", "source"]:
        _fail(
            path,
            "detailed integer-width-conversion requires one source, result, and predicate row",
        )
    result_row = next(row for row in rows if row["role"] == "destination-after")
    source_row = next(row for row in rows if row["role"] == "source")
    source_effects = {
        cell["effect"] for container in source_row["containers"] for cell in container
    }
    if not source_effects <= {"copy", "ignored"}:
        _fail(
            path,
            "detailed integer-width-conversion source cells must be copied or discarded",
        )
    result_effects = {
        cell["effect"] for container in result_row["containers"] for cell in container
    }
    if not result_effects <= {"copy", "preserve", "sign-fill", "zero"}:
        _fail(
            path,
            "detailed integer-width-conversion result cells have an invalid classification",
        )
    rows_by_id = {row["id"]: row for row in rows}
    edges = _decode_detailed_width_edges(raw["edges"], path, rows_by_id, result_row)
    return IntegerWidthConversionExample(scalable, tuple(rows), edges)


def _decode_predicate_range_bounds(
    raw: dict[str, Any],
    path: Path,
    *,
    start_symbols: dict[str, int] | None = None,
) -> tuple[int, int | str]:
    range_item = _mapping(raw["range"], path, "range")
    _check_keys(range_item, path, "range", {"start", "end"})
    start_value = range_item["start"]
    if isinstance(start_value, str):
        symbols = start_symbols or {}
        if start_value not in symbols:
            _fail(path, f"range.start symbol must be one of {sorted(symbols)}")
        start = symbols[start_value]
    else:
        start = _integer(start_value, path, "range.start")
    end_value = range_item["end"]
    if isinstance(end_value, str):
        if end_value != "lane-count":
            _fail(path, "range.end string must be lane-count")
        end: int | str = end_value
    else:
        end = _integer(end_value, path, "range.end")
    if not 0 <= start <= 8:
        _fail(path, "range.start must be within the eight visible W positions")
    if isinstance(end, int) and not start <= end <= 8:
        _fail(path, "range.end must follow start within the visible W positions")
    return start, end


def _decode_predicate_range_result(
    raw: dict[str, Any], path: Path, start: int, end: int | str
) -> dict[str, Any]:
    result = _mapping(raw["result"], path, "result")
    _check_keys(result, path, "result", {"label", "element_bits", "groups"})
    if result["element_bits"] != 16:
        _fail(path, "predicate-range-generation uses complete W predicate groups")
    groups = tuple(
        _decode_width_container(
            group,
            path,
            f"result.groups[{index}]",
            16,
            additional_appearances=frozenset({"predicate-result"}),
        )
        for index, group in enumerate(_list(result["groups"], path, "result.groups"))
    )
    if len(groups) != 8:
        _fail(path, "predicate-range-generation must cover all eight visible W groups")
    for index, group in enumerate(groups):
        if (
            len(group) != 2
            or group[0]["bits"] != 8
            or group[1]["bits"] != 8
            or group[1]["effect"] != "zero"
            or group[1].get("appearance") != "zero"
        ):
            _fail(
                path,
                f"result.groups[{index}] must show one significant bit and one cleared bit",
            )
        active = index >= start and (end == "lane-count" or index < end)
        significant = group[0]
        expected = (
            ("copy", "predicate-result", "1") if active else ("zero", "zero", "0")
        )
        actual = (
            significant["effect"],
            significant.get("appearance"),
            significant["value"],
        )
        if actual != expected:
            _fail(
                path, f"result.groups[{index}] does not match the authored active range"
            )
    return {
        "id": "result",
        "label": _string(result["label"], path, "result.label"),
        "role": "destination-after",
        "storage": "predicate",
        "element_bits": 16,
        "groups": groups,
    }


def _decode_stateful_predicate_range(
    raw: dict[str, Any], path: Path, scalable: bool
) -> VectorExample:
    _check_keys(
        raw,
        path,
        "predicate-range-generation root",
        {"kind", "view", "states", "range", "result"},
    )
    if not scalable:
        _fail(
            path, "stateful predicate-range-generation requires scalable continuation"
        )
    states: list[dict[str, str]] = []
    state_ids: set[str] = set()
    for index, value in enumerate(_list(raw["states"], path, "states")):
        where = f"states[{index}]"
        state = _mapping(value, path, where)
        _check_keys(
            state,
            path,
            where,
            {"id", "label", "before", "after", "anchor", "after_side"},
        )
        state_id = _string(state["id"], path, where + ".id")
        if state_id in state_ids:
            _fail(path, f"duplicate state ID {state_id!r}")
        state_ids.add(state_id)
        anchor = _string(state["anchor"], path, where + ".anchor")
        if anchor not in {"start", "end"}:
            _fail(path, f"{where}.anchor must be start or end")
        after_side = _string(state["after_side"], path, where + ".after_side")
        if after_side not in {"left", "right"}:
            _fail(path, f"{where}.after_side must be left or right")
        states.append(
            {
                "id": state_id,
                "label": _string(state["label"], path, where + ".label"),
                "before": _string(state["before"], path, where + ".before"),
                "after": _string(state["after"], path, where + ".after"),
                "anchor": anchor,
                "after_side": after_side,
            }
        )
    if len(states) != 2 or {state["id"] for state in states} != {"remaining", "offset"}:
        _fail(
            path,
            "stateful predicate-range-generation requires remaining and offset states",
        )

    start, end = _decode_predicate_range_bounds(raw, path)
    row = _decode_predicate_range_result(raw, path, start, end)
    return StatefulPredicateRangeExample(
        True,
        (row,),
        (),
        tuple(states),
        start,
        end,
    )


def _decode_counted_predicate_range(
    raw: dict[str, Any], path: Path, scalable: bool
) -> VectorExample:
    _check_keys(
        raw,
        path,
        "predicate-range-generation root",
        {"kind", "view", "count", "range", "result"},
    )
    if not scalable:
        _fail(path, "predicate-range-generation requires scalable continuation")
    count = _mapping(raw["count"], path, "count")
    _check_keys(count, path, "count", {"label", "value"})
    count_value = _integer(count["value"], path, "count.value")
    if not 0 <= count_value < 8:
        _fail(path, "count.value must select a boundary inside the visible W positions")
    start, end = _decode_predicate_range_bounds(
        raw, path, start_symbols={"count": count_value}
    )
    if start != count_value or end != "lane-count":
        _fail(
            path,
            "counted predicate-range-generation must span count through lane-count",
        )
    row = _decode_predicate_range_result(raw, path, start, end)
    return CountedPredicateRangeExample(
        True,
        (row,),
        (),
        {
            "label": _string(count["label"], path, "count.label"),
            "value": str(count_value),
        },
        start,
        end,
    )


def _decode_predicate_range(
    raw: dict[str, Any], path: Path, scalable: bool
) -> VectorExample:
    if "states" in raw:
        return _decode_stateful_predicate_range(raw, path, scalable)
    return _decode_counted_predicate_range(raw, path, scalable)


def _predicate_width_term(
    value: Any,
    path: Path,
    where: str,
    lane_counts: dict[str, int],
) -> tuple[int, str | int]:
    if isinstance(value, bool):
        _fail(path, f"{where} must be zero or a registered lane-count term")
    if isinstance(value, int):
        if value != 0:
            _fail(path, f"{where} literal must be zero")
        return 0, 0
    term = _string(value, path, where)
    if term not in PREDICATE_WIDTH_TERMS:
        _fail(path, f"{where} is not a registered lane-count term")
    return lane_counts[term], term


def _predicate_width_containers(
    element_bits: int,
    *,
    result: bool,
    mapped_values: dict[int, str],
) -> tuple[tuple[dict[str, Any], ...], ...]:
    lane_count = VISIBLE_BYTES * 8 // element_bits
    containers: list[tuple[dict[str, Any], ...]] = []
    for lane in range(lane_count):
        significant = (
            {
                "value": mapped_values[lane],
                "effect": "copy",
                "appearance": "predicate-result" if result else "source",
                "bits": 8,
            }
            if lane in mapped_values
            else {
                "value": "0" if result else f"p{lane}",
                "effect": "zero" if result else "copy",
                "appearance": "zero" if result else "source",
                "bits": 8,
            }
        )
        cells: list[dict[str, Any]] = []
        if element_bits == 16:
            cells.append(
                {
                    "value": "0" if result else "x",
                    "effect": "zero" if result else "ignored",
                    "appearance": "zero" if result else "dont-care",
                    "bits": 8,
                }
            )
        cells.append(significant)
        containers.append(tuple(cells))
    return tuple(containers)


def _decode_predicate_width(
    raw: dict[str, Any], path: Path, scalable: bool
) -> VectorExample:
    if scalable:
        _fail(path, "predicate-width-conversion requires a fixed 16-byte example view")
    _check_keys(
        raw,
        path,
        "predicate-width-conversion root",
        {"kind", "view", "source_element_bits", "source", "result"},
    )
    source_bits = _integer(raw["source_element_bits"], path, "source_element_bits")
    source = _mapping(raw["source"], path, "source")
    _check_keys(source, path, "source", {"label"})
    result = _mapping(raw["result"], path, "result")
    _check_keys(result, path, "result", {"label", "write", "element_bits", "mapping"})
    result_bits = _integer(result["element_bits"], path, "result.element_bits")
    if (source_bits, result_bits) not in {(16, 8), (8, 16)}:
        _fail(
            path,
            "predicate-width-conversion supports only the reviewed 16-to-8 and 8-to-16 examples",
        )
    if result["write"] != "complete":
        _fail(path, "predicate-width-conversion result.write must be complete")

    source_lanes = VISIBLE_BYTES * 8 // source_bits
    destination_lanes = VISIBLE_BYTES * 8 // result_bits
    lane_counts = {
        "source-lanes": source_lanes,
        "destination-lanes": destination_lanes,
    }
    mapping = _mapping(result["mapping"], path, "result.mapping")
    _check_keys(
        mapping,
        path,
        "result.mapping",
        {"source_start", "destination_start", "count"},
    )
    source_start, source_start_term = _predicate_width_term(
        mapping["source_start"], path, "result.mapping.source_start", lane_counts
    )
    destination_start, destination_start_term = _predicate_width_term(
        mapping["destination_start"],
        path,
        "result.mapping.destination_start",
        lane_counts,
    )
    count, count_term = _predicate_width_term(
        mapping["count"], path, "result.mapping.count", lane_counts
    )
    if source_bits == 16:
        if source_start_term != 0 or destination_start_term not in {0, "source-lanes"}:
            _fail(
                path,
                "16-to-8 predicate-width-conversion maps all source lanes to one destination half",
            )
        if count_term != "source-lanes":
            _fail(path, "16-to-8 predicate-width-conversion count must be source-lanes")
    else:
        if (
            source_start_term not in {0, "destination-lanes"}
            or destination_start_term != 0
        ):
            _fail(
                path,
                "8-to-16 predicate-width-conversion maps one source half to all destination lanes",
            )
        if count_term != "destination-lanes":
            _fail(
                path,
                "8-to-16 predicate-width-conversion count must be destination-lanes",
            )
    if (
        source_start + count > source_lanes
        or destination_start + count > destination_lanes
    ):
        _fail(
            path,
            "predicate-width-conversion mapping exceeds the fixed example lane bounds",
        )

    mapped_values = {
        destination_start + offset: f"p{source_start + offset}"
        for offset in range(count)
    }
    rows = (
        {
            "id": "source",
            "label": _string(source["label"], path, "source.label"),
            "role": "source",
            "element_bits": source_bits,
            "containers": _predicate_width_containers(
                source_bits,
                result=False,
                mapped_values={lane: f"p{lane}" for lane in range(source_lanes)},
            ),
        },
        {
            "id": "result",
            "label": _string(result["label"], path, "result.label"),
            "role": "destination-after",
            "element_bits": result_bits,
            "containers": _predicate_width_containers(
                result_bits, result=True, mapped_values=mapped_values
            ),
        },
    )
    source_significant_cell = 1 if source_bits == 16 else 0
    result_significant_cell = 1 if result_bits == 16 else 0
    edges = tuple(
        {
            "from_row": "source",
            "from_container": source_start + offset,
            "from_cell": source_significant_cell,
            "to_row": "result",
            "to_container": destination_start + offset,
            "to_cell": result_significant_cell,
            "effect": "copy",
        }
        for offset in range(count)
    )
    return PredicateWidthConversionExample(False, rows, edges)


def _decode_predicate_lane_map(
    raw: dict[str, Any], path: Path, scalable: bool
) -> VectorExample:
    _check_keys(
        raw, path, "predicate-lane-transfer root", {"kind", "view", "rows", "edges"}
    )
    raw_rows = _list(raw["rows"], path, "rows")
    if len(raw_rows) not in {2, 3}:
        _fail(path, "predicate-lane-transfer requires two or three authored rows")
    rows: list[dict[str, Any]] = []
    row_ids: set[str] = set()
    for index, value in enumerate(raw_rows):
        where = f"rows[{index}]"
        row = _mapping(value, path, where)
        _check_keys(
            row,
            path,
            where,
            {"id", "label", "role", "storage", "element_bits", "groups"},
        )
        row_id = _string(row["id"], path, where + ".id")
        if row_id in row_ids:
            _fail(path, f"duplicate row ID {row_id!r}")
        row_ids.add(row_id)
        role = _string(row["role"], path, where + ".role")
        storage = _string(row["storage"], path, where + ".storage")
        if role not in {"source", "destination-before", "destination-after"}:
            _fail(path, f"{where}.role is invalid for a predicate-lane-transfer")
        if storage not in {"predicate", "vector"}:
            _fail(path, f"{where}.storage must be predicate or vector")
        if row["element_bits"] != 16:
            _fail(path, f"{where}.element_bits must be the reviewed W presentation")
        groups = tuple(
            _decode_width_container(
                group,
                path,
                f"{where}.groups[{group_index}]",
                16,
                additional_appearances=frozenset({"predicate-result"}),
            )
            for group_index, group in enumerate(
                _list(row["groups"], path, where + ".groups")
            )
        )
        if len(groups) != 8:
            _fail(path, f"{where} must cover all eight visible W groups")
        if storage == "vector":
            if role != "source":
                _fail(path, f"{where} vector storage must be a source row")
            if any(
                len(group) != 1
                or group[0]["effect"] != "copy"
                or group[0].get("appearance") != "source"
                for group in groups
            ):
                _fail(
                    path,
                    f"{where} vector groups must be complete displayed source values",
                )
        elif role == "destination-after":
            if any(
                len(group) != 2
                or group[0]["effect"] not in {"copy", "zero"}
                or group[0].get("appearance")
                != ("predicate-result" if group[0]["effect"] == "copy" else "zero")
                or group[1]["effect"] != "zero"
                or group[1].get("appearance") != "zero"
                for group in groups
            ):
                _fail(
                    path,
                    f"{where} must explicitly classify significant results and cleared bits",
                )
        else:
            significant_appearance = "old" if role == "destination-before" else "source"
            if any(
                len(group) != 2
                or group[0]["effect"] != "copy"
                or group[0].get("appearance") != significant_appearance
                or group[1]["effect"] != "ignored"
                or group[1].get("appearance") != "dont-care"
                for group in groups
            ):
                _fail(
                    path,
                    f"{where} must explicitly classify source bits that contribute "
                    "and source bits that do not contribute",
                )
        rows.append(
            {
                "id": row_id,
                "label": _string(row["label"], path, where + ".label"),
                "role": role,
                "storage": storage,
                "element_bits": 16,
                "groups": groups,
            }
        )
    result_rows = [row for row in rows if row["role"] == "destination-after"]
    if len(result_rows) != 1 or result_rows[0]["storage"] != "predicate":
        _fail(
            path, "predicate-lane-transfer requires one predicate destination-after row"
        )
    result_row = result_rows[0]
    rows_by_id = {row["id"]: row for row in rows}
    edges: list[dict[str, Any]] = []
    targets: set[tuple[int, int, str]] = set()
    for index, value in enumerate(_list(raw["edges"], path, "edges")):
        where = f"edges[{index}]"
        edge = _mapping(value, path, where)
        _check_keys(
            edge,
            path,
            where,
            {
                "from_row",
                "from_group",
                "from_cell",
                "to_row",
                "to_group",
                "to_cell",
                "display",
            },
        )
        source_id = _string(edge["from_row"], path, where + ".from_row")
        target_id = _string(edge["to_row"], path, where + ".to_row")
        if source_id not in rows_by_id or target_id != result_row["id"]:
            _fail(path, f"{where} has an invalid row reference")
        source_row = rows_by_id[source_id]
        if source_row["role"] not in {"source", "destination-before"}:
            _fail(path, f"{where} source must be an authored input row")
        source_group = _integer(edge["from_group"], path, where + ".from_group")
        source_cell = _integer(edge["from_cell"], path, where + ".from_cell")
        target_group = _integer(edge["to_group"], path, where + ".to_group")
        target_cell = _integer(edge["to_cell"], path, where + ".to_cell")
        if not 0 <= source_group < len(source_row["groups"]):
            _fail(path, f"{where} source group is out of bounds")
        if not 0 <= source_cell < len(source_row["groups"][source_group]):
            _fail(path, f"{where} source cell is out of bounds")
        if not 0 <= target_group < len(result_row["groups"]):
            _fail(path, f"{where} target group is out of bounds")
        if not 0 <= target_cell < len(result_row["groups"][target_group]):
            _fail(path, f"{where} target cell is out of bounds")
        if source_row["groups"][source_group][source_cell]["effect"] != "copy":
            _fail(path, f"{where} source cell must be a displayed source value")
        if result_row["groups"][target_group][target_cell]["effect"] != "copy":
            _fail(path, f"{where} target cell must be a copied significant result")
        display = _string(edge["display"], path, where + ".display")
        if display not in PREDICATE_LANE_EDGE_DISPLAYS:
            _fail(path, f"{where}.display is not a predicate-lane connection")
        expected_storage = "vector" if display == "control" else "predicate"
        if source_row["storage"] != expected_storage:
            _fail(path, f"{where} {display} source must use {expected_storage} storage")
        target = (target_group, target_cell, display)
        if target in targets:
            _fail(path, f"{where} duplicates a displayed result connection")
        targets.add(target)
        edges.append(edge)
    copied_targets = {
        (group_index, cell_index)
        for group_index, group in enumerate(result_row["groups"])
        for cell_index, cell in enumerate(group)
        if cell["effect"] == "copy"
    }
    transfer_targets = {
        (edge["to_group"], edge["to_cell"])
        for edge in edges
        if edge["display"] == "transfer"
    }
    if transfer_targets != copied_targets:
        _fail(
            path,
            "predicate-lane-transfer transfers must cover exactly the copied result cells",
        )
    return PredicateLaneTransferExample(scalable, tuple(rows), tuple(edges))


def _decode_finite_lane_row(
    value: Any,
    path: Path,
    where: str,
    *,
    roles: frozenset[str],
    additional_appearances: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    row = _mapping(value, path, where)
    _check_keys(row, path, where, {"id", "label", "role", "element_bits", "cells"})
    role = _string(row["role"], path, where + ".role")
    if role not in roles:
        _fail(path, f"{where}.role is not valid for this finite variant")
    element_bits = _integer(row["element_bits"], path, where + ".element_bits")
    if element_bits not in {8, 16, 32, 64}:
        _fail(path, f"{where}.element_bits must be 8, 16, 32, or 64")
    cells = tuple(
        _cell(
            cell,
            path,
            f"{where}.cells[{index}]",
            additional_appearances=additional_appearances,
        )
        for index, cell in enumerate(_list(row["cells"], path, where + ".cells"))
    )
    if len(cells) != VISIBLE_BYTES * 8 // element_bits:
        _fail(path, f"{where} must cover the complete fixed view")
    return {
        "id": _string(row["id"], path, where + ".id"),
        "label": _string(row["label"], path, where + ".label"),
        "role": role,
        "element_bits": element_bits,
        "cells": cells,
    }


def _validate_finite_predicate_cells(
    cells: tuple[dict[str, Any], ...],
    active_lanes: set[int],
    element_bytes: int,
    path: Path,
    where: str,
) -> None:
    for byte_index, cell in enumerate(cells):
        lane = byte_index // element_bytes
        significant = byte_index % element_bytes == 0
        if significant and lane in active_lanes:
            expected = ("1", "set", "predicate-on")
            classification = f"active lane {lane}"
        elif significant:
            expected = ("0", "clear", "predicate-off")
            classification = f"inactive lane {lane}"
        else:
            expected = ("x", "ignored", "dont-care")
            classification = f"byte of lane {lane} that does not contribute"
        actual = (cell["value"], cell["effect"], cell.get("appearance"))
        if actual != expected:
            _fail(path, f"{where}.cells[{byte_index}] does not match {classification}")


def _decode_scalar_bridge(
    raw: dict[str, Any], path: Path, scalable: bool
) -> VectorExample:
    if not scalable:
        _fail(path, "scalar-vector-transfer requires scalable continuation")
    _check_keys(
        raw,
        path,
        "scalar-vector-transfer root",
        {"kind", "view", "rows", "scalars", "connections"},
    )
    raw_rows = _list(raw["rows"], path, "rows")
    if len(raw_rows) != 1:
        _fail(path, "scalar-vector-transfer requires exactly one authored vector row")
    row = _decode_finite_lane_row(
        raw_rows[0],
        path,
        "rows[0]",
        roles=frozenset({"source", "destination-after"}),
        additional_appearances=frozenset({"selected-source"}),
    )

    scalars: list[dict[str, str]] = []
    scalar_ids: set[str] = set()
    for index, value in enumerate(_list(raw["scalars"], path, "scalars")):
        where = f"scalars[{index}]"
        scalar = _mapping(value, path, where)
        _check_keys(scalar, path, where, {"id", "label", "role", "value"})
        scalar_id = _string(scalar["id"], path, where + ".id")
        if scalar_id in scalar_ids:
            _fail(path, f"duplicate scalar ID {scalar_id!r}")
        scalar_ids.add(scalar_id)
        role = _string(scalar["role"], path, where + ".role")
        if role not in {"source", "index", "destination"}:
            _fail(path, f"{where}.role is invalid for scalar-vector-transfer")
        scalars.append(
            {
                "id": scalar_id,
                "label": _string(scalar["label"], path, where + ".label"),
                "role": role,
                "value": _string(scalar["value"], path, where + ".value"),
            }
        )
    if len({scalar["role"] for scalar in scalars}) != len(scalars):
        _fail(path, "scalar-vector-transfer scalar roles must be unique")

    connections: list[dict[str, Any]] = []
    targets: set[tuple[str, int] | tuple[str, str]] = set()
    for index, value in enumerate(_list(raw["connections"], path, "connections")):
        where = f"connections[{index}]"
        item = _mapping(value, path, where)
        _check_keys(
            item,
            path,
            where,
            {"from_kind", "from_id", "to_kind", "to_id", "display"},
            {"from_cell", "to_cell"},
        )
        from_kind = _string(item["from_kind"], path, where + ".from_kind")
        to_kind = _string(item["to_kind"], path, where + ".to_kind")
        from_id = _string(item["from_id"], path, where + ".from_id")
        to_id = _string(item["to_id"], path, where + ".to_id")
        display = _string(item["display"], path, where + ".display")
        if from_kind not in {"row", "scalar"} or to_kind not in {"row", "scalar"}:
            _fail(path, f"{where} endpoint kind must be row or scalar")
        if from_kind == "row":
            if from_id != row["id"] or "from_cell" not in item:
                _fail(path, f"{where} has an invalid row source")
            from_cell = _integer(item["from_cell"], path, where + ".from_cell")
            if not 0 <= from_cell < len(row["cells"]):
                _fail(path, f"{where} source cell is out of bounds")
        else:
            if from_id not in scalar_ids or "from_cell" in item:
                _fail(path, f"{where} has an invalid scalar source")
            from_cell = None
        if to_kind == "row":
            if to_id != row["id"] or "to_cell" not in item:
                _fail(path, f"{where} has an invalid row target")
            to_cell = _integer(item["to_cell"], path, where + ".to_cell")
            if not 0 <= to_cell < len(row["cells"]):
                _fail(path, f"{where} target cell is out of bounds")
            target: tuple[str, int] | tuple[str, str] = (to_id, to_cell)
        else:
            if to_id not in scalar_ids or "to_cell" in item:
                _fail(path, f"{where} has an invalid scalar target")
            to_cell = None
            target = ("scalar", to_id)
        if target in targets and display != "control":
            _fail(path, f"{where} duplicates a displayed transfer target")
        targets.add(target)
        if display not in {"transfer", "control"}:
            _fail(path, f"{where}.display must be transfer or control")
        from_role = (
            row["role"]
            if from_kind == "row"
            else next(scalar["role"] for scalar in scalars if scalar["id"] == from_id)
        )
        if display == "control" and from_role != "index":
            _fail(path, f"{where} control source must be the scalar index")
        if display == "transfer" and from_role == "index":
            _fail(path, f"{where} scalar index cannot be a transfer source")
        decoded = {
            "from_kind": from_kind,
            "from_id": from_id,
            "to_kind": to_kind,
            "to_id": to_id,
            "display": display,
        }
        if from_cell is not None:
            decoded["from_cell"] = from_cell
        if to_cell is not None:
            decoded["to_cell"] = to_cell
        connections.append(decoded)
    scalars_by_role = {scalar["role"]: scalar for scalar in scalars}
    transfers = [item for item in connections if item["display"] == "transfer"]
    controls = [item for item in connections if item["display"] == "control"]
    selected_cells = {
        index
        for index, cell in enumerate(row["cells"])
        if cell.get("appearance") == "selected-source"
    }
    if "index" in scalars_by_role:
        if len(controls) != 1:
            _fail(
                path,
                "scalar-vector-transfer indexed forms require exactly one index control connection",
            )
        control = controls[0]
        control_index = connections.index(control)
        if (
            control["from_kind"] != "scalar"
            or control["from_id"] != scalars_by_role["index"]["id"]
            or control["to_kind"] != "row"
        ):
            _fail(
                path,
                f"connections[{control_index}] must connect the scalar index to the vector row",
            )
        controlled_lane = control["to_cell"]
    else:
        if controls:
            _fail(
                path,
                "scalar-vector-transfer unindexed broadcast cannot contain control connections",
            )
        controlled_lane = None

    if row["role"] == "source":
        if set(scalars_by_role) != {"index", "destination"}:
            _fail(
                path,
                "scalar-vector-transfer extraction requires one index and one scalar destination",
            )
        if len(selected_cells) != 1:
            _fail(
                path,
                "scalar-vector-transfer extraction requires exactly one selected source cell",
            )
        selected_lane = next(iter(selected_cells))
        if any(
            (cell["effect"], cell.get("appearance"))
            != ("copy", "selected-source" if index == selected_lane else "source")
            for index, cell in enumerate(row["cells"])
        ):
            _fail(
                path,
                "scalar-vector-transfer extraction source cells must display one selected source lane",
            )
        if len(transfers) != 1:
            _fail(
                path,
                "scalar-vector-transfer extraction requires exactly one displayed transfer",
            )
        transfer = transfers[0]
        transfer_index = connections.index(transfer)
        if (
            transfer["from_kind"] != "row"
            or transfer.get("from_cell") != selected_lane
            or transfer["to_kind"] != "scalar"
            or transfer["to_id"] != scalars_by_role["destination"]["id"]
        ):
            _fail(
                path,
                f"connections[{transfer_index}] must transfer the selected source lane to the scalar destination",
            )
        if controlled_lane != selected_lane:
            _fail(
                path,
                f"connections[{control_index}].to_cell must select the transferred source lane",
            )
    elif "index" in scalars_by_role:
        if set(scalars_by_role) != {"source", "index"}:
            _fail(
                path,
                "scalar-vector-transfer insertion requires one scalar source and one index",
            )
        if len(selected_cells) != 1:
            _fail(
                path,
                "scalar-vector-transfer insertion requires exactly one selected result cell",
            )
        selected_lane = next(iter(selected_cells))
        if any(
            (cell["effect"], cell.get("appearance"))
            != (
                ("copy", "selected-source")
                if index == selected_lane
                else ("preserve", "old")
            )
            for index, cell in enumerate(row["cells"])
        ):
            _fail(
                path,
                "scalar-vector-transfer insertion must preserve every unselected result lane",
            )
        if len(transfers) != 1:
            _fail(
                path,
                "scalar-vector-transfer insertion requires exactly one displayed transfer",
            )
        transfer = transfers[0]
        transfer_index = connections.index(transfer)
        if (
            transfer["from_kind"] != "scalar"
            or transfer["from_id"] != scalars_by_role["source"]["id"]
            or transfer["to_kind"] != "row"
            or transfer.get("to_cell") != selected_lane
        ):
            _fail(
                path,
                f"connections[{transfer_index}] must transfer the scalar source to the selected result lane",
            )
        if controlled_lane != selected_lane:
            _fail(
                path,
                f"connections[{control_index}].to_cell must select the transferred result lane",
            )
    else:
        if set(scalars_by_role) != {"source"}:
            _fail(
                path,
                "scalar-vector-transfer broadcast requires exactly one scalar source",
            )
        if selected_cells:
            _fail(
                path,
                "scalar-vector-transfer broadcast cannot contain a selected result lane",
            )
        if any(
            (cell["effect"], cell.get("appearance")) != ("copy", "source")
            for cell in row["cells"]
        ):
            _fail(
                path,
                "scalar-vector-transfer broadcast result cells must all display the scalar source",
            )
        expected_transfers = {
            (scalars_by_role["source"]["id"], index)
            for index in range(len(row["cells"]))
        }
        actual_transfers = {
            (item["from_id"], item.get("to_cell"))
            for item in transfers
            if item["from_kind"] == "scalar" and item["to_kind"] == "row"
        }
        if (
            len(transfers) != len(expected_transfers)
            or actual_transfers != expected_transfers
        ):
            _fail(
                path,
                "scalar-vector-transfer broadcast transfers must cover every result lane exactly once",
            )
    return ScalarVectorTransferExample(
        True,
        (row,),
        tuple(connections),
        tuple(scalars),
    )


def _decode_memory_lanes(
    raw: dict[str, Any], path: Path, scalable: bool
) -> VectorExample:
    if not scalable:
        _fail(path, "predicated-vector-load requires scalable continuation")
    _check_keys(
        raw,
        path,
        "predicated-vector-load root",
        {
            "kind",
            "view",
            "element_bits",
            "base",
            "address",
            "memory",
            "result",
            "predicate",
            "connections",
        },
    )
    element_bits = _integer(raw["element_bits"], path, "element_bits")
    if element_bits not in {8, 16, 32, 64}:
        _fail(path, "predicated-vector-load element_bits must be 8, 16, 32, or 64")
    lane_count = VISIBLE_BYTES * 8 // element_bits
    base = _mapping(raw["base"], path, "base")
    _check_keys(base, path, "base", {"label", "value"})
    base_data = {
        "label": _string(base["label"], path, "base.label"),
        "value": _string(base["value"], path, "base.value"),
    }

    def decode_cells(section_name: str) -> tuple[dict[str, str], ...]:
        section = _mapping(raw[section_name], path, section_name)
        _check_keys(section, path, section_name, {"label", "cells"})
        cells = tuple(
            _cell(
                cell,
                path,
                f"{section_name}.cells[{index}]",
                additional_appearances=(
                    frozenset({"no-access"})
                    if section_name in {"address", "memory"}
                    else frozenset()
                ),
            )
            for index, cell in enumerate(
                _list(section["cells"], path, section_name + ".cells")
            )
        )
        if len(cells) != lane_count:
            _fail(path, f"{section_name} must cover every visible memory lane")
        return cells

    address_section = _mapping(raw["address"], path, "address")
    memory_section = _mapping(raw["memory"], path, "memory")
    result_section = _mapping(raw["result"], path, "result")
    address_cells = decode_cells("address")
    memory_cells = decode_cells("memory")
    result_cells = decode_cells("result")
    active = {
        index for index, cell in enumerate(memory_cells) if cell["effect"] == "copy"
    }
    for section_name, cells, active_state, inactive_state in (
        ("address", address_cells, ("copy", "source"), ("ignored", "no-access")),
        ("memory", memory_cells, ("copy", "source"), ("ignored", "no-access")),
        ("result", result_cells, ("copy", "source"), ("preserve", "old")),
    ):
        for lane, cell in enumerate(cells):
            expected = active_state if lane in active else inactive_state
            if (cell["effect"], cell.get("appearance")) != expected:
                _fail(
                    path,
                    f"{section_name}.cells[{lane}] does not match active memory lanes",
                )

    predicate = _mapping(raw["predicate"], path, "predicate")
    _check_keys(predicate, path, "predicate", {"label", "element_bits", "cells"})
    if predicate["element_bits"] != 8:
        _fail(path, "predicated-vector-load predicate uses byte positions")
    predicate_cells = tuple(
        _cell(cell, path, f"predicate.cells[{index}]")
        for index, cell in enumerate(_list(predicate["cells"], path, "predicate.cells"))
    )
    if len(predicate_cells) != VISIBLE_BYTES:
        _fail(path, "predicated-vector-load predicate must cover all 16 byte positions")
    _validate_finite_predicate_cells(
        predicate_cells, active, element_bits // 8, path, "predicate"
    )

    rows = (
        {
            "id": "address",
            "label": _string(address_section["label"], path, "address.label"),
            "role": "address",
            "element_bits": element_bits,
            "cells": address_cells,
        },
        {
            "id": "memory",
            "label": _string(memory_section["label"], path, "memory.label"),
            "role": "source",
            "element_bits": element_bits,
            "cells": memory_cells,
        },
        {
            "id": "result",
            "label": _string(result_section["label"], path, "result.label"),
            "role": "destination-after",
            "element_bits": element_bits,
            "cells": result_cells,
        },
        {
            "id": "predicate",
            "label": _string(predicate["label"], path, "predicate.label"),
            "role": "predicate",
            "element_bits": 8,
            "cells": predicate_cells,
        },
    )
    rows_by_id = {row["id"]: row for row in rows}
    connections: list[dict[str, Any]] = []
    targets: set[tuple[str, int]] = set()
    for index, value in enumerate(_list(raw["connections"], path, "connections")):
        where = f"connections[{index}]"
        item = _mapping(value, path, where)
        _check_keys(
            item, path, where, {"from_row", "from_cell", "to_row", "to_cell", "display"}
        )
        from_row = _string(item["from_row"], path, where + ".from_row")
        to_row = _string(item["to_row"], path, where + ".to_row")
        if from_row not in rows_by_id or to_row not in rows_by_id:
            _fail(path, f"{where} has an invalid row reference")
        from_cell = _integer(item["from_cell"], path, where + ".from_cell")
        to_cell = _integer(item["to_cell"], path, where + ".to_cell")
        if not 0 <= from_cell < len(rows_by_id[from_row]["cells"]):
            _fail(path, f"{where} source cell is out of bounds")
        if not 0 <= to_cell < len(rows_by_id[to_row]["cells"]):
            _fail(path, f"{where} target cell is out of bounds")
        display = _string(item["display"], path, where + ".display")
        if (from_row, to_row, display) not in {
            ("address", "memory", "control"),
            ("memory", "result", "transfer"),
        }:
            _fail(
                path, f"{where} is not an address-control or memory-transfer connection"
            )
        if from_cell != to_cell:
            _fail(path, f"{where} must connect corresponding memory lanes")
        target = (to_row, to_cell)
        if target in targets:
            _fail(path, f"{where} duplicates a displayed lane target")
        targets.add(target)
        connections.append(
            {
                "from_row": from_row,
                "from_cell": from_cell,
                "to_row": to_row,
                "to_cell": to_cell,
                "display": display,
            }
        )
    controls = {item["to_cell"] for item in connections if item["display"] == "control"}
    transfers = {
        item["to_cell"] for item in connections if item["display"] == "transfer"
    }
    if controls != active or transfers != active:
        _fail(
            path,
            "predicated-vector-load connections must cover exactly every active access",
        )
    if any(
        (index in active) != (result_cells[index]["effect"] == "copy")
        for index in range(lane_count)
    ):
        _fail(
            path,
            "predicated-vector-load result classifications must match active accesses",
        )
    return PredicatedVectorLoadExample(True, rows, tuple(connections), base_data)


def _decode_reduction(raw: dict[str, Any], path: Path, scalable: bool) -> VectorExample:
    if not scalable:
        _fail(path, "reduction requires scalable continuation")
    _check_keys(
        raw,
        path,
        "reduction root",
        {
            "kind",
            "view",
            "element_bits",
            "predicate",
            "source",
            "selected",
            "fold",
            "result",
        },
    )
    element_bits = _integer(raw["element_bits"], path, "element_bits")
    if element_bits not in {8, 16, 32, 64}:
        _fail(path, "reduction element_bits must be 8, 16, 32, or 64")
    lane_count = VISIBLE_BYTES * 8 // element_bits
    source = _mapping(raw["source"], path, "source")
    _check_keys(source, path, "source", {"label", "cells"})
    source_cells = tuple(
        _cell(cell, path, f"source.cells[{index}]")
        for index, cell in enumerate(_list(source["cells"], path, "source.cells"))
    )
    if len(source_cells) != lane_count:
        _fail(path, "reduction source must cover every visible lane")
    selected_values = _list(raw["selected"], path, "selected")
    selected = tuple(
        _integer(value, path, f"selected[{index}]")
        for index, value in enumerate(selected_values)
    )
    if len(set(selected)) != len(selected) or any(
        not 0 <= lane < lane_count for lane in selected
    ):
        _fail(path, "reduction selected lanes must be unique visible lane indices")
    if selected != tuple(sorted(selected)):
        _fail(
            path,
            "reduction selected must list visible lanes in increasing logical-lane order",
        )
    if {
        index for index, cell in enumerate(source_cells) if cell["effect"] == "copy"
    } != set(selected):
        _fail(path, "reduction selected lanes must equal the displayed source inputs")
    for lane, cell in enumerate(source_cells):
        expected = ("copy", "source") if lane in selected else ("ignored", "discarded")
        if (cell["effect"], cell.get("appearance")) != expected:
            _fail(path, f"source.cells[{lane}] does not match selected lanes")

    predicate = _mapping(raw["predicate"], path, "predicate")
    _check_keys(predicate, path, "predicate", {"label", "element_bits", "cells"})
    if predicate["element_bits"] != 8:
        _fail(path, "reduction predicate uses byte positions")
    predicate_cells = tuple(
        _cell(cell, path, f"predicate.cells[{index}]")
        for index, cell in enumerate(_list(predicate["cells"], path, "predicate.cells"))
    )
    if len(predicate_cells) != VISIBLE_BYTES:
        _fail(path, "reduction predicate must cover all 16 byte positions")
    _validate_finite_predicate_cells(
        predicate_cells, set(selected), element_bits // 8, path, "predicate"
    )

    fold = _mapping(raw["fold"], path, "fold")
    _check_keys(fold, path, "fold", {"label", "terms", "continuation"})
    terms = tuple(
        _string(term, path, f"fold.terms[{index}]")
        for index, term in enumerate(_list(fold["terms"], path, "fold.terms"))
    )
    if not isinstance(fold["continuation"], bool):
        _fail(path, "fold.continuation must be a boolean")
    if tuple(source_cells[lane]["value"] for lane in selected) != terms[1:]:
        _fail(
            path, "fold terms after the identity must equal the selected source values"
        )
    result = _mapping(raw["result"], path, "result")
    _check_keys(result, path, "result", {"label", "value"})
    rows = (
        {
            "id": "predicate",
            "label": _string(predicate["label"], path, "predicate.label"),
            "role": "predicate",
            "element_bits": 8,
            "cells": predicate_cells,
        },
        {
            "id": "source",
            "label": _string(source["label"], path, "source.label"),
            "role": "source",
            "element_bits": element_bits,
            "cells": source_cells,
        },
    )
    return PredicatedVectorReductionExample(
        True,
        rows,
        (),
        selected,
        _string(fold["label"], path, "fold.label"),
        terms,
        fold["continuation"],
        _string(result["label"], path, "result.label"),
        _string(result["value"], path, "result.value"),
    )


def _decode_conversion_map(
    raw: dict[str, Any], path: Path, scalable: bool
) -> VectorExample:
    if not scalable:
        _fail(path, "floating-point-width-conversion requires scalable continuation")
    _check_keys(
        raw,
        path,
        "floating-point-width-conversion root",
        {
            "kind",
            "view",
            "container_bits",
            "source",
            "result",
            "predicate",
            "connections",
        },
    )
    container_bits = _integer(raw["container_bits"], path, "container_bits")
    if container_bits not in {32, 64}:
        _fail(path, "floating-point-width-conversion container_bits must be 32 or 64")
    container_count = VISIBLE_BYTES * 8 // container_bits
    rows: list[dict[str, Any]] = []
    for section_name, role in (("source", "source"), ("result", "destination-after")):
        section = _mapping(raw[section_name], path, section_name)
        _check_keys(
            section, path, section_name, {"label", "element_bits", "containers"}
        )
        element_bits = _integer(
            section["element_bits"], path, section_name + ".element_bits"
        )
        if element_bits not in {16, 32, 64} or element_bits > container_bits:
            _fail(
                path, f"{section_name}.element_bits is not a supported conversion width"
            )
        if section_name == "result" and element_bits == rows[0]["element_bits"]:
            _fail(
                path,
                "floating-point-width-conversion source and result widths must differ",
            )
        containers = tuple(
            _decode_width_container(
                container,
                path,
                f"{section_name}.containers[{index}]",
                container_bits,
            )
            for index, container in enumerate(
                _list(section["containers"], path, section_name + ".containers")
            )
        )
        if len(containers) != container_count:
            _fail(
                path, f"{section_name} must declare all visible conversion containers"
            )
        for container_index, container in enumerate(containers):
            copied = [
                (cell_index, cell)
                for cell_index, cell in enumerate(container)
                if cell["effect"] == "copy"
            ]
            if section_name == "source":
                if len(copied) != 1:
                    _fail(
                        path,
                        f"source.containers[{container_index}] must contain exactly one source element",
                    )
                for cell_index, cell in enumerate(container):
                    expected_appearance = (
                        "source" if cell["effect"] == "copy" else "discarded"
                    )
                    if (
                        cell["effect"] not in {"copy", "ignored"}
                        or cell.get("appearance") != expected_appearance
                    ):
                        _fail(
                            path,
                            f"source.containers[{container_index}].cells[{cell_index}] must display source or discarded fields",
                        )
            else:
                if len(copied) > 1:
                    _fail(
                        path,
                        f"result.containers[{container_index}] may contain at most one converted element",
                    )
                for cell_index, cell in enumerate(container):
                    expected_appearance = (
                        "source" if cell["effect"] == "copy" else "old"
                    )
                    if (
                        cell["effect"] not in {"copy", "preserve"}
                        or cell.get("appearance") != expected_appearance
                    ):
                        _fail(
                            path,
                            f"result.containers[{container_index}].cells[{cell_index}] must display converted or preserved fields",
                        )
            for cell_index, cell in copied:
                if cell["bits"] != element_bits:
                    _fail(
                        path,
                        f"{section_name}.containers[{container_index}].cells[{cell_index}].bits must equal {section_name}.element_bits",
                    )
        rows.append(
            {
                "id": section_name,
                "label": _string(section["label"], path, section_name + ".label"),
                "role": role,
                "element_bits": element_bits,
                "container_bits": container_bits,
                "containers": containers,
            }
        )
    predicate = _mapping(raw["predicate"], path, "predicate")
    _check_keys(predicate, path, "predicate", {"label", "element_bits", "cells"})
    if predicate["element_bits"] != 8:
        _fail(path, "floating-point-width-conversion predicate uses byte positions")
    predicate_cells = tuple(
        _cell(cell, path, f"predicate.cells[{index}]")
        for index, cell in enumerate(_list(predicate["cells"], path, "predicate.cells"))
    )
    if len(predicate_cells) != VISIBLE_BYTES:
        _fail(
            path,
            "floating-point-width-conversion predicate must cover all 16 byte positions",
        )
    active_containers = {
        container_index
        for container_index, container in enumerate(rows[1]["containers"])
        if any(cell["effect"] == "copy" for cell in container)
    }
    _validate_finite_predicate_cells(
        predicate_cells, active_containers, container_bits // 8, path, "predicate"
    )
    rows.append(
        {
            "id": "predicate",
            "label": _string(predicate["label"], path, "predicate.label"),
            "role": "predicate",
            "element_bits": 8,
            "cells": predicate_cells,
        }
    )
    rows_by_id = {row["id"]: row for row in rows}
    edges = _decode_detailed_width_edges(
        raw["connections"], path, rows_by_id, rows_by_id["result"]
    )
    copied_results = {
        (container_index, cell_index)
        for container_index, container in enumerate(rows_by_id["result"]["containers"])
        for cell_index, cell in enumerate(container)
        if cell["effect"] == "copy"
    }
    edge_targets = {(edge["to_container"], edge["to_cell"]) for edge in edges}
    if copied_results != edge_targets:
        _fail(
            path,
            "floating-point-width-conversion connections must cover exactly the converted result fields",
        )
    return FloatingPointWidthConversionExample(True, tuple(rows), edges)


def _decode(raw: dict[str, Any], path: Path) -> VectorExample:
    scalable = _view(raw, path)
    kind = raw.get("kind")
    if kind == "vector-lane-transfer":
        return _decode_lane_map(raw, path, scalable)
    if kind == "integer-width-conversion":
        return _decode_width_map(raw, path, scalable)
    if kind == "predicate-range-generation":
        return _decode_predicate_range(raw, path, scalable)
    if kind == "predicate-width-conversion":
        return _decode_predicate_width(raw, path, scalable)
    if kind == "predicate-lane-transfer":
        return _decode_predicate_lane_map(raw, path, scalable)
    if kind == "scalar-vector-transfer":
        return _decode_scalar_bridge(raw, path, scalable)
    if kind == "predicated-vector-load":
        return _decode_memory_lanes(raw, path, scalable)
    if kind == "predicated-vector-reduction":
        return _decode_reduction(raw, path, scalable)
    if kind == "floating-point-width-conversion":
        return _decode_conversion_map(raw, path, scalable)
    _fail(
        path,
        "kind must be one of vector-lane-transfer, integer-width-conversion, predicate-range-generation, "
        "predicate-width-conversion, predicate-lane-transfer, scalar-vector-transfer, predicated-vector-load, "
        "predicated-vector-reduction, floating-point-width-conversion",
    )


def load_vector_example(path: Path, schema: str | Path | None = None) -> VectorExample:
    """Load one standalone member, primarily for focused decoder tests."""

    source = Path(path).resolve()
    raw = load_schema_yaml(source, schema) if schema is not None else load_yaml(source)
    payload = {
        key: value
        for key, value in raw.items()
        if key not in {"id", "caption", "alt_text"}
    }
    return _decode(payload, source)


def load_vector_diagrams(*, owner: str, mnemonic: str, instruction: Reference[object], root: str | Path, schema) -> VectorDiagramCatalog:
    collection_root = Path(root).resolve()
    diagrams = {}
    if not collection_root.exists():
        return VectorDiagramCatalog(
            owner=owner,
            instruction=instruction,
            root=collection_root,
            inventory=None,
            diagrams=ReferenceIndex(diagrams),
        )
    inventory = require_exact(inspect_inventory(owner=owner, kind="vector-diagram", source=collection_root / "diagrams.yaml", root=collection_root, key="diagrams", name_pattern=r"[a-z][a-z0-9-]*", exact_keys=True))
    for diagram_id in inventory.declared:
        member_root = collection_root / diagram_id
        member_files = tuple(
            sorted(
                path.name
                for path in member_root.iterdir()
                if not path.name.startswith(".")
            )
        )
        if member_files != ("diagram.yaml",):
            raise ValueError(
                f"{member_root}: vector diagram member files must be exactly "
                "('diagram.yaml',)"
            )
        source = member_root / "diagram.yaml"
        raw = load_schema_yaml(source, schema)
        reference: Reference[VectorDiagram] = Reference(
            owner,
            ("instructions", mnemonic, "diagrams"),
            diagram_id,
        )
        payload = {
            key: value
            for key, value in raw.items()
            if key not in {"caption", "alt_text"}
        }
        register_reference(
            diagrams,
            reference,
            VectorDiagram(
                reference=reference,
                source=source,
                id=diagram_id,
                caption=raw["caption"],
                alt_text=raw["alt_text"],
                example=_decode(payload, source),
            ),
        )
    return VectorDiagramCatalog(
        owner=owner,
        instruction=instruction,
        root=collection_root,
        inventory=inventory,
        diagrams=ReferenceIndex(diagrams),
    )
