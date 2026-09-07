from engine.sail.composition import compose_sail
import unittest
from dataclasses import replace
from pathlib import Path
import tempfile

from engine.isa.catalog import ProjectLookupError
from engine.isa.catalog import ProjectLookupReason
from engine.isa.project import IsaProject
from engine.isa.configuration import IsaConfiguration
from engine.sail.validation import require_sail_entries
from engine.workspace import load_workspace


class SailCompositionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workspace = load_workspace(Path(__file__).parents[1])
        project = cls.workspace.require_provider("isa")
        if not isinstance(project, IsaProject):
            raise TypeError("workspace isa provider must be an IsaProject")
        cls.project = project

    def compose(self, extensions=None):
        configuration = IsaConfiguration.resolve(self.project.catalog, extensions)
        return compose_sail(self.project.catalog, self.project.model, self.project.types, self.project.registers, self.project.control_registers, self.project.events, configuration)

    def test_default_composition_contains_all_owned_instructions(self) -> None:
        program = self.compose()

        self.assertEqual(
            program.configuration.extension_ids,
            tuple(self.project.catalog.extensions),
        )
        self.assertEqual(program.bundles, self.project.catalog.select())
        self.assertEqual(
            tuple(unit.reference for unit in program.sail_units),
            self.project.model.sail_order,
        )

    def test_configuration_rejects_unknown_extension(self) -> None:
        with self.assertRaises(ProjectLookupError) as caught:
            IsaConfiguration.resolve(self.project.catalog, ("DOES_NOT_EXIST",))
        self.assertIs(caught.exception.reason, ProjectLookupReason.UNKNOWN_EXTENSION)

    def test_extension_selection_closes_declared_dependencies(self) -> None:
        selected = next(
            extension
            for extension in self.project.catalog.extensions.values()
            if extension.requires
        )
        program = self.compose((selected.metadata.id,))

        required = set()

        def collect(extension) -> None:
            for dependency in extension.requires:
                collect(dependency)
            required.add(extension.metadata.id)

        collect(selected)
        expected_ids = tuple(
            extension_id
            for extension_id in self.project.catalog.extensions
            if extension_id in required
        )
        self.assertEqual(program.configuration.extension_ids, expected_ids)
        self.assertEqual(
            program.bundles,
            tuple(
                bundle
                for bundle in self.project.catalog.select()
                if bundle.reference.owner in {"base", *expected_ids}
            ),
        )

    def test_dispatch_projection_is_exhaustive_for_selected_entries(self) -> None:
        program = self.compose()

        self.assertEqual(
            tuple((item.instruction, item.entry) for item in program.dispatch.entries),
            tuple(
                (
                    semantics.instruction,
                    f"execute_{semantics.instruction.mnemonic}",
                )
                for semantics in program.bundles
            ),
        )

    def test_missing_declared_entry_is_rejected(self) -> None:
        program = self.compose(())
        first = program.bundles[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / first.instruction.mnemonic
            root.mkdir()
            source = root / "semantics.sail"
            source.write_text("function execute_other() -> unit = ()\n")
            bad_bundle = replace(
                first,
                instruction=replace(first.instruction, source=root / "instruction.yaml"),
                encodings=replace(first.encodings, source=root / "encodings.yaml"),
                diagrams=replace(
                    first.diagrams,
                    root=root / "diagrams",
                    inventory=None,
                    diagrams={},
                ),
                semantics=source,
                description=root / "descriptions.tex",
            )
            bad_program = replace(program, bundles=(bad_bundle,), implementation_bundles=(bad_bundle,))

            with self.assertRaises(ValueError):
                require_sail_entries(bad_program)


if __name__ == "__main__":
    unittest.main()
