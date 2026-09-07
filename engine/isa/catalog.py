"""Values and operations owned by this specification boundary."""

from __future__ import annotations

from engine.isa.encoding import load_encodings
from engine.isa.extensions import load_extension_metadata
from engine.source.inventory import inspect_inventory, require_exact

from collections.abc import Iterator, Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from engine.diagnostics import Diagnostic
from engine.diagnostics import RelatedLocation
from engine.diagnostics import _error
from engine.entity import Entity
from engine.isa.cpuid import CpuidCatalog
from engine.isa.cpuid import CpuidField
from engine.isa.ea import EAMode
from engine.isa.ea import EAModeCatalog
from engine.isa.ea import validate_ea_modes
from engine.isa.encoding import EncodingCatalog
from engine.isa.encoding import EncodingForm
from engine.isa.encoding import UnknownConstraintValueError, resolve_encoding_form
from engine.isa.encoding_space import EncodingCube, analyze_encoding_space
from engine.isa.extensions import ExtensionDependencyCycleError
from engine.isa.extensions import ExtensionMetadata
from engine.isa.extensions import RepeatedCpuidRequirementError
from engine.isa.extensions import RequiredExtensionUnavailableError
from engine.isa.extensions import extension_owner_roots
from engine.isa.extensions import resolve_extension_requirements
from engine.isa.instructions import Instruction
from engine.isa.instructions import load_instruction
from engine.isa.types import TypeNamespace
from engine.isa.types import TypeSystem
from engine.isa.vector_examples import VectorDiagramCatalog, VectorDiagram, load_vector_diagrams
from engine.reference import Reference
from engine.reference import ReferenceIndex
from engine.reference import UnknownReferenceError
from engine.reference import register_reference
from engine.source.inventory import DirectoryInventory
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class Extension:
    """One declared ISA extension and all instructions owned by it."""

    metadata: ExtensionMetadata
    types: TypeNamespace
    instruction_set: InstructionSet
    requires: tuple["Extension", ...]
    required_cpuid_flags: tuple[CpuidField, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "requires", tuple(self.requires))
        object.__setattr__(self, "required_cpuid_flags", tuple(self.required_cpuid_flags))
        if self.types.owner != self.metadata.id or self.instruction_set.catalog.owner != self.metadata.id:
            raise ValueError("extension namespaces do not have the extension owner")
        if self.metadata.source != self.metadata.root / "extension.yaml" or self.types.root != self.metadata.root or self.instruction_set.catalog.root != self.metadata.root / "instructions/definitions":
            raise ValueError(f"{self.metadata.source}: extension members must share the declared owning root")









@dataclass(frozen=True, slots=True)
class InstructionBundle(Entity):
    """The complete authoring boundary for one instruction."""

    reference: Reference["InstructionBundle"]
    owner: str
    instruction: Instruction
    encodings: EncodingCatalog
    diagrams: VectorDiagramCatalog
    semantics: Path
    description: Path
    required_cpuid_flags: tuple[CpuidField, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "required_cpuid_flags", tuple(self.required_cpuid_flags))
        if self.reference != Reference(self.owner, ("instructions",), self.instruction.mnemonic):
            raise ValueError("instruction bundle identity differs from its canonical member")
        if self.diagrams.owner != self.owner or self.diagrams.instruction != self.reference:
            raise ValueError("diagram collection does not belong to the instruction bundle")
        root = self.instruction.source.parent
        if self.instruction.source != root / "instruction.yaml" or self.encodings.source != root / "encodings.yaml" or self.diagrams.root != root / "diagrams" or self.semantics != root / "semantics.sail" or self.description != root / "descriptions.tex":
            raise ValueError(f"{self.instruction.source}: bundle companions must belong to the same instruction root")

    @property
    def source(self) -> Path:
        return self.instruction.source

    def required_cpuid_flags_for(self, form: EncodingForm) -> tuple[CpuidField, ...]:
        """Return inherited and form-local CPUID requirements."""

        return (*self.required_cpuid_flags, *form.additional_cpuid_flags)


