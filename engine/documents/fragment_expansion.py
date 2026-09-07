"""Resolve parsed document directives into selected semantic values."""
from __future__ import annotations
from dataclasses import dataclass
from engine.reference import Reference
from engine.syntax.tex import SourceDirective, SourceSpan
from engine.documents.fragments.cpuid import CpuidLeafProjection, select_cpuid_leaf
from engine.documents.fragments.ea import EAModeDiagramProjection, select_ea_diagram
from engine.documents.fragments.events import EventCodeRow, EventClassesProjection, project_row_event_reference, select_event_classes
from engine.documents.fragments.registers import RegisterFigureProjection, ControlRegistersProjection, select_register_figure, select_control_registers
from engine.documents.fragments.disclosures import DisclosuresProjection, select_disclosures
from engine.documents.fragments.memory_records import MemoryRecordProjection, select_memory_record
from engine.documents.fragments.structured_fields import StructuredFieldTarget, select_structured_field
from engine.documents.fragments.vector import select_vector_diagram
from engine.isa.vector_examples import VectorDiagram
from engine.documents.abi import (
    ABI_DIRECTIVES, project_abi_fragment, CReturnRulesProjection, CMemoryOrdersProjection,
    CAtomicLoweringsProjection, ElfRelocationsProjection, ElfDebugRegistersProjection,
    ElfEntryStateProjection, IntrinsicGroupsProjection, IntrinsicGroupProjection, IntrinsicTypesProjection,
)

@dataclass(frozen=True, slots=True)
class SourceFragmentProjection:
    span: SourceSpan
    selection: (
        CpuidLeafProjection | EAModeDiagramProjection | EventCodeRow | EventClassesProjection
        | RegisterFigureProjection | ControlRegistersProjection | DisclosuresProjection
        | MemoryRecordProjection | StructuredFieldTarget | VectorDiagram
        | CReturnRulesProjection | CMemoryOrdersProjection | CAtomicLoweringsProjection
        | ElfRelocationsProjection | ElfDebugRegistersProjection | ElfEntryStateProjection
        | IntrinsicGroupsProjection | IntrinsicGroupProjection | IntrinsicTypesProjection
    )


def project_fragments(directive: SourceDirective, owner, catalogs) -> SourceFragmentProjection:
    if directive.name in ABI_DIRECTIVES:
        return SourceFragmentProjection(directive.span, project_abi_fragment(directive, catalogs))
    try:
        isa = catalogs["isa"]
        name, arguments = directive.name, directive.arguments
        if name == "register-figure":
            if len(arguments) != 2:
                raise ValueError(f"register-figure requires owner and group selection")
            selection = select_register_figure(arguments[0], tuple(arguments[1].split(",")), owner=owner, catalog=isa.registers)
        elif name in {"control-registers", "disclosures", "event-classes"}:
            if len(arguments) != 1 or not arguments[0]:
                raise ValueError(f"{name} requires an explicit member selection")
            keys = tuple(arguments[0].split(","))
            if name == "disclosures":
                selection = select_disclosures(keys, catalog=isa.disclosures)
            else:
                references = tuple(Reference.parse(key) for key in keys)
                selection = select_control_registers(references, catalog=isa.control_registers) if name == "control-registers" else select_event_classes(references, catalog=isa.events)
        else:
            if len(arguments) != 1:
                raise ValueError(f"{name} requires one reference")
            reference = Reference.parse(arguments[0])
            if name == "cpuid-leaf":
                selection = select_cpuid_leaf(reference, owner=owner, catalog=isa.cpuid)
            elif name == "ea-diagram":
                selection = select_ea_diagram(reference, owner=owner, modes=isa.catalog.ea_modes, field_types=isa.types.field_types, payload_types=isa.types.payload_types)
            elif name == "memory-record":
                selection = select_memory_record(reference, owner=owner, catalog=isa.memory_records)
            elif name == "event-code":
                selection = project_row_event_reference(isa.events, reference)
            elif name == "diagram":
                selection = select_vector_diagram(reference, owner=owner, source=directive.span.source)
            elif name in {
                "debug-trigger-target", "event-structure-target", "instruction-header-field-target",
                "cpuid-field-target", "pte-field-target", "register-field-target",
            }:
                selection = select_structured_field(name, reference, entities=isa.entities)
            else:
                raise ValueError(f"unknown document directive {name!r}")
        return SourceFragmentProjection(directive.span, selection)
    except (ValueError, KeyError) as error:
        raise ValueError(f"{directive.span}: {error}") from error


def fragment_targets(fragment: SourceFragmentProjection):
    selected = fragment.selection
    if isinstance(selected, StructuredFieldTarget):
        return (selected.entity.reference,)
    if isinstance(selected, EventCodeRow):
        return (selected.reference,)
    if isinstance(selected, EventClassesProjection):
        return tuple(item.reference for item in selected.classes)
    if isinstance(selected, CpuidLeafProjection):
        return tuple(field.reference for query in selected.queries for field in query.fields)
    return ()
