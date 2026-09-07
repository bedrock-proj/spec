"""Encoding source references resolve within the supplied canonical type index."""

import tempfile
import unittest
from pathlib import Path

import yaml

from engine.isa.cpuid import CpuidCatalog, CpuidCommonHeader
from engine.isa.encoding import load_encodings
from engine.reference import Reference, ReferenceIndex, UnknownReferenceError


class EncodingCatalogTest(unittest.TestCase):
    def test_unknown_field_type_reference_is_rejected(self):
        schema = yaml.safe_load((Path(__file__).parents[1] / "isa/schemas/instruction-encodings.yaml").read_text())
        document = {"encodings": {"rn_s": {
            "pattern": "0000000000ssss", "syntax": "OP Rn(s)",
            "fields": {"s": {"role": "src", "type": "base.field_types.MISSING"}},
        }}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "encodings.yaml"
            source.write_text(yaml.safe_dump(document))
            header = CpuidCommonHeader(Reference("base", ("cpuid",), "header"), root / "header.yaml", "header", 32, ())
            cpuid = CpuidCatalog(
                {}, header, *(ReferenceIndex({}) for _ in range(6)),
                ReferenceIndex({header.reference: header}), ReferenceIndex({}),
            )
            with self.assertRaises(UnknownReferenceError):
                load_encodings(source, schema=schema, field_types=ReferenceIndex({}), payload_types=ReferenceIndex({}), cpuid=cpuid)


if __name__ == "__main__":
    unittest.main()
