"""Document artifact publication and gated PDF compilation."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from engine.artifacts.definition import (
    ArtifactDefinition,
    GeneratedArtifact,
    GeneratedArtifactSet,
)
from engine.artifacts.generate import ArtifactGenerationContext
from engine.artifacts.write import write_artifacts
from artifacts._shared.latex import TexValidationReport
from artifacts._shared.latex import validate_tex, create_target_labels, render_latex_source
from engine.documents.sources import project_source, source_references, source_target_blocks
from engine.documents.targets import PublicTargetCatalog
from engine.observability import log_phase

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DocumentBuildResult:
    report: TexValidationReport
    tex: Path
    report_path: Path
    pdf: Path | None = None
    log: Path | None = None
    pdf_report: Path | None = None


def build_document(
    definition: ArtifactDefinition,
    context: ArtifactGenerationContext,
    *,
    compile_pdf: bool,
    latexmk: str = "latexmk",
) -> DocumentBuildResult:
    output = context.output_root
    repository = context.workspace.root
    if output in {Path("/").resolve(), Path.home().resolve(), repository}:
        raise ValueError(f"refusing unsafe document output root: {output}")
    generated = context.generate(definition.id, None)
    document_output = definition.outputs["document"]
    tex = generated.artifact(document_output).content
    if not isinstance(tex, str):
        raise TypeError(f"{definition.id}: TeX artifact must be text")
    derived = definition.derived_outputs
    required_derived = {
        "tex-validation",
        "compiled-document",
        "compile-log",
        "pdf-validation",
    }
    missing_derived = sorted(required_derived - set(derived))
    if missing_derived:
        raise ValueError(
            f"{definition.id}: document artifact is missing derived "
            f"outputs {missing_derived}"
        )
    with log_phase(_LOGGER, "document.tex.validate") as phase:
        report = validate_tex(tex)
        phase["issues"] = len(report.issues)
    validated = GeneratedArtifactSet(
        (
            *generated.artifacts,
            GeneratedArtifact(derived["tex-validation"], report.render_json()),
        ),
        artifact_id=definition.id,
    )
    definition.validate_owned(validated)
    write_artifacts(validated, output)
    tex_path = output / document_output
    report_path = output / derived["tex-validation"]
    result = DocumentBuildResult(report, tex_path, report_path)
    if not compile_pdf or not report.passed:
        return result
    with tempfile.TemporaryDirectory(prefix=f"bedrock-{definition.id}-") as directory:
        scratch_root = Path(directory)
        with log_phase(
            _LOGGER,
            "document.latex.compile",
            executable=latexmk,
        ):
            compiled = compile_latex(tex_path, scratch_root, repository, latexmk)
        with log_phase(_LOGGER, "document.pdf.validate") as phase:
            pdf_metrics = validate_pdf(compiled)
            phase["pages"] = pdf_metrics["pages"]
        published = GeneratedArtifactSet(
            (
                *validated.artifacts,
                GeneratedArtifact(
                    derived["compiled-document"], compiled.pdf.read_bytes()
                ),
                GeneratedArtifact(derived["compile-log"], compiled.log.read_bytes()),
                GeneratedArtifact(
                    derived["pdf-validation"],
                    json.dumps(pdf_metrics, indent=2, sort_keys=True) + "\n",
                ),
            ),
            artifact_id=definition.id,
        )
    definition.validate_owned(published)
    write_artifacts(published, output)
    pdf = output / derived["compiled-document"]
    log = output / derived["compile-log"]
    pdf_report = output / derived["pdf-validation"]
    return DocumentBuildResult(
        report,
        tex_path,
        report_path,
        pdf,
        log,
        pdf_report,
    )


FORBIDDEN_LOG_PATTERNS = (
    re.compile(r"LaTeX Warning: There were undefined references"),
    re.compile(r"LaTeX Warning: Reference .* undefined"),
    re.compile(r"Missing character:"),
    re.compile(r"destination with the same identifier"),
)


@dataclass(frozen=True, slots=True)
class CompiledPdf:
    pdf: Path
    log: Path


def compile_latex(
    source: Path,
    output_root: Path,
    repository: Path,
    executable: str,
) -> CompiledPdf:
    pdf_root = output_root / "pdf"
    pdf_root.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment["TEXINPUTS"] = os.pathsep.join(
        (str(repository), str(repository / "style"), "")
    )
    result = subprocess.run(
        [
            executable,
            "-pdf",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-file-line-error",
            f"-outdir={pdf_root}",
            str(source),
        ],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = "\n".join(part for part in (result.stdout, result.stderr) if part)
        raise RuntimeError(
            f"{source.stem} LaTeX compilation failed:\n" + detail[-12000:]
        )
    compiled = CompiledPdf(
        pdf_root / source.with_suffix(".pdf").name,
        pdf_root / source.with_suffix(".log").name,
    )
    if not compiled.pdf.is_file() or not compiled.log.is_file():
        raise RuntimeError("latexmk did not produce the expected PDF and log")
    return compiled


def validate_pdf(compiled: CompiledPdf) -> dict[str, object]:
    log_text = compiled.log.read_text(encoding="utf-8", errors="replace")
    failures = [
        pattern.pattern
        for pattern in FORBIDDEN_LOG_PATTERNS
        if pattern.search(log_text)
    ]
    if failures:
        raise RuntimeError("forbidden LaTeX diagnostics: " + ", ".join(failures))
    info = subprocess.run(
        ["pdfinfo", str(compiled.pdf)],
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    page_match = re.search(r"^Pages:\s+(\d+)$", info, flags=re.MULTILINE)
    size_match = re.search(r"^Page size:\s+(.+)$", info, flags=re.MULTILINE)
    if page_match is None or int(page_match.group(1)) < 1:
        raise RuntimeError("compiled PDF has no readable pages")
    fonts = subprocess.run(
        ["pdffonts", str(compiled.pdf)],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()[2:]
    if not fonts:
        raise RuntimeError("compiled PDF has no readable fonts")
    unembedded = []
    for row in fonts:
        columns = row.split()
        if len(columns) < 6 or columns[-5] != "yes":
            unembedded.append(row)
    if unembedded:
        raise RuntimeError("compiled PDF contains unembedded fonts")
    return {
        "pages": int(page_match.group(1)),
        "page_size": size_match.group(1) if size_match else None,
        "fonts": len(fonts),
        "unembedded_fonts": len(unembedded),
        "undefined_references": 0,
        "missing_characters": 0,
        "duplicate_destinations": 0,
        "overfull_boxes": len(re.findall(r"Overfull \\[hv]box", log_text)),
        "underfull_boxes": len(re.findall(r"Underfull \\[hv]box", log_text)),
    }


def authored_document_source(definition, context, catalogs, *, render_fragment) -> str:
    def selected():
        raw_source = definition.data.get("source")
        if not isinstance(raw_source, str):
            raise ValueError(f"{definition.source}: source must be a TeX path")
        root = context.workspace.root
        projection = project_source(root / raw_source, definition.source, root, catalogs, shared_result=context.shared_result)
        if projection.source.suffix != ".tex":
            raise ValueError(f"{definition.source}: invalid TeX source {raw_source!r}")
        targets = PublicTargetCatalog.create(catalogs["isa"].entities, source_target_blocks(projection), source_references(projection))
        content = render_latex_source(projection, create_target_labels(targets, {}), render_fragment)
        report = validate_tex(content)
        if not report.passed:
            raise ValueError(f"{definition.source}: invalid public document: {report.issues!r}")
        return content, definition, tuple(catalogs.values())
    return context.shared_result((authored_document_source, id(definition), render_fragment, tuple((name, id(value)) for name, value in catalogs.items())), selected)[0]


def authored_tex_generate(definition, content: str) -> GeneratedArtifactSet:
    return GeneratedArtifactSet(
        (GeneratedArtifact(definition.outputs["document"], content),), artifact_id=definition.id
    )
