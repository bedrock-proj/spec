"""Document selection, source provenance, and owned publication contracts."""

import json
import re
import tempfile
import unittest
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from artifacts._shared.documents import build_document
from artifacts._shared.latex import TexValidationCode, TexValidationIssue, TexValidationReport, validate_tex
from engine.artifacts.definition import ArtifactDefinition, GeneratedArtifact, GeneratedArtifactSet
from engine.artifacts.generate import artifact_context
from engine.artifacts.registry import ArtifactGeneratorRegistry
from engine.artifacts.write import write_artifacts
from engine.documents.composition import load_composition
from engine.documents.dependencies import DependencyEdge, DependencyGraph
from engine.documents.fragments.events import project_row_event_reference
from engine.documents.fragments.memory_records import select_memory_record
from engine.documents.fragments.registers import select_register_figure
from engine.documents.projection import project_document
from engine.documents.sources import SourceInputProjection, SourceReferenceProjection, StyleSourceProjection, project_source
from engine.documents.targets import PublicTargetCatalog
from engine.entity import create_entity_catalog
from engine.isa.events import ArchitecturalEvent, EventCatalog, EventClassDefinition, EventNamespace, EventSelector
from engine.isa.memory_records import ElementByteSize, MemoryRecord, MemoryRecordCatalog, MemoryRecordComponent, MemoryRecordNamespace
from engine.isa.model import DocumentTopic
from engine.isa.registers import ExplicitRegisterGroup, RegisterCatalog, RegisterNamespace
from engine.isa.terminology import Term, TermAbbreviation, TermCatalog, TermForms, TermGroup, TermRelations, TerminologyNamespace
from engine.reference import Reference, ReferenceIndex, UnknownReferenceError
from engine.source.inventory import DirectoryInventory
from engine.syntax.semantic_text import SemanticText, TermForm, TextOrigin
from engine.workspace import SpecWorkspace


def _topic(root, name, owner="base"):
    return DocumentTopic(owner, name, Reference(owner, ("topics",), name), root / "model.yaml", root / f"{name}.tex")


def _inventory(root, kind, names=(), owner="base"):
    return DirectoryInventory(owner, kind, root / f"{kind}.yaml", root, tuple(names), tuple(names))


def _term(root):
    return Term(
        Reference("base", ("terms",), "address"), Reference("base", ("term_groups",), "memory"),
        root / "term.yaml", root, "base", "address", TermForms("effective address"),
        TermAbbreviation("EA"), None, SemanticText.parse("Address.", origin=TextOrigin(root / "term.yaml")),
        {}, TermRelations(),
    )


def _semantic_catalogs(*entities):
    terms = ReferenceIndex({item.reference: item for item in entities if isinstance(item, Term)})
    groups = {}
    namespaces = {}
    for term in terms.values():
        if term.group not in groups:
            members = {item.id: item for item in terms.values() if item.group == term.group}
            groups[term.group] = TermGroup(term.group, term.source, term.root, term.owner, term.group.element, "Terms", _inventory(term.root, "terms", members, term.owner), members)
    for group in groups.values():
        if group.owner not in namespaces:
            members = {item.id: item for item in groups.values() if item.owner == group.owner}
            namespaces[group.owner] = TerminologyNamespace(group.owner, group.root, _inventory(group.root, "groups", members, group.owner), members)
    return {"isa": SimpleNamespace(
        entities=create_entity_catalog((item, item.reference.element) for item in entities),
        terminology=TermCatalog(namespaces, ReferenceIndex(groups), terms),
    )}


def _memo(root):
    return artifact_context(ArtifactGeneratorRegistry({}, {}), SpecWorkspace(root, {}), root / "output").shared_result


def _document_fixture(root):
    definition = ArtifactDefinition("manual", root / "artifact.yaml", {
        "outputs": {"document": "sources/manual.tex"},
        "derived-outputs": {
            "tex-validation": "reports/source.json", "compiled-document": "compiled/manual.pdf",
            "compile-log": "logs/compiler.log", "pdf-validation": "reports/compiled.json",
        },
    })
    text = r"\begin{document}A document.\end{document}"

    def generate(definition, context):
        return GeneratedArtifactSet((GeneratedArtifact(definition.outputs["document"], text),), definition.id)

    def validate(definition, context):
        definition.validate_generated(generate(definition, context))
        if not validate_tex(text).passed:
            raise ValueError("invalid synthetic document")

    registry = ArtifactGeneratorRegistry({definition.id: definition}, {definition.id: {"generate": generate, "validate": validate}})
    context = artifact_context(registry, SpecWorkspace(root, {}), root / "output")
    return definition, context


