"""
bridge_api.py

FastAPI bridge exposing a simplified /search endpoint for the ICPSR
Object-Export API, designed to be called by a ChatGPT Custom GPT
Action.

ChatGPT sends plain fields (subject, geography, PI, funding org,
title, date range, identifiers) -- it never needs to know ICPSR's
exact filter syntax. This service does all the translation into the
real where-clause shape, executes the two-step async query via
src/icpsr_client.py, downloads the resulting ZIP, parses it, and
returns clean flattened JSON. ChatGPT can't unzip files, so the ZIP
never leaves this service.

Auth: every request must include header `X-Bridge-Key` matching the
BRIDGE_API_KEY environment variable, so this public endpoint can't be
used to burn your ICPSR API quota by anyone who finds the URL.

Run locally:
    uvicorn bridge_api:app --reload

Deploy on Render:
    Start command: uvicorn bridge_api:app --host 0.0.0.0 --port $PORT
    Env vars: ICPSR_API_KEY, ICPSR_API_SECRET, BRIDGE_API_KEY
    (ICPSR_GATEWAY_URL / ICPSR_API_PATH / ICPSR_OAUTH_PATH / ICPSR_OAUTH_SCOPE
     are optional -- they default correctly in icpsr_client.py)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

import requests
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from src.icpsr_client import ICPSRAPIError, ICPSRClient
from src.parse_metadata import parse_export, unzip_export

app = FastAPI(
    title="ICPSR Search Bridge",
    description="Plain-language search over the ICPSR Object-Export API, for use as a ChatGPT Action.",
    version="1.0.0",
)


class SearchRequest(BaseModel):
    subject: Optional[str] = Field(None, description="Topic or subject area, e.g. 'labor migration'")
    geography: Optional[str] = Field(None, description="Place name, e.g. 'Southeast Asia' or 'Detroit'")
    principal_investigator: Optional[str] = Field(None, description="Researcher or PI name")
    funding_org: Optional[str] = Field(None, description="Funding organization name")
    title_contains: Optional[str] = Field(None, description="Words expected in the study title")
    identifiers: Optional[list[str]] = Field(None, description="Exact ICPSR study numbers, if already known")
    owner_archive: Optional[str] = Field(
        None, description="Specific ICPSR archive code if known, e.g. NACJD, DSDR, NAHDAP"
    )
    date_from: Optional[str] = Field(None, description="Earliest release date, format YYYY-MM-DD")
    date_to: Optional[str] = Field(None, description="Latest release date, format YYYY-MM-DD")
    date_field: str = Field(
        "dateIssued",
        description="Which date to filter on: 'dateIssued' (original release) or 'dateModified' (last updated)",
    )
    max_results: int = Field(20, ge=1, le=100, description="Cap on number of studies returned")


class StudyResult(BaseModel):
    identifier: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    keyword: Optional[list[str]] = None
    issued: Optional[str] = None
    modified: Optional[str] = None
    spatial: Optional[str] = None
    landingPage: Optional[str] = None


def verify_key(x_bridge_key: str = Header(...)) -> None:
    expected = os.getenv("BRIDGE_API_KEY")
    if not expected or x_bridge_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Bridge-Key header")


def build_where_clauses(req: SearchRequest) -> list[dict]:
    """
    Translate plain fields into the ICPSR API's exact where-clause
    filter shape. 'contains' is used for anything free-text-ish
    (case-insensitive substring match), 'in' for exact/structured
    values (identifiers, archive codes).
    """
    where: list[dict] = []

    if req.identifiers:
        where.append({"productType": "study", "attribute": "identifier", "op": "in", "value": req.identifiers})
    if req.subject:
        where.append({"productType": "study", "attribute": "subjectTerm", "op": "contains", "value": [req.subject]})
    if req.geography:
        where.append({"productType": "study", "attribute": "geographicArea", "op": "contains", "value": [req.geography]})
    if req.principal_investigator:
        where.append({
            "productType": "study", "attribute": "principalInvestigator",
            "op": "contains", "value": [req.principal_investigator],
        })
    if req.funding_org:
        where.append({"productType": "study", "attribute": "fundingOrg", "op": "contains", "value": [req.funding_org]})
    if req.title_contains:
        where.append({"productType": "study", "attribute": "title", "op": "contains", "value": [req.title_contains]})
    if req.owner_archive:
        where.append({"productType": "study", "attribute": "ownerArchive", "op": "in", "value": [req.owner_archive]})

    if req.date_from and req.date_to:
        where.append({
            "productType": "study", "attribute": req.date_field,
            "op": "between", "value": {"start": req.date_from, "end": req.date_to},
        })
    elif req.date_from:
        where.append({"productType": "study", "attribute": req.date_field, "op": "gte", "value": req.date_from})
    elif req.date_to:
        where.append({"productType": "study", "attribute": req.date_field, "op": "lte", "value": req.date_to})

    if not where:
        raise HTTPException(status_code=400, detail="At least one search field is required.")

    return where


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/search", response_model=list[StudyResult])
def search(req: SearchRequest, _: None = Depends(verify_key)):
    where = build_where_clauses(req)

    payload = {
        "format": "http://www.w3.org/ns/dcat#",
        "ext": "zip",
        "query": {"select": ["study"], "from": "pcms", "where": where},
    }

    client = ICPSRClient()
    try:
        result = client.run_query(payload, timeout_seconds=120)
    except ICPSRAPIError as e:
        raise HTTPException(status_code=502, detail=f"ICPSR API error: {e}")
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))

    location = result.get("location")
    if not location:
        return []

    resp = requests.get(location)
    resp.raise_for_status()

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = Path(tmpdir) / "export.zip"
        zip_path.write_bytes(resp.content)
        extracted = unzip_export(zip_path, Path(tmpdir) / "extracted")
        records = parse_export(extracted)

    results = [
        StudyResult(
            identifier=r.get("identifier"),
            title=r.get("title"),
            description=(r.get("description") or "")[:600],
            keyword=r.get("keyword"),
            issued=r.get("issued"),
            modified=r.get("modified"),
            spatial=r.get("spatial"),
            landingPage=r.get("landingPage"),
        )
        for r in records[: req.max_results]
    ]

    return results
