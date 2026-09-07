"""ISA manual drawing construction from resolved engine projections."""

from __future__ import annotations
from typing import Any
from artifacts._shared.latex import tex_escape
from engine.isa.vector_examples import (
    CountedPredicateRangeExample,
    FloatingPointWidthConversionExample,
    IntegerWidthConversionExample,
    PredicatedVectorLoadExample,
    PredicatedVectorReductionExample,
    PredicateLaneTransferExample,
    PredicateRangeExample,
    PredicateWidthConversionExample,
    ScalarVectorTransferExample,
    StatefulPredicateRangeExample,
    VectorDiagram,
    VectorExample,
    VectorLaneTransferExample,
)


def _layout(example: VectorExample) -> tuple[str, str, str]:
    """Return the established page reservation and TikZ unit scales."""

    has_predicate_row = any(row["role"] == "predicate" for row in example.rows)
    if isinstance(example, VectorLaneTransferExample) and has_predicate_row:
        needspace = 2.21 + 0.48 * (len(example.rows) - 2)
        if not example.scalable:
            needspace += 0.22
        return f"{needspace:.2f}in", ".76cm", ".70cm"
    if isinstance(example, PredicateWidthConversionExample):
        return "3.20in", ".76cm", ".70cm"
    if isinstance(example, PredicateLaneTransferExample):
        if len(example.rows) == 3:
            needspace = "2.69in" if example.scalable else "2.91in"
        else:
            needspace = "2.60in"
        return needspace, ".76cm", ".70cm"
    if isinstance(example, IntegerWidthConversionExample):
        return "3.29in", ".76cm", ".70cm"
    if isinstance(
        example, (StatefulPredicateRangeExample, CountedPredicateRangeExample)
    ):
        needspace = (
            "3.45in" if isinstance(example, StatefulPredicateRangeExample) else "3.25in"
        )
        return needspace, ".76cm", ".70cm"
    if isinstance(example, ScalarVectorTransferExample):
        needspace = "3.25in" if len(example.scalars) > 1 else "2.50in"
        return needspace, ".76cm", ".70cm"
    if isinstance(example, PredicatedVectorLoadExample):
        return "4.25in", ".76cm", ".70cm"
    if isinstance(example, PredicatedVectorReductionExample):
        return "4.05in", ".76cm", ".70cm"
    if isinstance(example, FloatingPointWidthConversionExample):
        return "3.29in", ".76cm", ".70cm"
    if isinstance(example, VectorLaneTransferExample):
        return "2.50in", "0.72cm", "0.66cm"
    raise TypeError(f"unsupported vector example {type(example).__name__}")


def render_vector_diagram(diagram: VectorDiagram) -> str:
    needspace, x_scale, y_scale = _layout(diagram.example)
    return "\n".join(
        (
            rf"\begin{{BedrockVectorExample}}{{{needspace}}}{{{x_scale}}}"
            rf"{{{y_scale}}}{{{tex_escape(diagram.caption)}}}"
            rf"{{{tex_escape(diagram.alt_text)}}}",
            render_tikz(diagram.example),
            r"\end{BedrockVectorExample}",
        )
    )






def _cell_style(cell: dict[str, str]) -> str:
    appearance = cell.get("appearance")
    if appearance is None:
        return "vectorExample" + cell["effect"].title()
    return (
        "vectorExample"
        + {
            "old": "Old",
            "source": "Source",
            "zero": "Zero",
            "discarded": "Discarded",
            "predicate-on": "PredicateOn",
            "predicate-off": "PredicateOff",
            "dont-care": "DontCare",
            "predicate-result": "PredicateResult",
            "selected-source": "SelectedSource",
            "no-access": "NoAccess",
        }[appearance]
    )


def _cell_geometry(row: dict[str, Any], index: int) -> tuple[float, float]:
    width = 8.0 / len(row["cells"])
    return 8.0 - (index + 1) * width, width


def _width_container_geometry(
    row: dict[str, Any], container_index: int
) -> tuple[float, float]:
    width = 8.0 / len(row["containers"])
    return 8.0 - (container_index + 1) * width, width


def _width_cell_geometry(
    row: dict[str, Any], container_index: int, cell_index: int
) -> tuple[float, float]:
    container_left, container_width = _width_container_geometry(row, container_index)
    cells = row["containers"][container_index]
    preceding_bits = sum(cell["bits"] for cell in cells[:cell_index])
    cell = cells[cell_index]
    return (
        container_left + container_width * preceding_bits / row["container_bits"],
        container_width * cell["bits"] / row["container_bits"],
    )


