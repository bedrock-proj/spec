"""Distributed architectural-event registry loading and lookup."""

from __future__ import annotations

from engine.source.inventory import inspect_inventory

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import cast

from engine.diagnostics import Diagnostic
from engine.entity import Entity
from engine.isa.extensions import extension_owner_roots, load_extension_inventory
from engine.reference import Reference, ReferenceIndex, register_reference
from engine.source.inventory import DirectoryInventory
from engine.source.yaml import load_schema_yaml


@dataclass(frozen=True, slots=True)
class EventSelector:
    """How an event class interprets the low event-code bits."""

    kind: str
    bits: int


@dataclass(frozen=True, slots=True)
class ArchitecturalEvent(Entity):
    """One independently defined leaf architectural event."""

    reference: Reference["ArchitecturalEvent"]
    source: Path
    root: Path
    id: str
    name: str
    summary: str
    code: int | None
    family: str | None
    frame: str
    payload: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", tuple(self.payload))



@dataclass(frozen=True, slots=True)
class EventClass(Entity):
    """Common authored content of one event-code class fragment."""

    reference: Reference["EventClass"]
    source: Path
    root: Path
    id: str
    event_inventory: DirectoryInventory
    events: Mapping[str, ArchitecturalEvent]

    def __post_init__(self) -> None:
        object.__setattr__(self, "events", MappingProxyType(dict(self.events)))



@dataclass(frozen=True, slots=True)
class EventClassDefinition(EventClass):
    name: str
    value: int
    selector: EventSelector


@dataclass(frozen=True, slots=True)
class EventClassOverlay(EventClass):
    extends: Reference[EventClass]


@dataclass(frozen=True, slots=True)
class EventCode:
    """Resolved class/selector representation of one architectural event."""

    class_value: int
    selector: EventSelector
    event_selector: int | None

    @property
    def value(self) -> int | None:
        if self.event_selector is None:
            return None
        return compose_event_code(self.class_value, self.event_selector)


@dataclass(frozen=True, slots=True)
class ResolvedEvent:
    """Leaf event joined with its owner, overlay class, root class, and code."""

    owner: str
    event_class: EventClass
    root_class: EventClassDefinition
    event: ArchitecturalEvent
    code: EventCode

    def __post_init__(self) -> None:
        if self.event_class.reference.owner != self.owner or self.event.reference.owner != self.owner or self.event_class.events.get(self.event.id) is not self.event:
            raise ValueError(f"{self.event.source}: resolved event must be a canonical member of its owning class")


@dataclass(frozen=True, slots=True)
class EventNamespace:
    """All architectural events owned by base or one extension."""

    owner: str
    root: Path
    class_inventory: DirectoryInventory
    classes: Mapping[str, EventClass]

    def __post_init__(self) -> None:
        object.__setattr__(self, "classes", MappingProxyType(dict(self.classes)))



