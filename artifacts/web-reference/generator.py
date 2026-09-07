"""Generate the complete MkDocs publication from current document artifacts."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import asdict
from importlib import import_module
from pathlib import Path, PurePosixPath

from engine.artifacts.definition import GeneratedArtifact, GeneratedArtifactSet
from engine.artifacts.generate import ArtifactGenerationContext

site_build = import_module("artifacts.web-reference.site.build")
site_projection = import_module("artifacts.web-reference.site.projection")

_DOCUMENTS = (
    (
        "isa-reference",
        "isa",
        "Programmer's Reference Manual",
        "isa_reference.pdf",
    ),
    (
        "elf-abi",
        "elf-abi",
        "ELF ABI",
        "bedrock-elf-abi.pdf",
    ),
    ("c-abi", "c-abi", "C ABI", "bedrock-c-abi.pdf"),
    (
        "c-target-intrinsics",
        "target-intrinsics",
        "Target Intrinsics",
        "bedrock-target-intrinsics.pdf",
    ),
)


def generate(definition, context: ArtifactGenerationContext) -> GeneratedArtifactSet:
    site = project_publication(context)
    registry = context.registry
    environment = dict(os.environ)
    environment["TEXINPUTS"] = os.pathsep.join(
        (str(context.workspace.root), str(context.workspace.root / "style"), "")
    )
    with tempfile.TemporaryDirectory(prefix="bedrock-reference-site-") as raw:
        stage = Path(raw)
        sources = stage / "documents"
        sources.mkdir()
        documents = []
        for artifact_id, site_id, title, pdf_name in _DOCUMENTS:
            document_generator = registry.definition(artifact_id)
            output = document_generator.outputs["document"]
            artifact = context.generate(artifact_id, None).artifact(output)
            if not isinstance(artifact.content, str):
                raise TypeError(f"{artifact_id}: TeX artifact must be text")
            source = sources / Path(output).name
            source.write_text(artifact.content, encoding="utf-8")
            derived = document_generator.derived_outputs
            pdf = context.output_root / derived["compiled-document"]
            validation = context.output_root / derived["pdf-validation"]
            if not pdf.is_file() or not validation.is_file():
                raise site_build.SiteOutputError(
                    f"{artifact_id}: build and validate the document before generating the reference site"
                )
            try:
                metrics = json.loads(validation.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise site_build.SiteOutputError(
                    f"{artifact_id}: invalid PDF validation report: {validation}"
                ) from error
            if not isinstance(metrics, dict) or not metrics.get("pages"):
                raise site_build.SiteOutputError(
                    f"{artifact_id}: PDF validation report has no page result"
                )
            documents.append(
                site_build.SiteDocument(
                    artifact_id, site_id, title, source, pdf, pdf_name
                )
            )
        site_root = stage / "site"
        metrics = site_build.build_site(
            site,
            documents,
            site_root,
            repository=context.workspace.root,
            source_revision=revision(context.workspace.root),
            pandoc=os.environ.get("PANDOC", "pandoc"),
            mkdocs=os.environ.get("MKDOCS", "mkdocs"),
            latexmk=os.environ.get("LATEXMK", "latexmk"),
            environment=environment,
        )
        reference = {
            "documents": [item[2] for item in _DOCUMENTS],
            "metrics": asdict(metrics),
        }
        assets = site_root / "assets"
        assets.mkdir(exist_ok=True)
        (assets / "reference.json").write_text(
            json.dumps(reference, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        artifacts = tuple(
            (
                GeneratedArtifact(
                    definition.outputs["publication"] / path.relative_to(site_root), path.read_bytes()
                )
                for path in sorted(site_root.rglob("*"))
                if path.is_file()
            )
        )
    return GeneratedArtifactSet(artifacts, artifact_id=definition.id)


def validate(definition, context: ArtifactGenerationContext) -> None:
    """Validate the public site projection without compiling or publishing it."""
    project_publication(context)
    if set(definition.outputs) != {"publication"}:
        raise ValueError(f"{definition.source}: web output must select one publication root")


def revision(repository: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def project_publication(context):
    key = (project_publication, id(context.workspace))

    def project_selected():
        registry = context.registry
        manual = registry.entrypoint("isa-reference", "project")(
            registry.definition("isa-reference"), context
        )
        documents = []
        for artifact_id, site_id, title, pdf_name in _DOCUMENTS:
            definition = registry.definition(artifact_id)
            source = registry.entrypoint(artifact_id, "render_source")(definition, context)
            if not isinstance(source, str):
                raise TypeError(f"{artifact_id}: TeX artifact must be text")
            documents.append(
                (site_id, title, PurePosixPath("downloads") / pdf_name, source)
            )
        return site_projection.project_site(
            documents, manual.instruction_groups
        )

    return context.shared_result(key, project_selected)
