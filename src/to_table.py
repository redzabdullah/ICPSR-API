"""
to_table.py

Unzip a downloaded ICPSR export and flatten it into a CSV you can open
in Excel, pandas, etc. Prints a structural preview first, since this is
the first time we're seeing a real export and parse_metadata.py's
assumptions about DCAT-US/MARCXML/Dublin Core shape were written before
we had a real file to check against.

Usage:
    python src/to_table.py --zip data/raw/single_identifier.zip
    python src/to_table.py --zip data/raw/single_identifier.zip --outfile my_table.csv
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

import pandas as pd

from parse_metadata import unzip_export, parse_export

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"


def preview_structure(zip_path: Path) -> None:
    """Print what's actually inside the ZIP and a peek at the first
    file's raw structure, so we can sanity-check parse_metadata.py's
    assumptions against a real export."""
    print(f"\n--- Structure preview: {zip_path.name} ---")
    with zipfile.ZipFile(zip_path, "r") as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        print(f"Files inside: {names}")

        for name in names:
            if name.lower().endswith(".json"):
                with z.open(name) as f:
                    data = json.load(f)
                print(f"\nTop-level keys in {name}: {list(data.keys()) if isinstance(data, dict) else type(data)}")
                # Show a trimmed preview, not the whole thing
                preview = json.dumps(data, indent=2)[:1500]
                print(f"Preview:\n{preview}\n...")
            elif name.lower().endswith(".xml"):
                with z.open(name) as f:
                    head = f.read(1500).decode("utf-8", errors="ignore")
                print(f"\nPreview of {name} (first 1500 chars):\n{head}\n...")


def main() -> int:
    parser = argparse.ArgumentParser(description="Flatten a downloaded ICPSR export ZIP into a CSV table.")
    parser.add_argument("--zip", required=True, help="Path to the export ZIP, relative to project root or absolute.")
    parser.add_argument("--outfile", default=None, help="Output CSV filename under data/processed/ (defaults to '<zipname>.csv').")
    args = parser.parse_args()

    zip_path = Path(args.zip)
    if not zip_path.is_absolute():
        zip_path = ROOT / zip_path
    if not zip_path.exists():
        print(f"ZIP not found: {zip_path}", file=sys.stderr)
        return 1

    preview_structure(zip_path)

    extract_dir = PROCESSED_DIR / zip_path.stem
    extracted_files = unzip_export(zip_path, extract_dir)
    records = parse_export(extracted_files)

    if not records:
        print("\nNo records parsed -- the real export structure likely differs from "
              "what parse_metadata.py assumes. Check the structure preview above "
              "and we'll adjust the parser to match.")
        return 1

    df = pd.json_normalize(records)
    outfile = PROCESSED_DIR / (args.outfile or f"{zip_path.stem}.csv")
    outfile.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(outfile, index=False)

    print(f"\nParsed {len(records)} record(s) into {len(df.columns)} column(s).")
    print(f"Saved to: {outfile}")
    print(f"\nColumn names: {list(df.columns)}")
    print(f"\nFirst row preview:\n{df.head(1).to_string()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
