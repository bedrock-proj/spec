"""Authored reservations within architectural opcode spaces."""

from __future__ import annotations

from engine.source.inventory import inspect_inventory

from engine.isa.encoding_architecture import encoding_class
from engine.isa.encoding_space import EncodingCube

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import re
from types import MappingProxyType

from jsonschema.exceptions import ValidationError

from engine.diagnostics import DiagnosticBag, RelatedLocation, _error, render_diagnostics_text
from engine.source.inventory import DirectoryInventory
from engine.source.yaml import load_schema_yaml, load_yaml


_RESERVATION_ID_PATTERN = r"[A-Z][A-Z0-9_]*"


class EncodingReservationError(ValueError):
    """Reservation loading or publication failed with located diagnostics."""

    def __init__(self, diagnostics: DiagnosticBag):
        self.diagnostics = diagnostics
        super().__init__(render_diagnostics_text(diagnostics))


@dataclass(frozen=True, slots=True)
class EncodingReservationRegion:
    """One encoding-class prefix owned by a reservation purpose."""

    encoding_class: str
    prefix: str


@dataclass(frozen=True, slots=True)
class EncodingReservation:
    """One purpose that withholds one or more opcode regions."""

    source: Path
    root: Path
    id: str
    summary: str
    regions: tuple[EncodingReservationRegion, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "regions", tuple(self.regions))
        if any(not isinstance(region, EncodingReservationRegion) for region in self.regions):
            raise TypeError("reservation regions require EncodingReservationRegion values")


@dataclass(frozen=True, slots=True)
class EncodingReservationCatalog:
    """All opcode reservations owned by the base encoding architecture."""

    inventory: DirectoryInventory
    reservations: Mapping[str, EncodingReservation]

    def __post_init__(self) -> None:
        candidate = dict(self.reservations)
        diagnostics = check_encoding_reservations(self.inventory, candidate)
        if diagnostics.has_errors:
            raise EncodingReservationError(diagnostics)
        object.__setattr__(self, "reservations", MappingProxyType(candidate))



def _load_inventory(root: Path) -> DirectoryInventory:
    return inspect_inventory(
        owner="base",
        kind="reservation",
        source=root / "reservations.yaml",
        root=root,
        key="reservations",
        exact_keys=True,
        name_pattern=_RESERVATION_ID_PATTERN,
    )


def _load_reservation(root: Path, schema: Mapping[str, object]) -> EncodingReservation:
    source = root / "reservation.yaml"
    raw = load_schema_yaml(source, schema)
    reservation_id = root.name
    return EncodingReservation(
        source=source,
        root=root,
        id=reservation_id,
        summary=raw["summary"],
        regions=tuple(
            EncodingReservationRegion(
                encoding_class=region["encoding_class"],
                prefix=region["prefix"],
            )
            for region in raw["regions"]
        ),
    )


