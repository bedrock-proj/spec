import unittest

from engine.reference import (
    DuplicateReferenceError,
    QualifiedReference,
    Reference,
    ReferenceError,
    ReferenceIndex,
    UnknownReferenceError,
    register_reference,
    resolve_reference,
)


class ReferenceTest(unittest.TestCase):
    def test_round_trip_with_arbitrary_path_depth(self) -> None:
        reference = Reference.parse("VECTOR.vea.EXT2.explicit_segment_index")

        self.assertEqual(reference.owner, "VECTOR")
        self.assertEqual(reference.path, ("vea", "EXT2"))
        self.assertEqual(reference.element, "explicit_segment_index")
        self.assertEqual(
            reference,
            Reference("VECTOR", ("vea", "EXT2"), "explicit_segment_index"),
        )

    def test_path_may_be_empty(self) -> None:
        self.assertEqual(Reference.parse("base.ADD").path, ())

    def test_document_style_hyphenated_segments_are_supported(self) -> None:
        reference = Reference.parse("base.architecture.terminology.address-values")

        self.assertEqual(reference.element, "address-values")

    def test_invalid_references_are_rejected(self) -> None:
        for value in ("register", "vector.vea.register", "base.ea..register"):
            with self.subTest(value=value), self.assertRaises(ReferenceError):
                Reference.parse(value)

    def test_index_normalizes_and_resolves_references(self) -> None:
        index = {}
        reference = register_reference(
            index, Reference.parse("base.ea.compact.register"), 7
        )

        self.assertEqual(resolve_reference(index, reference), 7)
        self.assertEqual(index[reference], 7)
        with self.assertRaises(DuplicateReferenceError):
            register_reference(index, reference, 8)
        with self.assertRaises(UnknownReferenceError):
            resolve_reference(index, Reference.parse("base.ea.compact.immediate"))

    def test_index_membership_does_not_resolve_missing_references(self) -> None:
        index = {}
        existing = Reference.parse("base.field_types.Rn")
        missing = Reference.parse("base.field_types.Missing")
        register_reference(index, existing, 4)

        index = ReferenceIndex(index)
        self.assertIn(existing, index)
        self.assertNotIn(missing, index)
        with self.assertRaises(ReferenceError):
            "not-a-reference" in index

    def test_qualified_reference_separates_domain_from_local_owner(self) -> None:
        reference = QualifiedReference.parse(
            "interfaces.c:FP.intrinsics.fpu.fclass_f32"
        )

        self.assertEqual(reference.domain, "interfaces.c")
        self.assertEqual(reference.local.owner, "FP")
        self.assertEqual(reference.local.path, ("intrinsics", "fpu"))
        self.assertEqual(reference.local.element, "fclass_f32")
        self.assertEqual(
            reference,
            QualifiedReference(
                "interfaces.c",
                Reference("FP", ("intrinsics", "fpu"), "fclass_f32"),
            ),
        )

    def test_local_reference_can_be_qualified_in_provider_context(self) -> None:
        reference = QualifiedReference.parse(
            "base.types.mmu.query_result", current_domain="interfaces.c"
        )

        self.assertEqual(
            reference,
            QualifiedReference(
                "interfaces.c",
                Reference("base", ("types", "mmu"), "query_result"),
            ),
        )

    def test_unqualified_reference_requires_context(self) -> None:
        with self.assertRaises(ReferenceError):
            QualifiedReference.parse("base.instructions.ADD")


if __name__ == "__main__":
    unittest.main()
