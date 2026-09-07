"""Canonical EA selection and architected evaluation order."""

import unittest
from pathlib import Path

from engine.documents.fragments.ea import project_mode, select_ea_diagram
from engine.isa.ea import (
    EAAutoupdate, EABaseSource, EABinary, EACalculate, EACapture, EAEncoding,
    EAField, EAIdentifier, EAModeCatalog, EAPayload, EARegisterUpdate,
    MemoryEAMode,
)
from engine.isa.model import DocumentTopic
from engine.isa.types import ImmediatePayloadType, RegisterFieldType
from engine.reference import Reference, ReferenceIndex


def _mode(update=None, owner="base"):
    root = Path("/synthetic")
    register_type = RegisterFieldType(Reference(owner, ("field_types",), "Rn"), root / "field_types.yaml", owner, "Rn", 3, Reference(owner, ("register_groups",), "GENERAL"))
    payload_type = ImmediatePayloadType(Reference(owner, ("payload_types",), "DISP"), root / "payload_types.yaml", owner, "DISP", 2, "signed_integer")
    fields = (EAField("b", "base", register_type.reference), EAField("i", "index", register_type.reference))
    payload = EAPayload("displacement", payload_type.reference)
    encoding = EAEncoding(("00bbbiii",), (payload,), update)
    catalog = EAModeCatalog(root / "modes.yaml", owner, "Ea", "extension", "Address modes", ("indexed",))
    expression = EABinary("+", EABinary("+", EAIdentifier("base"), EAIdentifier("index")), EAIdentifier("displacement"))
    mode = MemoryEAMode(catalog.reference("indexed"), root / "indexed/mode.yaml", catalog, "indexed", "Indexed", (encoding,), fields, "[Rn(b) + Rn(i) + displacement]", expression, None, EABaseSource.ENCODED)
    return mode, register_type, payload_type


class GenerateEADiagramsTest(unittest.TestCase):
    def test_selected_encoding_preserves_canonical_bindings_and_declared_widths(self):
        mode, field_type, payload_type = _mode()
        projection = project_mode(mode, mode.encodings[0], field_types=ReferenceIndex({field_type.reference: field_type}), payload_types=ReferenceIndex({payload_type.reference: payload_type}))
        resolved, = projection.encodings
        self.assertIs(projection.mode, mode)
        self.assertIs(resolved.encoding, mode.encodings[0])
        for binding, field in zip(resolved.fields, mode.fields, strict=True):
            self.assertIs(binding.field, field)
            self.assertIs(binding.definition, field_type)
        self.assertEqual(tuple(binding.positions for binding in resolved.fields), ((5, 4, 3), (2, 1, 0)))
        self.assertEqual(resolved.pattern.bit_width, 8)
        self.assertIs(resolved.payloads[0].payload, mode.encodings[0].payloads[0])
        self.assertIs(resolved.payloads[0].definition, payload_type)
        self.assertEqual(resolved.payload_width, 16)

    def test_predecrement_happens_before_capture_of_updated_base(self):
        update = EAAutoupdate("base", "predecrement", 8)
        mode, field_type, payload_type = _mode(update)
        projection = project_mode(mode, field_types=ReferenceIndex({field_type.reference: field_type}), payload_types=ReferenceIndex({payload_type.reference: payload_type}))
        steps, = projection.evaluation
        self.assertEqual(tuple(type(step) for step in steps), (EARegisterUpdate, EACapture, EACapture, EACapture, EACalculate))
        self.assertIs(steps[0].update, update)
        self.assertIs(steps[0].binding.field, mode.fields[0])
        self.assertIs(steps[1].binding.field, mode.fields[0])
        self.assertIs(steps[2].binding.field, mode.fields[1])
        self.assertIs(steps[3].binding.payload, mode.encodings[0].payloads[0])
        self.assertIs(steps[4].expression, mode.expression)

    def test_postincrement_happens_after_capture_and_before_next_operand(self):
        update = EAAutoupdate("base", "postincrement", 8)
        mode, field_type, payload_type = _mode(update)
        projection = project_mode(mode, field_types=ReferenceIndex({field_type.reference: field_type}), payload_types=ReferenceIndex({payload_type.reference: payload_type}))
        steps, = projection.evaluation
        self.assertEqual(tuple(type(step) for step in steps), (EACapture, EARegisterUpdate, EACapture, EACapture, EACalculate))
        self.assertIs(steps[0].binding.field, mode.fields[0])
        self.assertIs(steps[1].update, update)
        self.assertIs(steps[2].binding.field, mode.fields[1])

    def test_owner_local_selection_returns_the_canonical_mode(self):
        mode, field_type, payload_type = _mode()
        topic = DocumentTopic("base", "address", Reference("base", ("topics",), "address"), Path("/synthetic/model.yaml"), Path("/synthetic/address.tex"))
        selected = select_ea_diagram(mode.reference, owner=topic, modes=ReferenceIndex({mode.reference: mode}), field_types=ReferenceIndex({field_type.reference: field_type}), payload_types=ReferenceIndex({payload_type.reference: payload_type}))
        self.assertIs(selected.mode, mode)

    def test_foreign_mode_selection_is_rejected(self):
        mode, field_type, payload_type = _mode(owner="OTHER")
        topic = DocumentTopic("base", "address", Reference("base", ("topics",), "address"), Path("/synthetic/model.yaml"), Path("/synthetic/address.tex"))
        with self.assertRaises(ValueError):
            select_ea_diagram(mode.reference, owner=topic, modes=ReferenceIndex({mode.reference: mode}), field_types=ReferenceIndex({field_type.reference: field_type}), payload_types=ReferenceIndex({payload_type.reference: payload_type}))


if __name__ == "__main__":
    unittest.main()