def check_encoding_reservations(
    inventory: DirectoryInventory,
    reservations: Mapping[str, EncodingReservation],
) -> DiagnosticBag:
    """Validate candidate membership and regions without reading source files."""
    diagnostics = []
    if inventory.source != inventory.root / "reservations.yaml":
        diagnostics.append(_error("encoding-reservation.inventory-source", inventory.source,
                                  "reservation inventory must be reservations.yaml in its collection root"))
    for index, name in enumerate(inventory.declared):
        if re.fullmatch(_RESERVATION_ID_PATTERN, name) is None:
            diagnostics.append(_error("encoding-reservation.identity", inventory.source,
                                      f"invalid reservation directory name {name!r}",
                                      "reservations", index))
    for name in inventory.actual:
        if re.fullmatch(_RESERVATION_ID_PATTERN, name) is None:
            diagnostics.append(_error("encoding-reservation.identity", inventory.root,
                                      f"invalid reservation directory name {name!r}"))
    for missing in inventory.missing:
        diagnostics.append(_error(
            "encoding-reservation.missing-directory",
            inventory.source,
            f"declared reservation {missing!r} has no directory",
            "reservations", inventory.declared.index(missing),
        ))
    for undeclared in inventory.undeclared:
        diagnostics.append(_error(
            "encoding-reservation.undeclared-directory",
            inventory.root / undeclared,
            f"reservation directory {undeclared!r} is not in {inventory.source.name}",
        ))
    for duplicate in inventory.duplicates:
        diagnostics.append(_error(
            "encoding-reservation.duplicate",
            inventory.source,
            f"reservation {duplicate!r} is listed more than once",
            "reservations", inventory.declared.index(duplicate),
        ))

    if inventory.owner != "base" or inventory.kind != "reservation":
        diagnostics.append(_error("encoding-reservation.owner", inventory.source,
                                  "reservation inventory must belong to base architecture"))
    for index, name in enumerate(inventory.declared):
        if name in inventory.actual and name not in reservations:
            diagnostics.append(_error("encoding-reservation.missing-member", inventory.source,
                                      f"reservation {name!r} has no loaded member", "reservations", index))
    for name in reservations:
        if name not in inventory.declared:
            diagnostics.append(_error("encoding-reservation.undeclared-member", inventory.source,
                                      f"loaded reservation {name!r} is not declared"))

    resolved: list[
        tuple[
            EncodingReservation,
            int,
            EncodingReservationRegion,
            EncodingCube,
        ]
    ] = []
    for key, reservation in reservations.items():
        if not isinstance(key, str):
            diagnostics.append(_error("encoding-reservation.identity", inventory.source,
                                      f"reservation key must be a string: {key!r}"))
            continue
        if not isinstance(reservation, EncodingReservation):
            diagnostics.append(_error("encoding-reservation.member-type", inventory.source,
                                      f"member {key!r} must be an EncodingReservation"))
            continue
        if key != reservation.id:
            diagnostics.append(_error("encoding-reservation.identity", reservation.source,
                                      f"member {reservation.id!r} is indexed as {key!r}"))
        if reservation.root != inventory.root / key or reservation.source != reservation.root / "reservation.yaml":
            diagnostics.append(_error("encoding-reservation.source", reservation.source,
                                      f"member {key!r} must own its declared reservation directory"))
        if not isinstance(reservation.summary, str) or not reservation.summary:
            diagnostics.append(_error("encoding-reservation.summary", reservation.source,
                                      "reservation summary must be nonempty", "summary"))
        if not reservation.regions:
            diagnostics.append(_error("encoding-reservation.regions", reservation.source,
                                      "reservation must contain at least one region", "regions"))
        for region_index, region in enumerate(reservation.regions):
            base = ("regions", region_index)
            if not isinstance(region.encoding_class, str):
                diagnostics.append(_error("encoding-reservation.class", reservation.source,
                                          "encoding class must be a string", *base,
                                          "encoding_class"))
                continue
            try:
                owner = encoding_class(region.encoding_class)
            except ValueError:
                diagnostics.append(_error(
                    "encoding-reservation.class",
                    reservation.source,
                    f"unknown encoding class {region.encoding_class!r}",
                    *base,
                    "encoding_class",
                ))
                continue
            try:
                cube = reservation_cube(region)
            except ValueError as error:
                diagnostics.append(_error(
                    "encoding-reservation.prefix",
                    reservation.source,
                    str(error),
                    *base,
                    "prefix",
                ))
                continue
            namespaces = tuple(
                EncodingCube.parse(pattern) for pattern in owner.namespace
            )
            if not any(namespace.contains(cube) for namespace in namespaces):
                diagnostics.append(_error(
                    "encoding-reservation.namespace",
                    reservation.source,
                    f"prefix is outside the {owner.name} namespace",
                    *base,
                    "prefix",
                ))
                continue
            resolved.append((reservation, region_index, region, cube))

    for index, (left, left_index, left_region, left_cube) in enumerate(resolved):
        for right, right_index, right_region, right_cube in resolved[index + 1 :]:
            if not left_cube.overlaps(right_cube):
                continue
            diagnostics.append(_error(
                "encoding-reservation.overlap",
                left.source,
                f"{left_region.encoding_class} region in reservation {left.id} "
                f"overlaps {right_region.encoding_class} region in reservation "
                f"{right.id}",
                "regions",
                left_index,
                "prefix",
                related=(
                    RelatedLocation(
                        right.source,
                        f"conflicting reservation {right.id}",
                        ("regions", right_index, "prefix"),
                    ),
                ),
            ))

    return DiagnosticBag(tuple(diagnostics))


def reservation_cube(region: EncodingReservationRegion) -> EncodingCube:
    """Lower one authored reservation prefix into its encoding-class cube."""

    owner = encoding_class(region.encoding_class)
    if not isinstance(region.prefix, str) or not region.prefix or any(bit not in "01" for bit in region.prefix):
        raise ValueError("reservation prefix must be a nonempty binary string")
    return EncodingCube.parse(region.prefix, owner.pattern_bits)


def reservation_cubes(catalog: EncodingReservationCatalog) -> Mapping[str, tuple[EncodingCube, ...]]:
    """Project validated reservation membership to cubes; order has no precedence."""
    return MappingProxyType({
        name: tuple(reservation_cube(region) for region in reservation.regions)
        for name, reservation in catalog.reservations.items()
    })


def load_encoding_reservations(isa_root: str | Path) -> EncodingReservationCatalog:
    root = Path(isa_root).resolve()
    reservations_root = root / "encoding/reservations"
    source = reservations_root / "reservations.yaml"
    try:
        inventory = _load_inventory(reservations_root)
        source = root / "schemas/encoding-reservation.yaml"
        schema = load_yaml(source)
        reservations = {}
        for reservation_id in inventory.declared:
            if reservation_id in reservations or reservation_id not in inventory.actual:
                continue
            member_root = reservations_root / reservation_id
            source = member_root / "reservation.yaml"
            reservations[reservation_id] = _load_reservation(member_root, schema)
    except (OSError, ValueError) as error:
        if isinstance(error.__cause__, ValidationError):
            cause = error.__cause__
            diagnostic = _error("encoding-reservation.schema", source, cause.message,
                                *tuple(cause.absolute_path))
        else:
            diagnostic = _error("encoding-reservation.io" if isinstance(error, OSError)
                                else "encoding-reservation.source", source, str(error))
        raise EncodingReservationError(DiagnosticBag((diagnostic,))) from error
    return EncodingReservationCatalog(inventory, reservations)
