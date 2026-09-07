"""An EA family publishes exactly its declared local member inventory."""

import tempfile
import unittest
from pathlib import Path

from engine.isa.ea import load_ea_catalog


class EAModeInventoryTest(unittest.TestCase):
    def test_declared_member_order_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("first", "second"):
                (root / name).mkdir()
            source = root / "modes.yaml"
            source.write_text("name: Modes\nmodes: [second, first]\n")
            catalog = load_ea_catalog(source, owner="base", profile="Ea", mode_type="compact")
            self.assertEqual(catalog.modes, ("second", "first"))

    def test_missing_declared_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "modes.yaml"
            source.write_text("name: Modes\nmodes: [absent]\n")
            with self.assertRaises(ValueError):
                load_ea_catalog(source, owner="base", profile="Ea", mode_type="compact")

    def test_undeclared_member_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "unlisted").mkdir()
            source = root / "modes.yaml"
            source.write_text("name: Modes\nmodes: []\n")
            with self.assertRaises(ValueError):
                load_ea_catalog(source, owner="base", profile="Ea", mode_type="compact")

    def test_duplicate_declared_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "repeated").mkdir()
            source = root / "modes.yaml"
            source.write_text("name: Modes\nmodes: [repeated, repeated]\n")
            with self.assertRaises(ValueError):
                load_ea_catalog(source, owner="base", profile="Ea", mode_type="compact")


if __name__ == "__main__":
    unittest.main()