def _render_detailed_width_tikz(
    example: IntegerWidthConversionExample | FloatingPointWidthConversionExample,
) -> str:
    lines: list[str] = []
    top_y = (len(example.rows) - 2) * 1.10
    y_by_id = {
        row["id"]: top_y - index * 1.10 for index, row in enumerate(example.rows)
    }
    rows_by_id = {row["id"]: row for row in example.rows}
    for edge in example.edges:
        source_row = rows_by_id[edge["from_row"]]
        target_row = rows_by_id[edge["to_row"]]
        source_left, source_width = _width_cell_geometry(
            source_row, edge["from_container"], edge["from_cell"]
        )
        target_left, target_width = _width_cell_geometry(
            target_row, edge["to_container"], edge["to_cell"]
        )
        source_y = y_by_id[source_row["id"]]
        target_y = y_by_id[target_row["id"]]
        if source_y > target_y:
            source_anchor, target_anchor = source_y - 0.02, target_y + 0.68
        else:
            source_anchor, target_anchor = source_y + 0.64, target_y - 0.06
        if edge["display"] == "expansion-guide":
            lines.append(
                rf"\draw[vectorExampleExpansionGuide] "
                rf"({source_left + 0.06:.2f},{source_anchor:.2f}) -- "
                rf"({target_left + 0.06:.2f},{target_anchor:.2f}) "
                rf"({source_left + source_width - 0.06:.2f},{source_anchor:.2f}) -- "
                rf"({target_left + target_width - 0.06:.2f},{target_anchor:.2f});"
            )
        else:
            lines.append(
                rf"\draw[vectorExampleWidthTransferArrow] "
                rf"({source_left + source_width / 2:.2f},{source_anchor:.2f}) -- "
                rf"({target_left + target_width / 2:.2f},{target_anchor:.2f});"
            )
    for row in example.rows:
        y = y_by_id[row["id"]]
        lines.append(
            rf"\node[vectorExampleLabel] at (8.22,{y + 0.31:.2f}) "
            rf"{{{tex_escape(row['label'])}}};"
        )
        if row["role"] == "predicate":
            for index, cell in enumerate(row["cells"]):
                x, width = _cell_geometry(row, index)
                lines.append(
                    rf"\path[{_cell_style(cell)}] ({x:.2f},{y:.2f}) "
                    rf"rectangle ({x + width:.2f},{y + 0.62:.2f});"
                )
                lines.append(
                    rf"\node[vectorExampleCompactText] at "
                    rf"({x + width / 2:.2f},{y + 0.31:.2f}) "
                    rf"{{{tex_escape(cell['value'])}}};"
                )
        else:
            for container_index, container in enumerate(row["containers"]):
                for cell_index, cell in enumerate(container):
                    x, width = _width_cell_geometry(row, container_index, cell_index)
                    lines.append(
                        rf"\path[{_cell_style(cell)}] ({x:.2f},{y:.2f}) "
                        rf"rectangle ({x + width:.2f},{y + 0.62:.2f});"
                    )
                    text_style = (
                        "vectorExampleCellText"
                        if width >= 1.5
                        else "vectorExampleCompactText"
                    )
                    lines.append(
                        rf"\node[{text_style}] at "
                        rf"({x + width / 2:.2f},{y + 0.31:.2f}) "
                        rf"{{{tex_escape(cell['value'])}}};"
                    )
                x, width = _width_container_geometry(row, container_index)
                lines.append(
                    rf"\path[vectorExampleContainer] ({x:.2f},{y:.2f}) "
                    rf"rectangle ({x + width:.2f},{y + 0.62:.2f});"
                )
        lines.append(
            rf"\draw[vectorExampleWidthContinuation] (-2,{y:.2f}) -- (0,{y:.2f});"
            rf"\draw[vectorExampleWidthContinuation] (-2,{y + 0.62:.2f}) -- (0,{y + 0.62:.2f});"
            rf"\node[vectorExampleMuted] at (-1,{y + 0.31:.2f}) {{$\cdots$}};"
        )
    return "\n".join(lines)


def _predicate_width_cell_geometry(
    row: dict[str, Any], container_index: int, cell_index: int
) -> tuple[float, float]:
    container_width = row["element_bits"] / 16
    container_left = 8.0 - (container_index + 1) * container_width
    return container_left + cell_index * 0.5, 0.5


