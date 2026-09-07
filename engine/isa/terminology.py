"""Distributed terminology groups and renderer-independent definitions."""

from __future__ import annotations

from engine.source.inventory import inspect_inventory

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from engine.diagnostics import Diagnostic
from engine.entity import Entity
from engine.isa.extensions import extension_owner_roots, load_extension_inventory
from engine.reference import Reference, ReferenceIndex, register_reference
from engine.source.inventory import DirectoryInventory
from engine.source.yaml import load_schema_yaml
from engine.syntax.semantic_text import SemanticText, TermForm, TextOrigin


@dataclass(frozen=True, slots=True)
class TermForms:
    canonical: str
    plural: str | None = None
    adjective: str | None = None

    def supports(self, form: TermForm, abbreviation: "TermAbbreviation | None") -> bool:
        if form is TermForm.CANONICAL:
            return True
        if form is TermForm.PLURAL:
            return self.plural is not None
        if form is TermForm.ADJECTIVE:
            return self.adjective is not None
        return abbreviation is not None


@dataclass(frozen=True, slots=True)
class TermAbbreviation:
    canonical: str


@dataclass(frozen=True, slots=True)
class TermRelations:
    broader: tuple[Reference["Term"], ...] = ()
    related: tuple[Reference["Term"], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "broader", tuple(self.broader))
        object.__setattr__(self, "related", tuple(self.related))



@dataclass(frozen=True, slots=True)
class Term(Entity):
    """One canonical terminology entry owned by a terminology group."""

    reference: Reference["Term"]
    group: Reference["TermGroup"]
    source: Path
    root: Path
    owner: str
    id: str
    forms: TermForms
    abbreviation: TermAbbreviation | None
    article: str | None
    definition: SemanticText
    variants: Mapping[str, str]
    relations: TermRelations

    def __post_init__(self) -> None:
        object.__setattr__(self, "variants", MappingProxyType(dict(self.variants)))



@dataclass(frozen=True, slots=True)
class TermGroup(Entity):
    """A semantic group rendered as a subsection by the current manual."""

    reference: Reference["TermGroup"]
    source: Path
    root: Path
    owner: str
    id: str
    title: str
    term_inventory: DirectoryInventory
    terms: Mapping[str, Term]

    def __post_init__(self) -> None:
        object.__setattr__(self, "terms", MappingProxyType(dict(self.terms)))
        if self.term_inventory.owner != self.owner or set(self.terms) != set(self.term_inventory.declared) & set(self.term_inventory.actual):
            raise ValueError(f"{self.term_inventory.source}: loaded terms differ from declared existing members")



@dataclass(frozen=True, slots=True)
class TerminologyNamespace:
    owner: str
    root: Path
    group_inventory: DirectoryInventory
    groups: Mapping[str, TermGroup]

    def __post_init__(self) -> None:
        object.__setattr__(self, "groups", MappingProxyType(dict(self.groups)))
        if self.group_inventory.owner != self.owner or set(self.groups) != set(self.group_inventory.declared) & set(self.group_inventory.actual):
            raise ValueError(f"{self.group_inventory.source}: loaded term groups differ from declared existing members")



@dataclass(frozen=True, slots=True)
class TermSpelling:
    reference: Reference[Term]
    form: str
    value: str


@dataclass(frozen=True, slots=True)
class TermCatalog:
    """The union of base and extension-owned terminology namespaces."""

    namespaces: Mapping[str, TerminologyNamespace]
    groups: ReferenceIndex[TermGroup]
    terms: ReferenceIndex[Term]
    spellings: Mapping[str, tuple[TermSpelling, ...]] = field(init=False)



    def __post_init__(self) -> None:
        object.__setattr__(self, "namespaces", MappingProxyType(dict(self.namespaces)))
        groups = {}
        terms = {}
        for owner, namespace in self.namespaces.items():
            if owner != namespace.owner:
                raise ValueError("terminology namespace key differs from its owner")
            for group_id, group in namespace.groups.items():
                if group_id != group.id or group.owner != owner or group.reference != Reference(owner, ("term_groups",), group_id):
                    raise ValueError(f"{group.source}: term group identity differs from its namespace")
                register_reference(groups, group.reference, group)
                for term_id, term in group.terms.items():
                    if term_id != term.id or term.owner != owner or term.group != group.reference or term.reference != Reference(owner, ("terms",), term_id):
                        raise ValueError(f"{term.source}: term identity differs from its owning group")
                    register_reference(terms, term.reference, term)
        for name, expected in (("groups", groups), ("terms", terms)):
            supplied = getattr(self, name)
            index = supplied if isinstance(supplied, ReferenceIndex) else ReferenceIndex(supplied)
            if set(index) != set(expected) or any(index[reference] is not member for reference, member in expected.items()):
                raise ValueError(f"terminology {name} index differs from its canonical tree")
            object.__setattr__(self, name, index)
        spellings: dict[str, list[TermSpelling]] = {}
        for term in self.terms.values():
            for spelling in _term_spellings(term):
                spellings.setdefault(_normalize_spelling(spelling.value), []).append(spelling)
        object.__setattr__(self, "spellings", MappingProxyType({key: tuple(values) for key, values in spellings.items()}))

    @property
    def base(self) -> TerminologyNamespace:
        return self.namespaces["base"]

    def namespace(self, owner: str) -> TerminologyNamespace:
        try:
            return self.namespaces[owner]
        except KeyError as error:
            raise ValueError(f"unknown terminology namespace {owner!r}") from error


