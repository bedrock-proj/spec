"""Closed vector example grammar and explicit instruction-owned placement."""

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import yaml
from jsonschema import Draft202012Validator

from engine.artifacts.generate import artifact_context
from engine.artifacts.registry import ArtifactGeneratorRegistry
from engine.documents.composition import DocumentComposition
from engine.documents.fragment_expansion import SourceFragmentProjection
from engine.documents.projection import project_document
from engine.documents.sources import project_source
from engine.entity import create_entity_catalog
from engine.isa.catalog import InstructionBundle, InstructionSet, SourceCatalog
from engine.isa.encoding import EncodingCatalog
from engine.isa.instructions import Instruction
from engine.isa.vector_examples import VectorDiagram, VectorDiagramCatalog, VectorLaneTransferExample
from engine.reference import Reference, ReferenceIndex
from engine.source.inventory import DirectoryInventory
from engine.workspace import SpecWorkspace


def _bundle(root, name, diagrams=("selected",)):
    member_root = root / name
    member_root.mkdir()
    instruction = Instruction(member_root / "instruction.yaml", name, "Synthetic instruction", "alu", False, {})
    reference = Reference("base", ("instructions",), name)
    diagram_root = member_root / "diagrams"
    members = tuple(VectorDiagram(Reference("base", ("instructions", name, "diagrams"), key), diagram_root / key / "diagram.yaml", key, "Finite example", "Finite example", VectorLaneTransferExample(False, (), ())) for key in diagrams)
    inventory = DirectoryInventory("base", "vector-diagram", diagram_root / "diagrams.yaml", diagram_root, tuple(diagrams), tuple(diagrams))
    catalog = VectorDiagramCatalog("base", reference, diagram_root, inventory, ReferenceIndex({item.reference: item for item in members}))
    return InstructionBundle(reference, "base", instruction, EncodingCatalog(member_root / "encodings.yaml", ()), catalog, member_root / "semantics.sail", member_root / "descriptions.tex", ()), members


def _source_catalog(root, bundles):
    inventory = DirectoryInventory("base", "instruction", root / "instructions.yaml", root, tuple(item.instruction.mnemonic for item in bundles), tuple(item.instruction.mnemonic for item in bundles))
    return SourceCatalog(ReferenceIndex({item.reference: item for item in bundles}), ReferenceIndex({}), tuple(item.reference for item in bundles), InstructionSet(inventory, tuple(bundles)), {}, DirectoryInventory("isa", "extension", root / "extensions.yaml", root, (), ()))


def _directive(diagram):
    reference = diagram.reference
    return "(:diagram:" + ".".join((reference.owner, *reference.path, reference.element)) + ":)"


def _memo(root):
    return artifact_context(ArtifactGeneratorRegistry({}, {}), SpecWorkspace(root, {}), root / "output").shared_result


def _project(bundle, root):
    isa = SimpleNamespace(entities=create_entity_catalog(()))
    source = project_source(bundle.description, bundle, root, {"isa": isa}, shared_result=_memo(root))
    return project_document(DocumentComposition((source,)), isa, {})


class VectorDiagramGrammarTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema = yaml.safe_load((Path(__file__).parents[1] / "isa/schemas/vector-diagram.yaml").read_text())
        cls.validator = Draft202012Validator(schema)

    def _alternatives(self):
        common = {
            "caption": "Example", "alt_text": "Example predicate range", "kind": "predicate-range-generation",
            "view": {"visible_bytes": 16, "lane_order": "right-to-left", "scalable": True},
            "result": {"label": "result", "element_bits": 16, "groups": [{"cells": [{"value": "0", "effect": "zero", "appearance": "zero", "bits": 16}]}]},
        }
        counted = {**deepcopy(common), "count": {"label": "count", "value": 0}, "range": {"start": "count", "end": "lane-count"}}
        stateful = {**deepcopy(common), "states": [{"id": "remaining", "label": "remaining", "before": "before", "after": "after", "anchor": "start", "after_side": "right"}], "range": {"start": 0, "end": 1}}
        return counted, stateful

    def test_closed_counted_and_stateful_alternatives_are_accepted(self):
        for value in self._alternatives():
            with self.subTest(value=value):
                self.assertTrue(self.validator.is_valid(value))

    def test_count_and_state_hybrids_are_rejected(self):
        counted, stateful = self._alternatives()
        for invalid in ({**counted, "states": stateful["states"]}, {**stateful, "count": counted["count"]}):
            with self.subTest(value=invalid):
                self.assertFalse(self.validator.is_valid(invalid))

    def test_flattened_result_violates_grouped_result_grammar(self):
        counted, _ = self._alternatives()
        counted["result"] = {"label": "new Pn", "element_bits": 16, "cells": [{"value": "0", "effect": "clear"}] * 8}
        self.assertFalse(self.validator.is_valid(counted))


