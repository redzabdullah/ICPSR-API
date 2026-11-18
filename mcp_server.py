"""
mcp_server.py

MCP (Model Context Protocol) endpoint for the ICPSR Search Bridge, so the
service can be connected to ChatGPT as a *plugin* ("Add custom MCP server").

Why this exists: when a Custom GPT is migrated to a ChatGPT plugin, its
instructions and knowledge files move across, but Custom Actions (the
OpenAPI call to POST /search) do NOT. Plugins reach outside services
through MCP servers instead. This module exposes the same search as an
MCP tool, reusing bridge_api.run_search, so the ICPSR logic is not
duplicated and the REST /search route keeps working unchanged.

Auth: ChatGPT's custom MCP connection supports OAuth or "No authentication"
-- it cannot send the X-Bridge-Key header. To keep the endpoint from being
used to burn ICPSR quota, it is served only at a secret, unguessable path:

    https://icpsr-api.onrender.com/mcp/<MCP_PATH_TOKEN>

Set MCP_PATH_TOKEN (32+ random characters) in Render's environment. If it
is missing or too short, the MCP endpoint is not mounted at all (fails
closed). Treat the full URL like a password: anyone who has it can search.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Annotated, Any, Callable, Literal, Optional

import anyio
from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

MIN_TOKEN_LENGTH = 32

SERVER_INSTRUCTIONS = (
    "ICPSR Data Search: finds studies in ICPSR's curated metadata catalogue. "
    "Call search_icpsr_studies with plain fields (subject, geography, PI, funder, "
    "title words, study numbers, archive code, date range). Report only what the "
    "tool returns; never fill missing metadata from memory. Always show the "
    "parameters used, study identifiers and landing-page links. Coverage excludes "
    "openICPSR, the MCC Evidence Platform and ResearchDataGov."
)

TOOL_DESCRIPTION = (
    "Search ICPSR study metadata (Object-Export API). Provide at least one filter. "
    "Free-text filters are case-insensitive substring matches; separate filters are "
    "combined with AND. Returns the parameters used, a result count, and per-study "
    "identifier, title, description (truncated), keywords, issued/modified dates, "
    "spatial coverage, landing page and codebook/documentation links. Queries can "
    "take up to ~2 minutes; prefer narrow searches. Documentation links are links "
    "only -- their contents have not been read."
)


class StudyRecord(BaseModel):
    identifier: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    keyword: Optional[list[str]] = None
    issued: Optional[str] = None
    modified: Optional[str] = None
    spatial: Optional[str] = None
    landingPage: Optional[str] = None
    documentation_links: Optional[list[str]] = None


class SearchResponse(BaseModel):
    query_parameters: dict[str, Any] = Field(description="Filters actually sent, so the user can check them")
    result_count: int
    source: str
    results: list[StudyRecord]


def get_mcp_path_token() -> Optional[str]:
    token = os.getenv("MCP_PATH_TOKEN", "").strip()
    if len(token) < MIN_TOKEN_LENGTH:
        return None
    return token


def create_mcp(search_fn: Callable[[Any], list[Any]], request_model: type, token: str) -> FastMCP:
    """
    Build the MCP server. `search_fn` is bridge_api.run_search and
    `request_model` is bridge_api.SearchRequest (injected to avoid a
    circular import).
    """
    mcp = FastMCP(
        name="ICPSR Data Search",
        instructions=SERVER_INSTRUCTIONS,
        stateless_http=True,
        json_response=True,
        streamable_http_path=f"/mcp/{token}",
        # Host-header (DNS-rebinding) checks are for servers on localhost;
        # this one is public behind Render's proxy and protected by the path.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @mcp.tool(
        name="search_icpsr_studies",
        title="Search ICPSR studies",
        description=TOOL_DESCRIPTION,
        annotations=ToolAnnotations(
            title="Search ICPSR studies",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def search_icpsr_studies(
        subject: Annotated[Optional[str], Field(description="Topic or subject area, e.g. 'labor migration'")] = None,
        geography: Annotated[Optional[str], Field(description="Place name, e.g. 'Southeast Asia' or 'Detroit'")] = None,
        principal_investigator: Annotated[Optional[str], Field(description="Researcher or PI name")] = None,
        funding_org: Annotated[Optional[str], Field(description="Funding organization name")] = None,
        title_contains: Annotated[Optional[str], Field(description="Words expected in the study title")] = None,
        identifiers: Annotated[Optional[list[str]], Field(description="Exact ICPSR study numbers, e.g. ['5512']")] = None,
        owner_archive: Annotated[
            Optional[str], Field(description="ICPSR archive code (exact, case-sensitive), e.g. NACJD, DSDR, NAHDAP")
        ] = None,
        date_from: Annotated[Optional[date], Field(description="Earliest date, YYYY-MM-DD")] = None,
        date_to: Annotated[Optional[date], Field(description="Latest date, YYYY-MM-DD")] = None,
        date_field: Annotated[
            Literal["dateIssued", "dateModified"],
            Field(description="'dateIssued' (original release) or 'dateModified' (last updated)"),
        ] = "dateIssued",
        max_results: Annotated[int, Field(ge=1, le=100, description="Cap on studies returned")] = 20,
    ) -> SearchResponse:
        params = {
            "subject": subject,
            "geography": geography,
            "principal_investigator": principal_investigator,
            "funding_org": funding_org,
            "title_contains": title_contains,
            "identifiers": identifiers,
            "owner_archive": owner_archive,
            "date_from": date_from,
            "date_to": date_to,
            "date_field": date_field,
            "max_results": max_results,
        }
        try:
            req = request_model(**params)
        except ValidationError as e:
            raise ToolError(f"Invalid search parameters: {e.errors(include_url=False)}") from e

        try:
            # run_search is blocking (requests + polling), so keep it off the event loop.
            results = await anyio.to_thread.run_sync(search_fn, req)
        except HTTPException as e:
            raise ToolError(f"Search failed ({e.status_code}): {e.detail}") from e

        used = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in params.items() if v is not None}
        return SearchResponse(
            query_parameters=used,
            result_count=len(results),
            source="ICPSR Object-Export API (curated ICPSR collections only)",
            results=[StudyRecord(**r.model_dump()) for r in results],
        )

    return mcp
