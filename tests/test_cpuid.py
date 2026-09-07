import unittest
from pathlib import Path

from engine.reference import Reference, ReferenceIndex
from engine.source.inventory import DirectoryInventory
from engine.isa.cpuid import (
    CpuidCatalog,
    CpuidClassDefinition,
    CpuidCommonHeader,
    CpuidField,
    CpuidIndexRange,
    CpuidLeafDefinition,
    CpuidNamespace,
    CpuidQuery,
)

from engine.isa.cpuid import compose_selector, extension_discovery_leaf_value


class CpuidWireFormatTest(unittest.TestCase):
    def test_extension_directory_slot_determines_discovery_leaf(self) -> None:
        cases = (
            (1, 0, 1),
            (1, 63, 64),
            (2, 0, 65),
            (1023, 63, 65472),
        )
        for directory_index, directory_bit, expected_leaf in cases:
            with self.subTest(index=directory_index, bit=directory_bit):
                self.assertEqual(
                    extension_discovery_leaf_value(directory_index, directory_bit),
                    expected_leaf,
                )

    def test_composes_architectural_selector_fields(self) -> None:
        self.assertEqual(
            compose_selector(0x89ABCDEF, 0x4567, 0x0123),
            0x89ABCDEF45670123,
        )


class CpuidOwnershipTest(unittest.TestCase):
    def test_query_field_resolution_uses_canonical_membership(self) -> None:
        source = Path("synthetic.yaml")
        field = CpuidField(
            Reference("base", ("cpuid", "TEST", "CAPABILITIES", "FEATURES"), "FEATURE"), source, "FEATURE", 3, 1
        )
        query = CpuidQuery(
            Reference("base", ("cpuid", "TEST", "CAPABILITIES"), "FEATURES"),
            source,
            "FEATURES",
            CpuidIndexRange(5, 5),
            (field,),
        )
        leaf = CpuidLeafDefinition(
            reference=Reference("base", ("cpuid", "TEST"), "CAPABILITIES"),
            source=source,
            root=source.parent,
            id="CAPABILITIES",
            name="Capabilities",
            layouts={},
            queries=(query,),
            value=7,
        )
        cpuid_class = CpuidClassDefinition(
            reference=Reference("base", ("cpuid",), "TEST"),
            source=source,
            root=source.parent,
            id="TEST",
            name="Test",
            leaf_inventory=DirectoryInventory(
                "base",
                "leaves",
                source,
                source.parent,
                (leaf.id,),
                (leaf.id,),
            ),
            leaves={leaf.id: leaf},
            value=9,
        )
        header = CpuidCommonHeader(
            Reference("base", ("cpuid",), "HEADER"),
            source,
            "HEADER",
            64,
            (),
        )
        catalog = CpuidCatalog(
            namespaces={"base": CpuidNamespace(
                "base", source.parent,
                DirectoryInventory("base", "classes", source, source.parent, (cpuid_class.id,), (cpuid_class.id,)),
                {cpuid_class.id: cpuid_class},
            )},
            common_header=header,
            classes=ReferenceIndex({cpuid_class.reference: cpuid_class}),
            leaves=ReferenceIndex({leaf.reference: leaf}),
            layouts=ReferenceIndex(),
            queries=ReferenceIndex({query.reference: query}),
            fields=ReferenceIndex({field.reference: field}),
            layout_fields=ReferenceIndex(),
            common_headers=ReferenceIndex({header.reference: header}),
            common_header_fields=ReferenceIndex(),
        )

        resolved_leaf, resolved_query, resolved_field = catalog.resolve_field(
            field.reference
        )

        self.assertIs(resolved_leaf.leaf, leaf)
        self.assertIs(resolved_query, query)
        self.assertIs(resolved_field, field)
        self.assertEqual((resolved_leaf.class_value, resolved_leaf.leaf_value), (9, 7))


if __name__ == "__main__":
    unittest.main()
