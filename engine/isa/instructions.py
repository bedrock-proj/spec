"""Immutable instruction values and explicit source editing operations."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import yaml
from jsonschema import Draft202012Validator

from engine.reference import Reference
from engine.source.yaml import load_yaml


class UnknownRepeatObservedValueError(ValueError):
    """A repeat contract names neither an operand nor the computed result."""


@dataclass(frozen=True, slots=True)
class InstructionOperand:
    role: str
    access: str
    value_type: str
    domain: str | None = None


@dataclass(frozen=True, slots=True)
class Repcc:
    observed_value: str


@dataclass(frozen=True, slots=True)
class Instruction:
    """Validated instruction meaning; editing uses a detached source mapping."""

    source: Path
    name: str
    summary: str
    route: str
    privileged: bool
    operands: Mapping[str, InstructionOperand]
    width_suffix_aliases: bool = False
    repeat: Literal["rep"] | Repcc | None = None
    additional_cpuid_flags: tuple[Reference[object], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "operands", MappingProxyType(dict(self.operands)))
        object.__setattr__(self, "additional_cpuid_flags", tuple(self.additional_cpuid_flags))

    @property
    def mnemonic(self) -> str:
        return self.source.parent.name

    def to_dict(self) -> dict[str, Any]:
        """Serialize meaning into mutable source data, preserving operand aliases."""
        memo: dict[int, dict[str, str]] = {}
        operands = {}
        for name, operand in self.operands.items():
            identity = id(operand)
            if identity not in memo:
                value = {
                    "role": operand.role,
                    "access": operand.access,
                    "value_type": operand.value_type,
                }
                if operand.domain is not None:
                    value["domain"] = operand.domain
                memo[identity] = value
            operands[name] = memo[identity]
        data: dict[str, Any] = {
            "name": self.name,
            "summary": self.summary,
            "route": self.route,
            "privileged": self.privileged,
            "operands": operands,
        }
        if self.width_suffix_aliases:
            data["assembly"] = {"width_suffix_aliases": True}
        if self.repeat == "rep":
            data["repeat"] = {"type": "rep"}
        elif isinstance(self.repeat, Repcc):
            data["repeat"] = {"type": "repcc", "observed_value": self.repeat.observed_value}
        if self.additional_cpuid_flags:
            data["additional_cpuid_flags"] = [
                ".".join((reference.owner, *reference.path, reference.element))
                for reference in self.additional_cpuid_flags
            ]
        return data


def validate_instruction(
    data: Mapping[str, Any], *, source: str | Path, schema: Mapping[str, object]
) -> None:
    """Reject invalid source meaning without reading or writing a file."""
    source = Path(source)
    if source.name != "instruction.yaml":
        raise ValueError(f"{source}: expected a file named instruction.yaml")
    errors = sorted(
        Draft202012Validator(schema).iter_errors(data),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path)
        where = f" at {location}" if location else ""
        raise ValueError(f"{source}{where}: {error.message}")
    repeat = data.get("repeat")
    if repeat and repeat["type"] == "repcc":
        observed = repeat["observed_value"]
        if observed != "computed" and observed not in data["operands"]:
            raise UnknownRepeatObservedValueError(
                f"{source}: repeat observed_value {observed!r} does not name an operand"
            )


def decode_instruction(
    data: Mapping[str, Any], *, source: str | Path, schema: Mapping[str, object]
) -> Instruction:
    validate_instruction(data, source=source, schema=schema)
    memo: dict[int, InstructionOperand] = {}
    operands = {}
    for name, raw in data["operands"].items():
        identity = id(raw)
        if identity not in memo:
            memo[identity] = InstructionOperand(
                raw["role"], raw["access"], raw["value_type"], raw.get("domain")
            )
        operands[name] = memo[identity]
    raw_repeat = data.get("repeat")
    repeat = (
        None if raw_repeat is None else
        "rep" if raw_repeat["type"] == "rep" else
        Repcc(raw_repeat["observed_value"])
    )
    return Instruction(
        source=Path(source),
        name=data["name"], summary=data["summary"], route=data["route"],
        privileged=data["privileged"], operands=operands,
        width_suffix_aliases=data.get("assembly", {}).get("width_suffix_aliases", False),
        repeat=repeat,
        additional_cpuid_flags=tuple(Reference.parse(raw) for raw in data.get("additional_cpuid_flags", ())),
    )


def load_instruction(source: str | Path, *, schema: Mapping[str, object]) -> Instruction:
    return decode_instruction(load_yaml(source), source=source, schema=schema)


def save_instruction(
    data: Mapping[str, Any], destination: str | Path, *, schema: Mapping[str, object]
) -> Instruction:
    """Validate and serialize before replacing one authored source file.

    Preparation failures leave the source intact. A successful replacement
    publishes the validated serialization.
    """
    destination = Path(destination)
    value = decode_instruction(data, source=destination, schema=schema)
    content = yaml.safe_dump(
        value.to_dict(), sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    temporary = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix="bedrock-instruction-", delete=False
    )
    staged = Path(temporary.name)
    try:
        with temporary:
            temporary.write(content)
        if destination.exists():
            staged.chmod(stat.S_IMODE(destination.stat().st_mode))
        os.replace(staged, destination)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    return value