def _render_predicate_width_tikz(example: PredicateWidthConversionExample) -> str:
    lines = [
        r"\node[vectorExampleFixedView] at (4,2.18) {fixed example: VLEN = 16 bytes};"
    ]
    rows_by_id = {row["id"]: row for row in example.rows}
    y_by_id = {"source": 1.4, "result": 0.0}
    for row in example.rows:
        y = y_by_id[row["id"]]
        container_width = row["element_bits"] / 16
        for container_index, container in enumerate(row["containers"]):
            for cell_index, cell in enumerate(container):
                x, width = _predicate_width_cell_geometry(
                    row, container_index, cell_index
                )
                lines.append(
                    rf"\path[{_cell_style(cell)}] ({x:.2f},{y:.2f}) "
                    rf"rectangle ({x + width:.2f},{y + 0.62:.2f});"
                )
                lines.append(
                    rf"\node[vectorExampleCompactText] at "
                    rf"({x + width / 2:.2f},{y + 0.31:.2f}) "
                    rf"{{{tex_escape(cell['value'])}}};"
                )
            container_left = 8.0 - (container_index + 1) * container_width
            lines.append(
                rf"\path[vectorExampleContainer] ({container_left:.2f},{y:.2f}) "
                rf"rectangle ({container_left + container_width:.2f},{y + 0.62:.2f});"
            )
    for edge in example.edges:
        source_row = rows_by_id[edge["from_row"]]
        result_row = rows_by_id[edge["to_row"]]
        source_left, source_width = _predicate_width_cell_geometry(
            source_row, edge["from_container"], edge["from_cell"]
        )
        result_left, result_width = _predicate_width_cell_geometry(
            result_row, edge["to_container"], edge["to_cell"]
        )
        lines.append(
            rf"\draw[vectorExampleWidthTransferArrow] "
            rf"({source_left + source_width / 2:.2f},1.38) -- "
            rf"({result_left + result_width / 2:.2f},0.68);"
        )
    for row in example.rows:
        y = y_by_id[row["id"]]
        lines.append(
            rf"\node[vectorExampleLabel] at (8.22,{y + 0.31:.2f}) "
            rf"{{{tex_escape(row['label'])}}};"
        )
    return "\n".join(lines)


def _predicate_lane_cell_geometry(
    row: dict[str, Any], group_index: int, cell_index: int
) -> tuple[float, float]:
    group = row["groups"][group_index]
    preceding_bits = sum(cell["bits"] for cell in group[:cell_index])
    cell = group[cell_index]
    right = 8.0 - group_index
    return right - (preceding_bits + cell["bits"]) / 16, cell["bits"] / 16


def _render_predicate_lane_map_tikz(example: PredicateLaneTransferExample) -> str:
    lines: list[str] = []
    top_y = (len(example.rows) - 1) * 1.10
    y_by_id = {
        row["id"]: top_y - index * 1.10 for index, row in enumerate(example.rows)
    }
    if not example.scalable:
        lines.append(
            rf"\node[vectorExampleFixedView] at (4,{top_y + 0.78:.2f}) "
            r"{fixed example: VLEN = 16 bytes};"
        )
    for row in example.rows:
        y = y_by_id[row["id"]]
        lines.append(
            rf"\node[vectorExampleLabel] at (8.22,{y + 0.31:.2f}) "
            rf"{{{tex_escape(row['label'])}}};"
        )
        for group_index, group in enumerate(row["groups"]):
            for cell_index, cell in enumerate(group):
                x, width = _predicate_lane_cell_geometry(row, group_index, cell_index)
                lines.append(
                    rf"\path[{_cell_style(cell)}] ({x:.2f},{y:.2f}) "
                    rf"rectangle ({x + width:.2f},{y + 0.62:.2f});"
                )
                lines.append(
                    rf"\node[vectorExampleCompactText] at "
                    rf"({x + width / 2:.2f},{y + 0.31:.2f}) "
                    rf"{{{tex_escape(cell['value'])}}};"
                )
        if example.scalable:
            lines.append(
                rf"\draw[vectorExamplePredicateLaneContinuation] (-2,{y:.2f}) -- (0,{y:.2f});"
                rf"\draw[vectorExamplePredicateLaneContinuation] (-2,{y + 0.62:.2f}) -- (0,{y + 0.62:.2f});"
                rf"\node[vectorExampleMuted] at (-1,{y + 0.31:.2f}) {{$\cdots$}};"
            )
    rows_by_id = {row["id"]: row for row in example.rows}
    result_row = next(row for row in example.rows if row["role"] == "destination-after")
    result_y = y_by_id[result_row["id"]]
    for edge in example.edges:
        source_row = rows_by_id[edge["from_row"]]
        source_y = y_by_id[source_row["id"]]
        source_left, source_width = _predicate_lane_cell_geometry(
            source_row, edge["from_group"], edge["from_cell"]
        )
        target_left, target_width = _predicate_lane_cell_geometry(
            result_row, edge["to_group"], edge["to_cell"]
        )
        if source_y > result_y:
            source_anchor, target_anchor = source_y - 0.02, result_y + 0.68
        else:
            source_anchor, target_anchor = source_y + 0.64, result_y - 0.06
        style = (
            "vectorExamplePredicateControlArrow"
            if edge["display"] == "control"
            else "vectorExampleLaneTransferArrow"
        )
        lines.append(
            rf"\draw[{style}] "
            rf"({source_left + source_width / 2:.2f},{source_anchor:.2f}) -- "
            rf"({target_left + target_width / 2:.2f},{target_anchor:.2f});"
        )
    return "\n".join(lines)


