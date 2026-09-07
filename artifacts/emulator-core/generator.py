"""Generate the Sail C core and its host-facing C adapter."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from engine.artifacts.definition import GeneratedArtifact, GeneratedArtifactSet
from engine.artifacts.generate import ArtifactGenerationContext
from engine.artifacts.write import write_artifacts


_TEMPLATE_ROOT = Path(__file__).with_name("templates")
_PRESERVED_FUNCTIONS = (
    "initial_cpu",
    "platform_reset",
    "decode_and_execute_full",
    "resume_transaction",
    "post_interrupt",
    "advance_time",
)


def _template(name: str) -> str:
    return (_TEMPLATE_ROOT / name).read_text()


def sail_compiler_key(executable) -> str:
    result = subprocess.run([executable, "--version"], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "Sail version query failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    executable = shutil.which(executable) or executable
    return f"{Path(executable).resolve()}\n{result.stdout}{result.stderr}"


def compile_sail_c(executable, project: Path, output_prefix: Path) -> tuple[str, str]:
    command = [
        executable,
        "--project",
        str(project),
        "--all-modules",
        "-c",
        "--c-no-main",
        "-O",
        "--static",
        "--c-specialize",
        "--Oconstant-fold",
    ]
    for function in _PRESERVED_FUNCTIONS:
        command.extend(("--c-preserve", function))
    command.extend(("-o", str(output_prefix)))
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "Sail C generation failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return (
        output_prefix.with_suffix(".c").read_text(),
        output_prefix.with_suffix(".h").read_text(),
    )


def generate(definition, context: ArtifactGenerationContext) -> GeneratedArtifactSet:
    executable = os.environ.get("SAIL", "sail")
    outputs = definition.outputs
    with tempfile.TemporaryDirectory(prefix="bedrock-emulator-core-") as directory:
        root = Path(directory).resolve()
        model_root = root / "model"
        source_alias = root / "source"
        source_alias.symlink_to(context.workspace.root, target_is_directory=True)
        model = context.registry.definition("sail-model")
        program = context.registry.entrypoint("sail-model", "project_program")(model, context)
        model_artifacts = context.registry.entrypoint("sail-model", "render_program")(
            model, program, model_root,
            source_root=context.workspace.root, source_alias=source_alias,
        )
        model.validate_generated(model_artifacts)
        write_artifacts(model_artifacts, model_root)
        fingerprint = _generation_fingerprint(
            model_artifacts,
            sail_compiler_key(executable),
            program.sources,
            context.workspace.root,
        )
        cached_c = context.output_root / outputs["implementation"]
        cached_h = context.output_root / outputs["model-header"]
        cached_stamp = context.output_root / outputs["generation-stamp"]
        cache_hit = (
            cached_c.is_file()
            and cached_h.is_file()
            and cached_stamp.is_file()
            and (cached_stamp.read_text() == fingerprint)
        )
        if cache_hit:
            generated_c = cached_c.read_text()
            generated_h = cached_h.read_text()
        else:
            generated_c, generated_h = compile_sail_c(
                executable, model_root / model.outputs["project"], root / "bedrock_core"
            )
            generated_c = _supply_library_main(generated_c, _template("model_main.c"))
            generated_c += "\n" + _template("bedrock_core_adapter.c")
    return GeneratedArtifactSet(
        (
            GeneratedArtifact(outputs["implementation"], generated_c),
            GeneratedArtifact(outputs["model-header"], generated_h),
            GeneratedArtifact(outputs["abi-header"], _template("bedrock_core_abi.h")),
            GeneratedArtifact(outputs["generation-stamp"], fingerprint),
        ),
        definition.id,
    )


def validate(definition, context: ArtifactGenerationContext) -> None:
    """Validate the source projection without invoking the Sail compiler."""
    model = context.registry.definition("sail-model")
    context.registry.entrypoint("sail-model", "validate")(model, context)
    for template in ("model_main.c", "bedrock_core_adapter.c", "bedrock_core_abi.h"):
        _template(template)
    required = {"implementation", "model-header", "abi-header", "generation-stamp"}
    if set(definition.outputs) != required:
        raise ValueError(f"{definition.source}: emulator core requires exactly {sorted(required)} output roles")


def _generation_fingerprint(
    model: GeneratedArtifactSet,
    compiler_cache_key: str,
    sail_sources: tuple[Path, ...],
    repository: Path,
) -> str:
    digest = hashlib.sha256()
    digest.update(b"bedrock-emulator-core-generation\0")
    digest.update(Path(__file__).read_bytes())
    digest.update(b"\0")
    digest.update(compiler_cache_key.encode())
    for artifact in sorted(model.artifacts, key=lambda item: item.relative_path):
        digest.update(b"\0path\0")
        digest.update(artifact.relative_path.as_posix().encode())
        digest.update(b"\0content\0")
        digest.update(artifact.content.encode())
    for source in sorted(sail_sources):
        digest.update(b"\0sail-source\0")
        digest.update(source.relative_to(repository).as_posix().encode())
        digest.update(b"\0")
        digest.update(source.read_bytes())
    for template in sorted(_TEMPLATE_ROOT.iterdir()):
        if template.is_file():
            digest.update(b"\0template\0")
            digest.update(template.name.encode())
            digest.update(b"\0")
            digest.update(template.read_bytes())
    return digest.hexdigest() + "\n"




def _supply_library_main(generated_c: str, library_main: str) -> str:
    """Satisfy Sail 0.20's retained model_main in --c-no-main output."""
    if "unit zmain(" in generated_c:
        return generated_c
    marker = '#include "bedrock_core.h"\n'
    if generated_c.count(marker) != 1:
        raise ValueError("generated Sail C is missing its model header include")
    return generated_c.replace(marker, marker + library_main, 1)
