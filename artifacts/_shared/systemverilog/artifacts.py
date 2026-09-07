"""Values and operations owned by this specification boundary."""

from __future__ import annotations

import re
from engine.artifacts.definition import GeneratedArtifact, GeneratedArtifactSet


def _generated(body: str) -> str:
    return (
        "// Generated from canonical Bedrock ISA definitions. Do not edit.\n"
        + body.rstrip()
        + "\n"
    )


def _identifier(value: object) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value)).strip("_").upper()
    if not text:
        raise ValueError(f"cannot form a SystemVerilog identifier from {value!r}")
    return f"N_{text}" if text[0].isdigit() else text


def outputs(definition, contents: dict[str, str]) -> GeneratedArtifactSet:
    declared = definition.outputs
    if set(declared) != set(contents):
        raise ValueError(
            f"{definition.source}: declared output roles {sorted(declared)} do not match rendered output roles {sorted(contents)}"
        )
    return GeneratedArtifactSet(
        tuple(
            (
                GeneratedArtifact(declared[role], content)
                for role, content in contents.items()
            )
        ),
        definition.id,
    )
