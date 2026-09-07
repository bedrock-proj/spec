
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from engine.isa.project import load_isa


class InstructionEncodingsSchemaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        isa_root = Path(__file__).parents[1] / "isa"
        cls.isa_root = isa_root
        cls.schema = yaml.safe_load(
            (isa_root / "schemas/instruction-encodings.yaml").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema)
        cls.project = load_isa(isa_root)
        cls.types = cls.project.types

    @staticmethod
    def document() -> dict:
        return {
            "encodings": {
                "l_q_z_rn_s_rn_d": {
                    "pattern": "00001zssssdddd",
                    "syntax": "ADD.{L|Q}(z) Rn(s), Rn(d)",
                    "fields": {
                        "z": {
                            "role": "size",
                            "type": "base.field_types.SIZE_LQ",
                        },
                        "s": {
                            "role": "src",
                            "type": "base.field_types.Rn",
                        },
                        "d": {
                            "role": "dst",
                            "type": "base.field_types.Rn",
                        },
                    },
                    "constraints": [
                        {
                            "role": "dst",
                            "exclude": ["immediate"],
                            "reason": "invalid_destination",
                        }
                    ],
                    "overlaps": [
                        {
                            "operands": ["src", "dst"],
                            "type": "same_value",
                        }
                    ],
                },
                "imm16s": {
                    "pattern": "111101000000000000",
                    "syntax": "JMP <imm16s>",
                    "payloads": [
                        {
                            "role": "target",
                            "type": "base.payload_types.DISP16S",
                        }
                    ],
                },
            }
        }

    def assert_valid(self, document: dict) -> None:
        self.assertEqual(list(self.validator.iter_errors(document)), [])

    def assert_invalid(self, document: dict) -> None:
        self.assertNotEqual(list(self.validator.iter_errors(document)), [])

    def test_accepts_local_id_mapping_and_split_representations(self) -> None:
        self.assert_valid(self.document())

    def test_loaded_instruction_sets_match_their_declared_inventory(self) -> None:
        instruction_sets = (
            self.project.catalog.base,
            *(
                extension.instruction_set
                for extension in self.project.catalog.extensions.values()
            ),
        )
        for instruction_set in instruction_sets:
            with self.subTest(owner=instruction_set.catalog.owner):
                self.assertEqual(
                    set(instruction_set.catalog.declared),
                    set(instruction_set.catalog.actual),
                )

    def test_loaded_forms_obey_their_typed_encoding_contract(self) -> None:
        for bundle in self.project.catalog.select():
            for form in bundle.encodings.forms:
                with self.subTest(reference=bundle.reference, encoding=form.id):
                    self.assertEqual(form.syntax.mnemonic, bundle.instruction.mnemonic)
                    self.assertEqual(form.syntax.encoding_id, form.id)
                    self.assertEqual(
                        form.pattern.fields,
                        {field.marker for field in form.fields},
                    )
                    for field in form.fields:
                        definition = self.types.field_types.resolve(field.type)
                        self.assertEqual(
                            form.pattern.field_width(field.marker), definition.bits
                        )
                    for payload in form.payloads:
                        self.types.payload_types.resolve(payload.type)

    def test_rejects_unknown_form_properties(self) -> None:
        encoding_id = "l_q_z_rn_s_rn_d"
        document = self.document()
        document["encodings"][encoding_id]["unexpected"] = "value"
        self.assert_invalid(document)

    def test_rejects_nonlocal_ids(self) -> None:
        document = self.document()
        encoding_id = "l_q_z_rn_s_rn_d"
        document["encodings"]["short.add.rn_rn"] = document["encodings"].pop(
            encoding_id
        )
        self.assert_invalid(document)

    def test_rejects_unknown_pattern_widths(self) -> None:
        document = self.document()
        encoding_id = "l_q_z_rn_s_rn_d"
        document["encodings"][encoding_id]["pattern"] = "00000000"
        self.assert_invalid(document)

    def test_constraint_selects_exactly_one_operation(self) -> None:
        document = self.document()
        encoding_id = "l_q_z_rn_s_rn_d"
        constraint = document["encodings"][encoding_id]["constraints"][0]
        constraint["allow"] = [0]
        self.assert_invalid(document)

        document = self.document()
        del document["encodings"][encoding_id]["constraints"][0]["exclude"]
        self.assert_invalid(document)


if __name__ == "__main__":
    unittest.main()
