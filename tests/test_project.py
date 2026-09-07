from engine.isa.catalog import load_instruction_inventory
import unittest
from contextlib import contextmanager
from pathlib import Path
import shutil
import tempfile

import yaml

from engine.isa.project import load_isa
from engine.isa.catalog import ProjectLookupError
from engine.isa.catalog import ProjectLookupReason
from engine.reference import Reference


class IsaProjectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.isa_root = Path(__file__).parents[1] / "isa"
        cls.project = load_isa(cls.isa_root)

    @contextmanager
    def project_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "isa"
            shutil.copytree(self.isa_root, root)
            shutil.copytree(
                self.isa_root.parent / "artifacts/isa-reference/document",
                root.parent / "artifacts/isa-reference/document",
            )
            yield root

    def test_extension_projects_owner_declared_dependencies(self) -> None:
        for extension in self.project.catalog.extensions.values():
            with self.subTest(extension=extension.metadata.id):
                self.assertEqual(
                    tuple(required.metadata.id for required in extension.requires),
                    extension.metadata.requires,
                )
                inherited_fields = tuple(
                    field
                    for required in extension.requires
                    for field in required.required_cpuid_flags
                )
                direct_fields = tuple(
                    self.project.cpuid.fields[Reference.parse(reference)]
                    for reference in extension.metadata.required_cpuid_flags
                )
                self.assertEqual(
                    extension.required_cpuid_flags,
                    (*inherited_fields, *direct_fields),
                )
                self.assertIs(
                    extension.types,
                    self.project.types.namespace(extension.metadata.id),
                )

    def test_instruction_bundles_combine_owner_and_local_cpuid_requirements(
        self,
    ) -> None:
        owner_requirements = {
            "base": (),
            **{
                extension.metadata.id: extension.required_cpuid_flags
                for extension in self.project.catalog.extensions.values()
            },
        }

        for bundle in self.project.catalog.select():
            with self.subTest(reference=bundle.reference):
                local = tuple(
                    self.project.cpuid.fields[reference]
                    for reference in bundle.instruction.additional_cpuid_flags
                )
                self.assertEqual(
                    set(bundle.required_cpuid_flags),
                    {*owner_requirements[bundle.owner], *local},
                )
            for form in bundle.encodings.forms:
                with self.subTest(reference=bundle.reference, form=form.id):
                    self.assertEqual(
                        set(bundle.required_cpuid_flags_for(form)),
                        {
                            *bundle.required_cpuid_flags,
                            *form.additional_cpuid_flags,
                        },
                    )

    def test_rejects_unknown_extension(self) -> None:
        with self.assertRaises(ProjectLookupError) as caught:
            self.project.catalog.extension("DOESNOTEXIST")
        self.assertIs(caught.exception.reason, ProjectLookupReason.UNKNOWN_EXTENSION)

    def test_resolves_dependencies_independently_of_declaration_order(self) -> None:
        with self.project_fixture() as root:
            inventory = root / "extensions/extensions.yaml"
            document = yaml.safe_load(inventory.read_text(encoding="utf-8"))
            document["extensions"] = list(reversed(document["extensions"]))
            inventory.write_text(
                yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
            )
            project = load_isa(root)

        for extension in project.catalog.extensions.values():
            self.assertEqual(
                tuple(required.metadata.id for required in extension.requires),
                extension.metadata.requires,
            )

    def test_instruction_catalog_rejects_invalid_directory_names(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "definitions"
            (root / "ADD-INVALID").mkdir(parents=True)
            (root / "instructions.yaml").write_text(
                "instructions:\n- ADD-INVALID\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "invalid instructions names"):
                load_instruction_inventory(owner="base", root=root)

    def test_resolves_mnemonic_reference_and_source_path(self) -> None:
        expected = self.project.catalog.select()[0]
        by_name = self.project.catalog.bundle(expected.instruction.mnemonic)
        by_reference = self.project.catalog.bundle(expected.reference)
        by_path = self.project.catalog.bundle(expected.encodings.source)

        self.assertIs(expected, by_name)
        self.assertIs(expected, by_reference)
        self.assertIs(expected, by_path)

    def test_select_deduplicates_without_reordering_targets(self) -> None:
        first, second = self.project.catalog.select()[:2]
        selected = self.project.catalog.select(
            (first.reference, second.reference, first.reference)
        )
        self.assertEqual(selected, (first, second))

    def test_rejects_unknown_instruction(self) -> None:
        with self.assertRaises(ProjectLookupError) as caught:
            self.project.catalog.bundle("DOESNOTEXIST")
        self.assertIs(caught.exception.reason, ProjectLookupReason.UNKNOWN_INSTRUCTION)


if __name__ == "__main__":
    unittest.main()
