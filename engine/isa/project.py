"""Whole-tree loading and lookup for ISA authoring tools."""

from __future__ import annotations

from engine.entity import create_entity_catalog
from engine.isa.catalog import load_source_catalog
from engine.isa.control_registers import load_control_registers
from engine.isa.cpuid import load_cpuid
from engine.isa.debug_triggers import load_debug_triggers
from engine.isa.disclosures import load_disclosures
from engine.isa.encoding_reservations import load_encoding_reservations
from engine.isa.event_structures import load_event_frame
from engine.isa.event_structures import load_event_payloads
from engine.isa.events import load_events
from engine.isa.instruction_headers import load_instruction_header
from engine.isa.memory_records import load_memory_records
from engine.isa.model import load_model
from engine.isa.page_tables import load_page_table_entry
from engine.isa.registers import load_registers
from engine.isa.terminology import load_terminology
from engine.isa.types import load_type_system
from engine.isa.ea import load_ea_catalogs, load_ea_mode, validate_ea_modes
from engine.reference import ReferenceIndex, register_reference
from engine.source.yaml import load_yaml

from collections.abc import Iterator
from engine.diagnostics import Diagnostic
from engine.diagnostics import _error
from engine.isa.catalog import ProjectLookupError
from engine.isa.catalog import ProjectLookupReason
from engine.isa.registers import ConstantReset
from engine.isa.registers import Register
from engine.isa.registers import RegisterCatalog
from engine.isa.registers import SourcedReset
from engine.reference import Reference
from engine.reference import ReferenceError

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Callable, TypeVar, cast

from engine.entity import Entity, EntityCatalog, EntityDependency, EntityDisplayStyle
from engine.isa.control_registers import ControlRegisterCatalog
from engine.isa.cpuid import CpuidCatalog, CpuidClassOverlay, CpuidLeafOverlay
from engine.isa.debug_triggers import DebugTriggerCatalog
from engine.isa.disclosures import ImplementationDisclosureCatalog
from engine.isa.encoding_reservations import EncodingReservationCatalog
from engine.isa.event_structures import EventFrameCatalog, EventPayloadCatalog
from engine.isa.events import EventCatalog, EventClassOverlay
from engine.isa.catalog import Extension
from engine.isa.extensions import load_extension_inventory
from engine.isa.instruction_headers import InstructionHeaderCatalog
from engine.isa.catalog import InstructionBundle
from engine.isa.catalog import SourceCatalog
from engine.isa.memory_records import MemoryRecordCatalog
from engine.isa.model import ModelCatalog
from engine.isa.page_tables import PageTableEntryCatalog
from engine.isa.registers import RegisterCatalog, SourcedReset
from engine.isa.terminology import TermCatalog
from engine.isa.types import EffectiveAddressFieldType, TypeSystem
from engine.isa.vector_examples import VectorDiagram
from engine.observability import log_phase
from engine.reference import QualifiedReference, Reference, UnknownReferenceError

_T = TypeVar("_T")
_LOGGER = logging.getLogger(__name__)






