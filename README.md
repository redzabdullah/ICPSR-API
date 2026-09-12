# icpsr-api-project

Scaffold for working with the [ICPSR Object-Export API](https://www.icpsr.umich.edu/),
built from the *ICPSR Object-Export API User Guide* (last updated June 2026).

## Status: waiting on API approval

This is set up so you can start writing/testing logic now and just drop
in real credentials once your UM API Directory app is approved.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env
```

Once approved, fill in `.env` with:
- `ICPSR_API_KEY` / `ICPSR_API_SECRET` — from the "API Keys" section of
  your App in the UM API Directory (see Section I of the guide).
- Confirm `ICPSR_GATEWAY_URL` / `ICPSR_API_PATH` against the real
  Postman collection's `{{gateway_url}}` / `{{api_path}}` variables.

## Auth (confirmed)

Reverse-engineered from ICPSR's public Postman collection ("Public
Object Export Toolkit" → collection-level Scripts → Before request):

- Token endpoint: `POST {gateway_url}/um/oauth2/tokens`
- `Content-Type: application/x-www-form-urlencoded`
- Body: `grant_type=client_credentials&client_id=<api_key>&client_secret=<api_secret>&scope=icpsr-objectexport`
- Response: `{ access_token, token_type: "Bearer", expires_in, scope, client_id }`
- Every API call then sends `Authorization: Bearer <access_token>`

`src/icpsr_client.py` implements this with in-memory token caching
(refreshing ~10s before expiry, matching the Postman script's own
buffer). No further guessing needed here.

## Usage

```bash
python src/download_export.py --query all_studies
python src/download_export.py --query single_identifier --outfile study_5512.zip
```

Downloaded ZIPs land in `data/raw/`. Add/edit queries in
`config/sample_query.json`.

To parse a downloaded export:

```python
from pathlib import Path
from src.parse_metadata import unzip_export, parse_export

files = unzip_export(Path("data/raw/all_studies.zip"), Path("data/processed/all_studies"))
records = parse_export(files)
```

## Notes from the guide worth remembering

- The API is **asynchronous**: POST to submit a query, then GET
  (polling) until `status == "complete"`.
- The `"in"` operator is case-sensitive; `"contains"` is not. When in
  doubt about capitalization (e.g. place names, org names), prefer
  `"contains"`.
- Multiple values in one `"where"` condition are OR'd together, not
  AND'd — e.g. `identifier in ["5512","2594"]` matches *either*. To
  require multiple conditions simultaneously, use separate `"where"`
  objects (see `africa_dsdr_since_2020` / `nacjd_addiction_since_2020`
  in `config/sample_query.json`).
- The API does **not** cover openICPSR, the MCC Evidence Platform, or
  ResearchDataGov — only curated ICPSR-hosted collections.
- Dates must be `YYYY-MM-DD`.
