from __future__ import annotations

from abi.elf.model.projection import project_elf_abi

from abi.elf.model.project import resolve_debug_registers

from engine.artifacts.definition import load_artifact_definition

from engine.artifacts.registry import ArtifactGeneratorRegistry, load_artifact_registry
from engine.artifacts.generate import artifact_context

from collections.abc import Mapping
from importlib import import_module
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import tempfile
import unittest

from abi.elf.model.project import ElfAbiProject
from abi.elf.model.relocation_metasyntax import RelocationMetasyntax
from engine.artifacts.definition import ArtifactDefinition
from engine.workspace import load_workspace
from engine.source.yaml import load_yaml


ROOT = Path(__file__).resolve().parents[1]


class LlvmElfAbiArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workspace = load_workspace(ROOT)
        definition = load_artifact_definition(
            ROOT / "artifacts/llvm-elf-abi/artifact.yaml"
        )
        cls.generator_module = import_module("artifacts.llvm-elf-abi.generator")
        project = cls.workspace.require_provider("abi.elf")
        if not isinstance(project, ElfAbiProject):
            raise TypeError("workspace abi.elf provider must be an ElfAbiProject")
        cls.project = project
        cls.abi_projection = project_elf_abi(project, cls.workspace.require_provider("isa"))
        generated = cls.generator_module.generate(
            definition,
            artifact_context(
                load_artifact_registry(cls.workspace), cls.workspace, ROOT
            ),
        )
        cls.relocations = generated.artifact(definition.outputs["relocations"]).content
        cls.catalog = generated.artifact(definition.outputs["catalog"]).content

    def _compile(self, source: str) -> None:
        compiler = shutil.which("clang++") or shutil.which("c++")
        if compiler is None:
            self.skipTest("no C++ compiler is available")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "consume.cc"
            path.write_text(source, encoding="utf-8")
            completed = subprocess.run(
                [compiler, "-std=c++17", "-fsyntax-only", str(path)],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_relocation_definition_is_accepted_by_its_cpp_consumer(self) -> None:
        self._compile(
            "constexpr int relocation_count = 0\n"
            "#define ELF_RELOC(name, value) + 1\n"
            + self.relocations
            + f";\nstatic_assert(relocation_count == "
            f"{len(self.project.relocations)});\n" + "int main() { return 0; }\n"
        )

    def test_catalog_families_have_authoritative_model_cardinality(self) -> None:
        relocations = list(self.project.relocations.values())
        code_models = list(self.project.code_models.values())
        tls_models = list(self.project.tls_models.values())
        protocols = list(self.project.linkage_protocols.values())
        debug_ranges = resolve_debug_registers(self.project, self.workspace.require_provider("isa").registers)
        state = self.project.process_entry
        expected = {
            "BEDROCK_ELF_RELOCATION": len(relocations),
            "BEDROCK_ELF_RELAXATION": sum(
                len(item.relaxations) for item in relocations
            ),
            "BEDROCK_ELF_CODE_MODEL": len(code_models),
            "BEDROCK_ELF_CODE_MODEL_RELOCATION": sum(
                len(item.default_relocations) for item in code_models
            ),
            "BEDROCK_ELF_TLS_MODEL": len(tls_models),
            "BEDROCK_ELF_TLS_MODEL_RELOCATION": sum(
                len(item.relocations) for item in tls_models
            ),
            "BEDROCK_ELF_TLS_PROPERTY": sum(
                sum(path != ("selection",) for path, value in self.abi_projection.properties[item.reference])
                for item in tls_models
            ),
            "BEDROCK_ELF_LINKAGE_PROTOCOL": len(protocols),
            "BEDROCK_ELF_LINKAGE_STEP": sum(len(item.steps) for item in protocols),
            "BEDROCK_ELF_LINKAGE_REGISTER": sum(
                len(contract.registers) for item in protocols for contract in item.state
            ),
            "BEDROCK_ELF_LINKAGE_PROPERTY": sum(
                len(self.abi_projection.properties[item.reference])
                for item in protocols
            ),
            "BEDROCK_ELF_DEBUG_REGISTER_RANGE": len(debug_ranges),
            "BEDROCK_ELF_DEBUG_REGISTER_MAPPING": sum(
                len(item.registers) for item in debug_ranges
            ),
            "BEDROCK_ELF_ENTRY_STATE": 1,
            "BEDROCK_ELF_ENTRY_STACK_PERMISSION": len(state.stack_permissions),
            "BEDROCK_ELF_ENTRY_SEGMENT_CONTEXT": len(state.segment_contexts),
            "BEDROCK_ELF_ENTRY_READINESS": len(state.readiness),
            "BEDROCK_ELF_ENTRY_CLEARED_REGISTER": len(state.cleared),
        }
        compiler = shutil.which("clang++") or shutil.which("c++")
        if compiler is None:
            self.skipTest("no C++ compiler is available")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "BedrockGenELFABI.inc").write_text(self.catalog, encoding="utf-8")
            source = []
            for index, (macro, count) in enumerate(expected.items()):
                source.extend(
                    (
                        f"constexpr int family_{index} = 0",
                        f"#define {macro}(...) + 1",
                        '#include "BedrockGenELFABI.inc"',
                        ";",
                        f"static_assert(family_{index} == {count});",
                        f"#undef {macro}",
                    )
                )
            source.extend(
                (
                    "#define BEDROCK_ELF_RELOCATION(id, ...) "
                    "constexpr bool seen_relocation_##id = true;",
                    "#define BEDROCK_ELF_CODE_MODEL(id, ...) "
                    "constexpr bool seen_code_model_##id = true;",
                    "#define BEDROCK_ELF_TLS_MODEL(id, ...) "
                    "constexpr bool seen_tls_model_##id = true;",
                    "#define BEDROCK_ELF_LINKAGE_PROTOCOL(id) "
                    "constexpr bool seen_protocol_##id = true;",
                    '#include "BedrockGenELFABI.inc"',
                )
            )
            source.extend(
                f"static_assert(seen_relocation_{item.id});" for item in relocations
            )
            source.extend(
                f"static_assert(seen_code_model_{item.id});" for item in code_models
            )
            source.extend(
                f"static_assert(seen_tls_model_{item.id});" for item in tls_models
            )
            source.extend(
                f"static_assert(seen_protocol_{item.id});" for item in protocols
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

    def test_lld_expression_mapping_uses_the_parsed_ast(self) -> None:
        relocation = SimpleNamespace(
            id="R_BEDROCK_EQUIVALENT",
            source=Path("equivalent.yaml"),
            calculation=RelocationMetasyntax.parse("(symbol + addend) - place"),
        )
        self.assertEqual(self.generator_module._lld_expression(relocation), "PC")

    def test_unmapped_calculation_is_rejected_by_type(self) -> None:
        relocation = SimpleNamespace(
            id="R_BEDROCK_FUTURE",
            source=Path("future.yaml"),
            calculation=RelocationMetasyntax.parse("symbol - addend"),
        )
        with self.assertRaises(self.generator_module.UnmappedRelocationExpressionError):
            self.generator_module._lld_expression(relocation)


if __name__ == "__main__":
    unittest.main()