@dataclass(frozen=True, slots=True)
class EventCatalog:
    """The union of base and extension-owned event definitions."""

    namespaces: Mapping[str, EventNamespace]
    classes: ReferenceIndex[EventClass]
    events: ReferenceIndex[ArchitecturalEvent]



    def __post_init__(self) -> None:
        object.__setattr__(self, "namespaces", MappingProxyType(dict(self.namespaces)))
        classes = {}
        events = {}
        for owner, namespace in self.namespaces.items():
            if owner != namespace.owner:
                raise ValueError("event namespace key differs from its owner")
            for class_id, event_class in namespace.classes.items():
                if class_id != event_class.id or event_class.reference != Reference(owner, ("events",), class_id):
                    raise ValueError(f"{event_class.source}: event class identity differs from its namespace")
                register_reference(classes, event_class.reference, event_class)
                for event_id, event in event_class.events.items():
                    if event_id != event.id or event.reference != Reference(owner, ("events", class_id), event_id):
                        raise ValueError(f"{event.source}: event identity differs from its owning class")
                    register_reference(events, event.reference, event)
        for name, expected in (("classes", classes), ("events", events)):
            supplied = getattr(self, name)
            index = supplied if isinstance(supplied, ReferenceIndex) else ReferenceIndex(supplied)
            if set(index) != set(expected) or any(index[reference] is not member for reference, member in expected.items()):
                raise ValueError(f"event {name} index differs from its canonical tree")
            object.__setattr__(self, name, index)

    @property
    def base(self) -> EventNamespace:
        return self.namespaces["base"]

    def namespace(self, owner: str) -> EventNamespace:
        try:
            return self.namespaces[owner]
        except KeyError as error:
            raise ValueError(f"unknown event namespace {owner!r}") from error

    def resolved_events(
        self, owners: set[str] | frozenset[str] | None = None
    ) -> tuple[ResolvedEvent, ...]:
        """Return fully resolved leaf-event views in authored inventory order."""

        selected = frozenset(self.namespaces) if owners is None else frozenset(owners)
        roots, diagnostics = resolve_event_classes(self)
        if diagnostics:
            raise ValueError(diagnostics)
        result: list[ResolvedEvent] = []
        for owner, namespace in self.namespaces.items():
            if owner not in selected:
                continue
            for event_class in namespace.classes.values():
                root = roots.resolve(event_class.reference)
                for event in event_class.events.values():
                    result.append(
                        ResolvedEvent(
                            owner,
                            event_class,
                            root,
                            event,
                            EventCode(root.value, root.selector, event.code),
                        )
                    )
        return tuple(result)


def compose_event_code(class_value: int, selector: int) -> int:
    """Compose the fixed 8-bit class and 24-bit selector representation."""

    for name, value, limit in (
        ("class", class_value, 1 << 8),
        ("selector", selector, 1 << 24),
    ):
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value < limit
        ):
            raise ValueError(f"event {name} value {value!r} is out of range")
    return class_value << 24 | selector


def _load_namespace(
    owner: str,
    namespace_root: Path,
    isa_root: Path,
    references: dict,
) -> EventNamespace:
    classes_root = namespace_root / "events/classes"
    inventory = _load_inventory(owner, classes_root, "classes")
    classes: dict[str, EventClass] = {}
    for class_id in inventory.declared:
        class_root = classes_root / class_id
        if class_id in classes or not class_root.is_dir():
            continue
        event_class = _load_class(owner, class_root, isa_root, references)
        register_reference(references["classes"], event_class.reference, event_class)
        classes[class_id] = event_class
    return EventNamespace(owner, namespace_root, inventory, MappingProxyType(classes))


def _load_class(
    owner: str,
    root: Path,
    isa_root: Path,
    references: dict,
) -> EventClass:
    source = root / "class.yaml"
    raw = load_schema_yaml(source, isa_root / "schemas/event-class.yaml")
    class_id = root.name
    reference: Reference[EventClass] = Reference(owner, ("events",), class_id)
    events_root = root / "events"
    inventory = _load_inventory(owner, events_root, "events")
    events: dict[str, ArchitecturalEvent] = {}
    for event_id in inventory.declared:
        event_root = events_root / event_id
        if event_id in events or not event_root.is_dir():
            continue
        event = _load_event(owner, class_id, event_root, isa_root)
        register_reference(references["events"], event.reference, event)
        events[event_id] = event
    common = (
        reference,
        source,
        root,
        class_id,
        inventory,
        MappingProxyType(events),
    )
    if "extends" in raw:
        return EventClassOverlay(*common, Reference.parse(cast(str, raw["extends"])))
    selector_raw = cast(Mapping[str, object], raw["selector"])
    return EventClassDefinition(
        *common,
        cast(str, raw["name"]),
        cast(int, raw["value"]),
        EventSelector(cast(str, selector_raw["kind"]), cast(int, selector_raw["bits"])),
    )


