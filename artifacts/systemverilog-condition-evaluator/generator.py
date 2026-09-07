
from artifacts._shared.systemverilog.artifacts import outputs
from types import MappingProxyType
from engine.artifacts.definition import GeneratedArtifactSet
from engine.artifacts.generate import ArtifactGenerationContext



def _contents(context: ArtifactGenerationContext) -> dict[str, str]:
    body = "module bedrock_condition_eval (\n  input  logic [3:0] condition_i,\n  input  logic [3:0] flags_i,\n  output logic       holds_o\n);\n  logic flag_z;\n  logic flag_n;\n  logic flag_c;\n  logic flag_v;\n\n  always_comb begin\n    flag_z = flags_i[3];\n    flag_n = flags_i[2];\n    flag_c = flags_i[1];\n    flag_v = flags_i[0];\n    unique case (condition_i)\n      4'h0: holds_o = 1'b1;\n      4'h1: holds_o = 1'b0;\n      4'h2: holds_o = flag_z;\n      4'h3: holds_o = !flag_z;\n      4'h4: holds_o = flag_c;\n      4'h5: holds_o = !flag_c;\n      4'h6: holds_o = flag_n;\n      4'h7: holds_o = !flag_n;\n      4'h8: holds_o = flag_v;\n      4'h9: holds_o = !flag_v;\n      4'ha: holds_o = flag_c || flag_z;\n      4'hb: holds_o = !flag_c && !flag_z;\n      4'hc: holds_o = flag_n != flag_v;\n      4'hd: holds_o = flag_n == flag_v;\n      4'he: holds_o = flag_z || (flag_n != flag_v);\n      4'hf: holds_o = !flag_z && (flag_n == flag_v);\n    endcase\n  end\nendmodule"
    return {"evaluator": body}


def _inputs(context):
    return context.shared_result((_contents, id(context.workspace)), lambda: MappingProxyType(_contents(context)))


def validate(definition, context):
    if set(definition.outputs) != set(_inputs(context)):
        raise ValueError(f"{definition.source}: declared output selections do not match this SystemVerilog artifact")


def generate(definition, context) -> GeneratedArtifactSet:
    return outputs(definition, _inputs(context))