def load_instruction_inventory(*, owner: str, root: str | Path) -> "DirectoryInventory":
    """Load an instruction inventory whose directory names are mnemonics."""

    definitions = Path(root)
    catalog = inspect_inventory(
        owner=owner,
        kind="instruction",
        source=definitions / "instructions.yaml",
        root=definitions,
        key="instructions",
        name_pattern=r"[A-Za-z][A-Za-z0-9]*",
    )
    return require_exact(catalog)


@dataclass(frozen=True, slots=True)
class InstructionSet:
    """One instruction inventory and the bundles successfully loaded from it."""

    catalog: DirectoryInventory
    instructions: tuple[InstructionBundle, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "instructions", tuple(self.instructions))
        if self.catalog.kind != "instruction" or self.catalog.source != self.catalog.root / "instructions.yaml" or self.catalog.duplicates or self.catalog.missing or self.catalog.undeclared:
            raise ValueError(f"{self.catalog.source}: instruction inventory identity and membership must agree")
        references = tuple(bundle.reference for bundle in self.instructions)
        if len(set(references)) != len(references):
            raise ValueError("instruction set repeats a canonical reference")
        if any(bundle.owner != self.catalog.owner for bundle in self.instructions):
            raise ValueError("instruction set contains a foreign owner")
        if any(bundle.instruction.source.parent != self.catalog.root / bundle.instruction.mnemonic for bundle in self.instructions):
            raise ValueError(f"{self.catalog.source}: instruction bundle is outside its inventory root")
        if tuple(bundle.instruction.mnemonic for bundle in self.instructions) != self.catalog.declared:
            raise ValueError("instruction set differs from declared membership or order")


