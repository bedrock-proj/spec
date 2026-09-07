"""Place the selected RTL lowering outputs in this artifact's declared roles."""

from artifacts._shared.systemverilog_decoder import rendered_decoder
from artifacts._shared.systemverilog.artifacts import outputs


def generate(definition, context):
    rendered = rendered_decoder(context)
    return outputs(definition, {'package': rendered.package})


def validate(definition, context) -> None:
    rendered_decoder(context)