def _render_grouped_predicate_range_tikz(example: PredicateRangeExample) -> str:
    row = example.rows[0]
    start = example.start
    end = example.end
    start_x = 8 - start
    end_x = 0 if end == "lane-count" else 8 - end
    lines: list[str] = []
    if isinstance(example, StatefulPredicateRangeExample):
        for state in example.states:
            anchor_x = start_x if state["anchor"] == "start" else end_x
            gap = 1.35 if len(state["after"]) > 1 else 0.85
            after_x = (
                anchor_x + gap if state["after_side"] == "right" else anchor_x - gap
            )
            before_name = "predicateRange" + state["id"].title() + "Before"
            after_name = "predicateRange" + state["id"].title() + "After"
            lines.append(
                rf"\node[vectorExampleIndex] ({before_name}) at ({anchor_x:.2f},1.95) "
                rf"{{{tex_escape(state['before'])}}};"
            )
            lines.append(
                rf"\node[vectorExampleIndex] ({after_name}) at ({after_x:.2f},1.95) "
                rf"{{{tex_escape(state['after'])}}};"
            )
            start_anchor = "east" if state["after_side"] == "right" else "west"
            end_anchor = "west" if state["after_side"] == "right" else "east"
            lines.append(
                rf"\draw[vectorExampleLaneTransferArrow] ({before_name}.{start_anchor}) -- "
                rf"({after_name}.{end_anchor});"
            )
            lines.append(
                rf"\node[vectorExampleStateLabel] at ({(anchor_x + after_x) / 2:.2f},2.43) "
                rf"{{{tex_escape(state['label'])}}};"
            )
            lines.append(
                rf"\draw[vectorExampleControlArrow] ({before_name}.south) -- "
                rf"({anchor_x:.2f},1.18);"
            )
    else:
        count = example.count
        lines.append(
            rf"\node[vectorExampleIndex] (predicateRangeCount) at ({start_x:.2f},1.95) "
            rf"{{{tex_escape(count['value'])}}};"
        )
        lines.append(
            rf"\node[vectorExampleLabel] at (8.22,1.95) "
            rf"{{{tex_escape(count['label'])}}};"
        )
        lines.append(
            rf"\draw[vectorExampleControlArrow] (predicateRangeCount.south) -- "
            rf"({start_x:.2f},1.18);"
        )
    if end == "lane-count":
        lines.append(r"\draw[vectorExampleRange,densely dashed] (-2,1.12) -- (0,1.12);")
        lines.append(
            rf"\draw[vectorExampleRange] (0,1.12) -- ({start_x:.2f},1.12) -- ({start_x:.2f},0.94);"
        )
    else:
        lines.append(
            rf"\draw[vectorExampleRange] ({end_x:.2f},0.94) -- ({end_x:.2f},1.12) -- "
            rf"({start_x:.2f},1.12) -- ({start_x:.2f},0.94);"
        )
    lines.append(
        rf"\node[vectorExampleRangeLabel] at ({(start_x + end_x) / 2:.2f},1.12) {{active}};"
    )
    lines.append(rf"\node[vectorExampleLabel] at (8.22,.31) {{{tex_escape(row['label'])}}};")
    for group_index, group in enumerate(row["groups"]):
        for cell_index, cell in enumerate(group):
            x, width = _predicate_lane_cell_geometry(row, group_index, cell_index)
            lines.append(
                rf"\path[{_cell_style(cell)}] ({x:.2f},0) rectangle ({x + width:.2f},.62);"
            )
            lines.append(
                rf"\node[vectorExampleCompactText] at ({x + width / 2:.2f},.31) "
                rf"{{{tex_escape(cell['value'])}}};"
            )
        left = 7 - group_index
        lines.append(
            rf"\path[vectorExampleContainer] ({left:.2f},0) rectangle ({left + 1:.2f},.62);"
        )
    lines.append(
        r"\draw[vectorExamplePredicateLaneContinuation] (-2,0) -- (0,0);"
        r"\draw[vectorExamplePredicateLaneContinuation] (-2,.62) -- (0,.62);"
        r"\node[vectorExampleMuted] at (-1,.31) {$\cdots$};"
    )
    return "\n".join(lines)