def _load_event(
    owner: str, class_id: str, root: Path, isa_root: Path
) -> ArchitecturalEvent:
    source = root / "event.yaml"
    raw = load_schema_yaml(source, isa_root / "schemas/event.yaml")
    event_id = root.name
    return ArchitecturalEvent(
        reference=Reference(owner, ("events", class_id), event_id),
        source=source,
        root=root,
        id=event_id,
        name=cast(str, raw["name"]),
        summary=cast(str, raw["summary"]),
        code=cast(int | None, raw.get("code")),
        family=cast(str | None, raw.get("family")),
        frame=cast(str, raw["frame"]),
        payload=tuple(cast(list[str], raw.get("payload", ()))),
    )


def _load_inventory(owner: str, root: Path, key: str) -> DirectoryInventory:
    return inspect_inventory(
        owner=owner,
        kind={"classes": "class", "events": "event"}[key],
        source=root / f"{key}.yaml",
        root=root,
        key=key,
        allow_missing=True,
        name_pattern=r"[A-Z][A-Z0-9_]*",
    )




def check_events(catalog: EventCatalog) -> Iterator[Diagnostic]:
    yield from _events_validate_inventories(catalog)
    roots, diagnostics = resolve_event_classes(catalog)
    yield from diagnostics
    yield from _events_validate_class_values(catalog)
    yield from _events_validate_events(catalog, roots)


def _events_validate_inventories(catalog: EventCatalog) -> Iterator[Diagnostic]:
    from engine.diagnostics import _error

    for namespace in catalog.namespaces.values():
        inventories = [namespace.class_inventory]
        inventories.extend(
            event_class.event_inventory for event_class in namespace.classes.values()
        )
        for inventory in inventories:
            kind = inventory.kind
            for missing in inventory.missing:
                yield _error(
                    f"event.{kind}.missing-directory",
                    inventory.source,
                    f"declared event {kind} {missing!r} has no directory",
                )
            for undeclared in inventory.undeclared:
                yield _error(
                    f"event.{kind}.undeclared-directory",
                    inventory.root / undeclared,
                    f"event {kind} directory {undeclared!r} is not in "
                    f"{inventory.source.name}",
                )
            for duplicate in inventory.duplicates:
                yield _error(
                    f"event.{kind}.duplicate",
                    inventory.source,
                    f"event {kind} {duplicate!r} is listed more than once",
                )


def resolve_event_classes(
    catalog: EventCatalog,
) -> tuple[ReferenceIndex[EventClassDefinition], tuple[Diagnostic, ...]]:
    from engine.diagnostics import _error

    roots: dict[Reference[EventClass], EventClassDefinition] = {}
    diagnostics: list[Diagnostic] = []
    active: list[Reference[EventClass]] = []

    def resolve(event_class: EventClass) -> EventClassDefinition | None:
        if event_class.reference in roots:
            return roots[event_class.reference]
        if event_class.reference in active:
            cycle = (
                *active[active.index(event_class.reference) :],
                event_class.reference,
            )
            diagnostics.append(
                _error(
                    "event.class.extend-cycle",
                    event_class.source,
                    "circular event class overlay: "
                    + " -> ".join(catalog.classes.resolve(item).id for item in cycle),
                )
            )
            return None
        if isinstance(event_class, EventClassDefinition):
            roots[event_class.reference] = event_class
            return event_class
        if not isinstance(event_class, EventClassOverlay):
            diagnostics.append(_error("event.class.incomplete", event_class.source, "event class requires a numeric definition or an overlay target"))
            return None
        try:
            target = catalog.classes.resolve(event_class.extends)
        except ValueError:
            diagnostics.append(
                _error(
                    "event.class.unknown-extends",
                    event_class.source,
                    "unknown event class overlay target",
                    "extends",
                )
            )
            return None
        if event_class.id != target.id:
            diagnostics.append(
                _error(
                    "event.class.extend-id",
                    event_class.source,
                    f"class ID {event_class.id!r} does not match overlay target "
                    f"ID {target.id!r}",
                    "extends",
                )
            )
        active.append(event_class.reference)
        root = resolve(target)
        active.pop()
        if root is not None:
            roots[event_class.reference] = root
        return root

    for event_class in catalog.classes.values():
        resolve(event_class)
    return ReferenceIndex(roots), tuple(diagnostics)