def _build_entities(
    types,
    sources,
    cpuid,
    events,
    event_frames,
    event_payloads,
    registers,
    control_registers,
    debug_triggers,
    page_table_entries,
    instruction_headers,
    terminology,
    model,
    memory_records,
) -> EntityCatalog:
    """Compose the ISA provider's typed indexes into its workspace catalog."""

    entries: list[tuple[Entity, str, EntityDisplayStyle]] = []

    def add(
        reference: Reference[object],
        value: object,
        display: str,
        style: EntityDisplayStyle = EntityDisplayStyle.TEXT,
    ) -> None:
        if not isinstance(value, Entity):
            raise TypeError(f"{reference!r}: referencable value must inherit Entity")
        if value.reference != reference:
            raise ValueError(
                f"{reference!r}: entity reference disagrees with its typed index"
            )
        entries.append((value, display, style))

    def add_index(
        typed_index,
        display: Callable[[Reference[object], object], str],
        style: EntityDisplayStyle = EntityDisplayStyle.TEXT,
    ) -> None:
        for reference, value in typed_index.items():
            normalized = cast(Reference[object], reference)
            add(normalized, value, display(normalized, value), style)

    def identifier(_reference: Reference[object], value: object) -> str:
        return str(getattr(value, "id"))

    def name_or_identifier(_reference: Reference[object], value: object) -> str:
        return str(getattr(value, "name", None) or getattr(value, "id"))

    def qualified_field(reference: Reference[object], value: object) -> str:
        return f"{reference.path[-1]}.{getattr(value, 'id')}"

    for topic in model.document_topics.values():
        add(
            cast(Reference[object], topic.reference),
            topic,
            topic.id.replace("-", " ").replace("_", " "),
        )
    for reference, bundle in sources.instructions.items():
        add(
            cast(Reference[object], reference),
            bundle,
            bundle.instruction.mnemonic,
            EntityDisplayStyle.CODE,
        )
    for values in (
        sources.ea_modes,
        types.field_types,
        types.payload_types,
    ):
        add_index(values, identifier, EntityDisplayStyle.CODE)

    code = EntityDisplayStyle.CODE
    for typed_index, display, style in (
        (cpuid.classes, name_or_identifier, EntityDisplayStyle.TEXT),
        (cpuid.leaves, identifier, code),
        (cpuid.layouts, identifier, code),
        (cpuid.queries, identifier, code),
        (cpuid.fields, identifier, code),
        (cpuid.layout_fields, identifier, code),
        (cpuid.common_headers, identifier, code),
        (cpuid.common_header_fields, qualified_field, code),
        (events.classes, name_or_identifier, EntityDisplayStyle.TEXT),
        (events.events, identifier, code),
        (event_frames.frames, identifier, code),
        (event_frames.slots, identifier, code),
        (event_frames.fields, qualified_field, code),
        (event_payloads.formats_by_reference, identifier, code),
        (event_payloads.fields, qualified_field, code),
        (registers.groups, identifier, EntityDisplayStyle.TEXT),
        (registers.registers, identifier, code),
        (registers.fields, qualified_field, code),
        (
            control_registers.namespaces_by_reference,
            identifier,
            EntityDisplayStyle.TEXT,
        ),
        (
            control_registers.registers,
            identifier,
            EntityDisplayStyle.TEXT,
        ),
        (control_registers.fields, qualified_field, code),
        (debug_triggers.slots, identifier, code),
        (debug_triggers.words, identifier, code),
        (debug_triggers.fields, qualified_field, code),
        (page_table_entries.entries, identifier, code),
        (page_table_entries.fields, qualified_field, code),
        (instruction_headers.headers, identifier, code),
        (instruction_headers.fields, qualified_field, code),
    ):
        add_index(typed_index, display, style)

    add_index(memory_records.references, identifier, EntityDisplayStyle.TEXT)
    for bundle in sources.instructions.values():
        add_index(bundle.diagrams.diagrams, identifier, EntityDisplayStyle.TEXT)

    for group in terminology.groups.values():
        add(cast(Reference[object], group.reference), group, group.title)
    for term in terminology.terms.values():
        add(
            cast(Reference[object], term.reference),
            term,
            term.forms.canonical,
        )
    return create_entity_catalog(entries)


