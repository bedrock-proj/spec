import unittest
from pathlib import Path

from engine.isa.encoding_space import CandidateOutsideNamespaceError
from engine.isa.encoding_space import EncodingCube
from engine.isa.encoding_space import analyze_encoding_space
from engine.isa.encoding_space import entries_encoding_space
from engine.isa.encoding_space import summaries_encoding_space
from engine.isa.encoding_space import holes_encoding_space
from engine.isa.encoding_space import check_candidate_encoding_space
from engine.isa.encoding_space import EncodingSpaceEntry
from engine.isa.encoding_space import unavailable_cubes
from engine.isa.encoding import resolve_encoding_form
from engine.isa.encoding_reservations import reservation_cubes
from engine.isa.encoding_architecture import (
    ENCODING_CLASSES,
    OperatorSpaceUnavailableError,
    encoding_class,
    operator_space,
)
from engine.isa.project import load_isa
from engine.reference import Reference


class EncodingArchitectureTest(unittest.TestCase):
    def test_named_classes_retain_architectural_namespaces(self) -> None:
        self.assertEqual(encoding_class("short").pattern_bits, 14)
        self.assertEqual(
            encoding_class("medium").namespace,
            (
                "0?????????????????",
                "10????????????????",
                "110???????????????",
                "1110??????????????",
            ),
        )

    def test_operator_spaces_are_scoped_by_class(self) -> None:
        self.assertEqual(operator_space("extralong", "vector").prefix, "11111101??")
        with self.assertRaises(OperatorSpaceUnavailableError):
            operator_space("long", "vector")


class EncodingCubeTest(unittest.TestCase):
    def test_short_pattern_is_right_padded_and_intersected(self) -> None:
        prefix = EncodingCube.parse("1111", 8)
        subset = EncodingCube.parse("111100??")

        self.assertEqual(prefix.pattern, "1111????")
        self.assertTrue(prefix.contains(subset))
        self.assertEqual(prefix.intersection(subset), subset)


class EncodingSpaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.isa_root = Path(__file__).parents[1] / "isa"
        cls.project = load_isa(cls.isa_root)
        cls.forms = tuple(
            (bundle.reference, bundle.encodings.source, resolve_encoding_form(
                bundle.instruction, form, field_types=cls.project.types.field_types,
                payload_types=cls.project.types.payload_types,
                ea_modes=cls.project.catalog.ea_modes, registers=cls.project.registers,
            ))
            for bundle in cls.project.catalog.select() for form in bundle.encodings.forms
        )
        cls.reservations = reservation_cubes(cls.project.encoding_reservations)
        cls.entries = entries_encoding_space(cls.forms)

    def test_complete_map_contains_every_form_without_collisions(self) -> None:
        analysis = analyze_encoding_space(self.forms)
        form_count = sum(
            len(bundle.encodings.forms) for bundle in self.project.catalog.select()
        )

        self.assertEqual(len(analysis.entries), form_count)
        self.assertEqual(analysis.collisions, ())

    def test_summary_partitions_assigned_reclaimed_reserved_and_free(self) -> None:
        summaries = summaries_encoding_space(self.forms, reservations=self.reservations)

        by_class = {item.encoding_class: item for item in summaries}
        self.assertEqual(set(by_class), {item.name for item in ENCODING_CLASSES})
        for item in summaries:
            self.assertEqual(
                item.namespace_slots,
                item.assigned_slots
                + item.reclaimed_slots
                + item.reserved_slots
                + item.clean_free_slots,
            )
            self.assertEqual(
                item.remaining_slots,
                item.reclaimed_slots + item.clean_free_slots,
            )

    def test_entries_can_be_scoped_to_named_operator_space(self) -> None:
        entries = entries_encoding_space(self.forms, "extralong", space="vector")
        self.assertTrue(entries)
        selected_mnemonic = entries[0].mnemonic
        filtered = entries_encoding_space(
            self.forms, "extralong", space="vector", grep=selected_mnemonic
        )
        self.assertEqual(
            filtered,
            tuple(entry for entry in entries if selected_mnemonic in entry.name),
        )

    def test_candidate_outside_class_namespace_is_rejected(self) -> None:
        with self.assertRaises(CandidateOutsideNamespaceError) as caught:
            check_candidate_encoding_space(self.forms, "xxlong", "0000", reservations=self.reservations)

        self.assertEqual(caught.exception.encoding_class, "xxlong")
        self.assertEqual(caught.exception.pattern, "0000" + "?" * 38)
        self.assertIsNone(caught.exception.space)

    def test_reclaimed_policy_uses_legal_instead_of_raw_reservation(self) -> None:
        raw = EncodingCube.parse("10??")
        legal = EncodingCube.parse("100?")
        entry = EncodingSpaceEntry(
            Reference.parse("base.instructions.SYNTHETIC"),
            "base",
            "SYNTHETIC",
            "fixture",
            self.isa_root / "fixture.yaml",
            raw.pattern,
            (raw,),
            (legal,),
        )

        self.assertEqual(
            unavailable_cubes((entry,), include_reclaimed=False),
            (raw,),
        )
        self.assertEqual(
            unavailable_cubes((entry,), include_reclaimed=True),
            (legal,),
        )

    def test_holes_stay_inside_operator_space_and_avoid_raw_reservations(self) -> None:
        holes = holes_encoding_space(
            self.forms,
            "xxlong",
            reservations=self.reservations,
            space="vector",
            min_slots=16,
            limit=5,
        )
        space = operator_space("xxlong", "vector")
        scope = EncodingCube.parse(
            space.prefix,
            encoding_class(space.encoding_class).pattern_bits,
        )
        raw = tuple(
            cube
            for entry in self.entries
            if entry.width == 42
            for cube in entry.raw_cubes
        )

        self.assertLessEqual(len(holes), 5)
        for hole in holes:
            self.assertTrue(scope.contains(hole.cube))
            self.assertFalse(any(hole.cube.overlaps(cube) for cube in raw))


if __name__ == "__main__":
    unittest.main()
