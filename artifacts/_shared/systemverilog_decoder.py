"""Share one configured RTL lowering result within an artifact invocation."""

from engine.isa.configuration import IsaConfiguration
from engine.isa.decoding import project_decode
from artifacts._shared.systemverilog.lowering import lower


def rendered_decoder(context):
    isa = context.workspace.require_provider("isa")
    configuration = IsaConfiguration.resolve(isa.catalog)
    def render():
        decoded = context.shared_result((project_decode, id(isa), configuration), lambda: project_decode(
            tuple(isa.catalog.instructions.resolve(reference) for reference in isa.catalog.instruction_order),
            configuration=configuration, field_types=isa.types.field_types,
            payload_types=isa.types.payload_types, ea_modes=isa.catalog.ea_modes,
            registers=isa.registers, cpuid=isa.cpuid,
        ))
        return lower(decoded)
    return context.shared_result((lower, id(isa), configuration), render)