@dataclass(frozen=True, slots=True)
class SourceCatalog:
    """Declared instruction and EA source inventories."""

    instructions: ReferenceIndex[InstructionBundle]
    ea_modes: ReferenceIndex[EAMode]
    instruction_order: tuple[Reference[InstructionBundle], ...]
    base: InstructionSet
    extensions: Mapping[str, Extension]
    extension_catalog: DirectoryInventory

    def __post_init__(self) -> None:
        for name in ("instructions", "ea_modes"):
            if not isinstance(getattr(self, name), ReferenceIndex):
                object.__setattr__(self, name, ReferenceIndex(getattr(self, name)))
        object.__setattr__(self, "instruction_order", tuple(self.instruction_order))
        object.__setattr__(self, "extensions", MappingProxyType(dict(self.extensions)))
        inventory = self.extension_catalog
        if inventory.owner != "isa" or inventory.kind != "extension" or inventory.source != inventory.root / "extensions.yaml" or inventory.duplicates or inventory.missing or inventory.undeclared:
            raise ValueError(f"{inventory.source}: extension inventory identity and membership must agree")
        if self.base.catalog.owner != "base" or set(self.extensions) != set(self.extension_catalog.declared):
            raise ValueError("source catalog scopes differ from declared membership")
        if any(key != extension.metadata.id for key, extension in self.extensions.items()):
            raise ValueError("extension index key differs from its owner")
        if any(extension.metadata.root != self.extension_catalog.root / owner for owner, extension in self.extensions.items()):
            raise ValueError(f"{self.extension_catalog.source}: extension root differs from its declared member directory")
        sets = (self.base, *(self.extensions[name].instruction_set for name in self.extension_catalog.declared))
        members = {}
        for instruction_set in sets:
            for bundle in instruction_set.instructions:
                register_reference(members, bundle.reference, bundle)
        if self.instruction_order != tuple(members):
            raise ValueError("instruction order differs from source inventory order")
        if set(self.instructions) != set(members) or any(self.instructions[reference] is not member for reference, member in members.items()):
            raise ValueError("instruction index does not preserve canonical tree members")
        for extension in self.extensions.values():
            if tuple(item.metadata.id for item in extension.requires) != extension.metadata.requires:
                raise ValueError("resolved prerequisites differ from authored requirements")
            for required in extension.requires:
                if self.extensions.get(required.metadata.id) is not required:
                    raise ValueError("extension prerequisite is not the canonical extension")
        if any(reference != mode.reference for reference, mode in self.ea_modes.items()):
            raise ValueError("EA index key differs from member identity")
        families = {}
        family_members = {}
        for mode in self.ea_modes.values():
            family = mode.catalog
            if family.owner != "base" and family.owner not in self.extensions:
                raise ValueError(f"{family.source}: EA family owner is not a declared ISA scope")
            key = (family.owner, family.profile, family.mode_type)
            if families.setdefault(key, family) is not family:
                raise ValueError(f"{mode.source}: EA family must use one canonical catalog")
            family_members.setdefault(key, set()).add(mode.id)
        for key, family in families.items():
            if family_members[key] != set(family.modes):
                raise ValueError(f"{family.source}: loaded EA family differs from its declared modes")

    def bundle(
        self, value: str | Reference[InstructionBundle] | Path
    ) -> InstructionBundle:
        if isinstance(value, Reference):
            return self.instructions.resolve(value)

        candidate = Path(value)
        if candidate.exists():
            resolved = candidate.resolve()
            for bundle in self.instructions.values():
                directory = bundle.instruction.source.parent.resolve()
                if resolved == directory or resolved.is_relative_to(directory):
                    return bundle
            raise ValueError(f"path is outside every declared instruction: {resolved}")

        text = str(value)
        if "." in text:
            try:
                return self.instructions.resolve(Reference.parse(text))
            except UnknownReferenceError as error:
                raise ProjectLookupError(
                    ProjectLookupReason.UNKNOWN_INSTRUCTION, value
                ) from error

        matches = [
            bundle
            for bundle in self.instructions.values()
            if bundle.instruction.mnemonic == text
        ]
        if not matches:
            raise ProjectLookupError(ProjectLookupReason.UNKNOWN_INSTRUCTION, value)
        if len(matches) != 1:
            owners = ", ".join(bundle.owner for bundle in matches)
            raise ValueError(f"ambiguous instruction {text!r}: {owners}")
        return matches[0]
    def extension(self, extension_id: str) -> Extension:
        """Resolve one extension by its architectural ID."""

        try:
            return self.extensions[extension_id]
        except KeyError as error:
            raise ProjectLookupError(
                ProjectLookupReason.UNKNOWN_EXTENSION, extension_id
            ) from error
    def vector_diagram(self, value: str | Reference[VectorDiagram]) -> VectorDiagram:
        """Resolve one fully qualified instruction-owned vector diagram."""

        reference: Reference[VectorDiagram] = Reference.parse(value)
        if (
            len(reference.path) != 3
            or reference.path[0] != "instructions"
            or reference.path[2] != "diagrams"
        ):
            raise ValueError(
                "vector diagram references must have the form "
                "<owner>.instructions.<mnemonic>.diagrams.<id>"
            )
        instruction_reference: Reference[InstructionBundle] = Reference(
            reference.owner,
            ("instructions",),
            reference.path[1],
        )
        try:
            bundle = self.instructions.resolve(instruction_reference)
            return bundle.diagrams.diagrams.resolve(reference)
        except UnknownReferenceError as error:
            raise ValueError("unknown vector diagram reference") from error
    def select(
        self, targets: Iterable[str | Reference[InstructionBundle] | Path] = ()
    ) -> tuple[InstructionBundle, ...]:
        requested = tuple(targets)
        if not requested:
            return tuple(
                self.instructions.resolve(reference)
                for reference in self.instruction_order
            )
        selected: list[InstructionBundle] = []
        seen: set[Reference[InstructionBundle]] = set()
        for target in requested:
            bundle = self.bundle(target)
            if bundle.reference not in seen:
                selected.append(bundle)
                seen.add(bundle.reference)
        return tuple(selected)







