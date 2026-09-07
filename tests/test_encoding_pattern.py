import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from engine.syntax.encoding import EncodingMetasyntax, EncodingMetasyntaxError


class EncodingMetasyntaxTest(unittest.TestCase):
    def test_normalizes_pattern_chunks(self) -> None:
        self.assertEqual(
            EncodingMetasyntax.parse(["10aa", "01bb"]),
            EncodingMetasyntax.parse("10aa01bb"),
        )

    def test_reports_width_fields_and_fixed_bits(self) -> None:
        pattern = EncodingMetasyntax.parse("10aa01")

        self.assertEqual(pattern.bit_width, 6)
        self.assertEqual(pattern.fields, {"a"})
        self.assertEqual(pattern.field_width("a"), 2)
        self.assertEqual(pattern.fixed_mask, 0b110011)
        self.assertEqual(pattern.fixed_value, 0b100001)

    def test_matches_and_detects_overlap(self) -> None:
        pattern = EncodingMetasyntax.parse("10aa01")

        self.assertTrue(pattern.matches(0b101101))
        self.assertFalse(pattern.matches(0b111101))
        self.assertTrue(
            EncodingMetasyntax.parse("10aa").overlaps(EncodingMetasyntax.parse("1b0b"))
        )
        self.assertFalse(
            EncodingMetasyntax.parse("10aa").overlaps(EncodingMetasyntax.parse("11bb"))
        )
        self.assertFalse(
            EncodingMetasyntax.parse("10aa").overlaps(EncodingMetasyntax.parse("10aaa"))
        )

    def test_extracts_non_contiguous_fields(self) -> None:
        self.assertEqual(EncodingMetasyntax.parse("a1a0").extract(0b0110, "a"), 0b01)

    def test_rejects_invalid_patterns_fields_and_values(self) -> None:
        with self.assertRaises(EncodingMetasyntaxError):
            EncodingMetasyntax.parse("10-A")
        with self.assertRaises(EncodingMetasyntaxError):
            EncodingMetasyntax.parse([])
        with self.assertRaises(EncodingMetasyntaxError):
            EncodingMetasyntax.parse("10aa").field_width("field")
        with self.assertRaises(EncodingMetasyntaxError):
            EncodingMetasyntax.parse("10aa").matches(16)
        with self.assertRaises(EncodingMetasyntaxError):
            EncodingMetasyntax.parse("10aa").extract(0b1000, "b")


class EncodingMetasyntaxSchemaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        isa_root = Path(__file__).parents[1] / "isa"
        cls.schema = yaml.safe_load(
            (isa_root / "schemas/encoding-metasyntax.yaml").read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema)

    def assert_valid(self, pattern: str) -> None:
        self.assertEqual(list(self.validator.iter_errors(pattern)), [])

    def assert_invalid(self, pattern: object) -> None:
        self.assertNotEqual(list(self.validator.iter_errors(pattern)), [])

    def test_accepts_fixed_bits_and_field_markers(self) -> None:
        for pattern in ("0", "1", "10aa01", "a1a0"):
            self.assert_valid(pattern)

    def test_rejects_non_pattern_text(self) -> None:
        for pattern in ("", "10-A", "10 aa", ["10", "aa"]):
            self.assert_invalid(pattern)


if __name__ == "__main__":
    unittest.main()