def _events_validate_class_values(catalog: EventCatalog) -> Iterator[Diagnostic]:
    from engine.diagnostics import RelatedLocation, _error

    definitions = [
        event_class
        for event_class in catalog.classes.values()
        if isinstance(event_class, EventClassDefinition)
    ]
    for index, left in enumerate(definitions):
        for right in definitions[index + 1 :]:
            if left.value != right.value:
                continue
            yield _error(
                "event.class.value-overlap",
                left.source,
                f"class value 0x{left.value:02x} is also assigned to {right.id!r}",
                "value",
                related=(RelatedLocation(right.source, "conflicting class value"),),
            )


def _events_validate_events(
    catalog: EventCatalog,
    roots: Mapping[Reference[EventClass], EventClass],
) -> Iterator[Diagnostic]:
    from typing import Any

    from engine.diagnostics import RelatedLocation, _error

    assigned_codes: dict[tuple[Reference[EventClass], int], Any] = {}
    event_ids: dict[str, Any] = {}
    for event_class in catalog.classes.values():
        root = roots.get(event_class.reference)
        if not isinstance(root, EventClassDefinition):
            continue
        for event in event_class.events.values():
            previous_id = event_ids.get(event.id)
            if previous_id is not None:
                yield _error(
                    "event.id-overlap",
                    event.source,
                    f"event ID {event.id!r} is also used by {previous_id.id!r}",
                    "id",
                    related=(
                        RelatedLocation(previous_id.source, "conflicting event ID"),
                    ),
                )
            else:
                event_ids[event.id] = event

            if root.selector.kind == "fixed":
                if event.code is None:
                    yield _error(
                        "event.code.missing",
                        event.source,
                        f"event in fixed-selector class {root.id} requires a code",
                        "code",
                    )
                    continue
                if event.code >= 1 << root.selector.bits:
                    yield _error(
                        "event.code.range",
                        event.source,
                        f"event code 0x{event.code:x} exceeds the "
                        f"{root.selector.bits}-bit selector space",
                        "code",
                    )
                    continue
                key = (root.reference, event.code)
                previous = assigned_codes.get(key)
                if previous is not None:
                    yield _error(
                        "event.code.overlap",
                        event.source,
                        f"event code 0x{event.code:06x} is also assigned to "
                        f"{previous.id!r}",
                        "code",
                        related=(
                            RelatedLocation(previous.source, "conflicting event code"),
                        ),
                    )
                else:
                    assigned_codes[key] = event
            elif event.code is not None:
                yield _error(
                    "event.code.external-selector",
                    event.source,
                    f"{root.selector.kind}-selected event must not fix a code",
                    "code",
                )

            if event.frame == "basic" and event.payload:
                yield _error(
                    "event.payload.basic-frame",
                    event.source,
                    "basic event frame cannot carry an event payload",
                    "payload",
                )


def load_events(
    isa_root: str | Path,
    *,
    extensions: DirectoryInventory,
) -> "EventCatalog":
    root = Path(isa_root).resolve()
    references = {"classes": {}, "events": {}}
    namespaces: dict[str, EventNamespace] = {}
    for owner, namespace_root in extension_owner_roots(extensions):
        namespaces[owner] = _load_namespace(owner, namespace_root, root, references)
    return EventCatalog(
        namespaces=MappingProxyType(namespaces),
        classes=ReferenceIndex(references["classes"]),
        events=ReferenceIndex(references["events"]),
    )