def check_catalog(
    sources: SourceCatalog,
    selected: tuple[InstructionBundle, ...],
    *,
    complete: bool,
    field_types, payload_types, registers,
) -> Iterator[Diagnostic]:
    from engine.diagnostics import RelatedLocation, _error
    from engine.isa.encoding import UnknownConstraintValueError, resolve_encoding_form

    if complete:
        extension_catalog = sources.extension_catalog
        for missing in extension_catalog.missing:
            yield _error(
                "extension.missing-directory",
                extension_catalog.source,
                f"declared extension {missing!r} has no directory",
            )
        for undeclared in extension_catalog.undeclared:
            yield _error(
                "extension.undeclared-directory",
                extension_catalog.root / undeclared,
                f"extension directory {undeclared!r} is not in "
                f"{extension_catalog.source.name}",
            )
        for duplicate in extension_catalog.duplicates:
            yield _error(
                "extension.duplicate",
                extension_catalog.source,
                f"extension {duplicate!r} is listed more than once",
            )

        instruction_sets = (
            sources.base,
            *(
                extension.instruction_set
                for extension in sources.extensions.values()
            ),
        )
        for instruction_set in instruction_sets:
            catalog = instruction_set.catalog
            for missing in catalog.missing:
                yield _error(
                    "catalog.missing-directory",
                    catalog.source,
                    f"declared instruction {missing!r} has no directory",
                )
            for undeclared in catalog.undeclared:
                yield _error(
                    "catalog.undeclared-directory",
                    catalog.root / undeclared,
                    f"instruction directory {undeclared!r} is not in {catalog.source.name}",
                )
            for duplicate in catalog.duplicates:
                yield _error(
                    "catalog.duplicate",
                    catalog.source,
                    f"instruction {duplicate!r} is listed more than once",
                )

    selected_refs = {bundle.reference for bundle in selected}
    resolved_forms = []
    for bundle in sources.select():
        for form in bundle.encodings.forms:
            try:
                resolved = resolve_encoding_form(
                    bundle.instruction, form, field_types=field_types,
                    payload_types=payload_types, ea_modes=sources.ea_modes,
                    registers=registers,
                )
            except (ValueError, KeyError) as error:
                if bundle.reference in selected_refs:
                    yield _error("encoding.relation", bundle.encodings.source, str(error), "encodings", form.id)
                continue
            resolved_forms.append((bundle.reference, bundle.encodings.source, resolved))
    for collision in analyze_encoding_space(resolved_forms).collisions:
        left, right = collision.left, collision.right
        if left.reference not in selected_refs and right.reference not in selected_refs:
            continue
        yield _error(
            "encoding.overlap", left.source,
            f"{left.name} overlaps {right.name}",
            "encodings", left.form_id, "pattern",
            related=(RelatedLocation(
                right.source, f"conflicts with {right.name}",
                ("encodings", right.form_id, "pattern"),
            ),),
        )


class ProjectLookupReason(StrEnum):
    UNKNOWN_INSTRUCTION = "unknown_instruction"
    UNKNOWN_EXTENSION = "unknown_extension"


class ProjectLookupError(ValueError):
    def __init__(self, reason: ProjectLookupReason, value: object) -> None:
        self.reason = reason
        self.value = value
        super().__init__(f"{reason.value}: {value!r}")


def _load_instruction_set(
    owner: str,
    instruction_root: Path,
    *,
    types: TypeSystem,
    schemas: Mapping[str, object],
    required_cpuid_flags: tuple[CpuidField, ...],
    cpuid: CpuidCatalog,
) -> InstructionSet:
    inventory = load_instruction_inventory(owner=owner, root=instruction_root)
    bundles = []
    for mnemonic in inventory.declared:
        directory = instruction_root / mnemonic
        reference = Reference(owner, ("instructions",), mnemonic)
        instruction = load_instruction(directory / "instruction.yaml", schema=schemas["instruction"])
        flags = list(required_cpuid_flags)
        seen = {field.reference for field in flags}
        for reference_value in instruction.additional_cpuid_flags:
            field = cpuid.resolve_flag(reference_value, instruction.source)
            if field.reference in seen:
                raise RepeatedCpuidRequirementError(instruction.source, field)
            flags.append(field)
            seen.add(field.reference)
        encodings = load_encodings(
            directory / "encodings.yaml", schema=schemas["instruction-encodings"],
            field_types=types.field_types, payload_types=types.payload_types, cpuid=cpuid,
        )
        for form in encodings.forms:
            for field in form.additional_cpuid_flags:
                if field.reference in seen:
                    raise RepeatedCpuidRequirementError(encodings.source, field)
        diagrams = load_vector_diagrams(
            owner=owner, mnemonic=mnemonic, instruction=reference,
            root=directory / "diagrams", schema=schemas["vector-diagram"],
        )
        bundles.append(InstructionBundle(
            reference=reference, owner=owner, instruction=instruction,
            encodings=encodings, diagrams=diagrams,
            semantics=directory / "semantics.sail", description=directory / "descriptions.tex",
            required_cpuid_flags=tuple(flags),
        ))
    return InstructionSet(inventory, tuple(bundles))


