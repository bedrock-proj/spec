"""Artifact entry contracts, invocation caches, and owned publication."""

import tempfile
import unittest
from importlib import import_module
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from engine.artifacts.definition import ArtifactDefinition, GeneratedArtifact, GeneratedArtifactSet, load_artifact_definition
from engine.artifacts.generate import artifact_context, generate_artifact
from engine.artifacts.registry import ArtifactGeneratorRegistry, load_artifact_registry
from engine.artifacts.write import write_artifacts
from engine.isa.configuration import IsaConfiguration
from engine.isa.model import SailUnit
from engine.reference import Reference
from engine.sail.composition import SailProgram
from engine.sail.dispatch import SailDispatchProjection
from engine.sail.registry import SailRegistryProjection
from engine.workspace import create_workspace, load_workspace


_sail_project = import_module("artifacts.sail-model.project")
_sail_generator = import_module("artifacts.sail-model.generator")
_emulator = import_module("artifacts.emulator-core.generator")
_writer = import_module("engine.artifacts.write")
_web_generator = import_module("artifacts.web-reference.generator")


def _program(root):
    core = SailUnit("base", "core", Reference.parse("base.core"), root / "model.yaml", (root / "core.sail",), ())
    boundary = SailUnit("base", "boundary", Reference.parse("base.boundary"), root / "model.yaml", (root / "boundary.sail",), (core.reference,))
    registry = SailRegistryProjection((), (), (), (), (), (), frozenset(), frozenset(), frozenset())
    return SailProgram(
        configuration=IsaConfiguration(()), bundles=(), implementation_bundles=(),
        sail_units=(core, boundary), execution_provider=None, sources=(*core.sources, *boundary.sources),
        forms=(), ea_forms=(), declaration_forms=(), declaration_ea_forms=(), registry=registry,
        dispatch=SailDispatchProjection((), False), registry_type_sources=(),
        unit_sources={core.reference: core.sources, boundary.reference: boundary.sources},
        unit_dependencies={core.reference: (), boundary.reference: (core,)},
    )


def _emulator_fixture(root):
    source = root / "isa/model.sail"
    source.parent.mkdir()
    source.write_text("function value() -> int = 1\n")
    model = ArtifactDefinition("sail-model", root / "sail-model/artifact.yaml", {"outputs": {"registry": "registry.sail", "catalog": "catalog.sail", "dispatch": "dispatch.sail", "project": "model.sail_project"}})
    unit = SailUnit("base", "decode", Reference.parse("base.decode"), root / "model.yaml", (source,), ())
    program = replace(_program(root), sail_units=(unit,), sources=(source,), unit_sources={unit.reference: (source,)}, unit_dependencies={unit.reference: ()})
    emulator = ArtifactDefinition("emulator-core", root / "emulator-core/artifact.yaml", {
        "depends-on": ["sail-model"], "outputs": {
            "implementation": "core/bedrock_core.c", "model-header": "core/bedrock_core.h",
            "abi-header": "core/bedrock_core_abi.h", "generation-stamp": "core/.generation-stamp",
        },
    })

    def project_program(definition, context):
        return program

    def render_program(
        definition,
        program,
        output_root,
        *,
        source_root,
        source_alias,
    ):
        return GeneratedArtifactSet(
            tuple(
                GeneratedArtifact(path, f"synthetic {role}\n")
                for role, path in definition.outputs.items()
            ),
            definition.id,
        )

    def generate(definition, context):
        return render_program(
            definition,
            program,
            context.output_root,
            source_root=context.workspace.root,
            source_alias=context.workspace.root,
        )

    def validate(definition, context):
        if set(definition.outputs) != {"registry", "catalog", "dispatch", "project"} or not source.is_file():
            raise ValueError("invalid synthetic source input")

    registry = ArtifactGeneratorRegistry(
        {model.id: model, emulator.id: emulator},
        {model.id: {"generate": generate, "validate": validate,
                    "project_program": project_program, "render_program": render_program},
         emulator.id: {"generate": _emulator.generate, "validate": _emulator.validate}},
    )
    return registry, create_workspace(root, {}), emulator, source


