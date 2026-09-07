
from artifacts._shared.systemverilog.artifacts import outputs, _identifier
from engine.isa.event_structures import resolve_event_frame_layouts
from types import MappingProxyType
from engine.artifacts.definition import GeneratedArtifactSet
from engine.artifacts.generate import ArtifactGenerationContext



def _contents(context):
    decode = "module bedrock_decode_assertions\n  import bedrock_decode_pkg::*;\n(\n  input logic clk_i,\n  input logic reset_i,\n  input d0_result_t d0_i,\n  input d1_opcode_result_t d1_i,\n  input ea_decode_result_t ea_i\n);\n  default clocking cb @(posedge clk_i); endclocking\n  default disable iff (reset_i);\n\n  assert property (d0_i.status == D0_SUCCESS |-> d0_i.form != FORM_INVALID);\n  assert property (d0_i.status == D0_SUCCESS |-> $onehot(d0_i.form_high_decode));\n  assert property (d0_i.status == D0_SUCCESS |-> $onehot(d0_i.form_low_decode));\n  assert property (d1_i.valid |-> d1_i.stage == D1_STAGE_SUCCESS);\n  assert property (ea_i.valid |-> ea_i.stage == D1_STAGE_SUCCESS);\n  assert property (d1_i.valid |-> d1_i.form == d0_i.form);\n  cover property (d1_i.valid && ea_i.valid);\nendmodule"
    project = context.workspace.require_provider("isa")
    frame = context.shared_result((resolve_event_frame_layouts, id(project.event_frames.frame)), lambda: resolve_event_frame_layouts(project.event_frames.frame))
    assertions = tuple(f"  assert property (event_known_i && event_frame_i == EVENT_FRAME_{_identifier(layout.frame_type)} |-> event_frame_slots_i == {layout.allocated_slot_count});" for layout in frame.layouts.values())
    architecture = "\n".join(("module bedrock_architecture_assertions", "  import bedrock_event_pkg::*;", "(", "  input logic clk_i,", "  input logic reset_i,", "  input logic register_valid_i,", "  input logic register_reserved_zero_i,", "  input logic register_write_i,", "  input logic event_known_i,", "  input bedrock_event_frame_type_e event_frame_i,", f"  input logic [{frame.control_fields['FRAME_SIZE'].bits - 1}:0] event_frame_slots_i", ");", "  default clocking cb @(posedge clk_i); endclocking", "  default disable iff (reset_i);", "  assert property (register_valid_i && register_write_i |-> register_reserved_zero_i);", *assertions, "endmodule"))
    return MappingProxyType({"decode": decode, "architecture": architecture})


def _inputs(context):
    return context.shared_result((_contents, id(context.workspace)), lambda: _contents(context))


def validate(definition, context):
    if set(definition.outputs) != set(_inputs(context)):
        raise ValueError(f"{definition.source}: assertion output selections do not match")


def generate(definition, context):
    return outputs(definition, _inputs(context))