def load_source_catalog(
    root: str | Path, *, extensions: DirectoryInventory, types: TypeSystem,
    ea_modes: ReferenceIndex[EAMode], cpuid: CpuidCatalog, schemas: Mapping[str, object],
) -> SourceCatalog:
    isa_root = Path(root).resolve()
    extension_roots = extension_owner_roots(extensions)[1:]
    metadata = {
        owner: load_extension_metadata(member_root / "extension.yaml", isa_root)
        for owner, member_root in extension_roots
    }
    requirements = resolve_extension_requirements(metadata, resolve_flag=cpuid.resolve_flag)
    base = _load_instruction_set(
        "base", isa_root / "instructions/definitions", types=types, schemas=schemas,
        required_cpuid_flags=(), cpuid=cpuid,
    )
    resolved: dict[str, Extension] = {}
    for owner, flags in requirements.items():
        definition = metadata[owner]
        members = _load_instruction_set(
            owner, definition.root / "instructions/definitions", types=types,
            schemas=schemas, required_cpuid_flags=flags, cpuid=cpuid,
        )
        resolved[owner] = Extension(
            metadata=definition, types=types.namespace(owner), instruction_set=members,
            requires=tuple(resolved[required] for required in definition.requires),
            required_cpuid_flags=flags,
        )
    declared_extensions = {owner: resolved[owner] for owner in extensions.declared}
    instructions = {}
    order = []
    for instruction_set in (base, *(value.instruction_set for value in declared_extensions.values())):
        for bundle in instruction_set.instructions:
            register_reference(instructions, bundle.reference, bundle)
            order.append(bundle.reference)
    return SourceCatalog(
        instructions=ReferenceIndex(instructions), ea_modes=ea_modes,
        instruction_order=tuple(order), base=base,
        extensions=MappingProxyType(declared_extensions), extension_catalog=extensions,
    )


def check_bundle(
    bundle: InstructionBundle, *, field_types, payload_types, ea_modes,
    registers, reservations,
) -> Iterator[Diagnostic]:
    from engine.isa.encoding import check_encoding_form
    from engine.isa.encoding_architecture import ENCODING_CLASSES_BY_WIDTH
    from engine.isa.encoding_reservations import reservation_cube

    reserved = tuple(
        (reservation, index, region.encoding_class, reservation_cube(region))
        for reservation in reservations.reservations.values()
        for index, region in enumerate(reservation.regions)
    )

    for companion, source in (("semantics", bundle.semantics), ("description", bundle.description)):
        if not source.is_file():
            yield _error(
                "artifact.missing", source,
                f"{bundle.instruction.mnemonic} has no required {companion} artifact",
            )
    for form in bundle.encodings.forms:
        base = ("encodings", form.id)
        owner = ENCODING_CLASSES_BY_WIDTH.get(form.pattern.bit_width)
        if owner is None:
            yield _error("encoding.class", bundle.encodings.source,
                         f"pattern width {form.pattern.bit_width} has no encoding class", *base, "pattern")
        else:
            raw = EncodingCube.from_encoding(form)
            if not any(EncodingCube.parse(pattern).contains(raw) for pattern in owner.namespace):
                yield _error("encoding.namespace", bundle.encodings.source,
                             f"pattern is outside the {owner.name} namespace", *base, "pattern")
            for reservation, index, class_name, cube in reserved:
                if class_name == owner.name and raw.overlaps(cube):
                    yield _error(
                        "encoding.reserved", bundle.encodings.source,
                        f"{bundle.instruction.mnemonic}.{form.id} overlaps opcode reservation {reservation.id}",
                        *base, "pattern", related=(RelatedLocation(
                            reservation.source, reservation.summary, ("regions", index, "prefix"),
                        ),),
                    )
        yield from check_encoding_form(
            bundle.instruction, form, field_types=field_types, payload_types=payload_types,
            ea_modes=ea_modes, registers=registers,
            source=bundle.encodings.source,
        )
