from importlib import import_module
import unittest
from pathlib import PurePosixPath

ROOT_PAGE_KEY = import_module("artifacts.web-reference.site.model").ROOT_PAGE_KEY
NavigationGroup = import_module(
    "artifacts.web-reference.site.navigation"
).NavigationGroup
PageRegistry = import_module("artifacts.web-reference.site.navigation").PageRegistry
PageSpec = import_module("artifacts.web-reference.site.navigation").PageSpec
parse_latex_structure = import_module(
    "artifacts.web-reference.site.structure"
).parse_latex_structure
extract_visuals = import_module("artifacts.web-reference.site.visual").extract_visuals


class WebReferenceTest(unittest.TestCase):
    def test_visual_extraction_preserves_every_owned_label(self) -> None:
        source = "\n".join(
            (
                r"\begin{document}",
                r"\BedrockMakeTitlePage{Reference}{Testing}",
                r"\section{Example}",
                r"\label{page:example}",
                r"\begin{center}",
                r"\begin{tikzpicture}",
                r"\label{example:first}",
                r"\label{example:second}",
                r"\end{tikzpicture}",
                r"\end{center}",
                r"\end{document}",
            )
        )
        original = parse_latex_structure(source)

        transformed = parse_latex_structure(
            extract_visuals("reference", source, original).text
        )

        self.assertEqual(
            {label.name for label in transformed.labels},
            {label.name for label in original.labels},
        )

    def test_navigation_projects_each_owned_page_once(self) -> None:
        pages = []
        pages.append(PageSpec(ROOT_PAGE_KEY, "Home", PurePosixPath("index.md")))
        pages.append(
            PageSpec(
                "guide:landing",
                "Guide",
                PurePosixPath("guide/index.md"),
                group="guide",
            ),
        )
        pages.append(
            PageSpec(
                "guide:topic",
                "Topic",
                PurePosixPath("guide/topic.md"),
                group="guide",
                parent="guide:landing",
            ),
        )
        registry = PageRegistry(pages)
        groups = (NavigationGroup("guide", "Guide"),)

        def outputs(entries: list[dict[str, object]]) -> list[str]:
            projected: list[str] = []
            for entry in entries:
                value = next(iter(entry.values()))
                if isinstance(value, str):
                    projected.append(value)
                elif isinstance(value, list):
                    projected.extend(outputs(value))
                else:
                    self.fail(f"unexpected navigation entry: {entry!r}")
            return projected

        projected = outputs(registry.navigation(ROOT_PAGE_KEY, groups))
        owned = [page.output.as_posix() for page in registry.pages]

        self.assertEqual(sorted(projected), sorted(owned))


if __name__ == "__main__":
    unittest.main()
