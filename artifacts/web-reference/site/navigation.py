#!/usr/bin/env python3
"""Shared page, link, and navigation model for the generated reference site."""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType

IDENTIFIER_RE = re.compile(r"[a-z0-9][a-z0-9._:-]*")
ANCHOR_RE = re.compile(r"[a-z0-9][a-z0-9-]*")


class SiteError(ValueError):
    """The requested reference-site structure is ambiguous or unsafe."""


@dataclass(frozen=True)
class NavigationGroup:
    """One top-level reference-site navigation group."""

    key: str
    title: str


@dataclass(frozen=True)
class PageSpec:
    """One generated Markdown page and its navigation placement."""

    key: str
    title: str
    output: PurePosixPath
    group: str | None = None
    parent: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class LinkTarget:
    """A source semantic identifier resolved to a page or in-page anchor."""

    page: str
    anchor: str | None = None


def _identifier(value: str, where: str) -> str:
    if not IDENTIFIER_RE.fullmatch(value):
        raise SiteError(f"{where}: invalid identifier {value!r}")
    return value


def _output_path(value: PurePosixPath | str, where: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise SiteError(f"{where}: output must be a normalized relative path: {path}")
    if path.suffix != ".md":
        raise SiteError(f"{where}: page output must end in .md: {path}")
    return path


def _url_for_output(path: PurePosixPath) -> str:
    if path.name.casefold() == "index.md":
        logical = path.parent
    else:
        logical = path.with_suffix("")
    return "/" if not logical.parts else "/" + logical.as_posix()


def stable_anchor(target: str) -> str:
    """Derive one explicit web anchor from a stable semantic target."""
    anchor = re.sub(r"[^a-z0-9]+", "-", target.casefold()).strip("-")
    if not ANCHOR_RE.fullmatch(anchor):
        raise SiteError(f"link target {target!r}: cannot derive a stable anchor")
    return anchor


@dataclass(frozen=True, init=False)
class PageRegistry:
    """Own every generated page, summary location, and semantic link target."""

    _pages: Mapping[str, PageSpec]
    _targets: Mapping[str, LinkTarget]

    def __init__(
        self,
        page_values: Iterable[PageSpec],
        target_values: Iterable[tuple[str, LinkTarget]] = (),
    ) -> None:
        pages: dict[str, PageSpec] = {}
        targets: dict[str, LinkTarget] = {}
        outputs: dict[PurePosixPath, str] = {}
        urls: dict[str, str] = {}
        anchors: dict[tuple[str, str], str] = {}
        for page in page_values:
            key = _identifier(page.key, "page key")
            output = _output_path(page.output, f"page {key}")
            group = _identifier(page.group, f"page {key} group") if page.group else None
            parent = (
                _identifier(page.parent, f"page {key} parent") if page.parent else None
            )
            title = page.title.strip()
            if not title:
                raise SiteError(f"page {key}: title must not be empty")
            if key in pages:
                raise SiteError(f"duplicate page key {key!r}")
            if output in outputs:
                raise SiteError(
                    f"page {key}: output {output} is already owned by {outputs[output]}"
                )
            url = _url_for_output(output)
            if url in urls:
                raise SiteError(
                    f"page {key}: URL {url} is already owned by {urls[url]}"
                )
            normalized = PageSpec(
                key=key,
                title=title,
                output=output,
                group=group,
                parent=parent,
                source=page.source,
            )
            pages[key] = normalized
            outputs[output] = key
            urls[url] = key
        for name, target in target_values:
            page, anchor = target.page, target.anchor
            target_name = _identifier(name, "link target")
            page_key = _identifier(page, f"link target {target_name} page")
            if page_key not in pages:
                raise SiteError(f"link target {target_name}: unknown page {page_key!r}")
            if anchor is not None and not ANCHOR_RE.fullmatch(anchor):
                raise SiteError(f"link target {target_name}: invalid anchor {anchor!r}")
            if target_name in targets:
                previous = targets[target_name]
                raise SiteError(
                    f"duplicate link target {target_name!r}: {previous.page} and {page_key}"
                )
            if anchor is not None:
                owner = anchors.get((page_key, anchor))
                if owner is not None:
                    raise SiteError(
                        f"page {page_key}: anchor {anchor!r} is shared by "
                        f"{owner!r} and {target_name!r}"
                    )
                anchors[(page_key, anchor)] = target_name
            targets[target_name] = LinkTarget(page_key, anchor)

        object.__setattr__(self, "_pages", MappingProxyType(pages))
        object.__setattr__(self, "_targets", MappingProxyType(targets))

    @property
    def pages(self) -> tuple[PageSpec, ...]:
        return tuple(self._pages.values())

    @property
    def targets(self) -> dict[str, LinkTarget]:
        return dict(self._targets)

    def page(self, key: str) -> PageSpec:
        try:
            return self._pages[key]
        except KeyError as exc:
            raise SiteError(f"unknown page {key!r}") from exc

    def url(self, key: str) -> str:
        return _url_for_output(self.page(key).output)

    def relative_link(self, source_page: str, target_name: str) -> str:
        source = self.page(source_page)
        try:
            target = self._targets[target_name]
        except KeyError as exc:
            raise SiteError(f"unknown link target {target_name!r}") from exc
        destination = self.page(target.page)
        if source.key == destination.key and target.anchor:
            return f"#{target.anchor}"
        start = source.output.parent.as_posix() or "."
        relative = posixpath.relpath(destination.output.as_posix(), start=start)
        if target.anchor:
            relative += f"#{target.anchor}"
        return relative

    def relative_asset(self, source_page: str, asset: PurePosixPath | str) -> str:
        source = self.page(source_page)
        destination = PurePosixPath(asset)
        if (
            destination.is_absolute()
            or not destination.parts
            or any(part in {"", ".", ".."} for part in destination.parts)
        ):
            raise SiteError(
                f"page {source_page}: invalid site asset path {destination}"
            )
        start = source.output.parent.as_posix() or "."
        return posixpath.relpath(destination.as_posix(), start=start)

    def _validate_navigation(
        self, root: str, groups: tuple[NavigationGroup, ...]
    ) -> None:
        root_page = self.page(root)
        if root_page.group is not None or root_page.parent is not None:
            raise SiteError("root landing page must not belong to a group or parent")

        group_keys: set[str] = set()
        for group in groups:
            key = _identifier(group.key, "summary group key")
            if key in group_keys:
                raise SiteError(f"duplicate navigation group {key!r}")
            if not group.title.strip():
                raise SiteError(f"navigation group {key}: title must not be empty")
            group_keys.add(key)

        for page in self.pages:
            if page.key == root:
                continue
            if page.group not in group_keys:
                raise SiteError(
                    f"page {page.key}: group {page.group!r} is not declared"
                )
            if page.parent is None:
                continue
            parent = self.page(page.parent)
            if parent.group != page.group:
                raise SiteError(
                    f"page {page.key}: parent {parent.key} belongs to a different group"
                )

        parent_keys = {page.parent for page in self.pages if page.parent is not None}
        for parent_key in parent_keys:
            parent = self.page(parent_key)
            if parent.output.name.casefold() != "index.md":
                raise SiteError(
                    f"navigation parent {parent.key}: output must be an index.md page"
                )

        for page in self.pages:
            seen = {page.key}
            parent_key = page.parent
            while parent_key is not None:
                if parent_key in seen:
                    raise SiteError(f"page {page.key}: navigation parent cycle")
                seen.add(parent_key)
                parent_key = self.page(parent_key).parent

    def navigation(
        self, root: str, groups: Iterable[NavigationGroup]
    ) -> list[dict[str, object]]:
        """Return complete MkDocs navigation derived from the page registry."""
        normalized_groups = tuple(groups)
        self._validate_navigation(root, normalized_groups)
        children: dict[str | None, list[PageSpec]] = {}
        for page in self.pages:
            children.setdefault(page.parent, []).append(page)

        root_page = self.page(root)
        navigation: list[dict[str, object]] = [
            {root_page.title: root_page.output.as_posix()}
        ]

        def emit(page: PageSpec) -> dict[str, object]:
            descendants = children.get(page.key, [])
            if not descendants:
                return {page.title: page.output.as_posix()}
            entries: list[dict[str, object]] = [{"Overview": page.output.as_posix()}]
            entries.extend(emit(child) for child in descendants)
            return {page.title: entries}

        emitted: set[str] = {root}
        for group in normalized_groups:
            entries: list[dict[str, object]] = []
            for page in children.get(None, []):
                if page.key == root or page.group != group.key:
                    continue
                entries.append(emit(page))
            navigation.append({group.title: entries})

        def mark(page: PageSpec) -> None:
            if page.key in emitted:
                raise SiteError(f"page {page.key}: listed more than once")
            emitted.add(page.key)
            for child in children.get(page.key, []):
                mark(child)

        for page in children.get(None, []):
            if page.key == root:
                continue
            mark(page)
        missing = set(self._pages) - emitted
        if missing:
            raise SiteError(
                "pages are unreachable from site navigation: "
                + ", ".join(sorted(missing))
            )
        return navigation
