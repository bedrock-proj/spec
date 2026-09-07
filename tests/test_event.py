
from engine.isa.events import load_events
import unittest
from pathlib import Path

from engine.isa.events import EventCatalog, EventClassDefinition, compose_event_code, resolve_event_classes
from engine.isa.extensions import load_extension_inventory


class EventCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.isa_root = Path(__file__).parents[1] / "isa"
        cls.events = load_events(
            cls.isa_root, extensions=load_extension_inventory(cls.isa_root)
        )

    def test_class_overlays_resolve_to_numeric_roots(self) -> None:
        roots, diagnostics = resolve_event_classes(self.events)
        self.assertEqual(diagnostics, ())
        for event_class in self.events.classes.values():
            root = roots.resolve(event_class.reference)
            self.assertIsInstance(root, EventClassDefinition)

    def test_composes_event_codes(self) -> None:
        self.assertEqual(compose_event_code(0, 0x21), 0x00000021)
        self.assertEqual(compose_event_code(2, 7), 0x02000007)

    def test_resolved_events_join_roots_and_composed_codes(self) -> None:
        roots, diagnostics = resolve_event_classes(self.events)
        self.assertEqual(diagnostics, ())
        for resolved in self.events.resolved_events():
            self.assertIs(
                resolved.root_class,
                roots.resolve(resolved.event_class.reference),
            )
            self.assertEqual(resolved.code.class_value, resolved.root_class.value)
            self.assertEqual(resolved.code.event_selector, resolved.event.code)
            if resolved.event.code is None:
                self.assertIsNone(resolved.code.value)
            else:
                self.assertEqual(
                    resolved.code.value,
                    compose_event_code(resolved.code.class_value, resolved.event.code),
                )


if __name__ == "__main__":
    unittest.main()
