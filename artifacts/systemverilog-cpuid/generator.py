
from artifacts._shared.systemverilog.artifacts import _identifier
from artifacts._shared.systemverilog.artifacts import outputs
from types import MappingProxyType
from engine.artifacts.definition import GeneratedArtifactSet
from engine.artifacts.generate import ArtifactGenerationContext
from engine.isa.architecture import CpuidQueryProjection
from engine.isa.architecture import cpuid_project



def _contents(context: ArtifactGenerationContext) -> dict[str, str]:
    project = context.workspace.require_provider("isa")
    projection = cpuid_project(project.cpuid)
    package = render_cpuid(projection)
    rom = "module bedrock_cpuid_rom #(\n  parameter integer ENTRY_COUNT = 1,\n  parameter logic [63:0] ENTRY_SELECTOR [ENTRY_COUNT] = '{default: 64'h0},\n  parameter logic [63:0] ENTRY_MASK [ENTRY_COUNT] = '{default: 64'hffffffffffffffff},\n  parameter logic [63:0] ENTRY_DATA [ENTRY_COUNT] = '{default: 64'h0}\n) (\n  input  logic [63:0] selector_i,\n  output logic        valid_o,\n  output logic [63:0] data_o\n);\n  integer entry;\n  always_comb begin\n    valid_o = 1'b0;\n    data_o = '0;\n    for (entry = 0; entry < ENTRY_COUNT; entry = entry + 1) begin\n      if (!valid_o &&\n          ((selector_i & ENTRY_MASK[entry]) ==\n           (ENTRY_SELECTOR[entry] & ENTRY_MASK[entry]))) begin\n        valid_o = 1'b1;\n        data_o = ENTRY_DATA[entry];\n      end\n    end\n  end\nendmodule"
    return {"package": package, "rom": rom}


def render_cpuid(projection: tuple[CpuidQueryProjection, ...]) -> str:
    constants: list[str] = []
    class_constants: set[tuple[str, str]] = set()
    leaf_constants: set[tuple[str, str, str]] = set()
    for query in projection:
        prefix = f"CPUID_{_identifier(query.owner)}_{_identifier(query.class_id)}"
        class_key = (query.owner, query.class_id)
        if class_key not in class_constants:
            class_constants.add(class_key)
            constants.append(
                f"  localparam logic [31:0] {prefix}_CLASS = 32'h{query.class_value:08x};"
            )
        leaf_prefix = f"{prefix}_{_identifier(query.leaf_id)}"
        leaf_key = (query.owner, query.class_id, query.leaf_id)
        if leaf_key not in leaf_constants:
            leaf_constants.add(leaf_key)
            constants.append(
                f"  localparam logic [15:0] {leaf_prefix}_LEAF = 16'h{query.leaf_value:04x};"
            )
        query_prefix = f"{leaf_prefix}_{_identifier(query.query_id)}"
        constants.extend(
            (
                f"  localparam logic [15:0] {query_prefix}_FIRST = 16'h{query.first_index:04x};",
                f"  localparam logic [15:0] {query_prefix}_LAST = 16'h{query.last_index:04x};",
                f"  localparam logic [{max(16, query.stride.bit_length()) - 1}:0] {query_prefix}_STRIDE = {max(16, query.stride.bit_length())}'d{query.stride};",
            )
        )
        for field in query.fields:
            field_prefix = f"{query_prefix}_{_identifier(field.id)}"
            constants.extend(
                (
                    f"  localparam logic [6:0] {field_prefix}_LSB = 7'd{field.lsb};",
                    f"  localparam logic [6:0] {field_prefix}_BITS = 7'd{field.bits};",
                    f"  localparam logic [63:0] {field_prefix}_MASK = 64'h{field.mask:016x};",
                )
            )
    package = (
        "package bedrock_cpuid_pkg;\n  typedef struct packed {\n    logic [31:0] class_id;\n    logic [15:0] leaf_id;\n    logic [15:0] index;\n  } bedrock_cpuid_selector_t;\n\n"
        + "\n".join(constants)
        + "\nendpackage"
    )
    return package


def _inputs(context):
    return context.shared_result((_contents, id(context.workspace)), lambda: MappingProxyType(_contents(context)))


def validate(definition, context):
    if set(definition.outputs) != set(_inputs(context)):
        raise ValueError(f"{definition.source}: declared output selections do not match this SystemVerilog artifact")


def generate(definition, context) -> GeneratedArtifactSet:
    return outputs(definition, _inputs(context))