@dataclass(frozen=True, slots=True)
class IsaProject:
    """The single public loading and lookup facade for authoring commands."""

    root: Path
    types: TypeSystem
    encoding_reservations: EncodingReservationCatalog
    catalog: SourceCatalog
    cpuid: CpuidCatalog
    events: EventCatalog
    event_frames: EventFrameCatalog
    event_payloads: EventPayloadCatalog
    registers: RegisterCatalog
    memory_records: MemoryRecordCatalog
    control_registers: ControlRegisterCatalog
    debug_triggers: DebugTriggerCatalog
    page_table_entries: PageTableEntryCatalog
    instruction_headers: InstructionHeaderCatalog
    terminology: TermCatalog
    model: ModelCatalog
    disclosures: ImplementationDisclosureCatalog
    entities: EntityCatalog = field(init=False)

    def __post_init__(self) -> None:
        if set(self.catalog.extensions) != set(self.types.extensions):
            raise ValueError("ISA source and type extension membership must agree")
        for owner, extension in self.catalog.extensions.items():
            if extension.types is not self.types.namespace(owner):
                raise ValueError(f"{extension.metadata.source}: extension must share the ISA's canonical type namespace")
        with log_phase(_LOGGER, "project.catalog.load", level=logging.DEBUG, catalog="entities"):
            entities = _build_entities(
                self.types, self.catalog, self.cpuid, self.events,
                self.event_frames, self.event_payloads, self.registers,
                self.control_registers, self.debug_triggers, self.page_table_entries,
                self.instruction_headers, self.terminology, self.model,
                self.memory_records,
            )
        object.__setattr__(self, "entities", entities)

    def resolve(self, reference: Reference[_T]) -> _T:
        """Resolve one provider-local entity through the ISA entity catalog."""

        return cast(
            _T,
            self.entities.resolve(cast(Reference[Entity], reference)),
        )

    def entity_dependencies(
        self,
    ) -> tuple[EntityDependency, ...]:
        """Return the ISA domain's authored and typed entity relationships."""

        result: list[EntityDependency] = []

        def local(reference: Reference[object]) -> QualifiedReference[object]:
            return QualifiedReference("isa", reference)

        def add(
            source: Reference[object],
            target: Reference[object],
            kind: str,
        ) -> None:
            result.append(EntityDependency(source, local(target), kind))

        for bundle in self.catalog.instructions.values():
            source = cast(Reference[object], bundle.reference)
            required_cpuid_fields = {
                field.reference: field
                for field in (
                    *bundle.required_cpuid_flags,
                    *(
                        local
                        for form in bundle.encodings.forms
                        for local in form.additional_cpuid_flags
                    ),
                )
            }
            for field in required_cpuid_fields.values():
                add(
                    source,
                    cast(Reference[object], field.reference),
                    "requires-cpuid",
                )
            for form in bundle.encodings.forms:
                for field in form.fields:
                    add(
                        source,
                        cast(Reference[object], field.type),
                        "instruction-field-type",
                    )
                for payload in form.payloads:
                    add(
                        source,
                        cast(Reference[object], payload.type),
                        "instruction-payload-type",
                    )

        profile_types = {
            (definition.owner, definition.profile): definition.reference
            for definition in self.types.field_types.values()
            if isinstance(definition, EffectiveAddressFieldType)
        }
        for mode in self.catalog.ea_modes.values():
            source = cast(Reference[object], mode.reference)
            profile_type = profile_types.get((mode.catalog.owner, mode.catalog.profile))
            if profile_type is not None:
                add(
                    source,
                    cast(Reference[object], profile_type),
                    "ea-profile-type",
                )
            for field in mode.fields:
                add(
                    source,
                    cast(Reference[object], field.type),
                    "ea-field-type",
                )
            for encoding in mode.encodings:
                for payload in encoding.payloads:
                    add(
                        source,
                        cast(Reference[object], payload.type),
                        "ea-payload-type",
                    )

        for cpuid_class in self.cpuid.classes.values():
            if isinstance(cpuid_class, CpuidClassOverlay):
                add(
                    cast(Reference[object], cpuid_class.reference),
                    cast(Reference[object], cpuid_class.extends),
                    "cpuid-class-overlay",
                )
        for leaf in self.cpuid.leaves.values():
            if isinstance(leaf, CpuidLeafOverlay):
                add(
                    cast(Reference[object], leaf.reference),
                    cast(Reference[object], leaf.extends),
                    "cpuid-leaf-overlay",
                )
        for event_class in self.events.classes.values():
            if isinstance(event_class, EventClassOverlay):
                add(
                    cast(Reference[object], event_class.reference),
                    cast(Reference[object], event_class.extends),
                    "event-class-overlay",
                )

        for register in self.registers.registers.values():
            if isinstance(register.reset, SourcedReset):
                add(
                    cast(Reference[object], register.reference),
                    cast(Reference[object], register.reset.source),
                    "register-reset-source",
                )
        for register in self.control_registers.registers.values():
            if isinstance(register.reset, SourcedReset):
                add(
                    cast(Reference[object], register.reference),
                    cast(Reference[object], register.reset.source),
                    "control-register-reset-source",
                )

        for term in self.terminology.terms.values():
            source = cast(Reference[object], term.reference)
            for target in term.relations.broader:
                add(
                    source,
                    cast(Reference[object], target),
                    "term-broader",
                )
            for target in term.relations.related:
                add(
                    source,
                    cast(Reference[object], target),
                    "term-related",
                )
        return tuple(result)