def _render_scalar_bridge_tikz(example: ScalarVectorTransferExample) -> str:
    row = example.rows[0]
    scalars = example.scalars
    scalar_destination = next(
        (scalar for scalar in scalars if scalar["role"] == "destination"), None
    )
    scalar_source = next(
        (scalar for scalar in scalars if scalar["role"] == "source"), None
    )
    scalar_index = next(
        (scalar for scalar in scalars if scalar["role"] == "index"), None
    )
    row_y = 1.10 if scalar_destination is not None else 0.0
    transfer_to_row = [
        edge
        for edge in example.edges
        if edge["display"] == "transfer" and edge["to_kind"] == "row"
    ]
    transfer_from_row = next(
        (
            edge
            for edge in example.edges
            if edge["display"] == "transfer" and edge["from_kind"] == "row"
        ),
        None,
    )
    index_to_row = next(
        (
            edge
            for edge in example.edges
            if edge["display"] == "control" and edge["to_kind"] == "row"
        ),
        None,
    )
    if transfer_to_row:
        selected_cell = transfer_to_row[0]["to_cell"]
    elif transfer_from_row is not None:
        selected_cell = transfer_from_row["from_cell"]
    elif index_to_row is not None:
        selected_cell = index_to_row["to_cell"]
    else:
        selected_cell = 0
    selected_x, selected_width = _cell_geometry(row, selected_cell)
    selected_center = selected_x + selected_width / 2
    broadcast = (
        scalar_source is not None
        and len(transfer_to_row) > 1
        and {edge["from_id"] for edge in transfer_to_row} == {scalar_source["id"]}
    )
    lines: list[str] = []
    for index, cell in enumerate(row["cells"]):
        x, width = _cell_geometry(row, index)
        lines.append(
            rf"\path[{_cell_style(cell)}] ({x:.2f},{row_y:.2f}) rectangle "
            rf"({x + width:.2f},{row_y + 0.62:.2f});"
        )
        lines.append(
            rf"\coordinate (scalarBridge{row['id'].title()}Cell{index}North) at "
            rf"({x + width / 2:.2f},{row_y + 0.62:.2f});"
        )
        lines.append(
            rf"\coordinate (scalarBridge{row['id'].title()}Cell{index}South) at "
            rf"({x + width / 2:.2f},{row_y:.2f});"
        )
        lines.append(
            rf"\node[vectorExampleCompactText] at ({x + width / 2:.2f},{row_y + 0.31:.2f}) "
            rf"{{{tex_escape(cell['value'])}}};"
        )
    lines.append(
        rf"\node[vectorExampleLabel] at (8.22,{row_y + 0.31:.2f}) {{{tex_escape(row['label'])}}};"
    )
    lines.append(
        rf"\draw[vectorExamplePredicateLaneContinuation] (-2,{row_y:.2f}) -- (0,{row_y:.2f});"
        rf"\draw[vectorExamplePredicateLaneContinuation] (-2,{row_y + 0.62:.2f}) -- (0,{row_y + 0.62:.2f});"
        rf"\node[vectorExampleMuted] at (-1,{row_y + 0.31:.2f}) {{$\cdots$}};"
    )

    scalar_positions: dict[str, tuple[float, float]] = {}
    if scalar_destination is not None:
        scalar_positions[scalar_destination["id"]] = (selected_center, 0.31)
    if scalar_index is not None:
        index_y = 2.51 if scalar_destination is not None else 1.41
        index_x = (
            selected_center
            if scalar_destination is not None
            else selected_center + 0.75
        )
        scalar_positions[scalar_index["id"]] = (index_x, index_y)
    if scalar_source is not None:
        source_y = (
            2.51 if scalar_index is not None and scalar_destination is None else 1.41
        )
        if scalar_index is not None and scalar_destination is None:
            source_y = 2.51
        scalar_positions[scalar_source["id"]] = (
            4.0 if broadcast or not transfer_to_row else selected_center,
            source_y,
        )
    for scalar in scalars:
        x, y = scalar_positions[scalar["id"]]
        style = (
            "vectorExampleIndex" if scalar["role"] == "index" else "vectorExampleScalar"
        )
        lines.append(
            rf"\node[{style}] (scalarBridge{scalar['id'].title()}) at ({x:.2f},{y:.2f}) "
            rf"{{{tex_escape(scalar['value'])}}};"
        )
        lines.append(
            rf"\node[vectorExampleLabel] at (8.22,{y:.2f}) {{{tex_escape(scalar['label'])}}};"
        )

    if broadcast:
        target_centers = [
            sum(_cell_geometry(row, edge["to_cell"]))
            - _cell_geometry(row, edge["to_cell"])[1] / 2
            for edge in transfer_to_row
        ]
        source_name = "scalarBridge" + scalar_source["id"].title()
        lines.append(rf"\draw[vectorExampleBus] ({source_name}.south) -- (4,1.02);")
        lines.append(
            rf"\draw[vectorExampleBus] ({min(target_centers):.2f},1.02) -- ({max(target_centers):.2f},1.02);"
        )
        for edge in transfer_to_row:
            target_name = f"scalarBridge{row['id'].title()}Cell{edge['to_cell']}North"
            x, width = _cell_geometry(row, edge["to_cell"])
            lines.append(
                rf"\draw[vectorExampleLaneTransferArrow] ({x + width / 2:.2f},1.02) -- ({target_name});"
            )
    for edge in example.edges:
        if broadcast and edge in transfer_to_row:
            continue
        if edge["from_kind"] == "scalar":
            source = "scalarBridge" + edge["from_id"].title()
            source_anchor = source + ".south"
        else:
            source_anchor = (
                f"scalarBridge{row['id'].title()}Cell{edge['from_cell']}South"
            )
        if edge["to_kind"] == "scalar":
            target = "scalarBridge" + edge["to_id"].title()
            target_anchor = target + ".north"
        else:
            target_anchor = f"scalarBridge{row['id'].title()}Cell{edge['to_cell']}North"
        style = (
            "vectorExampleControlArrow"
            if edge["display"] == "control"
            else "vectorExampleLaneTransferArrow"
        )
        lines.append(rf"\draw[{style}] ({source_anchor}) -- ({target_anchor});")
    return "\n".join(lines)


