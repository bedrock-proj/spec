"""Command-line entry point for ISA authoring tools."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Sequence

from engine.artifacts.generate import artifact_context
from engine.artifacts.registry import ArtifactGeneratorRegistry, load_artifact_registry
from engine.artifacts.write import write_artifacts
from engine.check import check_workspace
from engine.diagnostics import (
    Diagnostic,
    DiagnosticBag,
    Severity,
    render_diagnostics_json,
    render_diagnostics_text,
)
from engine.isa.encoding import resolve_encoding_form
from engine.isa.encoding_reservations import EncodingReservationError
from engine.isa.encoding_reservations import reservation_cubes
from engine.isa.encoding_space import (
    CandidateOutsideNamespaceError,
    check_candidate_encoding_space,
    entries_encoding_space,
    holes_encoding_space,
    summaries_encoding_space,
)
from engine.isa.project import IsaProject
from engine.isa.catalog import ProjectLookupError
from engine.observability import configure_logging, log_caught_exception, log_phase
from engine.workspace import SpecWorkspace, load_workspace

_LOGGER = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m engine")
    parser.add_argument(
        "--isa-root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "isa",
        help="ISA source root (default: repository/isa)",
    )
    logging_group = parser.add_mutually_exclusive_group()
    logging_group.add_argument(
        "--verbose",
        action="store_true",
        help="report major execution phases and elapsed time on stderr",
    )
    logging_group.add_argument(
        "--debug",
        action="store_true",
        help="report detailed phases and caught exception tracebacks on stderr",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser(
        "check", help="validate authored specification sources"
    )
    check.add_argument(
        "targets", nargs="*", help="instruction names, references, or paths"
    )
    check.add_argument(
        "--format", choices=("text", "json"), default="text", dest="output_format"
    )
    encoding_space = subparsers.add_parser(
        "encoding-space", help="inspect named opcode spaces without editing sources"
    )
    space_commands = encoding_space.add_subparsers(
        dest="encoding_space_command", required=True
    )

    summary = space_commands.add_parser("summary", help="show occupancy by class")
    _add_format(summary)

    entries = space_commands.add_parser(
        "entries", help="list assigned encodings in a class"
    )
    entries.add_argument("encoding_class", metavar="CLASS")
    _add_encoding_space_scope(entries)
    entries.add_argument("--grep", help="case-insensitive instruction/form filter")
    _add_format(entries)

    candidate = space_commands.add_parser("check", help="check a candidate prefix")
    candidate.add_argument("encoding_class", metavar="CLASS")
    candidate.add_argument("pattern", metavar="PATTERN")
    candidate.add_argument("--space", help="named operator space")
    _add_format(candidate)

    holes = space_commands.add_parser("holes", help="list maximal free prefixes")
    holes.add_argument("encoding_class", metavar="CLASS")
    _add_encoding_space_scope(holes)
    holes.add_argument(
        "--include-reclaimed",
        action="store_true",
        help="treat constraint-reclaimed slots as available",
    )
    holes.add_argument("--min-slots", type=int, default=1)
    holes.add_argument("--max-slots", type=int)
    holes.add_argument("--limit", type=int, default=32)
    holes.add_argument("--sort", choices=("address", "size"), default="address")
    _add_format(holes)

    docs = subparsers.add_parser(
        "docs", help="validate and compile reader-facing documents"
    )
    docs.add_argument("action", choices=("validate", "build"))
    docs.add_argument(
        "--output-root",
        type=Path,
        default=Path("output"),
        help="generated document root (default: output)",
    )
    docs.add_argument(
        "--latexmk",
        default=os.environ.get("LATEXMK", "latexmk"),
        help="latexmk executable",
    )

    artifacts = subparsers.add_parser(
        "artifacts", help="discover and generate declared specification artifacts"
    )
    artifacts.add_argument("action", choices=("list", "generate"))
    artifacts.add_argument(
        "artifact_ids",
        nargs="*",
        metavar="ARTIFACT",
        help="artifact ids (default for generate: all)",
    )
    artifacts.add_argument(
        "--output-root",
        type=Path,
        default=Path("output"),
        help="generated artifact root (default: output)",
    )
    return parser


def _add_format(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format", choices=("text", "json"), default="text", dest="output_format"
    )


def _add_encoding_space_scope(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--space", help="named operator space")
    parser.add_argument(
        "--leading", help="leading 0/1/? prefix; omitted bits remain wildcards"
    )


def _load_failure(root: Path, error: Exception) -> DiagnosticBag:
    if isinstance(error, EncodingReservationError):
        return error.diagnostics
    if isinstance(error, ProjectLookupError):
        code = f"project.lookup.{error.reason.value.replace('_', '-')}"
    elif isinstance(error, CandidateOutsideNamespaceError):
        code = "encoding-space.candidate-outside-namespace"
    else:
        code = "project.load"
    return DiagnosticBag(
        [
            Diagnostic(
                Severity.ERROR,
                code,
                root,
                str(error),
            )
        ]
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging(verbose=args.verbose, debug=args.debug, stream=sys.stderr)
    try:
        workspace = load_workspace(args.isa_root.resolve().parent)
        provider = workspace.require_provider("isa")
        if not isinstance(provider, IsaProject):
            raise TypeError("workspace isa provider must be an IsaProject")
        project = provider
    except (OSError, ValueError) as error:
        log_caught_exception(_LOGGER, "workspace.load", error)
        diagnostics = _load_failure(args.isa_root, error)
        _emit_diagnostics(args, diagnostics)
        return 1

    with log_phase(_LOGGER, "command.run", command=args.command) as phase:
        if args.command == "encoding-space":
            result = _run_encoding_space(args, project)
        elif args.command == "docs":
            result = _run_docs(args, workspace)
        elif args.command == "artifacts":
            result = _run_artifacts(args, workspace)
        else:
            result = _run_check(args, workspace, project)
        phase["status"] = result
    return result


def _run_check(
    args: argparse.Namespace,
    workspace: SpecWorkspace,
    project: IsaProject,
) -> int:
    try:
        diagnostics = check_workspace(workspace, args.targets)
    except (OSError, ValueError) as error:
        log_caught_exception(_LOGGER, "check", error)
        diagnostics = _load_failure(args.isa_root, error)

    if diagnostics or args.output_format == "json":
        _emit_diagnostics(args, diagnostics)
    else:
        selected = project.catalog.select(args.targets)
        instruction_count = len(selected)
        form_count = sum(len(bundle.encodings.forms) for bundle in selected)
        instruction_label = "instruction" if instruction_count == 1 else "instructions"
        encoding_label = "encoding" if form_count == 1 else "encodings"
        print(
            f"checked {instruction_count} {instruction_label}, "
            f"{form_count} {encoding_label}: ok"
        )
    return 1 if diagnostics.has_errors else 0


def _emit_diagnostics(args: argparse.Namespace, diagnostics: DiagnosticBag) -> None:
    output_format = getattr(args, "output_format", "text")
    if output_format == "json":
        print(render_diagnostics_json(diagnostics))
        return
    rendered = render_diagnostics_text(diagnostics)
    if rendered:
        print(rendered, file=sys.stderr if diagnostics.has_errors else sys.stdout)


def _run_docs(args: argparse.Namespace, workspace: SpecWorkspace) -> int:
    try:
        registry = load_artifact_registry(workspace)
        generators = tuple(
            registry.definition(artifact_id)
            for artifact_id in registry.artifact_ids
            if "document" in registry.definition(artifact_id).outputs
        )
        context = artifact_context(registry, workspace, args.output_root)
        results = tuple(
            registry.entrypoint(generator.id, "build")(
                generator,
                context,
                compile_pdf=args.action == "build",
                latexmk=args.latexmk,
            )
            for generator in generators
        )
    except (OSError, RuntimeError, ValueError) as error:
        log_caught_exception(_LOGGER, "document.build", error)
        print(f"document command failed: {error}", file=sys.stderr)
        return 1
    for generator, result in zip(generators, results, strict=True):
        print(
            f"{generator.id} TeX validation: "
            f"{'passed' if result.report.passed else 'failed'}"
        )
        print(f"TeX: {result.tex}")
        print(f"validation report: {result.report_path}")
        if result.pdf is not None:
            print(f"PDF: {result.pdf}")
            print(f"PDF validation: {result.pdf_report}")
    return 0 if all(result.report.passed for result in results) else 1


def _run_artifacts(args: argparse.Namespace, workspace: SpecWorkspace) -> int:
    try:
        registry = load_artifact_registry(workspace)
        if args.action == "list":
            for artifact_id in registry.artifact_ids:
                generator = registry.definition(artifact_id)
                print(f"{artifact_id}\t{generator.source}")
            return 0
        selected = tuple(args.artifact_ids) or registry.artifact_ids
        context = artifact_context(registry, workspace, args.output_root)
        writer = write_artifacts
        for artifact_id in selected:
            artifacts = context.generate(artifact_id, None)
            written = writer(artifacts, args.output_root)
            print(f"generated {artifact_id}:")
            for path in written:
                print(f"  {path}")
        return 0
    except (NotImplementedError, OSError, ValueError) as error:
        log_caught_exception(_LOGGER, "artifact.command", error)
        print(f"artifact generation failed: {error}", file=sys.stderr)
        return 1


def _run_encoding_space(args: argparse.Namespace, project: IsaProject) -> int:
    try:
        forms = tuple(
            (bundle.reference, bundle.encodings.source, resolve_encoding_form(
                bundle.instruction, form,
                field_types=project.types.field_types,
                payload_types=project.types.payload_types,
                ea_modes=project.catalog.ea_modes,
                registers=project.registers,
            ))
            for bundle in project.catalog.select() for form in bundle.encodings.forms
        )
        reservations = reservation_cubes(project.encoding_reservations)
        if args.encoding_space_command == "summary":
            summaries = summaries_encoding_space(forms, reservations=reservations)
            if args.output_format == "json":
                print(
                    json.dumps(
                        [
                            {
                                "class": item.encoding_class,
                                "width": item.width,
                                "forms": item.forms,
                                "namespace": item.namespace_slots,
                                "assigned": item.assigned_slots,
                                "reclaimed": item.reclaimed_slots,
                                "reserved": item.reserved_slots,
                                "clean_free": item.clean_free_slots,
                                "remaining": item.remaining_slots,
                            }
                            for item in summaries
                        ],
                        indent=2,
                    )
                )
            else:
                print(
                    "class       bits  forms       namespace        assigned       reclaimed        reserved      clean-free       remaining"
                )
                for item in summaries:
                    print(
                        f"{item.encoding_class:<11}{item.width:>4}{item.forms:>7}"
                        f"{item.namespace_slots:>16,}{item.assigned_slots:>16,}"
                        f"{item.reclaimed_slots:>16,}{item.reserved_slots:>16,}"
                        f"{item.clean_free_slots:>16,}"
                        f"{item.remaining_slots:>16,}"
                    )
            return 0

        if args.encoding_space_command == "entries":
            entries = entries_encoding_space(
                forms,
                args.encoding_class,
                space=args.space,
                leading=args.leading,
                grep=args.grep,
            )
            if args.output_format == "json":
                print(
                    json.dumps(
                        [
                            {
                                "id": entry.name,
                                "instruction": f"{entry.owner}.{entry.mnemonic}",
                                "pattern": entry.pattern,
                                "raw": entry.raw_slots,
                                "assigned": entry.assigned_slots,
                                "reclaimed": entry.reclaimed_slots,
                                "source": str(entry.source),
                            }
                            for entry in entries
                        ],
                        indent=2,
                    )
                )
            else:
                print("id  pattern  raw  assigned  reclaimed")
                for entry in entries:
                    print(
                        f"{entry.name}  {entry.pattern}  {entry.raw_slots:,}  "
                        f"{entry.assigned_slots:,}  {entry.reclaimed_slots:,}"
                    )
            return 0

        if args.encoding_space_command == "check":
            result = check_candidate_encoding_space(
                forms,
                args.encoding_class,
                args.pattern,
                reservations=reservations,
                space=args.space,
            )
            if args.output_format == "json":
                print(
                    json.dumps(
                        {
                            "class": result.encoding_class,
                            "pattern": result.pattern,
                            "state": result.state,
                            "slots": result.slots,
                            "assigned": result.assigned_slots,
                            "reclaimed": result.reclaimed_slots,
                            "reserved": result.reserved_slots,
                            "clean_free": result.clean_free_slots,
                            "assigned_entries": [
                                entry.name for entry in result.assigned_entries
                            ],
                            "reclaimed_entries": [
                                entry.name for entry in result.reclaimed_entries
                            ],
                            "reservations": list(result.reservations),
                        },
                        indent=2,
                    )
                )
            else:
                print(f"class:      {result.encoding_class}")
                print(f"pattern:    {result.pattern}")
                print(f"state:      {result.state}")
                print(f"slots:      {result.slots:,}")
                print(f"assigned:   {result.assigned_slots:,}")
                print(f"reclaimed:  {result.reclaimed_slots:,}")
                print(f"reserved:   {result.reserved_slots:,}")
                print(f"clean-free: {result.clean_free_slots:,}")
                if result.assigned_entries:
                    print("assigned overlaps:")
                    for entry in result.assigned_entries:
                        print(f"  {entry.name}  {entry.pattern}")
                if result.reclaimed_entries:
                    print("reclaimed overlaps:")
                    for entry in result.reclaimed_entries:
                        print(f"  {entry.name}  {entry.pattern}")
                if result.reservations:
                    print("reservation overlaps:")
                    for name in result.reservations:
                        reservation = project.encoding_reservations.reservations[name]
                        print(f"  {name}  {reservation.summary}")
            return 1 if result.assigned_slots or result.reserved_slots else 0

        holes = holes_encoding_space(
            forms,
            args.encoding_class,
            reservations=reservations,
            space=args.space,
            leading=args.leading,
            include_reclaimed=args.include_reclaimed,
            min_slots=args.min_slots,
            max_slots=args.max_slots,
            limit=args.limit,
            sort=args.sort,
        )
        if args.output_format == "json":
            print(
                json.dumps(
                    [
                        {
                            "pattern": hole.pattern,
                            "first": hole.cube.first,
                            "last": hole.cube.last,
                            "slots": hole.slots,
                        }
                        for hole in holes
                    ],
                    indent=2,
                )
            )
        else:
            print("pattern  first..last  slots")
            for hole in holes:
                digits = (hole.cube.width + 3) // 4
                print(
                    f"{hole.pattern}  0x{hole.cube.first:0{digits}x}.."
                    f"0x{hole.cube.last:0{digits}x}  {hole.slots:,}"
                )
        return 0
    except (OSError, ValueError) as error:
        log_caught_exception(_LOGGER, "encoding-space.command", error)
        diagnostics = _load_failure(args.isa_root, error)
        _emit_diagnostics(args, diagnostics)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
