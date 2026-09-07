
from engine.isa.types import load_type_system
from engine.isa.extensions import load_extension_inventory
import tempfile
import unittest
from pathlib import Path

import yaml

from engine.reference import Reference, UnknownReferenceError
from engine.isa.types import TypeSystem


class TypeSystemTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self._write("extensions/extensions.yaml", {"extensions": ["SAMPLE"]})
        self._write(
            "field_types.yaml",
            {
                "field_types": {
                    "WIDTH": {
                        "type": "size_selector",
                        "bits": 1,
                        "values": [
                            {"value": 0, "code": "NARROW"},
                            {"value": 1, "code": "WIDE"},
                        ],
                    }
                }
            },
        )
        self._write(
            "payload_types.yaml",
            {
                "payload_types": {
                    "BASE_ONLY": {
                        "type": "immediate",
                        "bytes": 2,
                        "value_type": "unsigned_integer",
                    }
                }
            },
        )
        self._write(
            "extensions/SAMPLE/field_types.yaml",
            {
                "field_types": {
                    "COUNT": {
                        "type": "immediate",
                        "bits": 3,
                        "value_type": "unsigned_integer",
                    }
                }
            },
        )
        self._write("extensions/SAMPLE/payload_types.yaml", {"payload_types": {}})

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(self, relative: str, document: object) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    def test_loads_declared_owner_namespaces(self) -> None:
        types = load_type_system(self.root, extensions=load_extension_inventory(self.root))

        self.assertEqual(set(types.extensions), {"SAMPLE"})
        self.assertEqual(types.namespace("base").owner, "base")
        self.assertEqual(types.namespace("SAMPLE").owner, "SAMPLE")

    def test_projects_typed_definitions_into_global_indexes(self) -> None:
        types = load_type_system(self.root, extensions=load_extension_inventory(self.root))
        namespaces = (types.base, *types.extensions.values())

        for namespace in namespaces:
            for reference, definition in namespace.field_types.items():
                self.assertIs(types.field_types[reference], definition)
            for reference, definition in namespace.payload_types.items():
                self.assertIs(types.payload_types[reference], definition)

    def test_reference_resolution_does_not_fall_back_across_owners(self) -> None:
        types = load_type_system(self.root, extensions=load_extension_inventory(self.root))

        with self.assertRaises(UnknownReferenceError):
            types.payload_types.resolve(
                Reference.parse("SAMPLE.payload_types.BASE_ONLY")
            )

    def test_undeclared_extension_namespace_is_rejected_before_loading_types(self) -> None:
        self._write(
            "extensions/UNLISTED/field_types.yaml",
            {
                "field_types": {
                    "Hidden": {
                        "type": "immediate",
                        "bits": 1,
                        "value_type": "unsigned_integer",
                    }
                }
            },
        )
        self._write("extensions/UNLISTED/payload_types.yaml", {"payload_types": {}})

        with self.assertRaises(ValueError):
            load_type_system(self.root, extensions=load_extension_inventory(self.root))



if __name__ == "__main__":
    unittest.main()
