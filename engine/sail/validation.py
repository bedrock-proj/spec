"""Validate instruction-owned Sail entry declarations."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from engine.isa.catalog import InstructionBundle

if TYPE_CHECKING:
    from engine.sail.composition import SailProgram


def _sail_code(text: str) -> str:
    """Mask comments and strings while preserving source line boundaries."""
    result = list(text)
    index = 0
    depth = 0
    quoted = False
    line_comment = False
    while index < len(text):
        character = text[index]
        pair = text[index:index + 2]
        if line_comment:
            if character == "\n":
                line_comment = False
        elif quoted:
            if character == "\\" and index + 1 < len(text):
                result[index:index + 2] = [" ", "\n" if text[index + 1] == "\n" else " "]
                index += 2
                continue
            if character == '"':
                quoted = False
        elif pair == "/*":
            depth += 1
            result[index:index + 2] = [" ", " "]
            index += 2
            continue
        elif depth and pair == "*/":
            depth -= 1
            result[index:index + 2] = [" ", " "]
            index += 2
            continue
        elif not depth and pair == "//":
            line_comment = True
        elif not depth and character == '"':
            quoted = True
        elif not depth:
            index += 1
            continue
        if character != "\n":
            result[index] = " "
        index += 1
    return "".join(result)


def missing_sail_entry(semantics: InstructionBundle) -> str | None:
    source = semantics.semantics
    if not source.is_file():
        return None
    text = _sail_code(source.read_text(encoding="utf-8"))
    return (
        f"execute_{semantics.instruction.mnemonic}"
        if re.search(
            rf"(?m)^\s*function\s+{re.escape(f'execute_{semantics.instruction.mnemonic}')}\s*\(",
            text,
        )
        is None
        else None
    )


def require_sail_entries(program: "SailProgram") -> None:
    for semantics in program.implementation_bundles:
        missing = missing_sail_entry(semantics)
        if missing is not None:
            raise ValueError(
                f"{semantics.instruction.source}: Sail entry {missing} "
                f"is not defined by {semantics.semantics}"
            )
    provider = program.execution_provider
    if provider is None:
        return
    text = _sail_code(provider.provider.read_text(encoding="utf-8"))
    for entry in (
        "execute_operation_entry",
        "cpuid_flag_enabled",
        "event_from_fault",
    ):
        if re.search(rf"(?m)^\s*function\s+{entry}\s*\(", text) is None:
            raise ValueError(
                f"{provider.source}: execution provider does not define "
                f"{entry} in {provider.provider}"
            )


def check_sail_bundle(bundle: InstructionBundle):
    from engine.diagnostics import _error

    missing_entry = missing_sail_entry(bundle)
    if missing_entry is not None:
        yield _error(
            "sail.entry",
            bundle.instruction.source,
            f"instruction-owned Sail entry {missing_entry!r} is not defined by "
            f"{bundle.semantics}",
            "mnemonic",
        )
