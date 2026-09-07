from engine.artifacts.generate import generate_artifact
import json
from importlib import import_module
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from engine.artifacts.registry import ArtifactGeneratorRegistry, load_artifact_registry
from engine.isa.project import IsaProject
from engine.workspace import load_workspace
from interfaces.c.model.projection import project_c_interface


class SpecificationArtifactsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = Path(__file__).parents[1]
        cls.workspace = load_workspace(cls.repository)
        cls.project = cls.workspace.require_provider("isa")
        if not isinstance(cls.project, IsaProject):
            raise TypeError("workspace isa provider must be an IsaProject")
        cls.registry = load_artifact_registry(cls.workspace)

    def test_workspace_entity_dependencies_are_uniform_and_resolvable(self) -> None:
        for provider in self.workspace.providers.values():
            for dependency in provider.entity_dependencies():
                provider.entities.resolve(dependency.source)
                self.workspace.resolve(dependency.target)

    def test_explicit_c_target_header_projection_is_c_syntax_complete(self) -> None:
        interface = self.workspace.require_provider("interfaces.c")
        projection = import_module("artifacts.c-target-headers.generator").project_abi(
            project_c_interface(interface, self.workspace.require_provider("isa"), self.workspace.require_provider("abi.c")),
            self.registry.definitions["c-target-headers"].outputs,
        )
        generated = generate_artifact(
            self.registry,
            "c-target-headers",
            self.workspace,
            self.repository / "output",
        )
        compiler = shutil.which("clang") or shutil.which("cc")
        if compiler is None:
            self.skipTest("no C compiler is available")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for artifact in generated.artifacts:
                path = root / artifact.relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(artifact.content, encoding="utf-8")
            source = root / "consume.c"
            for group in projection:
                header = group.path.relative_to("include").as_posix()
                with self.subTest(header=header):
                    source.write_text(
                        f"#include <{header}>\n",
                        encoding="utf-8",
                    )
                    completed = subprocess.run(
                        (
                            compiler,
                            "-fsyntax-only",
                            "-std=c11",
                            "-Wno-implicit-function-declaration",
                            "-x",
                            "c",
                            "-I",
                            str(root / "include"),
                            str(source),
                        ),
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_reference_graph_is_a_standalone_workspace_visualization(self) -> None:
        def local(reference) -> str:
            return ".".join((reference.owner, *reference.path, reference.element))

        generator = self.registry.definition("reference-graph")
        generated = generate_artifact(
            self.registry, "reference-graph", self.workspace, self.repository / "output"
        )
        graph = json.loads(generated.artifact(generator.outputs["data"]).content)

        expected_nodes = {
            f"{domain}:{local(reference)}"
            for domain, provider in self.workspace.providers.items()
            for reference in provider.entities.references
        }
        self.assertEqual(graph["node_count"], len(expected_nodes))
        self.assertEqual(graph["link_count"], len(graph["links"]))
        nodes = {node["id"]: node for node in graph["nodes"]}
        self.assertEqual(set(nodes), expected_nodes)
        self.assertTrue(
            all(
                "anchor" not in node and "latex_label" not in node
                for node in nodes.values()
            )
        )

        expected_occurrences: dict[tuple[str, str, str], int] = {}
        for domain, provider in self.workspace.providers.items():
            for dependency in provider.entity_dependencies():
                source = f"{domain}:{local(dependency.source)}"
                target = f"{dependency.target.domain}:{local(dependency.target.local)}"
                if source == target:
                    continue
                key = (source, target, dependency.kind)
                expected_occurrences[key] = expected_occurrences.get(key, 0) + 1
        projected_occurrences = {
            (link["source"], link["target"], link["kind"]): link["weight"]
            for link in graph["links"]
        }
        self.assertEqual(projected_occurrences, expected_occurrences)


if __name__ == "__main__":
    unittest.main()