class VectorDiagramSelectionTest(unittest.TestCase):
    def test_inline_block_directive_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            bundle, (diagram,) = _bundle(root, "ONE")
            bundle.description.write_text("Prose " + _directive(diagram))
            with self.assertRaises(ValueError):
                _project(bundle, root)

    def test_duplicate_explicit_diagram_placement_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            bundle, (diagram,) = _bundle(root, "ONE")
            bundle.description.write_text(_directive(diagram) + "\n" + _directive(diagram))
            with self.assertRaises(ValueError):
                _project(bundle, root)

    def test_duplicate_diagram_reached_through_two_inputs_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            bundle, (diagram,) = _bundle(root, "ONE")
            child = root / "child.tex"
            child.write_text(_directive(diagram))
            bundle.description.write_text("\\input{child.tex}\n\\input{child.tex}")
            with self.assertRaisesRegex(ValueError, "duplicate.*diagram"):
                _project(bundle, root)

    def test_unknown_diagram_selection_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            bundle, _ = _bundle(root, "ONE")
            bundle.description.write_text("(:diagram:base.instructions.ONE.diagrams.unknown:)")
            with self.assertRaises(ValueError):
                _project(bundle, root)

    def test_another_instruction_in_same_namespace_cannot_supply_diagram(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            bundle, _ = _bundle(root, "ONE")
            _, (foreign,) = _bundle(root, "TWO")
            bundle.description.write_text(_directive(foreign))
            with self.assertRaises(ValueError):
                _project(bundle, root)

    def test_private_diagram_growth_does_not_change_selected_occurrences(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            bundle, (selected, private) = _bundle(root, "ONE", ("selected", "private"))
            bundle.description.write_text(_directive(selected))
            inventory = DirectoryInventory("base", "vector-diagram", bundle.diagrams.root / "diagrams.yaml", bundle.diagrams.root, (selected.id,), (selected.id,))
            variants = (VectorDiagramCatalog("base", bundle.reference, bundle.diagrams.root, inventory, ReferenceIndex({selected.reference: selected})), bundle.diagrams)
            projections = []
            for diagrams in variants:
                owner = InstructionBundle(bundle.reference, bundle.owner, bundle.instruction, bundle.encodings, diagrams, bundle.semantics, bundle.description, ())
                projections.append(_project(owner, root))
            for projection in projections:
                choices = tuple(part.selection for part in projection.blocks[0].parts if isinstance(part, SourceFragmentProjection))
                self.assertEqual(choices, (selected,))
                self.assertIs(choices[0], selected)
                self.assertFalse(projection.public_targets.contains(private.reference))

    def test_full_reference_lookup_returns_canonical_member(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            bundle, (diagram,) = _bundle(root, "ONE")
            catalog = _source_catalog(root, (bundle,))
            self.assertIs(catalog.vector_diagram(diagram.reference), diagram)
            self.assertIs(catalog.vector_diagram("base.instructions.ONE.diagrams.selected"), diagram)
            self.assertIs(catalog.bundle(bundle.reference).diagrams.diagrams.resolve(diagram.reference), diagram)

    def test_missing_instruction_scope_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            bundle, _ = _bundle(root, "ONE")
            with self.assertRaises(ValueError):
                _source_catalog(root, (bundle,)).vector_diagram("base.diagrams.selected")

    def test_unknown_instruction_scope_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            bundle, _ = _bundle(root, "ONE")
            with self.assertRaises(ValueError):
                _source_catalog(root, (bundle,)).vector_diagram("base.instructions.UNKNOWN.diagrams.selected")


if __name__ == "__main__":
    unittest.main()
