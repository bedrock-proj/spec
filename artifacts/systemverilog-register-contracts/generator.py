
from artifacts._shared.systemverilog.artifacts import _identifier
from artifacts._shared.systemverilog.artifacts import outputs
from types import MappingProxyType
from engine.artifacts.definition import GeneratedArtifactSet
from engine.artifacts.generate import ArtifactGenerationContext
from engine.isa.architecture import RegisterContractsProjection
from engine.isa.architecture import register_contracts_project
from engine.isa.registers import VariableRegisterWidth



def _contents(context: ArtifactGenerationContext) -> dict[str, str]:
    project = context.workspace.require_provider("isa")
    projection = register_contracts_project(
        project.registers, project.control_registers
    )
    package, contracts = render_register_contracts(projection)
    return {"package": package, "contracts": contracts}


def _register_width_kind(width: object) -> int:
    if isinstance(width, int):
        return 0
    expression = (
        width.expression if isinstance(width, VariableRegisterWidth) else str(width)
    )
    if expression.strip() == "MAX_VLEN":
        return 1
    if expression.replace(" ", "") == "MAX_VLEN/8":
        return 2
    return 3


def render_register_contracts(
    projection: RegisterContractsProjection,
) -> tuple[str, str]:
    groups = [
        (owner, group, registers)
        for (owner, group), registers in projection.groups.items()
    ] + [
        (owner, "CONTROL_REGISTERS", registers)
        for owner, registers in projection.control_registers.items()
    ]
    group_names = tuple(
        f"REGISTER_GROUP_{_identifier(owner)}_{_identifier(group)}"
        for owner, group, _ in groups
    )
    group_width = max(1, (len(group_names) - 1).bit_length())
    group_enum = ",\n".join(
        (
            f"    {name} = {group_width}'d{index}"
            for index, name in enumerate(group_names)
        )
    )
    reset_width = max((64, *(int(register.reset_value or 0).bit_length()
                             for _, _, registers in groups for register in registers)))
    constants: list[str] = [f"  localparam integer REGISTER_RESET_VALUE_BITS = {reset_width};"]
    cases: list[str] = []
    for (owner, group, registers), group_name in zip(groups, group_names):
        for register in registers:
            prefix = f"REGISTER_{_identifier(owner)}_{_identifier(group)}_{_identifier(register.register_id)}"
            constants.append(
                f"  localparam logic [15:0] {prefix} = 16'h{register.encoding:04x};"
            )
            for field in register.fields:
                field_prefix = f"{prefix}_{_identifier(field.id)}"
                constants.extend(
                    (
                        f"  localparam logic [6:0] {field_prefix}_LSB = 7'd{field.lsb};",
                        f"  localparam logic [63:0] {field_prefix}_MASK = 64'h{field.mask:016x};",
                    )
                )
            cases.append(
                f"      {{{group_name}, {prefix}}}: begin\n        valid_o = 1'b1; width_kind_o = 2'd{_register_width_kind(register.width)};\n        fixed_width_o = 16'd{register.width if isinstance(register.width, int) else 0}; writable_mask_o = 64'h{register.writable_mask:016x};\n        reset_known_o = 1'b{int(register.reset_value is not None)}; reset_value_o = {reset_width}'h{(register.reset_value or 0):x};\n      end"
            )
    package = f"package bedrock_register_pkg;\n  typedef enum logic [{group_width - 1}:0] {{\n{group_enum}\n  }} bedrock_register_group_e;\n\n{chr(10).join(constants)}\nendpackage"
    contracts = f"module bedrock_register_contracts\n  import bedrock_register_pkg::*;\n(\n  input  bedrock_register_group_e group_i,\n  input  logic [15:0] encoding_i,\n  input  logic [63:0] write_data_i,\n  output logic valid_o,\n  output logic [1:0] width_kind_o,\n  output logic [15:0] fixed_width_o,\n  output logic [63:0] writable_mask_o,\n  output logic reserved_zero_o,\n  output logic reset_known_o,\n  output logic [REGISTER_RESET_VALUE_BITS-1:0] reset_value_o\n);\n  always_comb begin\n    valid_o = 1'b0;\n    width_kind_o = '0;\n    fixed_width_o = '0;\n    writable_mask_o = '0;\n    reset_known_o = 1'b0;\n    reset_value_o = '0;\n    unique case ({{group_i, encoding_i}})\n{chr(10).join(cases)}\n      default: begin end\n    endcase\n    reserved_zero_o = valid_o && ((write_data_i & ~writable_mask_o) == 64'b0);\n  end\nendmodule"
    return package, contracts


def _inputs(context):
    return context.shared_result((_contents, id(context.workspace)), lambda: MappingProxyType(_contents(context)))


def validate(definition, context):
    if set(definition.outputs) != set(_inputs(context)):
        raise ValueError(f"{definition.source}: declared output selections do not match this SystemVerilog artifact")


def generate(definition, context) -> GeneratedArtifactSet:
    return outputs(definition, _inputs(context))