def _load_namespace(
    owner: str,
    namespace_root: Path,
    schemas: Mapping[str, Path],
    references: dict,
) -> TerminologyNamespace:
    groups_root = namespace_root / "terminology/groups"
    inventory = _load_inventory(owner, "group", groups_root, "groups")
    groups: dict[str, TermGroup] = {}
    for group_id in inventory.declared:
        group_root = groups_root / group_id
        if group_id in groups or not group_root.is_dir():
            continue
        group = _load_group(owner, group_root, schemas, references)
        register_reference(references["groups"], group.reference, group)
        groups[group_id] = group
    return TerminologyNamespace(
        owner, namespace_root, inventory, MappingProxyType(groups)
    )


def _load_group(
    owner: str,
    root: Path,
    schemas: Mapping[str, Path],
    references: dict,
) -> TermGroup:
    source = root / "group.yaml"
    raw = load_schema_yaml(source, schemas["group"])
    group_id = root.name
    group_reference: Reference[TermGroup] = Reference(owner, ("term_groups",), group_id)
    terms_root = root / "terms"
    inventory = _load_inventory(owner, "term", terms_root, "terms")
    terms: dict[str, Term] = {}
    for term_id in inventory.declared:
        term_root = terms_root / term_id
        if term_id in terms or not term_root.is_dir():
            continue
        term = _load_term(owner, group_reference, term_root, schemas["term"])
        register_reference(references["terms"], term.reference, term)
        terms[term_id] = term
    return TermGroup(
        group_reference,
        source,
        root,
        owner,
        group_id,
        raw["title"],
        inventory,
        MappingProxyType(terms),
    )


def _load_term(
    owner: str, group: Reference[TermGroup], root: Path, schema: Path
) -> Term:
    source = root / "term.yaml"
    raw = load_schema_yaml(source, schema)
    term_id = root.name
    display = raw["display"]
    abbreviation = raw.get("abbreviation")
    relations = raw.get("relations", {})
    return Term(
        reference=Reference(owner, ("terms",), term_id),
        group=group,
        source=source,
        root=root,
        owner=owner,
        id=term_id,
        forms=TermForms(
            display["canonical"], display.get("plural"), display.get("adjective")
        ),
        abbreviation=(
            TermAbbreviation(abbreviation["canonical"])
            if abbreviation is not None
            else None
        ),
        article=raw.get("article"),
        definition=SemanticText.parse(
            raw["definition"], origin=TextOrigin(source, ("definition",))
        ),
        variants=MappingProxyType(dict(raw.get("variants", {}))),
        relations=TermRelations(
            tuple(Reference.parse(item) for item in relations.get("broader", ())),
            tuple(Reference.parse(item) for item in relations.get("related", ())),
        ),
    )


def _load_inventory(owner: str, kind: str, root: Path, key: str) -> DirectoryInventory:
    return inspect_inventory(
        owner=owner,
        kind=kind,
        source=root / f"{key}.yaml",
        root=root,
        key=key,
        allow_missing=True,
        name_pattern=r"[a-z][a-z0-9_]*",
    )


def _term_spellings(term: Term) -> tuple[TermSpelling, ...]:
    values = [TermSpelling(term.reference, "canonical", term.forms.canonical)]
    for name, value in (
        ("plural", term.forms.plural),
        ("adjective", term.forms.adjective),
    ):
        if value is not None:
            values.append(TermSpelling(term.reference, name, value))
    if term.abbreviation is not None:
        values.append(
            TermSpelling(term.reference, "short", term.abbreviation.canonical)
        )
    values.extend(
        TermSpelling(term.reference, f"variant:{name}", value)
        for name, value in term.variants.items()
    )
    return tuple(values)


def _normalize_spelling(value: str) -> str:
    return re.sub(r"[\s-]+", " ", value.casefold()).strip()


def check_terminology(catalog: TermCatalog) -> Iterator[Diagnostic]:
    yield from _terminology_validate_inventories(catalog)
    yield from _terminology_validate_references(catalog)
    yield from _terminology_validate_spellings(catalog)
    yield from _terminology_validate_broader_relations(catalog)


