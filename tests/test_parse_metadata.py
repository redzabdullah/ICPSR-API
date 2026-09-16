import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from src.parse_metadata import (
    parse_dcat_us,
    parse_dublin_core,
    parse_marcxml,
    unzip_export,
)


class ParseMetadataTests(unittest.TestCase):
    def test_dcat_catalog_dataset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "record.json"
            path.write_text(json.dumps({"dataset": [{"identifier": "5512"}]}), encoding="utf-8")
            self.assertEqual(parse_dcat_us(path), [{"identifier": "5512"}])

    def test_marcxml_includes_leader_control_and_data_fields(self):
        xml = """<collection xmlns="http://www.loc.gov/MARC21/slim">
          <record><leader>00000nam a2200000 a 4500</leader>
          <controlfield tag="001">5512</controlfield>
          <datafield tag="245"><subfield code="a">Example title</subfield></datafield>
          </record></collection>"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "record.xml"
            path.write_text(xml, encoding="utf-8")
            record = parse_marcxml(path)[0]
            self.assertEqual(record["control_001"], ["5512"])
            self.assertEqual(record["245_a"], ["Example title"])
            self.assertIn("leader", record)

    def test_dublin_core_handles_namespaced_record(self):
        xml = """<root xmlns:oai="urn:oai" xmlns:dc="http://purl.org/dc/elements/1.1/">
          <oai:record><dc:title>Example</dc:title><dc:subject>History</dc:subject></oai:record>
          </root>"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "record.xml"
            path.write_text(xml, encoding="utf-8")
            self.assertEqual(parse_dublin_core(path), [{"title": ["Example"], "subject": ["History"]}])

    def test_zip_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("../outside.txt", "unsafe")
            with self.assertRaises(ValueError):
                unzip_export(archive, Path(tmp) / "output")


if __name__ == "__main__":
    unittest.main()
