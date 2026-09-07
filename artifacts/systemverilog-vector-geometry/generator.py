
from artifacts._shared.systemverilog.artifacts import outputs
from types import MappingProxyType
from engine.artifacts.definition import GeneratedArtifactSet
from engine.artifacts.generate import ArtifactGenerationContext
from engine.isa.architecture import VectorGeometryProjection
from engine.isa.architecture import vector_geometry_project



def _contents(context: ArtifactGenerationContext) -> dict[str, str]:
    project = context.workspace.require_provider("isa")
    projection = vector_geometry_project(project.registers)
    package = render_vector_geometry(projection)
    permute = "module bedrock_vector_permute\n  import bedrock_vector_geometry_pkg::*;\n#(\n  parameter integer VLEN = 256\n) (\n  input  bedrock_vector_permute_e operation_i,\n  input  logic [3:0] element_bytes_i,\n  input  logic [VLEN-1:0] left_i,\n  input  logic [VLEN-1:0] right_i,\n  output logic valid_o,\n  output logic [VLEN-1:0] result_o\n);\n  localparam integer VLEN_BYTES = VLEN / 8;\n  integer output_byte;\n  integer output_lane;\n  integer lane_byte;\n  integer lane_count;\n  integer element_bytes;\n  integer source_lane;\n  integer source_byte;\n  logic source_right;\n\n  always_comb begin\n    result_o = '0;\n    element_bytes = {28'b0, element_bytes_i};\n    output_lane = 0;\n    lane_byte = 0;\n    source_lane = 0;\n    source_byte = 0;\n    source_right = 1'b0;\n    valid_o = (element_bytes == 1 || element_bytes == 2 ||\n               element_bytes == 4 || element_bytes == 8);\n    if (valid_o)\n      valid_o = ((VLEN_BYTES % element_bytes) == 0);\n    lane_count = valid_o ? VLEN_BYTES / element_bytes : 0;\n    for (output_byte = 0; output_byte < VLEN_BYTES; output_byte = output_byte + 1) begin\n      output_lane = valid_o ? output_byte / element_bytes : 0;\n      lane_byte = valid_o ? output_byte % element_bytes : 0;\n      source_lane = 0;\n      source_right = 1'b0;\n      if (valid_o) begin\n        unique case (operation_i)\n          VECTOR_PERMUTE_ZIP_LO: begin\n            source_right = output_lane[0];\n            source_lane = output_lane / 2;\n          end\n          VECTOR_PERMUTE_ZIP_HI: begin\n            source_right = output_lane[0];\n            source_lane = lane_count / 2 + output_lane / 2;\n          end\n          VECTOR_PERMUTE_UNZIP_LO: begin\n            source_right = (2 * output_lane) >= lane_count;\n            source_lane = (2 * output_lane) % lane_count;\n          end\n          VECTOR_PERMUTE_UNZIP_HI: begin\n            source_right = (2 * output_lane + 1) >= lane_count;\n            source_lane = (2 * output_lane + 1) % lane_count;\n          end\n          VECTOR_PERMUTE_TRANSPOSE_LO: begin\n            source_right = output_lane[0];\n            source_lane = 2 * (output_lane / 2);\n          end\n          VECTOR_PERMUTE_TRANSPOSE_HI: begin\n            source_right = output_lane[0];\n            source_lane = 2 * (output_lane / 2) + 1;\n          end\n          default: valid_o = 1'b0;\n        endcase\n        source_byte = source_lane * element_bytes + lane_byte;\n        if (valid_o && source_byte < VLEN_BYTES)\n          result_o[output_byte*8 +: 8] = source_right\n            ? right_i[source_byte*8 +: 8]\n            : left_i[source_byte*8 +: 8];\n      end\n    end\n  end\nendmodule"
    return {"package": package, "permuter": permute}


def render_vector_geometry(projection: VectorGeometryProjection) -> str:
    package = f"package bedrock_vector_geometry_pkg;\n  localparam integer BEDROCK_VECTOR_REGISTER_COUNT = {projection.vector_register_count};\n  localparam integer BEDROCK_PREDICATE_REGISTER_COUNT = {projection.predicate_register_count};\n  typedef enum logic [2:0] {{\n    VECTOR_PERMUTE_ZIP_LO,\n    VECTOR_PERMUTE_ZIP_HI,\n    VECTOR_PERMUTE_UNZIP_LO,\n    VECTOR_PERMUTE_UNZIP_HI,\n    VECTOR_PERMUTE_TRANSPOSE_LO,\n    VECTOR_PERMUTE_TRANSPOSE_HI\n  }} bedrock_vector_permute_e;\n\n  function automatic integer bedrock_vector_lane_count(\n    input integer vlen_bits,\n    input integer element_bytes\n  );\n    bedrock_vector_lane_count = vlen_bits / (8 * element_bytes);\n  endfunction\n\n  function automatic integer bedrock_predicate_bit_index(\n    input integer lane,\n    input integer element_bytes\n  );\n    bedrock_predicate_bit_index = lane * element_bytes;\n  endfunction\nendpackage"
    return package


def _inputs(context):
    return context.shared_result((_contents, id(context.workspace)), lambda: MappingProxyType(_contents(context)))


def validate(definition, context):
    if set(definition.outputs) != set(_inputs(context)):
        raise ValueError(f"{definition.source}: declared output selections do not match this SystemVerilog artifact")


def generate(definition, context) -> GeneratedArtifactSet:
    return outputs(definition, _inputs(context))
