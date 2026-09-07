import tempfile
import unittest
from pathlib import Path

from engine.source.yaml import load_yaml


class SourceYamlTest(unittest.TestCase):
    def test_mapping_keys_must_be_unique_at_each_depth(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.yaml"
            for content in (
                "x: 1\nx: 2\n",
                "outer: {x: 1, x: 2}\n",
                "selected: {<<: {x: 1, x: 2}}\n",
            ):
                with self.subTest(content=content):
                    source.write_text(content)
                    with self.assertRaises(ValueError):
                        load_yaml(source)
            source.write_text("left: &shared {x: 1}\nright: *shared\n")
            value = load_yaml(source)
            self.assertIs(value["left"], value["right"])

    def test_root_alias_preserves_the_loaded_mapping_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.yaml"
            source.write_text("&root {self: *root}\n")

            value = load_yaml(source)

            self.assertIs(value["self"], value)

    def test_malformed_yaml_is_a_source_validation_error(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.yaml"
            source.write_text("x: [\n")
            with self.assertRaises(ValueError):
                load_yaml(source)

    def test_merge_override_is_not_an_authored_duplicate_key(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.yaml"
            source.write_text(
                "defaults: &defaults {left: 1, right: 2}\n"
                "layer: &layer {<<: *defaults, right: 3}\n"
                "selected: {<<: *layer}\n"
            )

            value = load_yaml(source)

            self.assertEqual(value["selected"], {"left": 1, "right": 3})
