"""Read-only, namespace-aware opcode-space analysis."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import product
from pathlib import Path

from engine.isa.encoding import EncodingForm, ResolvedEncodingForm, ResolvedEaRead
from engine.isa.encoding_architecture import (
    ENCODING_CLASSES, EncodingClass, encoding_class, operator_space,
)
from engine.reference import Reference


class CandidateOutsideNamespaceError(ValueError):
    """An encoding candidate is outside its selected namespace."""

    def __init__(
        self,
        encoding_class: str,
        pattern: str,
        space: str | None,
    ) -> None:
        self.encoding_class = encoding_class
        self.pattern = pattern
        self.space = space
        scope = f"{encoding_class}/{space}" if space else encoding_class
        super().__init__(f"candidate {pattern} is outside {scope} namespace")




@dataclass(frozen=True, slots=True)
class EncodingCube:
    """A set of bit strings represented by fixed-bit mask and value."""

    width: int
    mask: int
    value: int

    @classmethod
    def parse(cls, pattern: str, width: int | None = None) -> "EncodingCube":
        normalized = pattern.replace("x", "?").replace("_", "").replace(" ", "")
        target_width = len(normalized) if width is None else width
        if not normalized or len(normalized) > target_width:
            raise ValueError(
                f"pattern must contain 1..{target_width} bits, got {pattern!r}"
            )
        if set(normalized) - set("01?"):
            raise ValueError(f"pattern may contain only 0, 1, ?, x, or _: {pattern!r}")
        normalized += "?" * (target_width - len(normalized))
        mask = 0
        value = 0
        for character in normalized:
            mask <<= 1
            value <<= 1
            if character in "01":
                mask |= 1
                value |= int(character)
        return cls(target_width, mask, value)

    @classmethod
    def from_encoding(cls, form: EncodingForm) -> "EncodingCube":
        return cls(
            form.pattern.bit_width, form.pattern.fixed_mask, form.pattern.fixed_value
        )

    @property
    def slots(self) -> int:
        return 1 << (self.width - self.mask.bit_count())

    @property
    def pattern(self) -> str:
        return "".join(
            str((self.value >> bit) & 1) if self.mask & (1 << bit) else "?"
            for bit in range(self.width - 1, -1, -1)
        )

    @property
    def first(self) -> int:
        return self.value

    @property
    def last(self) -> int:
        return self.value | (((1 << self.width) - 1) ^ self.mask)

    def overlaps(self, other: "EncodingCube") -> bool:
        if self.width != other.width:
            return False
        common = self.mask & other.mask
        return (self.value ^ other.value) & common == 0

    def contains(self, other: "EncodingCube") -> bool:
        return (
            self.width == other.width
            and self.mask & other.mask == self.mask
            and (self.value ^ other.value) & self.mask == 0
        )

    def matches(self, value: int) -> bool:
        return value & self.mask == self.value

    def intersection(self, other: "EncodingCube") -> "EncodingCube | None":
        if not self.overlaps(other):
            return None
        return EncodingCube(
            self.width, self.mask | other.mask, self.value | other.value
        )

    def split(self) -> tuple["EncodingCube", "EncodingCube"]:
        wildcard = next(
            (
                bit
                for bit in range(self.width - 1, -1, -1)
                if not self.mask & (1 << bit)
            ),
            None,
        )
        if wildcard is None:
            raise ValueError("cannot split a fixed encoding cube")
        mask = self.mask | (1 << wildcard)
        return (
            EncodingCube(self.width, mask, self.value),
            EncodingCube(self.width, mask, self.value | (1 << wildcard)),
        )


@dataclass(frozen=True, slots=True)
class EncodingSpaceEntry:
    """Raw reservation and constraint-filtered assignment for one form."""

    reference: Reference
    owner: str
    mnemonic: str
    form_id: str
    source: Path
    pattern: str
    raw_cubes: tuple[EncodingCube, ...]
    legal_cubes: tuple[EncodingCube, ...]


    def __post_init__(self) -> None:
        object.__setattr__(self, "raw_cubes", tuple(self.raw_cubes))
        object.__setattr__(self, "legal_cubes", tuple(self.legal_cubes))

    @property
    def width(self) -> int:
        return len(self.pattern)

    @property
    def name(self) -> str:
        return f"{self.mnemonic}.{self.form_id}"

    @property
    def raw_slots(self) -> int:
        return sum(cube.slots for cube in self.raw_cubes)

    @property
    def assigned_slots(self) -> int:
        return sum(cube.slots for cube in self.legal_cubes)

    @property
    def reclaimed_slots(self) -> int:
        return self.raw_slots - self.assigned_slots


@dataclass(frozen=True, slots=True)
class EncodingSpaceCollision:
    left: EncodingSpaceEntry
    right: EncodingSpaceEntry


@dataclass(frozen=True, slots=True)
class EncodingSpaceSummary:
    encoding_class: str
    width: int
    forms: int
    namespace_slots: int
    assigned_slots: int
    reclaimed_slots: int
    reserved_slots: int
    clean_free_slots: int
    remaining_slots: int


@dataclass(frozen=True, slots=True)
class EncodingSpaceHole:
    cube: EncodingCube

    @property
    def pattern(self) -> str:
        return self.cube.pattern

    @property
    def slots(self) -> int:
        return self.cube.slots


@dataclass(frozen=True, slots=True)
class CandidateCheck:
    encoding_class: str
    pattern: str
    slots: int
    assigned_slots: int
    reclaimed_slots: int
    reserved_slots: int
    clean_free_slots: int
    assigned_entries: tuple[EncodingSpaceEntry, ...]
    reclaimed_entries: tuple[EncodingSpaceEntry, ...]
    reservations: tuple[str, ...]


    def __post_init__(self) -> None:
        object.__setattr__(self, "assigned_entries", tuple(self.assigned_entries))
        object.__setattr__(self, "reclaimed_entries", tuple(self.reclaimed_entries))
        object.__setattr__(self, "reservations", tuple(self.reservations))

    @property
    def state(self) -> str:
        states = []
        if self.assigned_slots:
            states.append("assigned")
        if self.reclaimed_slots:
            states.append("reclaimed")
        if self.reserved_slots:
            states.append("reserved")
        if self.clean_free_slots:
            states.append("clean-free")
        return "+".join(states)


@dataclass(frozen=True, slots=True)
class EncodingSpaceMap:
    entries: tuple[EncodingSpaceEntry, ...]
    collisions: tuple[EncodingSpaceCollision, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))
        object.__setattr__(self, "collisions", tuple(self.collisions))



def analyze_encoding_space(
    forms: Iterable[tuple[Reference, Path, ResolvedEncodingForm]],
    *,
    classes: tuple[EncodingClass, ...] = ENCODING_CLASSES,
) -> EncodingSpaceMap:
    widths = {owner.pattern_bits for owner in classes}
    entries = tuple(entry for entry in entries_encoding_space(forms) if entry.width in widths)
    collisions = tuple(
        EncodingSpaceCollision(left, right)
        for index, left in enumerate(entries)
        for right in entries[index + 1 :]
        if entries_overlap(left, right)
    )
    return EncodingSpaceMap(entries, collisions)


def entries_encoding_space(
    forms: Iterable[tuple[Reference, Path, ResolvedEncodingForm]],
    class_name: str | None = None,
    *,
    space: str | None = None,
    leading: str | None = None,
    grep: str | None = None,
) -> tuple[EncodingSpaceEntry, ...]:
    entries = tuple(
        _entry(reference, source, form) for reference, source, form in forms
    )
    if class_name is None:
        return entries
    owner = encoding_class(class_name)
    regions = search_regions(owner, space=space, leading=leading)
    needle = grep.lower() if grep else None
    return tuple(
        entry
        for entry in entries
        if entry.width == owner.pattern_bits
        and any(raw.overlaps(region) for raw in entry.raw_cubes for region in regions)
        and (needle is None or needle in entry.name.lower())
    )


def summaries_encoding_space(
    forms: Iterable[tuple[Reference, Path, ResolvedEncodingForm]],
    *,
    reservations: Mapping[str, tuple[EncodingCube, ...]],
    classes: tuple[EncodingClass, ...] = ENCODING_CLASSES,
) -> tuple[EncodingSpaceSummary, ...]:
    entries = entries_encoding_space(forms)
    result = []
    for owner in classes:
        regions = _namespace_cubes(owner)
        selected = tuple(
            entry for entry in entries if entry.width == owner.pattern_bits
        )
        raw = tuple(cube for entry in selected for cube in entry.raw_cubes)
        legal = tuple(cube for entry in selected for cube in entry.legal_cubes)
        reserved = tuple(cube for cubes in reservations.values() for cube in cubes if cube.width == owner.pattern_bits)
        namespace_slots = sum(region.slots for region in regions)
        raw_slots = covered_slots(regions, raw)
        assigned_slots = covered_slots(regions, legal)
        unavailable_slots = covered_slots(regions, (*raw, *reserved))
        result.append(
            EncodingSpaceSummary(
                owner.name,
                owner.pattern_bits,
                len(selected),
                namespace_slots,
                assigned_slots,
                raw_slots - assigned_slots,
                unavailable_slots - raw_slots,
                namespace_slots - unavailable_slots,
                raw_slots - assigned_slots + namespace_slots - unavailable_slots,
            )
        )
    return tuple(result)


def holes_encoding_space(
    forms: Iterable[tuple[Reference, Path, ResolvedEncodingForm]],
    class_name: str,
    *,
    reservations: Mapping[str, tuple[EncodingCube, ...]],
    space: str | None = None,
    leading: str | None = None,
    include_reclaimed: bool = False,
    min_slots: int = 1,
    max_slots: int | None = None,
    limit: int = 32,
    sort: str = "address",
) -> tuple[EncodingSpaceHole, ...]:
    if min_slots <= 0 or (max_slots is not None and max_slots <= 0):
        raise ValueError("hole slot limits must be positive")
    if max_slots is not None and min_slots > max_slots:
        raise ValueError("--min-slots cannot exceed --max-slots")
    if limit <= 0:
        raise ValueError("--limit must be positive")
    owner = encoding_class(class_name)
    regions = search_regions(owner, space=space, leading=leading)
    entries = entries_encoding_space(forms, owner.name, space=space, leading=leading)
    unavailable = unavailable_cubes(
        entries,
        include_reclaimed=include_reclaimed,
        reservations=tuple(cube for cubes in reservations.values() for cube in cubes if cube.width == owner.pattern_bits),
    )
    cubes = [cube for region in regions for cube in _uncovered(region, unavailable)]
    if max_slots is not None:
        cubes = [piece for cube in cubes for piece in _cap_cube(cube, max_slots)]
    cubes = [cube for cube in cubes if cube.slots >= min_slots]
    if sort == "size":
        cubes.sort(key=lambda cube: (-cube.slots, cube.first))
    elif sort == "address":
        cubes.sort(key=lambda cube: cube.first)
    else:
        raise ValueError("hole sort must be 'address' or 'size'")
    return tuple(EncodingSpaceHole(cube) for cube in cubes[:limit])


def check_candidate_encoding_space(
    forms: Iterable[tuple[Reference, Path, ResolvedEncodingForm]],
    class_name: str,
    pattern: str,
    *,
    reservations: Mapping[str, tuple[EncodingCube, ...]],
    space: str | None = None,
) -> CandidateCheck:
    owner = encoding_class(class_name)
    candidate = EncodingCube.parse(pattern, owner.pattern_bits)
    regions = search_regions(owner, space=space)
    if covered_slots((candidate,), regions) != candidate.slots:
        raise CandidateOutsideNamespaceError(
            owner.name,
            candidate.pattern,
            space,
        )
    entries = entries_encoding_space(forms, owner.name, space=space)
    legal = tuple(cube for entry in entries for cube in entry.legal_cubes)
    raw = tuple(cube for entry in entries for cube in entry.raw_cubes)
    reservation_entries = tuple(
        name for name, cubes in reservations.items()
        if any(candidate.overlaps(cube) for cube in cubes)
    )
    reserved = tuple(
        cube for name in reservation_entries for cube in reservations[name]
        if candidate.overlaps(cube)
    )
    assigned_slots = covered_slots((candidate,), legal)
    raw_slots = covered_slots((candidate,), raw)
    unavailable_slots = covered_slots((candidate,), (*raw, *reserved))
    assigned_entries = tuple(
        entry
        for entry in entries
        if any(candidate.overlaps(cube) for cube in entry.legal_cubes)
    )
    reclaimed_entries = tuple(
        entry
        for entry in entries
        if covered_slots((candidate,), entry.raw_cubes)
        > covered_slots((candidate,), entry.legal_cubes)
    )
    return CandidateCheck(
        owner.name,
        candidate.pattern,
        candidate.slots,
        assigned_slots,
        raw_slots - assigned_slots,
        unavailable_slots - raw_slots,
        candidate.slots - unavailable_slots,
        assigned_entries,
        reclaimed_entries,
        reservation_entries,
    )


def _entry(reference: Reference, source: Path, resolved_form: ResolvedEncodingForm) -> EncodingSpaceEntry:
    form = resolved_form.form
    return EncodingSpaceEntry(
        reference, reference.owner, resolved_form.instruction.mnemonic,
        form.id, source, form.pattern.code,
        (EncodingCube.from_encoding(form),), form_cubes(resolved_form),
    )


def unavailable_cubes(
    entries: tuple[EncodingSpaceEntry, ...],
    *,
    include_reclaimed: bool,
    reservations: tuple[EncodingCube, ...] = (),
) -> tuple[EncodingCube, ...]:
    """Choose raw or legal occupancy once, then add authored reservation cubes."""
    assigned = tuple(
        cube for entry in entries
        for cube in (entry.legal_cubes if include_reclaimed else entry.raw_cubes)
    )
    return (*assigned, *reservations)


def _interval_cubes(width: int, lower: int, upper: int) -> tuple[EncodingCube, ...]:
    """Partition one inclusive interval into aligned binary-prefix cubes."""

    cubes = []
    cursor = lower
    while cursor <= upper:
        remaining = upper - cursor + 1
        alignment = cursor & -cursor if cursor else 1 << width
        size = min(alignment, 1 << (remaining.bit_length() - 1))
        wildcard_mask = size - 1
        cubes.append(
            EncodingCube(
                width,
                ((1 << width) - 1) ^ wildcard_mask,
                cursor,
            )
        )
        cursor += size
    return tuple(cubes)


def form_cubes(resolved_form: ResolvedEncodingForm) -> tuple[EncodingCube, ...]:
    """Lower the resolved legal field domains into disjoint opcode cubes."""
    ea_reads = {read.operand.name: read for read in resolved_form.layout if isinstance(read, ResolvedEaRead)}
    domains = []
    for field in resolved_form.fields:
        cubes = tuple(cube for lower, upper in field.ranges for cube in _interval_cubes(len(field.positions), lower, upper))
        if field.field.role in ea_reads:
            patterns = tuple({(alternative.pattern.fixed_mask, alternative.pattern.fixed_value) for alternative in ea_reads[field.field.role].alternatives})
            cubes = _compress_cubes(tuple(intersection for cube in cubes for mask, value in patterns if (intersection := cube.intersection(EncodingCube(len(field.positions), mask, value))) is not None))
        domains.append((field.positions, cubes))
    pattern = resolved_form.form.pattern
    result = []
    for assignments in product(*(cubes for _, cubes in domains)):
        mask, value = pattern.fixed_mask, pattern.fixed_value
        for (positions, _), cube in zip(domains, assignments, strict=True):
            for offset, position in enumerate(positions):
                bit = len(positions) - offset - 1
                if cube.mask & (1 << bit):
                    mask |= 1 << position
                    value = (value & ~(1 << position)) | (((cube.value >> bit) & 1) << position)
        result.append(EncodingCube(pattern.bit_width, mask, value))
    return _compress_cubes(tuple(result))


def forms_overlap(left: ResolvedEncodingForm, right: ResolvedEncodingForm) -> bool:
    return any(a.overlaps(b) for a in form_cubes(left) for b in form_cubes(right))


def entries_overlap(left: EncodingSpaceEntry, right: EncodingSpaceEntry) -> bool:
    return any(a.overlaps(b) for a in left.legal_cubes for b in right.legal_cubes)


def _compress_cubes(cubes: tuple[EncodingCube, ...]) -> tuple[EncodingCube, ...]:
    """Merge complete sibling pairs without enumerating the represented slots."""

    current = set(cubes)
    while True:
        consumed: set[EncodingCube] = set()
        merged: set[EncodingCube] = set()
        for cube in sorted(current, key=lambda item: (item.mask, item.value)):
            if cube in consumed:
                continue
            for bit in range(cube.width):
                flag = 1 << bit
                if not cube.mask & flag:
                    continue
                sibling = EncodingCube(cube.width, cube.mask, cube.value ^ flag)
                if sibling in current and sibling not in consumed:
                    consumed.update((cube, sibling))
                    merged.add(
                        EncodingCube(
                            cube.width,
                            cube.mask ^ flag,
                            cube.value & ~flag,
                        )
                    )
                    break
        if not merged:
            break
        current = (current - consumed) | merged
    return tuple(sorted(current, key=lambda item: (item.value, item.mask)))


def search_regions(
    owner: EncodingClass,
    *,
    space: str | None = None,
    leading: str | None = None,
) -> tuple[EncodingCube, ...]:
    regions = _namespace_cubes(owner)
    filters = []
    if space is not None:
        filters.append(
            EncodingCube.parse(
                operator_space(owner.name, space).prefix, owner.pattern_bits
            )
        )
    if leading is not None:
        filters.append(EncodingCube.parse(leading, owner.pattern_bits))
    for selected_filter in filters:
        regions = tuple(
            intersection
            for region in regions
            if (intersection := region.intersection(selected_filter)) is not None
        )
    if not regions:
        raise ValueError("selected prefix does not intersect the encoding namespace")
    return regions


def _namespace_cubes(owner: EncodingClass) -> tuple[EncodingCube, ...]:
    return tuple(EncodingCube.parse(pattern) for pattern in owner.namespace)


def covered_slots(
    regions: tuple[EncodingCube, ...], cubes: tuple[EncodingCube, ...]
) -> int:
    """Count the union of ``cubes`` clipped to disjoint search regions."""

    return sum(_covered(region, cubes) for region in regions)


def _covered(region: EncodingCube, cubes: tuple[EncodingCube, ...]) -> int:
    relevant = tuple(cube for cube in cubes if cube.overlaps(region))
    if not relevant:
        return 0
    if any(cube.contains(region) for cube in relevant):
        return region.slots
    if region.slots == 1:
        return 1
    left, right = region.split()
    return _covered(left, relevant) + _covered(right, relevant)


def _uncovered(
    region: EncodingCube, unavailable: tuple[EncodingCube, ...]
) -> tuple[EncodingCube, ...]:
    relevant = tuple(cube for cube in unavailable if cube.overlaps(region))
    if not relevant:
        return (region,)
    if any(cube.contains(region) for cube in relevant):
        return ()
    if region.slots == 1:
        return ()
    left, right = region.split()
    return (*_uncovered(left, relevant), *_uncovered(right, relevant))


def _cap_cube(cube: EncodingCube, max_slots: int) -> tuple[EncodingCube, ...]:
    if cube.slots <= max_slots:
        return (cube,)
    left, right = cube.split()
    return (*_cap_cube(left, max_slots), *_cap_cube(right, max_slots))