def _render_memory_lanes_tikz(example: PredicatedVectorLoadExample) -> str:
    rows = {row["id"]: row for row in example.rows}
    y_by_id = {"address": 2.20, "memory": 1.10, "result": 0.0, "predicate": -1.10}
    base = example.base
    lines = [
        rf"\node[vectorExampleScalar] (memoryBase) at (3.35,3.71) {{{tex_escape(base['value'])}}};",
        rf"\node[vectorExampleLabel] at (3.93,3.71) {{{tex_escape(base['label'])}}};",
    ]
    for row in example.rows:
        y = y_by_id[row["id"]]
        lines.append(
            rf"\node[vectorExampleLabel] at (8.22,{y + 0.31:.2f}) {{{tex_escape(row['label'])}}};"
        )
        for index, cell in enumerate(row["cells"]):
            x, width = _cell_geometry(row, index)
            if row["id"] == "address":
                if cell["effect"] == "copy":
                    lines.append(
                        rf"\node[vectorExampleCellText] at ({x + width / 2:.2f},{y + 0.31:.2f}) "
                        rf"{{{tex_escape(cell['value'])}}};"
                    )
                continue
            lines.append(
                rf"\path[{_cell_style(cell)}] ({x:.2f},{y:.2f}) rectangle "
                rf"({x + width:.2f},{y + 0.62:.2f});"
            )
            lines.append(
                rf"\node[{'vectorExampleCompactText' if row['id'] == 'predicate' else 'vectorExampleCellText'}] "
                rf"at ({x + width / 2:.2f},{y + 0.31:.2f}) {{{tex_escape(cell['value'])}}};"
            )
        if row["id"] != "address":
            lines.append(
                rf"\draw[vectorExamplePredicateLaneContinuation] (-2,{y:.2f}) -- (0,{y:.2f});"
                rf"\draw[vectorExamplePredicateLaneContinuation] (-2,{y + 0.62:.2f}) -- (0,{y + 0.62:.2f});"
                rf"\node[vectorExampleMuted] at (-1,{y + 0.31:.2f}) {{$\cdots$}};"
            )
    for edge in example.edges:
        source_row = rows[edge["from_row"]]
        target_row = rows[edge["to_row"]]
        source_x, source_width = _cell_geometry(source_row, edge["from_cell"])
        target_x, target_width = _cell_geometry(target_row, edge["to_cell"])
        source_y = y_by_id[edge["from_row"]]
        target_y = y_by_id[edge["to_row"]]
        style = (
            "vectorExampleControlArrow"
            if edge["display"] == "control"
            else "vectorExampleLaneTransferArrow"
        )
        source_anchor = (
            source_y + 0.13 if edge["from_row"] == "address" else source_y - 0.02
        )
        lines.append(
            rf"\draw[{style}] ({source_x + source_width / 2:.2f},{source_anchor:.2f}) -- "
            rf"({target_x + target_width / 2:.2f},{target_y + 0.68:.2f});"
        )
    lines.append(r"\node[inner sep=0pt,minimum size=0pt] at (0,-1.46) {};")
    return "\n".join(lines)


