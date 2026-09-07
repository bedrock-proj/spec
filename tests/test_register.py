
from engine.isa.registers import load_registers
import shutil
import tempfile
import unittest
from pathlib import Path

from engine.isa.extensions import load_extension_inventory
from engine.isa.registers import (
    RegisterCatalog,
    RegisterGroupSourceConflictError,
    RegisterWidthDomainOrderError,
    SeriesRegisterGroup,
)
from engine.reference import Reference


class RegisterCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.isa_root = Path(__file__).parents[1] / "isa"
        cls.extensions = load_extension_inventory(cls.isa_root)
        cls.catalog = load_registers(cls.isa_root, extensions=cls.extensions)

    def test_expands_regular_groups_without_member_directories(self) -> None:
        with self.fixture() as directory:
            root = Path(directory)
            group = load_registers(root, extensions=load_extension_inventory(root)).groups[
                Reference.parse("base.registers.GPR")
            ]

            self.assertIsInstance(group, SeriesRegisterGroup)
            self.assertFalse((group.root / "registers").exists())
            self.assertEqual(tuple(group.registers), ("R0", "R1"))
            self.assertEqual(group.registers["R0"].encoding, 0)
            self.assertEqual(group.registers["R1"].encoding, 1)

    def test_rejects_unordered_variable_width_domain(self) -> None:
        with self.fixture() as directory:
            root = Path(directory)
            (root / "registers/groups/GPR/group.yaml").write_text(
                "width: {MAX_VLEN: [128, 512, 256]}\nseries: {prefix: R, count: 2}\n",
                encoding="utf-8",
            )

            with self.assertRaises(RegisterWidthDomainOrderError):
                load_registers(root, extensions=load_extension_inventory(root))

    def test_preserves_performance_selector_assignments(self) -> None:
        registers = self.catalog.registers

        self.assertEqual(
            registers[Reference.parse("base.registers.PERFORMANCE.CYCLE")].encoding, 1
        )
        self.assertEqual(
            registers[Reference.parse("base.registers.PERFORMANCE.PTWALK")].encoding, 3
        )

    def test_rejects_series_with_explicit_register_directory(self) -> None:
        with self.fixture() as directory:
            root = Path(directory)
            group = root / "registers/groups/GPR"
            (group / "registers").mkdir()
            (group / "registers/registers.yaml").write_text(
                "registers: []\n", encoding="utf-8"
            )

            with self.assertRaises(RegisterGroupSourceConflictError):
                load_registers(root, extensions=load_extension_inventory(root))

    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "schemas").mkdir()
        (root / "extensions").mkdir()
        (root / "registers/groups/GPR").mkdir(parents=True)
        for schema in (
            "register-group.yaml",
            "register.yaml",
            "register-layout.yaml",
        ):
            shutil.copy2(self.isa_root / "schemas" / schema, root / "schemas" / schema)
        (root / "extensions/extensions.yaml").write_text("extensions: []\n")
        (root / "registers/groups/groups.yaml").write_text("groups: [GPR]\n")
        (root / "registers/groups/GPR/group.yaml").write_text(
            "width: 64\nseries: {prefix: R, count: 2}\n"
        )
        return temporary


if __name__ == "__main__":
    unittest.main()
