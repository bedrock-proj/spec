import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from engine.artifacts.generate import artifact_context
from engine.artifacts.registry import load_artifact_registry
from engine.workspace import load_workspace
from artifacts._shared.systemverilog.lowering import _enum


class SystemVerilogNamingTest(unittest.TestCase):
    def test_enum_identifier_collisions_are_rejected(self) -> None:
        for values in (("a-b", "a_b"), ("invalid",)):
            with self.subTest(values=values):
                with self.assertRaisesRegex(ValueError, "identifier collision"):
                    _enum("sample_e", "SAMPLE", values)


class SystemVerilogDecoderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workspace = load_workspace(Path(__file__).parents[1])

    def test_generated_decoder_is_accepted_by_a_systemverilog_consumer(self) -> None:
        verilator = shutil.which("verilator")
        if verilator is None:
            self.skipTest("verilator is not available")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = []
            registry = load_artifact_registry(self.workspace)
            context = artifact_context(registry, self.workspace, root)
            for artifact_id in (
                "systemverilog-package",
                "systemverilog-instruction-decoder",
                "systemverilog-ea-decoder",
            ):
                generated = context.generate(artifact_id, None)
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