def _render_reduction_tikz(example: PredicatedVectorReductionExample) -> str:
    y_by_id = {"predicate": 3.30, "source": 2.20}
    lines: list[str] = []
    for row in example.rows:
        y = y_by_id[row["id"]]
        lines.append(
            rf"\node[vectorExampleLabel] at (8.22,{y + 0.31:.2f}) {{{tex_escape(row['label'])}}};"
        )
        for index, cell in enumerate(row["cells"]):
            x, width = _cell_geometry(row, index)
            lines.append(
                rf"\path[{_cell_style(cell)}] ({x:.2f},{y:.2f}) rectangle ({x + width:.2f},{y + 0.62:.2f});"
            )
            lines.append(
                rf"\node[{'vectorExampleCompactText' if row['id'] == 'predicate' else 'vectorExampleCellText'}] "
                rf"at ({x + width / 2:.2f},{y + 0.31:.2f}) {{{tex_escape(cell['value'])}}};"
            )
            if row["id"] == "source" and index in example.selected:
                lines.append(
                    rf"\draw[vectorExampleLaneTransferArrow] ({x + width / 2:.2f},{y - 0.02:.2f}) -- "
                    rf"({x + width / 2:.2f},1.78);"
                )
        lines.append(
            rf"\draw[vectorExamplePredicateLaneContinuation] (-2,{y:.2f}) -- (0,{y:.2f});"
            rf"\draw[vectorExamplePredicateLaneContinuation] (-2,{y + 0.62:.2f}) -- (0,{y + 0.62:.2f});"
            rf"\node[vectorExampleMuted] at (-1,{y + 0.31:.2f}) {{$\cdots$}};"
        )
    expression = " + ".join(tex_escape(term) for term in example.terms)
    if example.continuation:
        expression += r" + \cdots"
    lines.append(r"\path[vectorExampleFold] (0,1.10) rectangle (8,1.72);")
    lines.append(rf"\node[vectorExampleCellText] at (4,1.41) {{$ {expression} $}};")
    lines.append(
        rf"\node[vectorExampleLabel] at (8.22,1.41) {{{tex_escape(example.fold_label)}}};"
    )
    lines.append(
        rf"\node[vectorExampleScalar] (reductionResult) at (4,.31) "
        rf"{{{tex_escape(example.result_value)}}};"
    )
    lines.append(
        rf"\node[vectorExampleLabel] at (8.22,.31) {{{tex_escape(example.result_label)}}};"
    )
    lines.append(r"\node[vectorExampleEquality] at (4,.86) {$=$};")
    return "\n".join(lines)


