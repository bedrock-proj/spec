"""Instruction companions, closed membership, and typed overlap boundaries."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from engine.isa.catalog import InstructionBundle, check_bundle
from engine.isa.encoding import (
    AllowedOperandConstraint, EncodingCatalog, EncodingForm,
    ExcludedOperandConstraint, FieldBinding, constraint_ranges,
    resolve_encoding_form,
)
from engine.isa.encoding_reservations import EncodingReservationCatalog
from engine.isa.encoding_space import forms_overlap
from engine.isa.instructions import Instruction, InstructionOperand
from engine.isa.types import ImmediateFieldType
from engine.isa.vector_examples import VectorDiagramCatalog
from engine.reference import Reference, ReferenceIndex
from engine.sail.validation import check_sail_bundle
from engine.source.inventory import DirectoryInventory, require_exact
from engine.syntax.encoding import EncodingMetasyntax
from engine.syntax.instruction import InstructionMetasyntax


def _bundle(root):
    member = root / "SYN"
    member.mkdir()
    instruction = Instruction(member / "instruction.yaml", "Synthetic", "Synthetic", "alu", False, {})
    reference = Reference("base", ("instructions",), "SYN")
    diagrams = VectorDiagramCatalog("base", reference, member / "diagrams", None, ReferenceIndex({}))
    return InstructionBundle(reference, "base", instruction, EncodingCatalog(member / "encodings.yaml", ()), diagrams, member / "semantics.sail", member / "descriptions.tex", ())


class InstructionBoundaryTest(unittest.TestCase):
    def test_missing_companion_is_reported_without_stopping_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = _bundle(root)
            bundle.description.write_text("Authored explanation")
            inventory = DirectoryInventory("base", "reservation", root / "reservations.yaml", root, (), ())
            diagnostics = tuple(check_bundle(
                bundle, field_types=ReferenceIndex({}), payload_types=ReferenceIndex({}),
                ea_modes=ReferenceIndex({}), registers=SimpleNamespace(groups=ReferenceIndex({})),
                reservations=EncodingReservationCatalog(inventory, {}),
            ))
            self.assertEqual(tuple(item.code for item in diagnostics), ("artifact.missing",))
            self.assertEqual(diagnostics[0].source, bundle.semantics)

    def test_missing_instruction_owned_sail_entry_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = _bundle(Path(directory))
            bundle.semantics.write_text("function execute_OTHER() -> unit = ()\n")
            diagnostics = tuple(check_sail_bundle(bundle))
            self.assertEqual(tuple(item.code for item in diagnostics), ("sail.entry",))
            self.assertEqual(diagnostics[0].source, bundle.instruction.source)

    def test_declared_directory_missing_is_rejected_before_publication(self):
        inventory = DirectoryInventory("base", "instruction", Path("instructions.yaml"), Path("instructions"), ("MISSING",), ())
        self.assertEqual(inventory.missing, ("MISSING",))
        with self.assertRaises(ValueError):
            require_exact(inventory)

    def test_operand_constraints_separate_overlapping_raw_encodings(self):
        source = Path("SYN/instruction.yaml")
        instruction = Instruction(source, "Synthetic", "Synthetic", "alu", False, {"selector": InstructionOperand("source", "read", "integer")})
        field_type = ImmediateFieldType(Reference("base", ("field_types",), "SELECTOR"), Path("types.yaml"), "base", "SELECTOR", 2, "unsigned_integer")
        syntax = InstructionMetasyntax.parse("SYN <selector>(a)")
        raw = EncodingForm(syntax.encoding_id, EncodingMetasyntax.parse("00000aa"), syntax, fields=(FieldBinding("a", "selector", field_type.reference),))
        excluded = replace(raw, constraints=(ExcludedOperandConstraint("selector", "excluded value", (0,)),))
        allowed = replace(raw, constraints=(AllowedOperandConstraint("selector", "allowed value", (0,)),))
        context = dict(field_types=ReferenceIndex({field_type.reference: field_type}), payload_types=ReferenceIndex({}), ea_modes=ReferenceIndex({}), registers=SimpleNamespace(groups=ReferenceIndex({})))
        left = resolve_encoding_form(instruction, excluded, **context)
        right = resolve_encoding_form(instruction, allowed, **context)
        whole = resolve_encoding_form(instruction, raw, **context)
        self.assertTrue(raw.pattern.overlaps(allowed.pattern))
        self.assertFalse(forms_overlap(left, right))
        self.assertTrue(forms_overlap(whole, right))

    def test_typed_form_rejects_more_than_one_binding_for_a_pattern_marker(self):
        source = Path("SYN/instruction.yaml")
        instruction = Instruction(
            source,
            "Synthetic",
            "Synthetic",
            "alu",
            False,
            {
                "left": InstructionOperand("source", "read", "integer"),
                "right": InstructionOperand("source", "read", "integer"),
            },
        )
        field_type = ImmediateFieldType(
            Reference("base", ("field_types",), "BIT"),
            Path("types.yaml"),
            "base",
            "BIT",
            1,
            "unsigned_integer",
        )
        syntax = InstructionMetasyntax.parse("SYN <left>(a), <right>(a)")
        form = EncodingForm(
            syntax.encoding_id,
            EncodingMetasyntax.parse("000000a"),
            syntax,
            fields=(
                FieldBinding("a", "left", field_type.reference),
                FieldBinding("a", "right", field_type.reference),
            ),
        )
        context = dict(
            field_types=ReferenceIndex({field_type.reference: field_type}),
            payload_types=ReferenceIndex({}),
            ea_modes=ReferenceIndex({}),
            registers=SimpleNamespace(groups=ReferenceIndex({})),
        )

        with self.assertRaisesRegex(ValueError, "field.duplicate-marker"):
            resolve_encoding_form(instruction, form, **context)

    def test_resolved_operands_put_hidden_selectors_before_authored_display_order(self):
        source = Path("SYN/instruction.yaml")
        instruction = Instruction(
            source,
            "Synthetic",
            "Synthetic",
            "alu",
            False,
            {
                "hidden": InstructionOperand("implicit", "read", "integer"),
                "src": InstructionOperand("source", "read", "integer"),
                "dst": InstructionOperand("destination", "write", "integer"),
            },
        )
        field_type = ImmediateFieldType(
            Reference("base", ("field_types",), "BIT"),
            Path("types.yaml"),
            "base",
            "BIT",
            1,
            "unsigned_integer",
        )
        syntax = InstructionMetasyntax.parse("SYN <src>(s), <dst>(d)")
        form = EncodingForm(
            syntax.encoding_id,
            EncodingMetasyntax.parse("0000hsd"),
            syntax,
            fields=tuple(
                FieldBinding(marker, role, field_type.reference)
                for marker, role in (("h", "hidden"), ("s", "src"), ("d", "dst"))
            ),
        )
        context = dict(
            field_types=ReferenceIndex({field_type.reference: field_type}),
            payload_types=ReferenceIndex({}),
            ea_modes=ReferenceIndex({}),
            registers=SimpleNamespace(groups=ReferenceIndex({})),
        )

        resolved = resolve_encoding_form(instruction, form, **context)

        self.assertEqual(tuple(item.name for item in resolved.operands), ("hidden", "src", "dst"))

    def test_repeated_display_occurrence_keeps_one_canonical_operand(self):
        source = Path("SYN/instruction.yaml")
        instruction = Instruction(
            source,
            "Synthetic",
            "Synthetic",
            "alu",
            False,
            {"src": InstructionOperand("source", "read", "integer")},
        )
        field_type = ImmediateFieldType(
            Reference("base", ("field_types",), "BIT"),
            Path("types.yaml"),
            "base",
            "BIT",
            1,
            "unsigned_integer",
        )
        syntax = InstructionMetasyntax.parse("SYN <src>(s), <src>(s)")
        form = EncodingForm(
            syntax.encoding_id,
            EncodingMetasyntax.parse("000000s"),
            syntax,
            fields=(FieldBinding("s", "src", field_type.reference),),
        )
        context = dict(
            field_types=ReferenceIndex({field_type.reference: field_type}),
            payload_types=ReferenceIndex({}),
            ea_modes=ReferenceIndex({}),
            registers=SimpleNamespace(groups=ReferenceIndex({})),
        )

        resolved = resolve_encoding_form(instruction, form, **context)

        self.assertEqual(tuple(item.name for item in resolved.operands), ("src",))
        self.assertEqual(
            tuple(item.name for item in resolved.display_order), ("src", "src")
        )

    def test_constraint_ranges_reject_values_outside_the_field_domain(self):
        field_type = ImmediateFieldType(
            Reference("base", ("field_types",), "SELECTOR"),
            Path("types.yaml"),
            "base",
            "SELECTOR",
            2,
            "unsigned_integer",
        )
        syntax = InstructionMetasyntax.parse("SYN <selector>(a)")
        form = EncodingForm(
            syntax.encoding_id,
            EncodingMetasyntax.parse("00000aa"),
            syntax,
            fields=(FieldBinding("a", "selector", field_type.reference),),
        )
        context = dict(
            field_types=ReferenceIndex({field_type.reference: field_type}),
            ea_modes=(),
        )
        constraints = (
            AllowedOperandConstraint("selector", "outside", (4,)),
            ExcludedOperandConstraint("selector", "outside", (4,)),
            AllowedOperandConstraint("selector", "reversed", ("3..1",)),
            AllowedOperandConstraint("selector", "malformed", ("x..2",)),
            AllowedOperandConstraint("selector", "signed-token", ("+1..2",)),
            AllowedOperandConstraint("selector", "binary-token", ("0b1..0b10",)),
            ExcludedOperandConstraint("selector", "empty", ("0..3",)),
        )

        for constraint in constraints:
            with self.subTest(constraint=constraint):
                with self.assertRaises(ValueError):
                    constraint_ranges(form, constraint, **context)


if __name__ == "__main__":
    unittest.main()
