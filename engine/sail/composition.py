"""Projection of a loaded ISA project into a Sail program."""

from __future__ import annotations

from engine.isa.configuration import IsaConfiguration

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from engine.reference import Reference
from typing import TYPE_CHECKING

from engine.isa.catalog import InstructionBundle
from engine.isa.model import ExecutionProvider, SailUnit, resolve_sail_sources


def compose_sail(
    catalog, model, types, registers, control_register_catalog, event_catalog,
    configuration: IsaConfiguration,
) -> SailProgram:
    owners = configuration.owners
    ordered_bundles = tuple(
        catalog.instructions.resolve(reference)
        for reference in catalog.instruction_order
    )
    bundles = tuple(bundle for bundle in ordered_bundles if bundle.owner in owners)
    needed_units = {
        unit.reference for unit in model.sail_units.values() if unit.owner in owners
    }
    pending = list(needed_units)
    while pending:
        unit = model.sail_units[pending.pop()]
        for reference in unit.requires:
            if reference not in needed_units:
                needed_units.add(reference)
                pending.append(reference)
    sail_units = tuple(
        model.sail_units[reference]
        for reference in model.sail_order
        if reference in needed_units
    )
    implementation_owners = owners | frozenset(unit.owner for unit in sail_units)
    implementation_bundles = tuple(
        bundle for bundle in ordered_bundles if bundle.owner in implementation_owners
    )
    providers = tuple(
        namespace.execution_provider
        for namespace in (model.base, *model.extensions.values())
        if namespace.owner in owners and namespace.execution_provider is not None
    )
    if len(providers) > 1:
        sources = ", ".join(str(provider.source) for provider in providers)
        raise ValueError(
            f"selected ISA configuration has multiple execution providers: {sources}"
        )
    from engine.isa.encoding import resolve_encoding_form
    from engine.isa.ea import resolve_ea_encoding
    from engine.sail.dispatch import project_sail_dispatch
    from engine.sail.registry import project_sail_registry

    modes = tuple(
        mode
        for mode in catalog.ea_modes.values()
        if mode.catalog.owner in owners
    )
    execution_provider = providers[0] if providers else None
    field_types = types.field_types
    payload_types = types.payload_types
    ea_modes = modes
    model_namespaces = {
        owner: namespace
        for owner, namespace in model.extensions.items()
    }
    declaration_configuration = IsaConfiguration.resolve(catalog)
    events = event_catalog.resolved_events()
    control_registers = control_register_catalog.selected(declaration_configuration.owners)
    generated_state_registers = frozenset(
        (owner, register_id)
        for owner in declaration_configuration.owners
        for register_id in control_register_catalog.namespace(
            owner
        ).generated_state_registers
    )
    registry_type_sources, unit_sources = resolve_sail_sources(sail_units, control_register_catalog)
    declaration_forms = tuple(
        resolve_encoding_form(bundle.instruction, form,
            field_types=field_types, payload_types=payload_types,
            ea_modes=catalog.ea_modes, registers=registers)
        for bundle in ordered_bundles for form in bundle.encodings.forms
    )
    selected_instructions = {id(bundle.instruction) for bundle in bundles}
    forms = tuple(form for form in declaration_forms if id(form.instruction) in selected_instructions)
    declaration_ea_forms = tuple(
        resolve_ea_encoding(mode, encoding, field_types=field_types, payload_types=payload_types)
        for mode in catalog.ea_modes.values() for encoding in mode.encodings
    )
    ea_forms = tuple(form for form in declaration_ea_forms if form.mode.catalog.owner in owners)
    registry = project_sail_registry(
        bundles=ordered_bundles,
        configuration=declaration_configuration,
        active_owners=owners,
        control_registers=control_registers,
        events=events,
        generated_state_registers=generated_state_registers,
        model_namespaces=model_namespaces,
    )
    dispatch = project_sail_dispatch(
        bundles=bundles, execution_provider=execution_provider
    )
    selected_units = {unit.reference: unit for unit in sail_units}
    unit_dependencies = MappingProxyType(
        {
            unit.reference: tuple(
                selected_units[reference]
                for reference in unit.requires
            )
            for unit in sail_units
        }
    )
    sources = tuple(
        dict.fromkeys(
            (
                *(source for unit in sail_units for source in unit_sources[unit.reference]),
                *(bundle.semantics for bundle in implementation_bundles),
                *registry_type_sources,
                *(
                    (execution_provider.provider,)
                    if execution_provider is not None
                    else ()
                ),
            )
        )
    )
    return SailProgram(
        configuration,
        bundles,
        implementation_bundles,
        sail_units,
        execution_provider,
        sources,
        forms,
        ea_forms,
        declaration_forms,
        declaration_ea_forms,
        registry,
        dispatch,
        registry_type_sources,
        unit_sources,
        unit_dependencies,
    )




@dataclass(frozen=True, slots=True)
class SailProgram:
    """Selected semantics and their required Sail implementation sources."""

    configuration: IsaConfiguration
    bundles: tuple[InstructionBundle, ...]
    implementation_bundles: tuple[InstructionBundle, ...]
    sail_units: tuple[SailUnit, ...]
    execution_provider: ExecutionProvider | None
    sources: tuple[Path, ...]
    forms: tuple[ResolvedEncodingForm, ...]
    ea_forms: tuple[ResolvedEAEncoding, ...]
    declaration_forms: tuple[ResolvedEncodingForm, ...]
    declaration_ea_forms: tuple[ResolvedEAEncoding, ...]
    registry: SailRegistryProjection
    dispatch: SailDispatchProjection
    registry_type_sources: tuple[Path, ...]
    unit_sources: Mapping[Reference[SailUnit], tuple[Path, ...]]
    unit_dependencies: Mapping[Reference[SailUnit], tuple[SailUnit, ...]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "bundles", tuple(self.bundles))
        object.__setattr__(self, "implementation_bundles", tuple(self.implementation_bundles))
        object.__setattr__(self, "sail_units", tuple(self.sail_units))
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "forms", tuple(self.forms))
        object.__setattr__(self, "ea_forms", tuple(self.ea_forms))
        object.__setattr__(self, "declaration_forms", tuple(self.declaration_forms))
        object.__setattr__(self, "declaration_ea_forms", tuple(self.declaration_ea_forms))
        object.__setattr__(self, "registry_type_sources", tuple(self.registry_type_sources))
        object.__setattr__(self, "unit_sources", MappingProxyType({key0: tuple(value0) for key0, value0 in self.unit_sources.items()}))
        object.__setattr__(self, "unit_dependencies", MappingProxyType({key0: tuple(value0) for key0, value0 in self.unit_dependencies.items()}))



if TYPE_CHECKING:
    from engine.isa.encoding import ResolvedEncodingForm
    from engine.isa.ea import ResolvedEAEncoding
    from engine.sail.dispatch import SailDispatchProjection
    from engine.sail.registry import SailRegistryProjection
