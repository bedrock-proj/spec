"""Strict rendering for repository-owned SystemVerilog templates."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Mapping


_TEMPLATE_ROOT = Path(__file__).with_name("templates")
_MARKER = re.compile(r"@@([A-Z][A-Z0-9_]*)@@")


def render_template(name: str, sections: Mapping[str, object]) -> str:
    """Render *name* and reject missing, unused, or unresolved sections."""
    text = (_TEMPLATE_ROOT / name).read_text(encoding="utf-8")
    required = set(_MARKER.findall(text))
    supplied = set(sections)
    if missing := required - supplied:
        raise ValueError(f"missing template sections for {name}: {sorted(missing)}")
    if unused := supplied - required:
        raise ValueError(f"unused template sections for {name}: {sorted(unused)}")
    for key, value in sections.items():
        text = text.replace(f"@@{key}@@", str(value))
    if unresolved := _MARKER.findall(text):
        raise ValueError(f"unresolved template sections for {name}: {unresolved}")
    return text
