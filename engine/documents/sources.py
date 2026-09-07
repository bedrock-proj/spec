"""One immutable occurrence graph for authored document sources."""
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from engine.documents.dependencies import DependencyEdge
from engine.documents.abi import ABI_DIRECTIVES
from engine.documents.fragment_expansion import SourceFragmentProjection, project_fragments, fragment_targets
from engine.documents.terminology import ResolvedTextReference, resolve_text_reference
from engine.entity import Entity
from engine.syntax.semantic_text import TermReferenceText, TextOrigin, parse_escape
from engine.syntax.tex import SourceDirective, SourceSpan, mask_tex_code

_SOURCE_START_RE = re.compile(r"\(:|\\input\b|\\BedrockGenerated[A-Za-z]+")
_DIRECTIVE_HEAD_RE = re.compile(r"\(:([a-z][a-z0-9_-]*)(?=:)")
_INPUT_RE = re.compile(r"\\input\s*\{([^{}]+)\}")
_BLOCK_DIRECTIVES = frozenset({
    "register-figure", "control-registers", "disclosures", "event-classes",
    "cpuid-leaf", "ea-diagram", "memory-record", "event-code", "diagram",
})

@dataclass(frozen=True, slots=True)
class SourceInputProjection:
    span: SourceSpan
    requested: str
    source: AuthoredSourceProjection

@dataclass(frozen=True, slots=True)
class SourceReferenceProjection:
    span: SourceSpan
    reference: ResolvedTextReference

@dataclass(frozen=True, slots=True)
class AuthoredSourceProjection:
    source: Path
    owner: Entity | Path | None
    raw: str
    parts: tuple[SourceInputProjection | SourceReferenceProjection | SourceFragmentProjection, ...]

    def __post_init__(self):
        object.__setattr__(self, "parts", tuple(self.parts))
        previous = 0
        for part in self.parts:
            if part.span.source != self.source or not previous <= part.span.start < part.span.end <= len(self.raw):
                raise ValueError(f"{self.source}: invalid or overlapping source occurrence span")
            previous = part.span.end

@dataclass(frozen=True, slots=True)
class SemanticSourceProjection(AuthoredSourceProjection):
    pass

@dataclass(frozen=True, slots=True)
class StyleSourceProjection(AuthoredSourceProjection):
    pass


def project_source(source, owner, source_root, catalogs, *, shared_result) -> AuthoredSourceProjection:
    requested_root = Path(source_root).absolute()
    root = shared_result((project_source, "root", requested_root), requested_root.resolve)
    snapshot = _source_snapshot(Path(source), root, shared_result)
    return _project_source(snapshot, owner, root, catalogs, (), shared_result)


def _source_snapshot(source: Path, root: Path, shared_result) -> tuple[Path, str]:
    requested = source.absolute()
    def resolved():
        path = requested.resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError(f"document source {requested} escapes source root {root}")
        def read_source():
            if not path.is_file():
                raise ValueError(f"cannot read document source {path}")
            return path.read_text(encoding="utf-8")
        raw = shared_result((project_source, "source", path), read_source)
        return path, raw
    return shared_result((project_source, "path", root, requested), resolved)