def _terminology_validate_inventories(catalog: TermCatalog) -> Iterator[Diagnostic]:
    for namespace in catalog.namespaces.values():
        yield from _terminology_validate_inventory(namespace.group_inventory)
        for group in namespace.groups.values():
            yield from _terminology_validate_inventory(group.term_inventory)


def _terminology_validate_inventory(
    inventory: DirectoryInventory,
) -> Iterator[Diagnostic]:
    from engine.diagnostics import _error

    for missing in inventory.missing:
        yield _error(
            f"terminology.{inventory.kind}.missing-directory",
            inventory.source,
            f"declared terminology {inventory.kind} {missing!r} has no directory",
        )
    for undeclared in inventory.undeclared:
        yield _error(
            f"terminology.{inventory.kind}.undeclared-directory",
            inventory.root / undeclared,
            f"terminology {inventory.kind} directory {undeclared!r} is not in "
            f"{inventory.source.name}",
        )
    for duplicate in inventory.duplicates:
        yield _error(
            f"terminology.{inventory.kind}.duplicate",
            inventory.source,
            f"terminology {inventory.kind} {duplicate!r} is listed more than once",
        )


def _terminology_validate_references(catalog: TermCatalog) -> Iterator[Diagnostic]:
    from engine.diagnostics import _error
    from engine.syntax.semantic_text import TermReferenceText

    terms = catalog.terms
    for term in terms.values():
        for relation_name, references in (
            ("broader", term.relations.broader),
            ("related", term.relations.related),
        ):
            for index, reference in enumerate(references):
                if reference not in terms:
                    yield _error(
                        "terminology.relation.unknown",
                        term.source,
                        f"{relation_name} relation names an unknown term",
                        "relations",
                        relation_name,
                        index,
                    )
        for part in term.definition.parts:
            if not isinstance(part, TermReferenceText):
                continue
            if part.reference not in terms:
                yield _error(
                    "terminology.definition.unknown-term",
                    term.source,
                    "definition names an unknown term",
                    "definition",
                )
                continue
            target = terms.resolve(part.reference)
            if not target.forms.supports(part.form, target.abbreviation):
                yield _error(
                    "terminology.definition.unavailable-form",
                    term.source,
                    f"term {target.id!r} does not define form {part.form.value!r}",
                    "definition",
                )


def _terminology_validate_spellings(catalog: TermCatalog) -> Iterator[Diagnostic]:
    from engine.diagnostics import RelatedLocation, _error

    for spellings in catalog.spellings.values():
        references = {item.reference for item in spellings}
        if len(references) < 2:
            continue
        first = catalog.terms[spellings[0].reference]
        conflict = next(item for item in spellings if item.reference != first.reference)
        conflicting = catalog.terms[conflict.reference]
        yield _error(
            "terminology.spelling.conflict",
            conflicting.source,
            f"spelling {conflict.value!r} is also owned by {first.id!r}",
            related=(
                RelatedLocation(first.source, "conflicting terminology spelling"),
            ),
        )


def _terminology_validate_broader_relations(
    catalog: TermCatalog,
) -> Iterator[Diagnostic]:
    from engine.diagnostics import _error

    terms = catalog.terms
    complete: set[Reference[Term]] = set()
    active: list[Reference[Term]] = []

    def visit(term: Term) -> Iterator[Diagnostic]:
        if term.reference in complete:
            return
        if term.reference in active:
            start = active.index(term.reference)
            cycle = (*active[start:], term.reference)
            yield _error(
                "terminology.relation.broader-cycle",
                term.source,
                "circular broader relation: "
                + " -> ".join(terms.resolve(item).id for item in cycle),
                "relations",
                "broader",
            )
            return
        active.append(term.reference)
        for reference in term.relations.broader:
            if reference in terms:
                yield from visit(terms.resolve(reference))
        active.pop()
        complete.add(term.reference)

    for term in terms.values():
        yield from visit(term)


def load_terminology(
    isa_root: str | Path,
    *,
    extensions: DirectoryInventory,
) -> "TermCatalog":
    root = Path(isa_root).resolve()
    schemas = {
        "group": root / "schemas/terminology-group.yaml",
        "term": root / "schemas/term.yaml",
    }
    references = {"groups": {}, "terms": {}}
    namespaces: dict[str, TerminologyNamespace] = {}
    for owner, namespace_root in extension_owner_roots(extensions):
        namespaces[owner] = _load_namespace(
            owner, namespace_root, schemas, references
        )
    return TermCatalog(
        namespaces=MappingProxyType(namespaces),
        groups=ReferenceIndex(references["groups"]),
        terms=ReferenceIndex(references["terms"]),
    )