def render_tikz(example: VectorExample) -> str:
    lines: list[str] = []
    if isinstance(example, ScalarVectorTransferExample):
        return _render_scalar_bridge_tikz(example)
    if isinstance(example, PredicatedVectorLoadExample):
        return _render_memory_lanes_tikz(example)
    if isinstance(example, PredicatedVectorReductionExample):
        return _render_reduction_tikz(example)
    if isinstance(example, FloatingPointWidthConversionExample):
        return _render_detailed_width_tikz(example)
    if isinstance(example, PredicateLaneTransferExample):
        return _render_predicate_lane_map_tikz(example)
    if isinstance(example, PredicateWidthConversionExample):
        return _render_predicate_width_tikz(example)
    if isinstance(
        example, (StatefulPredicateRangeExample, CountedPredicateRangeExample)
    ):
        return _render_grouped_predicate_range_tikz(example)
    if isinstance(example, IntegerWidthConversionExample):
        return _render_detailed_width_tikz(example)
    if not isinstance(example, VectorLaneTransferExample):
        raise TypeError(f"unsupported vector example {type(example).__name__}")
    detailed_lane_view = any(row["role"] == "predicate" for row in example.rows)
    if not detailed_lane_view:
        y_by_id = {
            row["id"]: 2.2 - index * 1.05 for index, row in enumerate(example.rows)
        }
        result_y = next(
            y_by_id[row["id"]]
            for row in example.rows
            if row["role"] == "destination-after"
        )
        for edge in example.edges:
            source_y = y_by_id[edge["from_row"]]
            source_x = (
                len(
                    next(row for row in example.rows if row["id"] == edge["from_row"])[
                        "cells"
                    ]
                )
                - edge["from_cell"]
                - 0.5
            )
            result_x = (
                len(
                    next(row for row in example.rows if row["id"] == edge["to_row"])[
                        "cells"
                    ]
                )
                - edge["to_cell"]
                - 0.5
            )
            lines.append(
                rf"\draw[vectorExample{edge['effect'].title()}Arrow] "
                rf"({source_x:.2f},{source_y:.2f}) -- "
                rf"({result_x:.2f},{result_y + 0.68:.2f});"
            )
        for row in example.rows:
            y = y_by_id[row["id"]]
            lines.append(
                rf"\node[vectorExampleLabel] at (8.8,{y + 0.34:.2f}) "
                rf"{{{tex_escape(row['label'])}}};"
            )
            for index, cell in enumerate(row["cells"]):
                x = len(row["cells"]) - 1 - index
                lines.append(
                    rf"\path[vectorExample{cell['effect'].title()}] "
                    rf"({x},{y:.2f}) rectangle ({x + 1},{y + 0.68:.2f});"
                )
                lines.append(
                    rf"\node[vectorExampleCellText] at ({x + 0.5},{y + 0.34:.2f}) "
                    rf"{{{tex_escape(cell['value'])}}};"
                )
            if example.scalable:
                lines.append(
                    rf"\draw[vectorExampleContinuation] (-2,{y:.2f}) -- (0,{y:.2f});"
                    rf"\draw[vectorExampleContinuation] (-2,{y + 0.68:.2f}) -- (0,{y + 0.68:.2f});"
                    rf"\node[vectorExampleMuted] at (-1,{y + 0.34:.2f}) {{$\cdots$}};"
                )
        return "\n".join(lines)

    top_y = (len(example.rows) - 2) * 1.10
    y_by_id = {
        row["id"]: top_y - index * 1.10 for index, row in enumerate(example.rows)
    }
    rows_by_id = {row["id"]: row for row in example.rows}
    # Draw transfers first; cells and their values are then the top visual layer.
    result_y = next(
        y_by_id[row["id"]] for row in example.rows if row["role"] == "destination-after"
    )
    if not example.scalable:
        lines.append(
            rf"\node[vectorExampleFixedView] at (4,{top_y + 0.78:.2f}) "
            r"{fixed example: VLEN = 16 bytes};"
        )
    for edge in example.edges:
        source_y = y_by_id[edge["from_row"]]
        source_row = rows_by_id[edge["from_row"]]
        result_row = rows_by_id[edge["to_row"]]
        source_left, source_width = _cell_geometry(source_row, edge["from_cell"])
        result_left, result_width = _cell_geometry(result_row, edge["to_cell"])
        source_x = source_left + source_width / 2
        result_x = result_left + result_width / 2
        if source_y > result_y:
            source_anchor, result_anchor = source_y - 0.02, result_y + 0.68
        else:
            source_anchor, result_anchor = source_y + 0.64, result_y - 0.06
        arrow_style = "vectorExampleLaneTransferArrow"
        lines.append(
            rf"\draw[{arrow_style}] ({source_x:.2f},{source_anchor:.2f}) -- ({result_x:.2f},{result_anchor:.2f});"
        )
    for row in example.rows:
        y = y_by_id[row["id"]]
        lines.append(
            rf"\node[vectorExampleLabel] at (8.22,{y + 0.31:.2f}) {{{tex_escape(row['label'])}}};"
        )
        for index, cell in enumerate(row["cells"]):
            x, width = _cell_geometry(row, index)
            lines.append(
                rf"\path[{_cell_style(cell)}] ({x:.2f},{y:.2f}) rectangle ({x + width:.2f},{y + 0.62:.2f});"
            )
            lines.append(
                rf"\node[vectorExampleCompactText] at ({x + width / 2:.2f},{y + 0.31:.2f}) {{{tex_escape(cell['value'])}}};"
            )
        if example.scalable:
            continuation_style = "vectorExampleLaneContinuation"
            lines.append(
                rf"\draw[{continuation_style}] (-2,{y:.2f}) -- (0,{y:.2f});\draw[{continuation_style}] (-2,{y + 0.62:.2f}) -- (0,{y + 0.62:.2f});\node[vectorExampleMuted] at (-1,{y + 0.31:.2f}) {{$\cdots$}};"
            )
    return "\n".join(lines)
