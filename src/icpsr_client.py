"""
icpsr_client.py

Thin client for the ICPSR Object-Export API, based on the
"ICPSR Object-Export API User Guide" (last updated June 2026) and the
real auth logic reverse-engineered from ICPSR's public Postman
collection ("Public Object Export Toolkit" -> collection-level
Scripts -> Before request).

Two-step async flow described in the guide:
  1. POST  {gateway_url}/{api_path}/export_requests   -> returns an "id"
  2. GET   {gateway_url}/{api_path}/export_requests/{export_id}
         -> poll until status == "complete", then use "location" to
            download the ZIP of metadata records.

AUTH (confirmed from the Postman collection's pre-request script):
  Token endpoint: {gateway_url}/um/oauth2/tokens
  Method: POST, Content-Type: application/x-www-form-urlencoded
  Body: grant_type=client_credentials, client_id=<api_key>,
        client_secret=<api_secret>, scope=icpsr-objectexport
  Response: { access_token, token_type: "Bearer", expires_in, scope, client_id }
  Every subsequent API call sends: Authorization: Bearer <access_token>

Tokens are cached in-memory and refreshed ~10s before expiry, mirroring
the collection's own caching behavior.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests
from dotenv import load_dotenv

load_dotenv()


class ICPSRAPIError(RuntimeError):
    """Raised when the API returns an error response (e.g. HTTP 400)."""


@dataclass
class ICPSRConfig:
    api_key: str
    api_secret: str
    gateway_url: str = "https://gw.api.it.umich.edu"
    api_path: str = "icpsr/objectexport"
    oauth_path: str = "um/oauth2/token"
    scope: str = "icpsr-objectexport"

    @classmethod
    def from_env(cls) -> "ICPSRConfig":
        api_key = os.getenv("ICPSR_API_KEY", "")
        api_secret = os.getenv("ICPSR_API_SECRET", "")
        if not api_key or not api_secret:
            raise ValueError(
                "ICPSR_API_KEY / ICPSR_API_SECRET not set. Copy .env.example "
                "to .env and fill them in once your API access is approved."
            )
        return cls(
            api_key=api_key,
            api_secret=api_secret,
            gateway_url=os.getenv("ICPSR_GATEWAY_URL", cls.gateway_url),
            api_path=os.getenv("ICPSR_API_PATH", cls.api_path),
            oauth_path=os.getenv("ICPSR_OAUTH_PATH", cls.oauth_path),
            scope=os.getenv("ICPSR_OAUTH_SCOPE", cls.scope),
        )


class ICPSRClient:
    def __init__(self, config: Optional[ICPSRConfig] = None):
        self.config = config or ICPSRConfig.from_env()
        self.session = requests.Session()
        self._base_url = f"{self.config.gateway_url}/{self.config.api_path}"
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    def _build_auth_headers(self) -> dict[str, str]:
        """Bearer token via OAuth2 client-credentials grant (confirmed
        from the ICPSR Postman collection's pre-request script)."""
        return {"Authorization": f"Bearer {self._get_oauth_token()}"}

    def _get_oauth_token(self) -> str:
        """
        Return a cached token if still valid, otherwise fetch a new one
        from {gateway_url}/um/oauth2/tokens via the client-credentials
        grant. Mirrors the Postman collection's own 10-second early
        expiry buffer to avoid race conditions.
        """
        now = time.monotonic()
        expire_early_seconds = 10
        if self._token and (self._token_expires_at - expire_early_seconds) > now:
            return self._token

        token_url = f"{self.config.gateway_url}/{self.config.oauth_path}"
        resp = self.session.post(
            token_url,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={
                "grant_type": "client_credentials",
                "client_id": self.config.api_key,
                "client_secret": self.config.api_secret,
                "scope": self.config.scope,
            },
        )

        content_type = resp.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            # Something answered, but not with JSON -- likely a network
            # intermediary (proxy/VPN/TLS inspection) rather than the
            # real ICPSR gateway. Redact anything that looks like the
            # secret before showing the body.
            body_preview = resp.text[:500]
            body_preview = body_preview.replace(self.config.api_secret, "[REDACTED]")
            raise ICPSRAPIError(
                f"Token endpoint returned HTTP {resp.status_code} with "
                f"Content-Type '{content_type}' (expected application/json). "
                f"This usually means the request was intercepted before "
                f"reaching the real ICPSR/UM gateway (e.g. a campus proxy "
                f"or VPN), not a code bug. Response body preview:\n{body_preview}"
            )

        if resp.status_code >= 400:
            self._raise_for_error(resp)

        data = resp.json()

        if data.get("token_type") != "Bearer":
            raise ICPSRAPIError(f"Unexpected token_type in OAuth response: {data.get('token_type')!r}")
        if data.get("scope") != self.config.scope:
            raise ICPSRAPIError(
                f"Unexpected scope in OAuth response: {data.get('scope')!r} "
                f"(expected {self.config.scope!r})"
            )

        self._token = data["access_token"]
        self._token_expires_at = time.monotonic() + float(data["expires_in"])
        return self._token

    # ------------------------------------------------------------------
    # Step 1: POST -- identify records of interest
    # ------------------------------------------------------------------
    def submit_query(self, payload: dict[str, Any]) -> str:
        """
        Submit an export_requests POST. Returns the export request ID
        (the trailing numeric segment of the "id" URL in the response).
        """
        url = f"{self._base_url}/export_requests"
        headers = {"Content-Type": "application/json", **self._build_auth_headers()}
        resp = self.session.post(url, json=payload, headers=headers)

        if resp.status_code != 201:
            self._raise_for_error(resp)

        data = resp.json()
        export_id_url = data["id"]
        export_id = export_id_url.rstrip("/").split("/")[-1]
        return export_id

    # ------------------------------------------------------------------
    # Step 2: GET -- retrieve any relevant records (with polling)
    # ------------------------------------------------------------------
    def get_export_result(self, export_id: str) -> dict[str, Any]:
        url = f"{self._base_url}/export_requests/{export_id}"
        headers = self._build_auth_headers()
        resp = self.session.get(url, headers=headers)

        if resp.status_code >= 400:
            self._raise_for_error(resp)

        return resp.json()

    def poll_until_complete(
        self,
        export_id: str,
        poll_interval_seconds: float = 3.0,
        timeout_seconds: float = 300.0,
    ) -> dict[str, Any]:
        """
        Repeatedly GET the export result until status == "complete"
        (per the guide: status is one of "pending", "processing", or
        "complete"), or until timeout_seconds is exceeded.
        """
        start = time.monotonic()
        while True:
            result = self.get_export_result(export_id)
            status = result.get("status")

            if status == "complete":
                return result

            if time.monotonic() - start > timeout_seconds:
                raise TimeoutError(
                    f"Export {export_id} still '{status}' after "
                    f"{timeout_seconds}s -- try again later."
                )

            time.sleep(poll_interval_seconds)

    # ------------------------------------------------------------------
    # Convenience: full round trip
    # ------------------------------------------------------------------
    def run_query(self, payload: dict[str, Any], **poll_kwargs) -> dict[str, Any]:
        """POST the query, then poll GET until complete. Returns the
        final result dict (includes 'status', 'message', 'location')."""
        export_id = self.submit_query(payload)
        return self.poll_until_complete(export_id, **poll_kwargs)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _raise_for_error(resp: requests.Response) -> None:
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text
        raise ICPSRAPIError(f"HTTP {resp.status_code}: {detail}")