class DocumentSourceTest(unittest.TestCase):
    def test_input_occurrence_preserves_original_child_and_parent_span(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, child = root / "root.tex", root / "child.tex"
            text = r"Before \input{child.tex} after."
            source.write_text(text)
            child.write_text("Child source.")
            projection = project_source(source, None, root, {}, shared_result=_memo(root))
            occurrence, = projection.parts
            self.assertIsInstance(occurrence, SourceInputProjection)
            self.assertEqual(text[occurrence.span.start:occurrence.span.end], r"\input{child.tex}")
            self.assertEqual(occurrence.span.source, source)
            self.assertEqual(occurrence.source.source, child)
            self.assertEqual(occurrence.source.raw, "Child source.")

    def test_source_snapshot_is_shared_across_roots_only_within_one_invocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            first, second, child = (root / name for name in ("first.tex", "second.tex", "child.tex"))
            first.write_text(r"\input{child.tex}")
            second.write_text(r"\input{child.tex}")
            child.write_text("First snapshot.")
            memo = _memo(root)
            initial = project_source(first, None, root, {}, shared_result=memo)
            child.write_text("Next snapshot.")
            same_invocation = project_source(second, None, root, {}, shared_result=memo)
            next_invocation = project_source(second, None, root, {}, shared_result=_memo(root))
            self.assertIs(initial.parts[0].source, same_invocation.parts[0].source)
            self.assertEqual(same_invocation.parts[0].source.raw, "First snapshot.")
            self.assertEqual(next_invocation.parts[0].source.raw, "Next snapshot.")

    def test_term_modifier_preserves_canonical_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            term = _term(root)
            source = root / "root.tex"
            source.write_text("Use (:term:base.terms.address|short:).")
            projection = project_source(source, None, root, _semantic_catalogs(term), shared_result=_memo(root))
            occurrence, = projection.parts
            self.assertIs(occurrence.reference.entity, term)
            self.assertIs(occurrence.reference.occurrence.form, TermForm.SHORT)
            self.assertEqual(occurrence.reference.presentation.display, "EA")

    def test_active_input_path_cycle_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, child = root / "a.tex", root / "b.tex"
            source.write_text(r"\input{b.tex}")
            child.write_text(r"\input{a.tex}")
            with self.assertRaises(ValueError) as caught:
                project_source(source, None, root, {}, shared_result=_memo(root))
            self.assertIn(str(source), str(caught.exception))
            self.assertIn(str(child), str(caught.exception))
            self.assertIn("offset 0", str(caught.exception))
            self.assertIsInstance(caught.exception.__cause__, ValueError)

    def test_missing_input_reports_the_parent_occurrence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "root.tex"
            source.write_text(r"Before \input{missing.tex}")
            with self.assertRaises(ValueError) as caught:
                project_source(source, None, root, {}, shared_result=_memo(root))
            self.assertIn(f"{source}, offset 7", str(caught.exception))
            self.assertIsInstance(caught.exception.__cause__, FileNotFoundError)

    def test_malformed_directive_start_is_rejected_at_its_occurrence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "root.tex"
            source.write_text("Before (:ref) after.")
            with self.assertRaisesRegex(
                ValueError,
                rf"{re.escape(str(source))}, offset 7: malformed document directive",
            ):
                project_source(source, None, root, {}, shared_result=_memo(root))

    def test_style_source_keeps_semantic_like_text_literal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "style.sty"
            text = r"\newcommand{\example}{(:term:base.terms.unknown:)}"
            source.write_text(text)
            projection = project_source(source, None, root, {}, shared_result=_memo(root))
            self.assertIsInstance(projection, StyleSourceProjection)
            self.assertEqual(projection.raw, text)
            self.assertFalse(any(isinstance(part, SourceReferenceProjection) for part in projection.parts))

    def test_authored_literal_characters_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "root.tex"
            text = "Literal text, % a comment\n" + r"\texttt{value} and \% percent."
            source.write_text(text)
            projection = project_source(source, None, root, {}, shared_result=_memo(root))
            self.assertEqual(projection.raw, text)

    def test_ignored_tex_regions_do_not_create_source_occurrences(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "root.tex"
            text = "% (:ref:base.topics.missing:)\n" + r"\(:term:base.terms.missing:) \\input{missing.tex}"
            source.write_text(text)
            projection = project_source(source, None, root, {}, shared_result=_memo(root))
            self.assertEqual(projection.parts, ())

    def test_unknown_term_is_rejected_during_source_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "root.tex"
            source.write_text("(:term:base.terms.unknown:)")
            with self.assertRaises(ValueError) as caught:
                project_source(source, None, root, _semantic_catalogs(), shared_result=_memo(root))
            self.assertIn(f"{source}, offset 0", str(caught.exception))
            self.assertIsInstance(caught.exception.__cause__, UnknownReferenceError)

    def test_reference_preserves_canonical_entity_and_original_span(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entity = _topic(root, "selected")
            source = root / "root.tex"
            text = "See (:ref:base.topics.selected:)."
            source.write_text(text)
            catalogs = _semantic_catalogs(entity)
            projection = project_source(source, None, root, catalogs, shared_result=_memo(root))
            occurrence, = projection.parts
            self.assertIs(occurrence.reference.entity, entity)
            self.assertEqual(text[occurrence.span.start:occurrence.span.end], "(:ref:base.topics.selected:)")
            targets = PublicTargetCatalog.create(catalogs["isa"].entities, ((entity.reference,),), (entity.reference,))
            self.assertIs(targets.resolve(entity.reference), entity)

    def test_unknown_entity_is_rejected_during_source_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "root.tex"
            source.write_text("(:ref:base.topics.unknown:)")
            with self.assertRaises(ValueError) as caught:
                project_source(source, None, root, _semantic_catalogs(), shared_result=_memo(root))
            self.assertIn(f"{source}, offset 0", str(caught.exception))
            self.assertIsInstance(caught.exception.__cause__, UnknownReferenceError)


class DocumentSelectionTest(unittest.TestCase):
    def test_explicit_topic_order_preserves_canonical_members(self):
        root = Path("/synthetic")
        first, second = _topic(root, "first"), _topic(root, "second")
        isa = SimpleNamespace(model=SimpleNamespace(document_topics=ReferenceIndex({value.reference: value for value in (first, second)})), entities=create_entity_catalog(((first, "First"), (second, "Second"))))
        composition = load_composition([{"topic": "base.topics.second"}, {"topic": "base.topics.first"}], isa=isa, sources={})
        self.assertIs(composition.blocks[0], second)
        self.assertIs(composition.blocks[1], first)
        with tempfile.TemporaryDirectory() as directory:
            source_root = Path(directory).resolve()
            path = source_root / "empty.tex"
            path.write_text("Authored text.")
            sources = {value.reference: project_source(path, value, source_root, {}, shared_result=_memo(source_root)) for value in (first, second)}
            projection = project_document(composition, isa, sources)
            self.assertIs(projection.blocks[0].topic, second)
            self.assertIs(projection.blocks[1].topic, first)

    def test_internal_catalog_growth_does_not_change_explicit_selection(self):
        root = Path("/synthetic")
        selected, private = _topic(root, "selected"), _topic(root, "private")
        results = []
        for members in ((selected,), (private, selected)):
            isa = SimpleNamespace(model=SimpleNamespace(document_topics=ReferenceIndex({item.reference: item for item in members})))
            results.append(load_composition([{"topic": "base.topics.selected"}], isa=isa, sources={}))
        for result in results:
            self.assertEqual(result.blocks, (selected,))
            self.assertIs(result.blocks[0], selected)

    def test_unselected_source_lookup_member_does_not_add_public_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            selected, private = _topic(root, "selected"), _topic(root, "private")
            selected.document.write_text("Selected prose.")
            private.document.write_text("(:ref:base.topics.private:)")
            catalogs = _semantic_catalogs(selected, private)
            isa = catalogs["isa"]
            isa.model = SimpleNamespace(document_topics=ReferenceIndex({item.reference: item for item in (selected, private)}))
            memo = _memo(root)
            sources = {item.reference: project_source(item.document, item, root, catalogs, shared_result=memo) for item in (selected, private)}
            composition = load_composition([{"topic": "base.topics.selected"}], isa=isa, sources={})
            projection = project_document(composition, isa, sources)
            self.assertEqual(projection.dependencies.edges, ())
            self.assertFalse(projection.public_targets.contains(private.reference))

    def test_duplicate_explicit_topic_placement_is_rejected(self):
        value = _topic(Path("/synthetic"), "selected")
        isa = SimpleNamespace(model=SimpleNamespace(document_topics=ReferenceIndex({value.reference: value})))
        with self.assertRaises(ValueError):
            load_composition([{"topic": "base.topics.selected"}] * 2, isa=isa, sources={})

    def test_known_unselected_reference_does_not_acquire_public_target(self):
        selected, private = (_topic(Path("/synthetic"), name) for name in ("selected", "private"))
        entities = create_entity_catalog(((selected, "Selected"), (private, "Private")))
        with self.assertRaises(ValueError):
            PublicTargetCatalog.create(entities, ((selected.reference,),), (private.reference,))

    def test_dependency_serialization_preserves_endpoint_closure_and_occurrences(self):
        root = Path("/synthetic")
        first, second = _topic(root, "first"), _topic(root, "second")
        entities = create_entity_catalog(((first, "First"), (second, "Second")))
        graph = DependencyGraph(tuple(DependencyEdge(first.reference, second.reference, "reference", first.document, offset) for offset in (3, 17)))
        render = import_module("artifacts.isa-reference.dependencies").render_dependency_graph
        serialized = json.loads(render(graph, {item.reference: (item, entities.presentation(item.reference)) for item in (first, second)}, root))
        nodes = {node["display"]: node for node in serialized["nodes"]}
        edge, = serialized["edges"]
        self.assertEqual((edge["source"], edge["target"]), (nodes["First"]["id"], nodes["Second"]["id"]))
        self.assertEqual(edge["occurrences"], 2)
        self.assertEqual([location["offset"] for location in edge["locations"]], [3, 17])

    def test_event_row_preserves_declared_class_and_selector(self):
        root = Path("/synthetic")
        event = ArchitecturalEvent(Reference("base", ("events", "fault"), "sample"), root / "event.yaml", root, "sample", "Sample", "Sample event", 0x123456, None, "basic", ())
        event_class = EventClassDefinition(Reference("base", ("events",), "fault"), root / "class.yaml", root, "fault", _inventory(root, "events", ("sample",)), {"sample": event}, "Fault", 0xAB, EventSelector("fixed", 24))
        namespace = EventNamespace("base", root, _inventory(root, "classes", ("fault",)), {"fault": event_class})
        catalog = EventCatalog({"base": namespace}, ReferenceIndex({event_class.reference: event_class}), ReferenceIndex({event.reference: event}))
        row = project_row_event_reference(catalog, event.reference)
        self.assertEqual(row.reference, event.reference)
        self.assertEqual(row.code, 0xAB123456)

    def test_register_selection_preserves_explicit_group_order(self):
        root = Path("/synthetic")
        groups = tuple(ExplicitRegisterGroup(Reference("base", ("registers",), name), root / name / "group.yaml", root / name, "base", name, 64, None, None, None, {}, _inventory(root / name, "registers")) for name in ("LEFT", "RIGHT"))
        namespace = RegisterNamespace("base", root, _inventory(root, "groups", ("LEFT", "RIGHT")), {item.id: item for item in groups})
        catalog = RegisterCatalog({"base": namespace}, ReferenceIndex({item.reference: item for item in groups}), ReferenceIndex({}), ReferenceIndex({}))
        selected = select_register_figure("base", ("RIGHT", "LEFT"), owner=_topic(root, "registers"), catalog=catalog)
        self.assertIs(selected.groups[0], groups[1])
        self.assertIs(selected.groups[1], groups[0])

    def test_register_selection_rejects_foreign_owner(self):
        with self.assertRaises(ValueError):
            select_register_figure("OTHER", ("REGS",), owner=_topic(Path("/synthetic"), "registers"), catalog=RegisterCatalog({}, ReferenceIndex({}), ReferenceIndex({}), ReferenceIndex({})))

    def test_record_selection_preserves_canonical_record(self):
        root = Path("/synthetic")
        record = MemoryRecord(Reference("base", ("records",), "STATE"), root / "record.yaml", root, "base", "STATE", "State", 8, None, (MemoryRecordComponent("word", "Word", 1, ElementByteSize(fixed=8), None, None),))
        namespace = MemoryRecordNamespace("base", root, _inventory(root, "records", (record.id,)), {record.id: record})
        catalog = MemoryRecordCatalog({"base": namespace}, ReferenceIndex({record.reference: record}))
        projection = select_memory_record(record.reference, owner=_topic(root, "records"), catalog=catalog)
        self.assertIs(projection.record, record)

    def test_record_selection_rejects_foreign_owner(self):
        root = Path("/synthetic")
        record = MemoryRecord(Reference("OTHER", ("records",), "STATE"), root / "record.yaml", root, "OTHER", "STATE", "State", 8, None, ())
        namespace = MemoryRecordNamespace("OTHER", root, _inventory(root, "records", (record.id,), "OTHER"), {record.id: record})
        catalog = MemoryRecordCatalog({"OTHER": namespace}, ReferenceIndex({record.reference: record}))
        with self.assertRaises(ValueError):
            select_memory_record(record.reference, owner=_topic(root, "records"), catalog=catalog)


class DocumentPublicationTest(unittest.TestCase):
    def test_document_environment_multiplicity_is_rejected(self):
        report = validate_tex(r"\begin{document}\end{document}\begin{document}")
        self.assertFalse(report.passed)
        self.assertEqual(report.issues, (TexValidationIssue(TexValidationCode.DOCUMENT_ENVIRONMENT_COUNT, counts=(("begin", 2), ("end", 1))),))

    def test_missing_hyperref_target_is_rejected(self):
        report = validate_tex(r"\begin{document}\hyperref[entity:missing]{link}\end{document}")
        self.assertFalse(report.passed)
        self.assertEqual(report.issues, (TexValidationIssue(TexValidationCode.UNRESOLVED_PUBLIC_TARGETS, values=("entity:missing",)),))

    def test_generation_does_not_publish_files(self):
        with tempfile.TemporaryDirectory() as directory:
            definition, context = _document_fixture(Path(directory).resolve())
            result = context.generate(definition.id)
            self.assertEqual(tuple(item.relative_path for item in result.artifacts), (definition.outputs["document"],))
            self.assertFalse(context.output_root.exists())

    def test_disabled_compilation_publishes_declared_source_and_report(self):
        with tempfile.TemporaryDirectory() as directory:
            definition, context = _document_fixture(Path(directory).resolve())
            with patch("artifacts._shared.documents.compile_latex") as compiler:
                result = build_document(definition, context, compile_pdf=False)
            compiler.assert_not_called()
            self.assertTrue(result.report.passed)
            self.assertIsNone(result.pdf)
            self.assertEqual(result.tex, context.output_root / definition.outputs["document"])
            self._assert_source_ownership(definition, context.output_root)

    def test_primary_and_derived_output_ownership_collision_is_rejected(self):
        first = ArtifactDefinition("first", Path("first/artifact.yaml"), {"outputs": {"source": "source.tex"}, "derived-outputs": {"document": "compiled/manual.pdf"}})
        second = ArtifactDefinition("second", Path("second/artifact.yaml"), {"outputs": {"document": "compiled/manual.pdf"}})
        with self.assertRaisesRegex(ValueError, "overlaps"):
            ArtifactGeneratorRegistry({"first": first, "second": second}, {})

    def test_compile_failure_removes_prior_owned_compiled_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            definition, context = _document_fixture(Path(directory).resolve())
            self._seed_compiled_outputs(definition, context.output_root)
            failure = RuntimeError("synthetic compiler failure")
            with patch("artifacts._shared.documents.compile_latex", side_effect=failure):
                with self.assertRaises(RuntimeError) as caught:
                    build_document(definition, context, compile_pdf=True)
            self.assertIs(caught.exception, failure)
            self._assert_source_ownership(definition, context.output_root)
            self._assert_compiled_outputs_absent(definition, context.output_root)

    def test_validation_failure_removes_prior_compiled_outputs_without_compiling(self):
        with tempfile.TemporaryDirectory() as directory:
            definition, context = _document_fixture(Path(directory).resolve())
            self._seed_compiled_outputs(definition, context.output_root)
            report = TexValidationReport(False, (TexValidationIssue(TexValidationCode.UNRESOLVED_PLACEHOLDERS),), {}, {})
            with patch("artifacts._shared.documents.validate_tex", return_value=report), patch("artifacts._shared.documents.compile_latex") as compiler:
                result = build_document(definition, context, compile_pdf=True)
            compiler.assert_not_called()
            self.assertIs(result.report, report)
            self.assertIsNone(result.pdf)
            self._assert_source_ownership(definition, context.output_root)
            self._assert_compiled_outputs_absent(definition, context.output_root)

    def _seed_compiled_outputs(self, definition, output):
        write_artifacts(GeneratedArtifactSet(tuple(GeneratedArtifact(definition.derived_outputs[role], b"prior owned result") for role in ("compiled-document", "compile-log", "pdf-validation")), definition.id), output)

    def _assert_source_ownership(self, definition, output):
        manifest = json.loads((output / ".artifact-ownership" / f"{definition.id}.json").read_text())
        expected = {definition.outputs["document"].as_posix(), definition.derived_outputs["tex-validation"].as_posix()}
        self.assertEqual(set(manifest["paths"]), expected)
        for relative in expected:
            self.assertTrue((output / relative).is_file())

    def _assert_compiled_outputs_absent(self, definition, output):
        for role in ("compiled-document", "compile-log", "pdf-validation"):
            self.assertFalse((output / definition.derived_outputs[role]).exists())


if __name__ == "__main__":
    unittest.main()
