"""
download_export.py

CLI entry point: run one of the sample queries (config/sample_query.json)
against the ICPSR Object-Export API and download the resulting ZIP of
metadata records into data/raw/.

Usage:
    python src/download_export.py --query all_studies
    python src/download_export.py --query single_identifier --outfile my_export.zip
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

from icpsr_client import ICPSRClient, ICPSRAPIError

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_QUERY_PATH = ROOT / "config" / "sample_query.json"
RAW_DATA_DIR = ROOT / "data" / "raw"


def load_queries() -> dict:
    with open(SAMPLE_QUERY_PATH, "r") as f:
        return json.load(f)


def download_zip(location_url: str, outfile: Path) -> None:
    outfile.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(location_url, stream=True)
    resp.raise_for_status()
    with open(outfile, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an ICPSR Object-Export query and download results.")
    parser.add_argument(
        "--query",
        required=True,
        help="Name of the query in config/sample_query.json (e.g. all_studies, single_identifier).",
    )
    parser.add_argument(
        "--outfile",
        default=None,
        help="Output filename under data/raw/ (defaults to '<query>.zip').",
    )
    args = parser.parse_args()

    queries = load_queries()
    if args.query not in queries:
        available = [k for k in queries if not k.startswith("_")]
        print(f"Unknown query '{args.query}'. Available: {available}", file=sys.stderr)
        return 1

    payload = queries[args.query]
    outfile = RAW_DATA_DIR / (args.outfile or f"{args.query}.zip")

    client = ICPSRClient()

    print(f"Submitting query '{args.query}'...")
    try:
        result = client.run_query(payload)
    except ICPSRAPIError as e:
        print(f"API error: {e}", file=sys.stderr)
        return 1
    except TimeoutError as e:
        print(str(e), file=sys.stderr)
        return 1

    print(f"Status: {result.get('status')}")
    print(f"Message: {result.get('message')}")

    location = result.get("location")
    if not location:
        print("No download location returned (likely 0 matching records).")
        return 0

    print(f"Downloading to {outfile}...")
    download_zip(location, outfile)
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
