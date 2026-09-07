
from engine.isa.terminology import load_terminology
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from engine.check import check_terminology
from engine.isa.extensions import load_extension_inventory
from engine.reference import Reference
from engine.syntax.semantic_text import SemanticText
from engine.isa.terminology import TermCatalog


class TermCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.isa_root = Path(__file__).parents[1] / "isa"
        cls.catalog = load_terminology(
            cls.isa_root, extensions=load_extension_inventory(cls.isa_root)
        )

    def test_projects_owner_local_terms_into_shared_reference_index(self) -> None:
        with self.fixture() as directory:
            root = Path(directory)
            self.write_sample_term(root)
            catalog = load_terminology(root, extensions=load_extension_inventory(root))

        group = catalog.groups[Reference.parse("base.term_groups.sample")]
        term = catalog.terms[Reference.parse("base.terms.address")]
        self.assertEqual(set(group.terms), {"address"})
        self.assertIs(group.terms["address"], term)

    def test_projects_term_content_into_semantic_and_spelling_indexes(self) -> None:
        with self.fixture() as directory:
            root = Path(directory)
            self.write_sample_term(root)
            catalog = load_terminology(root, extensions=load_extension_inventory(root))

        term = catalog.terms[Reference.parse("base.terms.address")]
        self.assertIsInstance(term.definition, SemanticText)
        self.assertEqual(term.abbreviation.canonical, "SA")
        self.assertEqual(
            catalog.spellings["sample address"][0].reference, term.reference
        )

    def write_sample_term(self, root: Path) -> None:
        terms_root = root / "terminology/groups/sample/terms"
        self.write_yaml(terms_root / "terms.yaml", {"terms": ["address"]})
        self.write_yaml(
            terms_root / "address/term.yaml",
            {
                "display": {"canonical": "sample address"},
                "abbreviation": {"canonical": "SA"},
                "definition": "A sample address.",
            },
        )

    def test_validator_reports_missing_inventory_member(self) -> None:
        with self.fixture() as directory:
            root = Path(directory)
            terms_root = root / "terminology/groups/sample/terms"
            self.write_yaml(terms_root / "terms.yaml", {"terms": ["known", "missing"]})
            self.write_yaml(
                terms_root / "known/term.yaml",
                {
                    "display": {"canonical": "known term"},
                    "definition": "A known term.",
                },
            )

            catalog = load_terminology(root, extensions=load_extension_inventory(root))
            codes = [item.code for item in check_terminology(catalog)]

        self.assertEqual(codes, ["terminology.term.missing-directory"])

    def test_validator_reports_unknown_definition_reference(self) -> None:
        with self.fixture() as directory:
            root = Path(directory)
            terms_root = root / "terminology/groups/sample/terms"
            self.write_yaml(terms_root / "terms.yaml", {"terms": ["known"]})
            self.write_yaml(
                terms_root / "known/term.yaml",
                {
                    "display": {"canonical": "known term"},
                    "definition": "See (:term:base.terms.unknown:).",
                },
            )

            catalog = load_terminology(root, extensions=load_extension_inventory(root))
            codes = [item.code for item in check_terminology(catalog)]

        self.assertEqual(codes, ["terminology.definition.unknown-term"])

    def test_validator_reports_spelling_conflict(self) -> None:
        with self.fixture() as directory:
            root = Path(directory)
            terms_root = root / "terminology/groups/sample/terms"
            self.write_yaml(terms_root / "terms.yaml", {"terms": ["first", "second"]})
            for term_id in ("first", "second"):
                self.write_yaml(
                    terms_root / term_id / "term.yaml",
                    {
                        "display": {
                            "canonical": (
                                "same-term" if term_id == "first" else "same term"
                            )
                        },
                        "definition": f"The {term_id} term.",
                    },
                )

            catalog = load_terminology(root, extensions=load_extension_inventory(root))
            codes = [item.code for item in check_terminology(catalog)]

        self.assertEqual(codes, ["terminology.spelling.conflict"])

    def test_validator_reports_broader_cycle(self) -> None:
        with self.fixture() as directory:
            root = Path(directory)
            terms_root = root / "terminology/groups/sample/terms"
            self.write_yaml(terms_root / "terms.yaml", {"terms": ["first", "second"]})
            for term_id, broader in (("first", "second"), ("second", "first")):
                self.write_yaml(
                    terms_root / term_id / "term.yaml",
                    {
                        "display": {"canonical": f"{term_id} term"},
                        "definition": f"The {term_id} term.",
                        "relations": {"broader": [f"base.terms.{broader}"]},
                    },
                )

            catalog = load_terminology(root, extensions=load_extension_inventory(root))
            codes = [item.code for item in check_terminology(catalog)]

        self.assertEqual(codes, ["terminology.relation.broader-cycle"])

    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "schemas").mkdir()
        (root / "extensions").mkdir()
        for schema in ("terminology-group.yaml", "term.yaml"):
            shutil.copy2(self.isa_root / "schemas" / schema, root / "schemas" / schema)
        self.write_yaml(root / "extensions/extensions.yaml", {"extensions": []})
        self.write_yaml(root / "terminology/groups/groups.yaml", {"groups": ["sample"]})
        self.write_yaml(
            root / "terminology/groups/sample/group.yaml",
            {"title": "Sample"},
        )
        return temporary

    @staticmethod
    def write_yaml(path: Path, document: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(document, sort_keys=False, width=120), encoding="utf-8"
        )


if __name__ == "__main__":
    unittest.main()