def load_isa(root: str | Path) -> IsaProject:
    isa_root = Path(root).resolve()
    schemas = {name: load_yaml(isa_root / "schemas" / f"{name}.yaml")
               for name in ("instruction", "instruction-encodings", "vector-diagram", "ea-mode-compact", "ea-mode-extended")}
    with log_phase(
        _LOGGER,
        "project.catalog.load",
        level=logging.DEBUG,
        catalog="extensions",
    ):
        extension_catalog = load_extension_inventory(isa_root)
    with log_phase(
        _LOGGER, "project.catalog.load", level=logging.DEBUG, catalog="types"
    ):
        types = load_type_system(isa_root, extensions=extension_catalog)
    with log_phase(
        _LOGGER,
        "project.catalog.load",
        level=logging.DEBUG,
        catalog="encoding-reservations",
    ):
        encoding_reservations = load_encoding_reservations(isa_root)
    with log_phase(
        _LOGGER, "project.catalog.load", level=logging.DEBUG, catalog="cpuid"
    ):
        cpuid = load_cpuid(isa_root, extensions=extension_catalog)
    with log_phase(
        _LOGGER, "project.catalog.load", level=logging.DEBUG, catalog="events"
    ):
        events = load_events(isa_root, extensions=extension_catalog)
    with log_phase(
        _LOGGER,
        "project.catalog.load",
        level=logging.DEBUG,
        catalog="event-frames",
    ):
        event_frames = load_event_frame(isa_root)
    with log_phase(
        _LOGGER,
        "project.catalog.load",
        level=logging.DEBUG,
        catalog="event-payloads",
    ):
        event_payloads = load_event_payloads(isa_root)
    with log_phase(
        _LOGGER, "project.catalog.load", level=logging.DEBUG, catalog="registers"
    ):
        registers = load_registers(isa_root, extensions=extension_catalog)
    with log_phase(
        _LOGGER,
        "project.catalog.load",
        level=logging.DEBUG,
        catalog="memory-records",
    ):
        memory_records = load_memory_records(isa_root, extensions=extension_catalog)
    with log_phase(
        _LOGGER,
        "project.catalog.load",
        level=logging.DEBUG,
        catalog="control-registers",
    ):
        control_registers = load_control_registers(isa_root, extensions=extension_catalog)
    with log_phase(
        _LOGGER,
        "project.catalog.load",
        level=logging.DEBUG,
        catalog="page-table-entries",
    ):
        page_table_entries = load_page_table_entry(isa_root)
    with log_phase(
        _LOGGER,
        "project.catalog.load",
        level=logging.DEBUG,
        catalog="debug-triggers",
    ):
        debug_triggers = load_debug_triggers(isa_root)
    with log_phase(
        _LOGGER,
        "project.catalog.load",
        level=logging.DEBUG,
        catalog="instruction-headers",
    ):
        instruction_headers = load_instruction_header(isa_root)
    with log_phase(
        _LOGGER,
        "project.catalog.load",
        level=logging.DEBUG,
        catalog="terminology",
    ):
        terminology = load_terminology(isa_root, extensions=extension_catalog)
    with log_phase(
        _LOGGER,
        "project.catalog.load",
        level=logging.DEBUG,
        catalog="instructions",
    ):
        ea_members = {}
        for family in load_ea_catalogs(
            isa_root, field_types=types.field_types, owners=("base", *extension_catalog.declared)
        ):
            schema_name = "ea-mode-compact" if family.mode_type == "compact" else "ea-mode-extended"
            for mode_id in family.modes:
                mode = load_ea_mode(
                    family.mode_path(mode_id), schema=schemas[schema_name],
                    field_types=types.field_types, payload_types=types.payload_types, catalog=family,
                )
                register_reference(ea_members, mode.reference, mode)
        validate_ea_modes(tuple(ea_members.values()))
        catalog = load_source_catalog(
            isa_root, extensions=extension_catalog, types=types,
            ea_modes=ReferenceIndex(ea_members), cpuid=cpuid, schemas=schemas,
        )
    with log_phase(
        _LOGGER, "project.catalog.load", level=logging.DEBUG, catalog="model"
    ):
        model = load_model(isa_root, extension_catalog)

    with log_phase(
        _LOGGER,
        "project.catalog.load",
        level=logging.DEBUG,
        catalog="disclosures",
    ):
        disclosures = load_disclosures(isa_root, extensions=extension_catalog)
    return IsaProject(
        isa_root,
        types,
        encoding_reservations,
        catalog,
        cpuid,
        events,
        event_frames,
        event_payloads,
        registers,
        memory_records,
        control_registers,
        debug_triggers,
        page_table_entries,
        instruction_headers,
        terminology,
        model,
        disclosures,
    )


