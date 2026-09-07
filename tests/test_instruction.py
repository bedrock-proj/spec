"""Published instruction values and explicit detached source editing."""

import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

import yaml

from engine.isa.instructions import decode_instruction, load_instruction, save_instruction, UnknownRepeatObservedValueError


class InstructionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = yaml.safe_load((Path(__file__).parents[1] / "isa/schemas/instruction.yaml").read_text())

    @staticmethod
    def document():
        return {
            "name": "Add", "summary": "Adds the source to the destination.",
            "route": "integer_alu", "privileged": False,
            "repeat": {"type": "repcc", "observed_value": "dst"},
            "operands": {
                "src": {"role": "source", "access": "read", "value_type": "integer"},
                "dst": {"role": "destination", "access": "read_write", "value_type": "integer"},
            },
        }

    def test_published_operands_cannot_be_modified(self):
        instruction = decode_instruction(self.document(), source=Path("/synthetic/ADD/instruction.yaml"), schema=self.schema)
        with self.assertRaises(TypeError):
            instruction.operands["src"] = instruction.operands["dst"]
        with self.assertRaises(FrozenInstanceError):
            instruction.operands["src"].access = "write"
        self.assertEqual(instruction.operands["src"].access, "read")

    def test_detached_edit_cannot_change_the_published_snapshot(self):
        source = Path("/synthetic/ADD/instruction.yaml")
        instruction = decode_instruction(self.document(), source=source, schema=self.schema)
        draft = instruction.to_dict()
        draft["operands"]["src"]["access"] = "write"
        self.assertEqual(instruction.operands["src"].access, "read")

    def test_unknown_repeat_observed_operand_is_rejected(self):
        document = self.document()
        document["repeat"]["observed_value"] = "missing"
        with self.assertRaises(UnknownRepeatObservedValueError):
            decode_instruction(document, source=Path("/synthetic/ADD/instruction.yaml"), schema=self.schema)

    def test_computed_repeat_observed_value_is_preserved(self):
        document = self.document()
        document["repeat"]["observed_value"] = "computed"
        instruction = decode_instruction(document, source=Path("/synthetic/ADD/instruction.yaml"), schema=self.schema)
        self.assertEqual(instruction.repeat.observed_value, "computed")

    def test_rejected_edit_leaves_the_original_snapshot_unchanged(self):
        source = Path("/synthetic/ADD/instruction.yaml")
        instruction = decode_instruction(self.document(), source=source, schema=self.schema)
        draft = instruction.to_dict()
        draft["privileged"] = "no"
        with self.assertRaises(ValueError):
            decode_instruction(draft, source=source, schema=self.schema)
        self.assertIs(instruction.privileged, False)

    def test_save_publishes_the_validated_edit_and_keeps_prior_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "instruction.yaml"
            source.write_text(yaml.safe_dump(self.document(), sort_keys=False))
            original = load_instruction(source, schema=self.schema)
            draft = original.to_dict()
            draft["summary"] = "Updated authored description."
            saved = save_instruction(draft, source, schema=self.schema)
            self.assertEqual(saved.summary, draft["summary"])
            self.assertEqual(yaml.safe_load(source.read_text())["summary"], draft["summary"])
            self.assertEqual(original.summary, self.document()["summary"])

    def test_serialization_failure_does_not_replace_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "instruction.yaml"
            original = yaml.safe_dump(self.document()).encode()
            source.write_bytes(original)
            with patch("engine.isa.instructions.yaml.safe_dump", side_effect=OSError("serialization failure")), self.assertRaises(OSError):
                save_instruction(self.document(), source, schema=self.schema)
            self.assertEqual(source.read_bytes(), original)

    def test_replacement_rejected_before_effect_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "instruction.yaml"
            original = yaml.safe_dump(self.document()).encode()
            source.write_bytes(original)
            with patch("engine.isa.instructions.os.replace", side_effect=PermissionError("replacement denied")), self.assertRaises(PermissionError):
                save_instruction(self.document(), source, schema=self.schema)
            self.assertEqual(source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
