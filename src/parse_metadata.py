"""
parse_metadata.py

Helpers for unzipping and parsing the three metadata formats the
ICPSR Object-Export API can return (per Section IV of the guide):
  - DCAT-US   (JSON,  http://www.w3.org/ns/dcat#)
  - MARCXML   (XML,   http://www.loc.gov/MARC21/slim)
  - Dublin Core (XML, http://www.purl.org/dc/terms/)

These are intentionally light-touch: they get you from "a ZIP full of
files" to "a list of dicts" so you can decide how to model the data
further. Exact field names/structure should be checked against a real
export once you have one, and against the ICPSR Metadata API Mappings
doc referenced in the guide.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from lxml import etree

MARC_NS = {"marc": "http://www.loc.gov/MARC21/slim"}
DC_NS = {"dc": "http://purl.org/dc/elements/1.1/", "dcterms": "http://purl.org/dc/terms/"}


def unzip_export(zip_path: Path, extract_to: Path) -> list[Path]:
    """Extract a downloaded export ZIP and return the list of extracted files."""
    extract_to.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(extract_to)
        return [extract_to / name for name in z.namelist() if not name.endswith("/")]


def parse_dcat_us(json_path: Path) -> list[dict[str, Any]]:
    """DCAT-US records are JSON; typically a 'dataset' array per the DCAT spec."""
    with open(json_path, "r") as f:
        data = json.load(f)
    if isinstance(data, dict) and "dataset" in data:
        return data["dataset"]
    if isinstance(data, list):
        return data
    return [data]


def parse_marcxml(xml_path: Path) -> list[dict[str, Any]]:
    """
    Flatten MARCXML <record> elements into simple dicts of
    {tag_subfieldcode: [values]} plus leader/control fields.
    """
    tree = etree.parse(str(xml_path))
    records = []
    for record_el in tree.findall(".//marc:record", MARC_NS):
        record: dict[str, Any] = {}
        for datafield in record_el.findall("marc:datafield", MARC_NS):
            tag = datafield.get("tag")
            for subfield in datafield.findall("marc:subfield", MARC_NS):
                code = subfield.get("code")
                key = f"{tag}_{code}"
                record.setdefault(key, []).append(subfield.text)
        records.append(record)
    return records


def parse_dublin_core(xml_path: Path) -> list[dict[str, Any]]:
    """Flatten Dublin Core <dc:*> / <dcterms:*> elements per record."""
    tree = etree.parse(str(xml_path))
    records = []
    # Adjust the record-level XPath once you see a real export's root structure.
    for record_el in tree.findall(".//record") or [tree.getroot()]:
        record: dict[str, Any] = {}
        for el in record_el.iter():
            local_tag = etree.QName(el).localname
            if el.text and el.text.strip():
                record.setdefault(local_tag, []).append(el.text.strip())
        records.append(record)
    return records


def parse_export(extracted_files: list[Path]) -> list[dict[str, Any]]:
    """Dispatch to the right parser based on file extension."""
    all_records: list[dict[str, Any]] = []
    for path in extracted_files:
        if path.suffix.lower() == ".json":
            all_records.extend(parse_dcat_us(path))
        elif path.suffix.lower() == ".xml":
            # Peek at the file to decide MARCXML vs Dublin Core
            with open(path, "r", errors="ignore") as f:
                head = f.read(2000)
            if "MARC21/slim" in head:
                all_records.extend(parse_marcxml(path))
            else:
                all_records.extend(parse_dublin_core(path))
    return all_records
