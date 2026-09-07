"""Values and operations owned by this specification boundary."""

from __future__ import annotations


def intrinsic_group_header(group_id: str) -> str:
    """Return the public header name for one intrinsic output group."""

    return f"bedrock{group_id}intrin.h"


def intrinsic_collection_header(collection_id: str) -> str:
    """Return the conventional umbrella header for a group collection."""

    infix = "" if collection_id == "public" else collection_id
    return f"bedrock{infix}intrin.h"


def intrinsic_spelling(intrinsic_id: str) -> str:
    return f"__bedrock_{intrinsic_id}"


def clang_builtin_spelling(intrinsic_id: str) -> str:
    return f"__builtin_bedrock_{intrinsic_id}"
