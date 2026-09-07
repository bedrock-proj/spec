from __future__ import annotations

from abi.c.model.projection import project_c_abi

from engine.artifacts.definition import load_artifact_definition

from engine.artifacts.registry import ArtifactGeneratorRegistry, load_artifact_registry
from engine.artifacts.generate import artifact_context

from importlib import import_module
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from abi.c.model.project import CAbiProject
from engine.artifacts.definition import ArtifactDefinition
from engine.workspace import load_workspace
from engine.source.yaml import load_yaml


ROOT = Path(__file__).resolve().parents[1]


class LlvmCAbiArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workspace = load_workspace(ROOT)
        definition = load_artifact_definition(
            ROOT / "artifacts/llvm-c-abi/artifact.yaml"
        )
        cls.definition = definition
        generated = import_module("artifacts.llvm-c-abi.generator").generate(
            definition,
            artifact_context(
                load_artifact_registry(cls.workspace), cls.workspace, ROOT
            ),
        )
        cls.generator_module = import_module("artifacts.llvm-c-abi.generator")
        project = cls.workspace.require_provider("abi.c")
        if not isinstance(project, CAbiProject):
            raise TypeError("workspace abi.c provider must be a CAbiProject")
        cls.project = project
        cls.abi_projection = project_c_abi(project, cls.workspace.require_provider("isa"))
        cls.catalog = generated.artifact(definition.outputs["catalog"]).content
        cls.calling_convention = generated.artifact(
            definition.outputs["calling-convention"]
        ).content
        cls.calling_convention_projection = (
            cls.generator_module._project_calling_convention(
                cls.abi_projection
            )
        )

    def test_catalog_families_have_authoritative_model_cardinality(self) -> None:
        compiler = shutil.which("clang++") or shutil.which("c++")
        if compiler is None:
            self.skipTest("no C++ compiler is available")
        convention = self.project.calling_convention
        namespace = self.project.namespaces["base"]
        register_classes = [
            self.project.register_classes.resolve(reference)
            for reference in convention.register_classes
        ]
        value_classes = [
            self.project.value_classes.resolve(reference)
            for reference in convention.value_classes
        ]
        promotions = [
            self.project.promotions.resolve(reference)
            for reference in convention.promotions
        ]
        helpers = list(namespace.runtime_helpers.values())
        memory_orders = list(namespace.memory_orders.values())
        atomic_lowerings = list(namespace.atomic_lowerings.values())
        expected = {
            "BEDROCK_C_TYPE": len(namespace.types),
            "BEDROCK_C_CALLING_CONVENTION": 1,
            "BEDROCK_C_REGISTER_CLASS": len(register_classes),
            "BEDROCK_C_ARGUMENT_REGISTER": sum(
                len(item.arguments) for item in register_classes
            ),
            "BEDROCK_C_RESULT_REGISTER": sum(
                len(item.results) for item in register_classes
            ),
            "BEDROCK_C_VALUE_CLASS": len(value_classes),
            "BEDROCK_C_VALUE_KIND": sum(len(item.kinds) for item in value_classes),
            "BEDROCK_C_LOCATION_POLICY": 2 * len(value_classes),
            "BEDROCK_C_PROMOTION": sum(len(item.source_kinds) for item in promotions),
            "BEDROCK_C_PRESERVATION_REGISTER": sum(
                len(item.registers) for item in convention.preservation
            ),
            "BEDROCK_C_RUNTIME_HELPER": len(helpers),
            "BEDROCK_C_RUNTIME_PARAMETER": sum(
                len(item.parameters) for item in helpers
            ),
            "BEDROCK_C_MEMORY_ORDER": len(memory_orders),
            "BEDROCK_C_MEMORY_ORDER_STEP": sum(
                len(sequence or ())
                for item in memory_orders
                for sequence in (item.load, item.store, item.thread_fence)
            ),
            "BEDROCK_C_ATOMIC_LOWERING": len(atomic_lowerings),
            "BEDROCK_C_ATOMIC_OPERATION": sum(
                len(item.c_operations) for item in atomic_lowerings
            ),
            "BEDROCK_C_ATOMIC_INSTRUCTION": sum(
                len(item.instructions) for item in atomic_lowerings
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "BedrockGenCABI.inc").write_text(self.catalog, encoding="utf-8")
            source = []
            for index, (macro, count) in enumerate(expected.items()):
                source.extend(
                    (
                        f"constexpr int family_{index} = 0",
                        f"#define {macro}(...) + 1",
                        '#include "BedrockGenCABI.inc"',
                        ";",
                        f"static_assert(family_{index} == {count});",
                        f"#undef {macro}",
                    )
                )
            source.extend(
                (
                    "#define BEDROCK_C_TYPE(id, ...) "
                    "constexpr bool seen_type_##id = true;",
                    "#define BEDROCK_C_REGISTER_CLASS(id, ...) "
                    "constexpr bool seen_register_class_##id = true;",
                    "#define BEDROCK_C_VALUE_CLASS(id) "
                    "constexpr bool seen_value_class_##id = true;",
                    "#define BEDROCK_C_RUNTIME_HELPER(id, ...) "
                    "constexpr bool seen_runtime_helper_##id = true;",
                    "#define BEDROCK_C_MEMORY_ORDER(id, ...) "
                    "constexpr bool seen_memory_order_##id = true;",
                    "#define BEDROCK_C_ATOMIC_LOWERING(id, ...) "
                    "constexpr bool seen_atomic_lowering_##id = true;",
                    '#include "BedrockGenCABI.inc"',
                )
            )
            source.extend(
                f"static_assert(seen_type_{item.id});"
                for item in namespace.types.values()
            )
            source.extend(
                f"static_assert(seen_register_class_{item.id});"
                for item in register_classes
            )
            source.extend(
                f"static_assert(seen_value_class_{item.id});" for item in value_classes
            )
            source.extend(
                f"static_assert(seen_runtime_helper_{item.id});" for item in helpers
            )
            source.extend(
                f"static_assert(seen_memory_order_{item.id});" for item in memory_orders
            )
            source.extend(
                f"static_assert(seen_atomic_lowering_{item.id});"
                for item in atomic_lowerings
            )
            source.append("int main() { return 0; }")
            path = root / "consume.cc"
            path.write_text("\n".join(source), encoding="utf-8")
            completed = subprocess.run(
                [compiler, "-std=c++17", "-fsyntax-only", str(path)],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_calling_convention_is_accepted_by_llvm_tablegen(self) -> None:
        binary_root = os.environ.get("BEDROCK_LLVM_BIN")
        if binary_root is None:
            self.skipTest("BEDROCK_LLVM_BIN is not configured")
        tablegen = Path(binary_root) / "llvm-tblgen"
        self.assertTrue(tablegen.is_file(), f"llvm-tblgen is missing: {tablegen}")
        project_root = os.environ.get("LLVM_PROJECT_ROOT")
        self.assertIsNotNone(project_root, "LLVM_PROJECT_ROOT is not configured")
        assert project_root is not None
        include_root = Path(project_root) / "llvm/include"
        self.assertTrue(
            include_root.is_dir(), f"LLVM include directory is missing: {include_root}"
        )
        target_root = Path(project_root) / "llvm/lib/Target/Bedrock"
        wrapper = [
            'include "llvm/Target/Target.td"',
            'include "BedrockRegisterInfo.td"',
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated = root / "BedrockGenCallingConv.td"
            generated.write_text(self.calling_convention, encoding="utf-8")
            wrapper.append(f'include "{generated}"')
            source = root / "consume.td"
            source.write_text("\n".join(wrapper), encoding="utf-8")
            completed = subprocess.run(
                [
                    str(tablegen),
                    "-I",
                    str(include_root),
                    "-I",
                    str(target_root),
                    "-print-records",
                    str(source),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