def _project_source(snapshot, owner, root, catalogs, active, shared_result):
    path, raw = snapshot
    if path in active:
        raise ValueError("cyclic document input: " + " -> ".join(str(item) for item in (*active, path)))
    key = (project_source, "projection", path, id(owner), root, tuple(sorted((name, id(value)) for name, value in catalogs.items())))

    def projected():
        lexical = mask_tex_code(raw)
        parts = []
        cursor = 0
        style = path.suffix == ".sty"
        while match := _SOURCE_START_RE.search(lexical, cursor):
            start = match.start()
            token = match.group()
            if token == r"\input":
                parsed = _INPUT_RE.match(lexical, start)
                if parsed is None:
                    raise ValueError(f"{path}, offset {start}: malformed document input")
                requested = parsed.group(1)
                relative = Path(requested)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError(f"{path}, offset {start}: document input must be root-relative")
                if relative.suffix == "":
                    relative = relative.with_suffix(".tex")
                cursor = parsed.end()
                span = SourceSpan(path, start, cursor)
                try:
                    included = _source_snapshot(root / relative, root, shared_result)
                    child = _project_source(included, owner, root, catalogs, (*active, path), shared_result)
                except (OSError, ValueError) as error:
                    raise ValueError(
                        f"{path}, offset {start}: document input {requested!r}: {error}"
                    ) from error
                parts.append(SourceInputProjection(span, requested, child))
            elif style:
                cursor = match.end()
            elif token.startswith(r"\BedrockGenerated"):
                raise ValueError(f"{path}, offset {start}: unresolved document macro {token!r}")
            else:
                head = _DIRECTIVE_HEAD_RE.match(lexical, start)
                if head is None:
                    raise ValueError(f"{path}, offset {start}: malformed document directive")
                end = lexical.find(":)", head.end())
                if end < 0:
                    raise ValueError(f"{path}, offset {start}: unterminated document directive")
                cursor = end + 2
                name = head.group(1)
                payload = lexical[head.end() + 1:end]
                span = SourceSpan(path, start, cursor)
                if name in {"ref", "term"}:
                    parsed = parse_escape(name, payload, TextOrigin(path), start, cursor)
                    try:
                        isa = catalogs["isa"]
                        resolved = resolve_text_reference(
                            parsed,
                            entities=isa.entities,
                            terms=isa.terminology.terms,
                        )
                    except (KeyError, ValueError) as error:
                        raise ValueError(
                            f"{path}, offset {start}: cannot resolve {name} directive: {error}"
                        ) from error
                    parts.append(SourceReferenceProjection(span, resolved))
                else:
                    if name in _BLOCK_DIRECTIVES or name in ABI_DIRECTIVES:
                        line_start, line_end = raw.rfind("\n", 0, start) + 1, raw.find("\n", cursor)
                        if line_end < 0:
                            line_end = len(raw)
                        if lexical[line_start:start].strip() or lexical[cursor:line_end].strip():
                            raise ValueError(f"{path}, offset {start}: {name} must occupy a standalone line")
                    parts.append(project_fragments(SourceDirective(span, name, tuple(payload.split(":"))), owner, catalogs))
        projection_type = StyleSourceProjection if style else SemanticSourceProjection
        result = projection_type(path, owner, raw, tuple(parts))
        # Retain the exact provider inputs for as long as their identity-keyed result.
        return result, tuple(catalogs.values())

    return shared_result(key, projected)[0]


def source_references(projection: AuthoredSourceProjection):
    return frozenset(edge.target for edge in source_dependencies(projection))


def source_occurrences(projection: AuthoredSourceProjection, input_chain=()):
    """Visit every use of a parsed node, retaining its original input provenance."""
    for part in projection.parts:
        if isinstance(part, SourceInputProjection):
            yield from source_occurrences(part.source, (*input_chain, part.span))
        else:
            yield projection, part, input_chain


def source_target_blocks(projection: AuthoredSourceProjection):
    for _, part, _ in source_occurrences(projection):
        if isinstance(part, SourceFragmentProjection):
            yield fragment_targets(part)


def source_dependencies(projection: AuthoredSourceProjection) -> tuple[DependencyEdge, ...]:
    edges = []
    for origin, part, _ in source_occurrences(projection):
        if isinstance(part, SourceReferenceProjection):
            source = origin.owner.reference if isinstance(origin.owner, Entity) else origin.owner or origin.source
            occurrence = part.reference.occurrence
            edges.append(DependencyEdge(source, occurrence.reference, "term" if isinstance(occurrence, TermReferenceText) else "reference", origin.source, part.span.start))
    return tuple(edges)
