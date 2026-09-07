"""Project document sources once for both source checks and site builds."""

from __future__ import annotations

from .model import DocumentSiteSpec, project_navigation, scoped_target
from .navigation import SiteError
from .structure import parse_latex_structure
from .visual import extract_visuals


def project_site(documents, instruction_groups):
    projected = []
    preserved_targets = set()
    for document_id, title, download, source in documents:
        text = source
        structure = parse_latex_structure(text)
        visualized = extract_visuals(document_id, text, structure)
        transformed = parse_latex_structure(visualized.text)
        original = {label.name for label in structure.labels}
        retained = {label.name for label in transformed.labels}
        if original - retained:
            raise SiteError(
                f"{document_id}: visual projection lost public labels: {sorted(original - retained)}"
            )
        preserved_targets.update(
            scoped_target(document_id, label) for label in retained
        )
        projected.append(
            DocumentSiteSpec(document_id, title, download, structure, text, visualized)
        )
    site = project_navigation(tuple(projected), instruction_groups)
    expected = {
        name
        for name, target in site.registry.targets.items()
        if target.anchor is not None
    }
    if expected - preserved_targets:
        raise SiteError(
            f"source anchor ownership mismatch: {sorted(expected - preserved_targets)}"
        )
    return site
