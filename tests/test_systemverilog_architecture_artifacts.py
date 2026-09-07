import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from engine.isa.architecture import cpuid_project
from engine.isa.architecture import event_codec_project
from engine.isa.event_structures import resolve_event_frame_layouts
from engine.isa.architecture import register_contracts_project
from engine.isa.architecture import vector_geometry_project
from engine.artifacts.generate import artifact_context, generate_artifact
from engine.artifacts.registry import ArtifactGeneratorRegistry, load_artifact_registry
from engine.isa.project import IsaProject
from engine.workspace import load_workspace


class SystemVerilogArchitectureArtifactsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = Path(__file__).parents[1]
        cls.workspace = load_workspace(cls.repository)
        cls.registry = load_artifact_registry(cls.workspace)
        project = cls.workspace.require_provider("isa")
        if not isinstance(project, IsaProject):
            raise TypeError("workspace isa provider must be an IsaProject")
        cls.project = project
        cls.context = artifact_context(
            load_artifact_registry(cls.workspace),
            cls.workspace,
            cls.repository / "output",
        )

    def test_cpuid_projection_owns_queries_fields_and_masks(self) -> None:
        projection = cpuid_project(self.project.cpuid)
        expected_queries = {
            (owner, cpuid_class.id, leaf.id, query.id)
            for owner, namespace in self.project.cpuid.namespaces.items()
            for cpuid_class in namespace.classes.values()
            for leaf in cpuid_class.leaves.values()
            for query in leaf.queries
        }

        self.assertEqual(
            {
                (query.owner, query.class_id, query.leaf_id, query.query_id)
                for query in projection
            },
            expected_queries,
        )
        for query in projection:
            self.assertLessEqual(query.first_index, query.last_index)
            self.assertGreater(query.stride, 0)
            for field in query.fields:
                self.assertEqual(field.mask, ((1 << field.bits) - 1) << field.lsb)

    def test_event_projection_owns_fixed_and_dynamic_routes(self) -> None:
        projection = event_codec_project(self.project.events, resolve_event_frame_layouts(self.project.event_frames.frame))
        resolved = self.project.events.resolved_events()
        expected_fixed = {
            (item.owner, item.event.id, item.code.value, item.event.frame)
            for item in resolved
            if item.code.value is not None
        }
        expected_dynamic = {
            (item.code.class_value, item.event.frame)
            for item in resolved
            if item.code.selector.kind != "fixed"
        }

        self.assertEqual(
            {
                (route.owner, route.event_id, route.code, route.frame.frame_type)
                for route in projection.fixed_routes
            },
            expected_fixed,
        )
        self.assertEqual(
            {(route.class_value, route.frame.frame_type) for route in projection.dynamic_routes},
            expected_dynamic,
        )

    def test_register_projection_owns_selectors_and_write_masks(self) -> None:
        projection = register_contracts_project(
            self.project.registers, self.project.control_registers
        )
        expected = {}
        for owner, namespace in self.project.registers.namespaces.items():
            for group in namespace.groups.values():
                for register in group.registers.values():
                    if register.encoding is None:
                        continue
                    expected[(owner, group.id, register.id)] = (
                        register.encoding,
                        (
                            (1 << 64) - 1
                            if register.layout is None
                            else sum(
                                ((1 << field.bits) - 1) << field.lsb
                                for field in register.layout.fields
                            )
                        ),
                    )
        for owner, namespace in self.project.control_registers.namespaces.items():
            for register in namespace.registers.values():
                expected[(owner, "CONTROL_REGISTERS", register.id)] = (
                    register.selector,
                    (
                        (1 << 64) - 1
                        if register.layout is None
                        else sum(
                            ((1 << field.bits) - 1) << field.lsb
                            for field in register.layout.fields
                        )
                    ),
                )

        self.assertEqual(
            {
                (owner, group, register.register_id): (
                    register.encoding,
                    register.writable_mask,
                )
                for (owner, group), registers in {
                    **projection.groups,
                    **{
                        (owner, "CONTROL_REGISTERS"): registers
                        for owner, registers in projection.control_registers.items()
                    },
                }.items()
                for register in registers
            },
            expected,
        )

    def test_vector_projection_owns_architectural_register_counts(self) -> None:
        projection = vector_geometry_project(self.project.registers)
        namespace = self.project.registers.namespaces["VECTOR"]

        self.assertEqual(
            projection.vector_register_count,
            len(namespace.groups["VECTOR"].registers),
        )
        self.assertEqual(
            projection.predicate_register_count,
            len(namespace.groups["PREDICATE"].registers),
        )

    def test_generated_contracts_are_accepted_by_a_systemverilog_consumer(self) -> None:
        verilator = shutil.which("verilator")
        if verilator is None:
            self.skipTest("verilator is not available")
        for artifact_id in (
            "systemverilog-condition-evaluator",
            "systemverilog-cpuid",
            "systemverilog-event-codec",
            "systemverilog-register-contracts",
            "systemverilog-vector-geometry",
        ):
            with (
                self.subTest(artifact=artifact_id),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                generated = generate_artifact(
                    self.registry, artifact_id, self.workspace, root
                )
                sources = []
                for artifact in generated.artifacts:
                    source = root / artifact.relative_path
                    source.parent.mkdir(parents=True, exist_ok=True)
                    source.write_text(str(artifact.content), encoding="utf-8")
                    sources.append(source)
                sources.sort(
                    key=lambda path: (not path.name.endswith("_pkg.sv"), path.name)
                )
                completed = subprocess.run(
                    [
                        verilator,
                        "--lint-only",
                        "--sv",
                        "-Wno-fatal",
                        *(str(source) for source in sources),
                    ],
                    cwd=root,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