def _check_reset_sources(
    catalog: RegisterCatalog, control_registers: ControlRegisterCatalog
) -> Iterator[Diagnostic]:
    from engine.diagnostics import _error
    from engine.reference import ReferenceError

    active: list[Reference[Register]] = []
    resolved: set[Reference[Register]] = set()

    def resolve(register: Register) -> Iterator[Diagnostic]:
        reference = register.reference
        if reference in resolved:
            return
        reset = register.reset
        if isinstance(reset, ConstantReset):
            width = register.width if isinstance(register.width, int) else register.width.minimum
            if reset.value >= 1 << width:
                yield _error(
                    "register.reset.range",
                    register.source,
                    f"reset value {reset.value} does not fit {width}-bit "
                    f"register {register.id!r}",
                    "reset",
                )
        if isinstance(reset, SourcedReset):
            try:
                target = (
                    control_registers.registers.resolve(reset.source)
                    if reset.source.path[:1] == ("control_registers",)
                    else catalog.registers.resolve(reset.source)
                )
            except (ReferenceError, ValueError):
                yield _error(
                    "register.reset.unknown-source",
                    register.source,
                    "unknown reset source",
                    "reset",
                )
            else:
                if target.width != register.width:
                    yield _error(
                        "register.reset.width",
                        register.source,
                        f"reset source {target.id!r} has width {target.width}, "
                        f"expected {register.width}",
                        "reset",
                    )
                if target.reference in active:
                    cycle = (
                        *active[active.index(target.reference) :],
                        target.reference,
                    )
                    yield _error(
                        "register.reset.cycle",
                        register.source,
                        "register reset cycle: "
                        + " -> ".join(
                            (
                                control_registers.registers.resolve(item).id
                                if item.path[:1] == ("control_registers",)
                                else catalog.registers.resolve(item).id
                            )
                            for item in cycle
                        ),
                        "reset",
                    )
                else:
                    active.append(reference)
                    if target.reference.path[:1] == ("registers",):
                        yield from resolve(target)
                    active.pop()
        resolved.add(reference)

    for register in catalog.registers.values():
        yield from resolve(register)
