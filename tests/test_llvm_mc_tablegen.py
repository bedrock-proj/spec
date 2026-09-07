from __future__ import annotations

from engine.artifacts.definition import load_artifact_definition

from engine.artifacts.registry import ArtifactGeneratorRegistry, load_artifact_registry
from engine.artifacts.generate import artifact_context

from importlib import import_module
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from engine.isa.configuration import IsaConfiguration
from engine.isa.decoding import project_decode
from engine.isa.control_registers import control_register_selector_members
from engine.isa.encoding import (
    EncodingForm,
    ExcludedOperandConstraint,
    ResolvedEaRead,
)
from engine.isa.ea import CompactExtensionEAMode
from engine.isa.encoding_architecture import ENCODING_CLASSES_BY_WIDTH
from engine.artifacts.definition import ArtifactDefinition
from engine.isa.catalog import InstructionBundle
from engine.isa.project import IsaProject
from engine.isa.types import (
    ControlRegisterSelectorPayloadType,
    EnumConditionFieldType,
    EffectiveAddressFieldType,
    RegisterSelectorPayloadType,
)
from engine.workspace import load_workspace
from engine.source.yaml import load_yaml


ROOT = Path(__file__).resolve().parents[1]


class LlvmMcTableGenArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workspace = load_workspace(ROOT)
        project = cls.workspace.require_provider("isa")
        if not isinstance(project, IsaProject):
            raise TypeError("workspace isa provider must be an IsaProject")
        cls.project = project
        definition = load_artifact_definition(
            ROOT / "artifacts/llvm-mc-tablegen/artifact.yaml"
        )
        module = import_module("artifacts.llvm-mc-tablegen.generator")
        context = artifact_context(
            load_artifact_registry(cls.workspace), cls.workspace, ROOT
        )
        generated = module.generate(definition, context)
        configuration = IsaConfiguration.resolve(project.catalog)
        decoded = project_decode(
            tuple(project.catalog.instructions.resolve(reference)
                  for reference in project.catalog.instruction_order),
            configuration=configuration, field_types=project.types.field_types,
            payload_types=project.types.payload_types,
            ea_modes=project.catalog.ea_modes, registers=project.registers,
            cpuid=project.cpuid,
        )
        cls.decoded = decoded
        controls = tuple(
            entry for namespace in project.control_registers.namespaces.values()
            if namespace.owner in configuration.owners
            for entry in control_register_selector_members(namespace)
        )
        cls.projection = module.project_tablegen(
            decoded, control_register_selectors=controls,
        )
        cls.catalog = generated.artifact(definition.outputs["catalog"]).content

    @staticmethod
    def _form_identifier(bundle: InstructionBundle, form: EncodingForm) -> str:
        return f"{bundle.owner}.{bundle.instruction.mnemonic}.{form.id}"

    def test_every_canonical_form_has_one_searchable_record(self) -> None:
        expected = {
            self._form_identifier(bundle, form)
            for bundle in self.project.catalog.instructions.values()
            for form in bundle.encodings.forms
        }
        self.assertEqual({form.identifier for form in self.projection.forms}, expected)

    def test_variable_length_forms_are_not_native_codec_candidates(self) -> None:
        expected: dict[str, bool] = {}
        for bundle in self.project.catalog.instructions.values():
            for form in bundle.encodings.forms:
                field_types = tuple(
                    self.project.types.field_types.resolve(field.type)
                    for field in form.fields
                )
                has_variable_length = any(
                    isinstance(field_type, EffectiveAddressFieldType)
                    for field_type in field_types
                )
                if not has_variable_length:
                    continue
                primary_bytes = ENCODING_CLASSES_BY_WIDTH[
                    form.pattern.bit_width
                ].opcode_space_bytes
                fixed_payload_bytes = sum(
                    self.project.types.payload_types.resolve(payload.type).bytes
                    for payload in form.payloads
                )
                expected[self._form_identifier(bundle, form)] = (
                    not has_variable_length and primary_bytes + fixed_payload_bytes <= 8
                )
        projected = {
            form.identifier: form.tablegen_codec_candidate
            for form in self.projection.forms
            if form.has_variable_length
        }
        self.assertTrue(expected)
        self.assertEqual(projected, expected)

    def test_vector_table_covers_forms_and_projects_alias_policy(self) -> None:
        expected = {
            self._form_identifier(bundle, form): bundle.instruction.width_suffix_aliases
            for bundle in self.project.catalog.instructions.values()
            if bundle.instruction.route == "vector"
            for form in bundle.encodings.forms
        }
        actual = {
            form.identifier: form.has_width_only_aliases
            for form in self.projection.vector_forms
        }
        self.assertEqual(actual, expected)

    def test_scalar_records_reference_canonical_non_vector_forms(self) -> None:
        canonical = {
            self._form_identifier(bundle, form): (bundle, form)
            for bundle in self.project.catalog.instructions.values()
            for form in bundle.encodings.forms
        }
        self.assertTrue(self.projection.scalar_forms)
        for record in self.projection.scalar_forms:
            with self.subTest(identifier=record.identifier):
                bundle, form = canonical[record.identifier]
                self.assertNotEqual(bundle.instruction.route, "vector")
                self.assertEqual(record.pattern, form.pattern.code)
                self.assertEqual(
                    record.fixed_payload_bytes,
                    sum(
                        self.project.types.payload_types.resolve(payload.type).bytes
                        for payload in form.payloads
                    ),
                )

    def test_codec_records_project_canonical_tail_operand_order(self) -> None:
        owners = {
            id(bundle.instruction): bundle.owner for bundle in self.decoded.bundles
        }
        canonical = {
            f"{owners[id(resolved.instruction)]}.{resolved.instruction.mnemonic}.{resolved.form.id}": resolved
            for resolved in self.decoded.forms
        }
        mixed_layouts = 0

        for records, vector in (
            (self.projection.scalar_forms, False),
            (self.projection.vector_forms, True),
        ):
            for record in records:
                with self.subTest(identifier=record.identifier):
                    resolved = canonical[record.identifier]
                    if vector:
                        prefix_roles = tuple(
                            field.field.role
                            for field in resolved.fields
                            if isinstance(field.definition, EnumConditionFieldType)
                        )
                    elif resolved.form.syntax.order_field is not None:
                        order_field = resolved.form.field_for_marker(
                            resolved.form.syntax.order_field
                        )
                        self.assertIsNotNone(order_field)
                        assert order_field is not None
                        prefix_roles = (order_field.role,)
                    else:
                        prefix_roles = ()
                    roles = (
                        *prefix_roles,
                        *(operand.name for operand in resolved.display_order),
                    )
                    expected = tuple(
                        roles.index(operation.operand.name)
                        for operation in resolved.layout
                    )
                    self.assertEqual(record.tail_operand_order, expected)
                    self.assertEqual(
                        record.ea_operand_count,
                        sum(
                            isinstance(operation, ResolvedEaRead)
                            for operation in resolved.layout
                        ),
                    )
                    mixed_layouts += (
                        0 < record.ea_operand_count < len(record.tail_operand_order)
                    )

        self.assertGreater(mixed_layouts, 0)

    def test_ea_layouts_follow_profile_selectors_and_declared_extensions(self) -> None:
        families = {
            (family.owner, family.profile, family.name): family
            for family in self.decoded.effective_addresses.descriptor_families
        }
        expected = {}
        for profile in self.decoded.effective_addresses.profiles:
            for entry in profile.compact_entries:
                if entry.form is None:
                    continue
                descriptor_bytes = 0
                if isinstance(entry.form.mode, CompactExtensionEAMode):
                    family = families[
                        entry.form.mode.catalog.owner,
                        entry.form.mode.catalog.profile,
                        entry.form.mode.extension.id,
                    ]
                    self.assertEqual(
                        family.descriptor_bytes,
                        entry.form.mode.extension.bytes,
                    )
                    self.assertFalse(any(form.payloads for form in family.forms))
                    descriptor_bytes = family.descriptor_bytes
                expected[profile.definition.profile, entry.raw] = (
                    descriptor_bytes,
                    entry.form.payload_width // 8,
                )

        actual = {
            (layout.profile, layout.selector): (
                layout.descriptor_bytes,
                layout.payload_bytes,
            )
            for layout in self.projection.ea_layouts
        }
        self.assertEqual(actual, expected)

    def test_selector_metadata_comes_from_register_catalogs(self) -> None:
        selector_group_references = {
            payload_type.register_group
            for payload_type in self.project.types.payload_types.values()
            if isinstance(payload_type, RegisterSelectorPayloadType)
        }
        expected_groups = [
            {
                (register.id.lower(), register.encoding)
                for register in self.project.registers.groups.resolve(
                    group_reference
                ).registers.values()
                if register.encoding is not None
            }
            for group_reference in selector_group_references
        ]
        if any(
            isinstance(payload_type, ControlRegisterSelectorPayloadType)
            for payload_type in self.project.types.payload_types.values()
        ):
            expected_groups.append(
                {
                    (register.id.lower(), register.selector)
                    for register in self.project.control_registers.registers.values()
                }
            )
        actual_groups: dict[int, set[tuple[str, int]]] = {}
        for selector in self.projection.register_selectors:
            actual_groups.setdefault(selector.group, set()).add(
                (selector.name, selector.encoding)
            )
        self.assertCountEqual(list(actual_groups.values()), expected_groups)

    def test_authored_destination_exclusions_reach_scalar_projection(self) -> None:
        canonical = {
            self._form_identifier(bundle, form): form
            for bundle in self.project.catalog.instructions.values()
            for form in bundle.encodings.forms
        }
        scalar_ids = {record.identifier for record in self.projection.scalar_forms}
        constrained = {
            identifier: form
            for identifier, form in canonical.items()
            if identifier in scalar_ids
            and any(
                constraint.role == "dst"
                and isinstance(constraint, ExcludedOperandConstraint)
                and "immediate" in constraint.values
                for constraint in form.constraints
            )
        }
        projected = {
            record.identifier: record
            for record in self.projection.scalar_forms
            if record.identifier in constrained
        }
        self.assertEqual(set(projected), set(constrained))
        for identifier, record in projected.items():
            form = constrained[identifier]
            destination_index = next(
                index
                for index, operand in enumerate(form.syntax.operands)
                if operand.field is not None
                and (binding := form.field_for_marker(operand.field)) is not None
                and binding.role == "dst"
            )
            self.assertFalse(
                record.operands[destination_index].allow_immediate_ea,
                identifier,
            )

    def test_serialized_catalog_is_accepted_by_llvm_tablegen(self) -> None:
        binary_root = os.environ.get("BEDROCK_LLVM_BIN")
        if binary_root is None:
            self.skipTest("BEDROCK_LLVM_BIN is not configured")
        tablegen = Path(binary_root) / "llvm-tblgen"
        self.assertTrue(tablegen.is_file(), f"llvm-tblgen is missing: {tablegen}")
        project_root = os.environ.get("LLVM_PROJECT_ROOT")
        self.assertIsNotNone(project_root, "LLVM_PROJECT_ROOT is not configured")
        assert project_root is not None
        include_root = Path(project_root) / "llvm/include"
        self.assertTrue(
            include_root.is_dir(), f"LLVM include directory is missing: {include_root}"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated = root / "BedrockGenISACatalog.td"
            generated.write_text(self.catalog, encoding="utf-8")
            source = root / "consume.td"
            source.write_text(
                f'include "llvm/TableGen/SearchableTable.td"\ninclude "{generated}"\n',
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    str(tablegen),
                    "-I",
                    str(include_root),
                    "-print-records",
                    str(source),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
