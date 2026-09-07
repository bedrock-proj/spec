import unittest
from pathlib import Path

from engine.reference import Reference
from engine.syntax.semantic_text import (
    EntityReferenceText,
    LiteralText,
    SemanticText,
    SemanticTextError,
    SemanticTextErrorReason,
    TermForm,
    TermReferenceText,
    TextOrigin,
)


class SemanticTextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.origin = TextOrigin(Path("term.yaml"), ("definition",))

    def test_plain_text_is_one_literal_part(self) -> None:
        text = SemanticText.parse("plain text", origin=self.origin)

        self.assertEqual(text.parts, (LiteralText("plain text", 0, 10),))
        self.assertEqual(text.dependencies, ())

    def test_parses_entity_and_term_references(self) -> None:
        raw = (
            "The (:term:base.terms.effective_address|short:) is reported by "
            "(:ref:base.instructions.SEGLEA:)."
        )
        text = SemanticText.parse(raw, origin=self.origin)

        term = next(part for part in text.parts if isinstance(part, TermReferenceText))
        entity = next(
            part for part in text.parts if isinstance(part, EntityReferenceText)
        )
        self.assertEqual(
            term.reference,
            Reference("base", ("terms",), "effective_address"),
        )
        self.assertIs(term.form, TermForm.SHORT)
        self.assertEqual(
            entity.reference,
            Reference("base", ("instructions",), "SEGLEA"),
        )
        self.assertEqual(
            text.dependencies,
            (
                Reference("base", ("terms",), "effective_address"),
                Reference("base", ("instructions",), "SEGLEA"),
            ),
        )

    def test_non_reserved_parenthesis_colon_text_remains_literal(self) -> None:
        raw = "f(x): value and (: note)"

        self.assertEqual(
            SemanticText.parse(raw, origin=self.origin).parts,
            (LiteralText(raw, 0, len(raw)),),
        )

    def test_rejects_unknown_unterminated_and_modified_ref_escapes(self) -> None:
        cases = (
            (
                "(:reff:base.instructions.ADD:)",
                SemanticTextErrorReason.UNKNOWN_ESCAPE_KIND,
            ),
            (
                "(:ref:base.instructions.ADD",
                SemanticTextErrorReason.UNTERMINATED_ESCAPE,
            ),
            (
                "(:ref:base.instructions.ADD|name:)",
                SemanticTextErrorReason.MODIFIED_ENTITY_REFERENCE,
            ),
            (
                "(:term:base.terms.address|many:)",
                SemanticTextErrorReason.UNKNOWN_TERM_FORM,
            ),
        )

        for raw, reason in cases:
            with self.subTest(raw=raw), self.assertRaises(SemanticTextError) as caught:
                SemanticText.parse(raw, origin=self.origin)
            self.assertIs(caught.exception.reason, reason)
            self.assertEqual(caught.exception.origin, self.origin)


if __name__ == "__main__":
    unittest.main()
