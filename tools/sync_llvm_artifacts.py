#!/usr/bin/env python3
"""Synchronize catalog-generated Bedrock ABI/MC files into llvm-project."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

SPEC_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LLVM_ROOT = SPEC_ROOT.parent / "llvm-project"
sys.path.insert(0, str(SPEC_ROOT))

from engine.artifacts.registry import load_artifact_registry  # noqa: E402
from engine.artifacts.generate import artifact_context  # noqa: E402
from engine.workspace import load_workspace  # noqa: E402

DESTINATIONS = {
    ("llvm-mc-tablegen", "BedrockGenISACatalog.td"):
        Path("llvm/lib/Target/Bedrock/BedrockGenISACatalog.td"),
    ("llvm-elf-abi", "ELFRelocs/Bedrock.def"):
        Path("llvm/include/llvm/BinaryFormat/ELFRelocs/Bedrock.def"),
    ("llvm-elf-abi", "BedrockGenELFABI.inc"):
        Path("llvm/include/llvm/BinaryFormat/BedrockGenELFABI.inc"),
    ("llvm-c-abi", "BedrockGenCallingConv.td"):
        Path("llvm/include/llvm/TargetParser/BedrockGenCallingConv.td"),
    ("llvm-c-abi", "BedrockGenCABI.inc"):
        Path("llvm/include/llvm/TargetParser/BedrockGenCABI.inc"),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "llvm_root",
        nargs="?",
        type=Path,
        default=DEFAULT_LLVM_ROOT,
        help="llvm-project checkout (default: sibling llvm-project)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report stale generated files without modifying them",
    )
    args = parser.parse_args()

    llvm_root = args.llvm_root.resolve()
    if not (llvm_root / "llvm" / "CMakeLists.txt").is_file():
        parser.error(f"not an llvm-project checkout: {llvm_root}")

    workspace = load_workspace(SPEC_ROOT)
    registry = load_artifact_registry(workspace)
    context = artifact_context(registry, workspace, SPEC_ROOT)
    stale: list[Path] = []
    for artifact_id in ("llvm-mc-tablegen", "llvm-elf-abi", "llvm-c-abi"):
        generated = context.generate(artifact_id, None)
        for artifact in generated.artifacts:
            key = (artifact_id, str(artifact.relative_path))
            try:
                relative_destination = DESTINATIONS[key]
            except KeyError as error:
                raise ValueError(
                    f"no llvm-project destination for {artifact_id}:"
                    f"{artifact.relative_path}"
                ) from error
            destination = llvm_root / relative_destination
            current = (
                destination.read_text(encoding="utf-8")
                if destination.is_file()
                else None
            )
            if current == artifact.content:
                continue
            stale.append(relative_destination)
            if not args.check:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(artifact.content, encoding="utf-8")

    if stale:
        action = "stale" if args.check else "updated"
        for path in stale:
            print(f"{action}: {path}")
        return 1 if args.check else 0
    print("Bedrock LLVM generated artifacts are current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
