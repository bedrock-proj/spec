
from artifacts._shared.systemverilog.artifacts import _identifier
from types import MappingProxyType
from engine.isa.event_structures import resolve_event_frame_layouts
_FRAME_TYPE_VALUES = MappingProxyType({"basic": 0, "error": 1, "page": 2, "auxiliary": 3})
from artifacts._shared.systemverilog.artifacts import outputs
from engine.artifacts.definition import GeneratedArtifactSet
from engine.artifacts.generate import ArtifactGenerationContext
from engine.isa.architecture import EventCodecProjection
from engine.isa.architecture import event_codec_project



def _inputs(context):
    project = context.workspace.require_provider("isa")
    frame = context.shared_result((resolve_event_frame_layouts, id(project.event_frames.frame)), lambda: resolve_event_frame_layouts(project.event_frames.frame))
    return context.shared_result((event_codec_project, id(project.events), id(frame)), lambda: event_codec_project(project.events, frame))


def _contents(context):
    projection = _inputs(context)
    package, codec = render_event_codec(projection)
    return {"package": package, "codec": codec, "frame": render_event_frame(projection.frame)}


def generate(definition, context) -> GeneratedArtifactSet:
    return outputs(definition, _contents(context))


def validate(definition, context):
    if set(definition.outputs) != set(_contents(context)):
        raise ValueError(f"{definition.source}: event codec output selections do not match")


def render_event_frame(frame) -> str:
    control, info = frame.header_slots[:2]
    fields = frame.control_fields
    field_inputs = {"FRAME_SIZE": "frame_slots_o", "FRAME_TYPE": "frame_type_i", "SAVED_DFA": "saved_dfa_i", "FLAGS": "flags_i", "STATUS": "status_i"}
    slot_inputs = {"SAVED_PC": "saved_pc_i", "SAVED_SP": "saved_sp_i", "SAVED_CS": "saved_cs_i", "SAVED_DS": "saved_ds_i", "SAVED_SS": "saved_ss_i", "ERROR_CODE": "error_code_i", "FAULT_EA": "fault_ea_i", "FAULT_LINEAR": "fault_linear_i", "EVENT_AUX": "event_aux_i"}
    ports = ["  input bedrock_event_frame_type_e frame_type_i"]
    for name in ("SAVED_DFA", "FLAGS", "STATUS"):
        ports.append(f"  input logic [{fields[name].bits - 1}:0] {field_inputs[name]}")
    ports.append(f"  input logic [{frame.event_code.bits - 1}:0] event_code_i")
    slots = {slot.id: slot for slot in frame.header_slots}
    for layout in frame.layouts.values():
        slots.update((slot.id, slot) for slot in layout.payload_slots)
    for name, port in slot_inputs.items():
        ports.append(f"  input logic [{slots[name].bits - 1}:0] {port}")
    ports.append(f"  output logic [{fields['FRAME_SIZE'].bits - 1}:0] frame_slots_o")
    ports.append(f"  output logic [{max(layout.byte_length for layout in frame.layouts.values()) * 8 - 1}:0] frame_o")
    cases = []
    for layout in frame.layouts.values():
        cases.append(f"      EVENT_FRAME_{_identifier(layout.frame_type)}: begin")
        cases.append(f"        frame_slots_o = {fields['FRAME_SIZE'].bits}'d{layout.allocated_slot_count};")
        cases.extend(f"        frame_o[{slot.offset * 8} +: {slot.bits}] = {slot_inputs[slot.id]};" for slot in layout.payload_slots)
        cases.append("      end")
    assignments = [f"    frame_o[{control.offset * 8 + field.lsb} +: {field.bits}] = {field.bits}'({field_inputs[name]});" for name, field in fields.items()]
    assignments.append(f"    frame_o[{info.offset * 8 + frame.event_code.lsb} +: {frame.event_code.bits}] = event_code_i;")
    assignments.extend(f"    frame_o[{slot.offset * 8} +: {slot.bits}] = {slot_inputs[slot.id]};" for slot in frame.header_slots if slot.id in slot_inputs)
    return "\n".join(("module bedrock_event_frame", "  import bedrock_event_pkg::*;", "(", ",\n".join(ports), ");", "  always_comb begin", "    frame_o = '0;", "    frame_slots_o = '0;", "    unique case (frame_type_i)", *cases, "      default: begin frame_o = 'x; frame_slots_o = 'x; end", "    endcase", *assignments, "  end", "endmodule"))


def render_event_codec(projection: EventCodecProjection) -> tuple[str, str]:
    payload_bits = {name: bit for bit, name in enumerate(sorted(projection.payloads))}
    constants: list[str] = []
    cases: list[str] = []
    for route in projection.fixed_routes:
        payload_mask = sum(1 << payload_bits[name] for name in route.payloads)
        constant = f"EVENT_{_identifier(route.owner)}_{_identifier(route.event_id)}"
        constants.append(
            f"  localparam logic [31:0] {constant} = 32'h{route.code:08x};"
        )
        cases.append(
            f"      {constant}: begin frame_o = EVENT_FRAME_{_identifier(route.frame.frame_type)}; payload_mask_o = {max(1, len(payload_bits))}'h{payload_mask:x}; end"
        )
    for name, bit in payload_bits.items():
        constants.append(
            f"  localparam logic [{max(1, len(payload_bits)) - 1}:0] EVENT_PAYLOAD_{_identifier(name)} = {max(1, len(payload_bits))}'h{1 << bit:x};"
        )
    enum_items = ",\n".join(
        (
            f"    EVENT_FRAME_{_identifier(name)} = 2'd{value}"
            for name, value in _FRAME_TYPE_VALUES.items()
        )
    )
    package = f"package bedrock_event_pkg;\n  localparam integer BEDROCK_EVENT_PAYLOAD_KINDS = {max(1, len(payload_bits))};\n  typedef enum logic [1:0] {{\n{enum_items}\n  }} bedrock_event_frame_type_e;\n  typedef struct packed {{\n    logic [7:0] class_id;\n    logic [23:0] selector;\n  }} bedrock_event_code_t;\n\n{chr(10).join(constants)}\nendpackage"
    dynamic_cases = "\n".join(
        (
            f"      8'h{route.class_value:02x}: frame_o = EVENT_FRAME_{_identifier(route.frame.frame_type)};"
            for route in projection.dynamic_routes
        )
    )
    codec = f"module bedrock_event_codec\n  import bedrock_event_pkg::*;\n(\n  input  logic [31:0] code_i,\n  output logic        known_o,\n  output bedrock_event_frame_type_e frame_o,\n  output logic [BEDROCK_EVENT_PAYLOAD_KINDS-1:0] payload_mask_o\n);\n  always_comb begin\n    known_o = 1'b1;\n    frame_o = EVENT_FRAME_BASIC;\n    payload_mask_o = '0;\n    unique case (code_i)\n{chr(10).join(cases)}\n      default: begin\n        unique case (code_i[31:24])\n{dynamic_cases}\n          default: known_o = 1'b0;\n        endcase\n      end\n    endcase\n  end\nendmodule"
    return package, codec
