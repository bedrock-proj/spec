import json
import unittest
from pathlib import Path

from engine.diagnostics import (
    render_diagnostics_json,
    Diagnostic,
    DiagnosticBag,
    RelatedLocation,
    Severity,
)


class DiagnosticBagTest(unittest.TestCase):
    def setUp(self) -> None:
        self.diagnostic = Diagnostic(
            Severity.ERROR,
            "encoding.overlap",
            Path("left.yaml"),
            "forms overlap",
            ("encodings", "left", "pattern"),
            (RelatedLocation(Path("right.yaml"), "conflicting form"),),
        )
        self.bag = DiagnosticBag([self.diagnostic])

    def test_reports_error_state_from_structured_severity(self) -> None:
        self.assertTrue(self.bag.has_errors)
        self.assertFalse(DiagnosticBag().has_errors)

    def test_json_preserves_structured_location_and_relation(self) -> None:
        rendered = json.loads(render_diagnostics_json(self.bag))
        self.assertEqual(rendered[0]["severity"], Severity.ERROR.value)
        self.assertEqual(rendered[0]["code"], self.diagnostic.code)
        self.assertEqual(rendered[0]["path"], ["encodings", "left", "pattern"])
        self.assertEqual(rendered[0]["related"][0]["source"], "right.yaml")

if __name__ == "__main__":
    unittest.main()
