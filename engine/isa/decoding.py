"""Configured decoder inputs built from canonical resolved ISA members."""

from __future__ import annotations

from dataclasses import dataclass

from engine.isa.catalog import InstructionBundle
from engine.isa.configuration import IsaConfiguration
from engine.isa.cpuid import CpuidField, CpuidQuery, ResolvedCpuidLeaf
from engine.isa.ea import ResolvedEAEncoding, resolve_ea_encoding
from engine.isa.encoding import ResolvedEncodingForm, ResolvedEffectiveAddressOperand, ResolvedEaRead, resolve_encoding_form
from engine.isa.encoding_architecture import ENCODING_CLASSES_BY_WIDTH, MAX_RECORD_BYTES
from engine.isa.types import EffectiveAddressFieldType


@dataclass(frozen=True, slots=True)
class CpuidFlagIR:
    field: CpuidField
    leaf: ResolvedCpuidLeaf
    query: CpuidQuery


@dataclass(frozen=True, slots=True)
class CompactEaEntryIR:
    raw: int
    form: ResolvedEAEncoding | None


@dataclass(frozen=True, slots=True)
class EaDescriptorFamilyIR:
    owner: str
    profile: str
    name: str
    descriptor_bytes: int
    forms: tuple[ResolvedEAEncoding, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "forms", tuple(self.forms))



@dataclass(frozen=True, slots=True)
class EaProfileIR:
    definition: EffectiveAddressFieldType
    compact_forms: tuple[ResolvedEAEncoding, ...]
    compact_entries: tuple[CompactEaEntryIR, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "compact_forms", tuple(self.compact_forms))
        object.__setattr__(self, "compact_entries", tuple(self.compact_entries))



@dataclass(frozen=True, slots=True)
class EffectiveAddressIR:
    compact_width: int
    profiles: tuple[EaProfileIR, ...]
    descriptor_families: tuple[EaDescriptorFamilyIR, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "profiles", tuple(self.profiles))
        object.__setattr__(self, "descriptor_families", tuple(self.descriptor_families))



@dataclass(frozen=True, slots=True)
class DerivedLimitsIR:
    form_count: int
    mnemonic_count: int
    max_opcode_width: int
    max_operands: int
    max_ea_operands: int
    max_overlaps: int
    max_fields: int
    max_layout_ops: int
    max_fixed_required_bytes: int
    max_required_bytes: int
    max_record_bytes: int
    compact_ea_values: int
    max_descriptor_bytes: int


@dataclass(frozen=True, slots=True)
class DecodeIR:
    configuration: IsaConfiguration
    bundles: tuple[InstructionBundle, ...]
    cpuid_flags: tuple[CpuidFlagIR, ...]
    limits: DerivedLimitsIR
    forms: tuple[ResolvedEncodingForm, ...]
    effective_addresses: EffectiveAddressIR

    def __post_init__(self) -> None:
        object.__setattr__(self, "bundles", tuple(self.bundles))
        object.__setattr__(self, "cpuid_flags", tuple(self.cpuid_flags))
        object.__setattr__(self, "forms", tuple(self.forms))



def _derive_limits(forms, effective_addresses) -> DerivedLimitsIR:
    return DerivedLimitsIR(
        len(forms), len({form.instruction.mnemonic for form in forms}),
        max(ENCODING_CLASSES_BY_WIDTH),
        max((len(form.operands) for form in forms), default=0),
        max((sum(isinstance(operand, ResolvedEffectiveAddressOperand) for operand in form.operands) for form in forms), default=0),
        max((len(form.overlaps) for form in forms), default=0),
        max((len(form.fields) for form in forms), default=0),
        max((len(form.layout) for form in forms), default=0),
        max((form.fixed_required_bytes for form in forms), default=0),
        max((form.maximum_required_bytes for form in forms), default=0),
        MAX_RECORD_BYTES, 1 << effective_addresses.compact_width,
        max((family.descriptor_bytes for family in effective_addresses.descriptor_families), default=0),
    )


def _effective_addresses(*, configuration, field_types, payload_types, ea_modes):
    definitions = tuple(definition for definition in field_types.values() if isinstance(definition, EffectiveAddressFieldType) and definition.owner in configuration.owners)
    widths = {definition.bits for definition in definitions}
    if len(widths) != 1:
        raise ValueError(f"EA profiles must have one compact selector width, got {sorted(widths)}")
    modes = tuple(mode for mode in ea_modes.values() if mode.catalog.owner in configuration.owners)
    profiles = []
    families = []
    for definition in definitions:
        selected = tuple(mode for mode in modes if (mode.catalog.owner, mode.catalog.profile) == (definition.owner, definition.profile))
        compact = tuple(resolve_ea_encoding(mode, encoding, field_types=field_types, payload_types=payload_types) for mode in selected if mode.catalog.mode_type == "compact" for encoding in mode.encodings)
        entries = []
        for raw in range(1 << definition.bits):
            matches = tuple(form for form in compact if form.pattern.matches(raw))
            if len(matches) > 1:
                raise ValueError(f"{definition.source}: compact EA selector {raw} has multiple members")
            entries.append(CompactEaEntryIR(raw, matches[0] if matches else None))
        profiles.append(EaProfileIR(definition, compact, tuple(entries)))
        family_names = tuple(dict.fromkeys(mode.catalog.mode_type for mode in selected if mode.catalog.mode_type != "compact"))
        for name in family_names:
            forms = tuple(resolve_ea_encoding(mode, encoding, field_types=field_types, payload_types=payload_types) for mode in selected if mode.catalog.mode_type == name for encoding in mode.encodings)
            byte_counts = {form.pattern.bit_width // 8 for form in forms}
            if len(byte_counts) != 1:
                raise ValueError(f"{definition.source}: descriptor family {name} has inconsistent byte widths")
            families.append(EaDescriptorFamilyIR(definition.owner, definition.profile, name, byte_counts.pop(), forms))
    return EffectiveAddressIR(widths.pop(), tuple(profiles), tuple(families))


def project_decode(bundles, *, configuration, field_types, payload_types, ea_modes, registers, cpuid) -> DecodeIR:
    """Resolve precisely the supplied bundles enabled by the configuration."""
    selected = tuple(bundle for bundle in bundles if bundle.owner in configuration.owners)
    forms = tuple(resolve_encoding_form(bundle.instruction, form, field_types=field_types, payload_types=payload_types, ea_modes=ea_modes, registers=registers) for bundle in selected for form in bundle.encodings.forms)
    fields = {field.reference: field for bundle in selected for form in bundle.encodings.forms for field in bundle.required_cpuid_flags_for(form)}
    flags = []
    for field in fields.values():
        leaf, query, canonical_field = cpuid.resolve_field(field.reference)
        if canonical_field is not field:
            raise ValueError(f"{field.source}: CPUID requirement is not the canonical field")
        flags.append(CpuidFlagIR(field, leaf, query))
    effective_addresses = _effective_addresses(configuration=configuration, field_types=field_types, payload_types=payload_types, ea_modes=ea_modes)
    return DecodeIR(configuration, selected, tuple(flags), _derive_limits(forms, effective_addresses), forms, effective_addresses)