class GenerationTest(unittest.TestCase):
    def test_artifact_schema_rejects_invalid_named_output_declarations(self):
        schema = (Path(__file__).parents[1] / "artifacts/schema.yaml").read_text()
        invalid = {
            "missing": "generator: generator.py\n",
            "sequence": "generator: generator.py\noutputs: [example.tex]\n",
            "absolute": "generator: generator.py\noutputs: {document: /example.tex}\n",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "schema.yaml").write_text(schema)
            for name, content in invalid.items():
                with self.subTest(condition=name):
                    source = root / name / "artifact.yaml"
                    source.parent.mkdir()
                    source.write_text(content)
                    with self.assertRaises(ValueError):
                        load_artifact_definition(source)

    def test_discovery_binds_declared_provider_inputs_and_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "artifacts/combined"
            source.mkdir(parents=True)
            (source.parent / "schema.yaml").write_text((Path(__file__).parents[1] / "artifacts/schema.yaml").read_text())
            (source / "artifact.yaml").write_text("generator: generator.py\ninputs: [left, right]\noutputs: {combined: combined.txt}\n")
            (source / "generator.py").write_text('''from engine.artifacts.definition import GeneratedArtifact, GeneratedArtifactSet

def validate(definition, context):
    if set(definition.outputs) != {"combined"}:
        raise ValueError("wrong output declaration")
    context.workspace.require_provider("left")
    context.workspace.require_provider("right")

def generate(definition, context):
    left = context.workspace.require_provider("left")
    right = context.workspace.require_provider("right")
    return GeneratedArtifactSet((GeneratedArtifact(definition.outputs["combined"], left + right),), definition.id)
''')
            workspace = create_workspace(root, {"left": "Left ", "right": "right."})
            registry = load_artifact_registry(workspace)
            result = generate_artifact(registry, "combined", workspace, root / "output")
            self.assertEqual(result.artifact("combined.txt").content, "Left right.")
            self.assertFalse((root / "output").exists())

    def test_sail_operation_contribution_precedes_and_is_required_by_boundary(self):
        root = Path("/synthetic")
        program = _program(root)
        outputs = {"registry": Path("generated/registry.sail"), "catalog": Path("generated/catalog.sail"), "dispatch": Path("generated/dispatch.sail"), "project": Path("model.sail_project")}
        project = _sail_project.project_sail_project(program, root, outputs, source_root=root, source_alias=root)
        operation = next(module for module in project.modules if outputs["dispatch"].as_posix() in module.sources)
        boundary = next(module for module in project.modules if "boundary.sail" in module.sources)
        self.assertLess(project.modules.index(operation), project.modules.index(boundary))
        self.assertIn(operation.name, boundary.requirements)

    def test_sail_project_paths_are_relative_to_the_project_file(self):
        root = Path("/synthetic")
        program = _program(root)
        outputs = {
            "registry": Path("generated/registry.sail"),
            "catalog": Path("generated/catalog.sail"),
            "dispatch": Path("generated/dispatch.sail"),
            "project": Path("nested/model.sail_project"),
        }
        project = _sail_project.project_sail_project(
            program, root, outputs, source_root=root, source_alias=root
        )
        sources = tuple(source for module in project.modules for source in module.sources)
        self.assertIn("../generated/registry.sail", sources)
        self.assertIn("../generated/dispatch.sail", sources)
        self.assertIn("../core.sail", sources)

    def test_sail_project_rejects_colliding_module_names(self):
        root = Path("/synthetic")
        first = SailUnit("base", "a.b", Reference.parse("base.a.b"), root / "model.yaml", (root / "first.sail",), ())
        second = SailUnit("base", "a_b", Reference.parse("base.a_b"), root / "model.yaml", (root / "second.sail",), ())
        program = replace(
            _program(root),
            sail_units=(first, second),
            sources=(*first.sources, *second.sources),
            unit_sources={first.reference: first.sources, second.reference: second.sources},
            unit_dependencies={first.reference: (), second.reference: ()},
        )
        outputs = {
            "registry": Path("generated/registry.sail"),
            "catalog": Path("generated/catalog.sail"),
            "dispatch": Path("generated/dispatch.sail"),
            "project": Path("model.sail_project"),
        }
        with self.assertRaisesRegex(ValueError, "duplicate Sail project module names"):
            _sail_project.project_sail_project(
                program, root, outputs, source_root=root, source_alias=root
            )

    def test_sail_renderer_rejects_incomplete_output_roles(self):
        root = Path("/synthetic")
        definition = ArtifactDefinition(
            "sail-model",
            root / "artifact.yaml",
            {
                "outputs": {
                    "registry": "generated/registry.sail",
                    "catalog": "generated/catalog.sail",
                    "dispatch": "generated/dispatch.sail",
                }
            },
        )
        with self.assertRaisesRegex(ValueError, "outputs must be exactly"):
            _sail_generator.render_program(
                definition,
                _program(root),
                root,
                source_root=root,
                source_alias=root,
            )

    def test_sail_serialization_returns_declared_outputs_without_publishing(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(__file__).parents[1].resolve()
            workspace = load_workspace(repository)
            registry = load_artifact_registry(workspace)
            definition = registry.definition("sail-model")
            output = Path(directory).resolve() / "output"
            context = artifact_context(registry, workspace, output)
            artifacts = context.generate(definition.id)
            self.assertEqual({item.relative_path for item in artifacts.artifacts}, set(definition.outputs.values()))
            self.assertFalse(output.exists())

    def _compiler(self):
        compiler = patch.object(_emulator, "compile_sail_c", return_value=('#include "bedrock_core.h"\ngenerated C\n', "generated header\n"))
        identity = patch.object(_emulator, "sail_compiler_key", return_value="synthetic compiler")
        self.addCleanup(compiler.stop)
        self.addCleanup(identity.stop)
        identity.start()
        return compiler.start()

    def test_completed_emulator_generation_is_reused_within_invocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            registry, workspace, definition, _ = _emulator_fixture(root)
            compiler = self._compiler()
            context = artifact_context(registry, workspace, root / "output")
            first = context.generate(definition.id)
            self.assertIs(context.generate(definition.id), first)
            self.assertEqual(compiler.call_count, 1)

    def test_unchanged_inputs_reuse_published_compiler_result_in_new_invocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            registry, workspace, definition, _ = _emulator_fixture(root)
            compiler = self._compiler()
            output = root / "output"
            first = artifact_context(registry, workspace, output).generate(definition.id)
            write_artifacts(first, output)
            second = artifact_context(registry, workspace, output).generate(definition.id)
            self.assertEqual(compiler.call_count, 1)
            self.assertEqual(second.artifact(definition.outputs["implementation"]).content, first.artifact(definition.outputs["implementation"]).content)

    def test_changed_source_invalidates_compiler_result_in_new_invocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            registry, workspace, definition, source = _emulator_fixture(root)
            compiler = self._compiler()
            output = root / "output"
            first = artifact_context(registry, workspace, output).generate(definition.id)
            write_artifacts(first, output)
            source.write_text("function value() -> int = 2\n")
            second = artifact_context(registry, workspace, output).generate(definition.id)
            self.assertEqual(compiler.call_count, 2)
            self.assertNotEqual(second.artifact(definition.outputs["generation-stamp"]).content, first.artifact(definition.outputs["generation-stamp"]).content)

    def test_failed_shared_projection_is_retried_and_only_success_is_memoized(self):
        context = artifact_context(ArtifactGeneratorRegistry({}, {}), create_workspace(Path("/synthetic"), {}), Path("/synthetic/output"))
        result = object()
        calls = []
        def factory():
            calls.append(None)
            if len(calls) == 1:
                raise ValueError("first attempt rejected")
            return result
        with self.assertRaises(ValueError):
            context.shared_result("one projection", factory)
        self.assertIs(context.shared_result("one projection", factory), result)
        self.assertIs(context.shared_result("one projection", factory), result)
        self.assertEqual(len(calls), 2)

    def test_pure_publication_projection_is_shared_across_output_destinations(self):
        root = Path("/synthetic")
        definition = ArtifactDefinition(
            "isa-reference", root / "artifact.yaml", {"outputs": {"document": "manual.tex"}}
        )

        def generate(definition, context):
            return GeneratedArtifactSet((GeneratedArtifact(Path("manual.tex"), ""),), definition.id)

        def validate(definition, context):
            pass

        def project(definition, context):
            return SimpleNamespace(instruction_groups=())

        registry = ArtifactGeneratorRegistry(
            {definition.id: definition},
            {definition.id: {"generate": generate, "validate": validate, "project": project}},
        )
        context = artifact_context(registry, create_workspace(root, {}), root / "first")
        with patch.object(_web_generator, "_DOCUMENTS", ()), patch.object(
            _web_generator.site_projection, "project_site", return_value=("selected publication",)
        ) as project_site:
            first = _web_generator.project_publication(context)
            second = _web_generator.project_publication(replace(context, output_root=root / "second"))

        self.assertIs(second, first)
        self.assertEqual(project_site.call_count, 1)

    def test_writer_is_the_filesystem_mutation_boundary(self) -> None:
        artifacts = GeneratedArtifactSet(
            (GeneratedArtifact(Path("generated/value.txt"), "value\n"),),
            artifact_id="example",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "output"

            written = write_artifacts(artifacts, root)

            self.assertEqual(written, ((root / "generated/value.txt").resolve(),))
            self.assertEqual(written[0].read_text(), "value\n")

    def test_writer_preserves_binary_artifacts(self) -> None:
        artifacts = GeneratedArtifactSet(
            (GeneratedArtifact(Path("generated/asset.bin"), b"\x00\xff\x10"),),
            artifact_id="binary-example",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "output"

            written = write_artifacts(artifacts, root)

            self.assertEqual(written[0].read_bytes(), b"\x00\xff\x10")

    def test_failed_file_preparation_preserves_that_live_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "output"
            paths = (Path("first.bin"), Path("second.bin"))
            old = GeneratedArtifactSet(
                tuple(GeneratedArtifact(path, b"old complete bytes") for path in paths),
                "example",
            )
            new = GeneratedArtifactSet(
                tuple(GeneratedArtifact(path, b"new complete bytes") for path in paths),
                "example",
            )
            write_artifacts(old, root)
            manifest = root / ".artifact-ownership/example.json"
            previous_manifest = manifest.read_bytes()
            copy_file = _writer.shutil.copyfile

            def interrupted_copy(source, destination):
                if Path(source).name == paths[1].name:
                    Path(destination).write_bytes(b"partial")
                    raise OSError("file preparation interrupted")
                return copy_file(source, destination)

            with patch.object(_writer.shutil, "copyfile", side_effect=interrupted_copy):
                with self.assertRaisesRegex(OSError, "file preparation interrupted"):
                    write_artifacts(new, root)

            self.assertEqual((root / paths[0]).read_bytes(), b"new complete bytes")
            self.assertEqual((root / paths[1]).read_bytes(), b"old complete bytes")
            self.assertEqual(manifest.read_bytes(), previous_manifest)

    def test_failed_manifest_preparation_preserves_committed_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "output"
            path = Path("value.bin")
            write_artifacts(
                GeneratedArtifactSet((GeneratedArtifact(path, b"old complete bytes"),), "example"),
                root,
            )
            manifest = root / ".artifact-ownership/example.json"
            previous_manifest = manifest.read_bytes()
            write_text = Path.write_text

            def interrupted_write(destination, content, *args, **kwargs):
                if destination.name == manifest.name:
                    destination.write_bytes(b"partial")
                    raise OSError("manifest preparation interrupted")
                return write_text(destination, content, *args, **kwargs)

            with patch.object(Path, "write_text", new=interrupted_write):
                with self.assertRaisesRegex(OSError, "manifest preparation interrupted"):
                    write_artifacts(
                        GeneratedArtifactSet((GeneratedArtifact(path, b"new complete bytes"),), "example"),
                        root,
                    )

            self.assertEqual((root / path).read_bytes(), b"new complete bytes")
            self.assertEqual(manifest.read_bytes(), previous_manifest)

    def test_manifest_commit_follows_file_publication_and_stale_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "output"
            path, stale = Path("value.bin"), Path("stale.bin")
            write_artifacts(
                GeneratedArtifactSet(
                    (GeneratedArtifact(path, b"old complete bytes"), GeneratedArtifact(stale, b"stale")),
                    "example",
                ),
                root,
            )
            manifest = root / ".artifact-ownership/example.json"
            previous_manifest = manifest.read_bytes()
            replace_file = _writer.os.replace

            def interrupted_commit(source, destination):
                self.assertEqual(Path(source).stat().st_dev, Path(destination).parent.stat().st_dev)
                if Path(destination) == manifest:
                    self.assertEqual((root / path).read_bytes(), b"new complete bytes")
                    self.assertFalse((root / stale).exists())
                    self.assertEqual(manifest.read_bytes(), previous_manifest)
                    raise OSError("manifest commit interrupted")
                return replace_file(source, destination)

            with patch.object(_writer.os, "replace", side_effect=interrupted_commit):
                with self.assertRaisesRegex(OSError, "manifest commit interrupted"):
                    write_artifacts(
                        GeneratedArtifactSet((GeneratedArtifact(path, b"new complete bytes"),), "example"),
                        root,
                    )

            self.assertEqual(manifest.read_bytes(), previous_manifest)

    def test_writer_removes_only_stale_files_owned_by_the_same_artifact(self) -> None:
        writer = write_artifacts
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "output"
            writer(
                GeneratedArtifactSet(
                    (
                        GeneratedArtifact(Path("site/index.html"), "old index\n"),
                        GeneratedArtifact(Path("site/topics/legacy.html"), "legacy\n"),
                    ),
                    artifact_id="web-reference",
                ),
                root,
            )
            writer(
                GeneratedArtifactSet(
                    (GeneratedArtifact(Path("tex/isa-reference.tex"), "isa\n"),),
                    artifact_id="isa-reference",
                ),
                root,
            )

            written = writer(
                GeneratedArtifactSet(
                    (GeneratedArtifact(Path("site/index.html"), "new index\n"),),
                    artifact_id="web-reference",
                ),
                root,
            )

            self.assertEqual(written, ((root / "site/index.html").resolve(),))
            self.assertEqual(written[0].read_text(), "new index\n")
            self.assertFalse((root / "site/topics/legacy.html").exists())
            self.assertEqual((root / "tex/isa-reference.tex").read_text(), "isa\n")

    def test_writer_rejects_a_path_owned_by_another_artifact(self) -> None:
        writer = write_artifacts
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "output"
            writer(
                GeneratedArtifactSet(
                    (GeneratedArtifact(Path("shared/value.txt"), "first\n"),),
                    artifact_id="first",
                ),
                root,
            )

            with self.assertRaises(ValueError):
                writer(
                    GeneratedArtifactSet(
                        (GeneratedArtifact(Path("shared/value.txt"), "second\n"),),
                        artifact_id="second",
                    ),
                    root,
                )

            self.assertEqual((root / "shared/value.txt").read_text(), "first\n")

    def test_writer_rejects_nested_symlinks_before_mutating_output(self) -> None:
        writer = write_artifacts
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "output"
            actual = root / "actual"
            actual.mkdir(parents=True)
            value = actual / "value.txt"
            value.write_text("original\n")
            (root / "alias").symlink_to(actual, target_is_directory=True)

            with self.assertRaises(ValueError):
                writer(
                    GeneratedArtifactSet(
                        (GeneratedArtifact(Path("alias/value.txt"), "replacement\n"),),
                        artifact_id="symlink-example",
                    ),
                    root,
                )

            self.assertEqual(value.read_text(), "original\n")

    def test_writer_refuses_to_overwrite_an_unowned_existing_file(self) -> None:
        writer = write_artifacts
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "output"
            existing = root / "generated/value.txt"
            existing.parent.mkdir(parents=True)
            existing.write_text("user-owned\n")

            with self.assertRaises(ValueError):
                writer(
                    GeneratedArtifactSet(
                        (GeneratedArtifact(Path("generated/value.txt"), "new\n"),),
                        artifact_id="example",
                    ),
                    root,
                )

            self.assertEqual(existing.read_text(), "user-owned\n")

    def test_artifact_rejects_output_escape(self) -> None:
        for path in (Path("../outside"), Path("/absolute")):
            with self.subTest(path=path), self.assertRaises(ValueError):
                GeneratedArtifact(path, "")

    def test_artifact_set_rejects_duplicate_output_paths(self):
        with self.assertRaises(ValueError):
            GeneratedArtifactSet((GeneratedArtifact(Path("same.txt"), "first"), GeneratedArtifact(Path("same.txt"), "second")), "duplicate")

    def test_artifact_set_rejects_file_and_directory_overlap(self):
        parent = GeneratedArtifact(Path("site/topic"), "parent")
        child = GeneratedArtifact(Path("site/topic/detail"), "child")
        for artifacts in ((parent, child), (child, parent)):
            with self.subTest(paths=tuple(item.relative_path for item in artifacts)):
                with self.assertRaisesRegex(ValueError, "overlap"):
                    GeneratedArtifactSet(artifacts, "example")

    def test_artifact_set_rejects_reserved_ownership_paths(self):
        for path in (Path(".artifact-ownership"), Path(".artifact-ownership/example.json")):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "reserved ownership"):
                GeneratedArtifactSet((GeneratedArtifact(path, "reserved"),), "example")

    def test_registry_rejects_unknown_dependency(self):
        definition = ArtifactDefinition("dependent", Path("dependent/artifact.yaml"), {"depends-on": ["missing"], "outputs": {"result": "dependent.txt"}})
        with self.assertRaisesRegex(ValueError, "unknown.*dependency"):
            ArtifactGeneratorRegistry({definition.id: definition}, {})

    def test_registry_rejects_overlapping_output_owners(self):
        for first_path, second_path in (("same.txt", "same.txt"), ("site", "site/index.html")):
            with self.subTest(first=first_path, second=second_path):
                first = ArtifactDefinition("first", Path("first/artifact.yaml"), {"outputs": {"result": first_path}})
                second = ArtifactDefinition("second", Path("second/artifact.yaml"), {"outputs": {"result": second_path}})
                with self.assertRaisesRegex(ValueError, "overlaps"):
                    ArtifactGeneratorRegistry({first.id: first, second.id: second}, {})

    def test_registry_rejects_missing_validation_entry(self):
        definition = ArtifactDefinition("example", Path("example/artifact.yaml"), {"outputs": {"result": "result.txt"}})
        def generate(definition, context):
            return GeneratedArtifactSet((GeneratedArtifact(Path("result.txt"), "value"),), definition.id)
        with self.assertRaisesRegex(ValueError, "validate"):
            ArtifactGeneratorRegistry({definition.id: definition}, {definition.id: {"generate": generate}})

    def test_generated_descendant_populates_declared_output_root(self):
        definition = ArtifactDefinition("publication", Path("publication/artifact.yaml"), {"outputs": {"site": "site"}})
        definition.validate_generated(GeneratedArtifactSet((GeneratedArtifact(Path("site/index.html"), "index"),), definition.id))

    def test_generated_path_outside_declared_output_is_rejected(self):
        definition = ArtifactDefinition("publication", Path("publication/artifact.yaml"), {"outputs": {"site": "site"}})
        with self.assertRaises(ValueError):
            definition.validate_generated(GeneratedArtifactSet((GeneratedArtifact(Path("outside.txt"), "outside"),), definition.id))

    def test_unpopulated_declared_output_is_rejected(self):
        definition = ArtifactDefinition("publication", Path("publication/artifact.yaml"), {"outputs": {"site": "site"}})
        with self.assertRaises(ValueError):
            definition.validate_generated(GeneratedArtifactSet((), definition.id))


if __name__ == "__main__":
    unittest.main()
