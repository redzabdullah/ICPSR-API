"""
Protocol-level tests for the ChatGPT plugin (MCP) endpoint.
Uses only local fakes: no ICPSR calls, no real credentials.
"""

import contextlib
import importlib
import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import bridge_api
from bridge_api import SearchRequest, StudyResult
from mcp_server import create_mcp, get_mcp_path_token

TOKEN = "t" * 40
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def rpc(client, method, params=None, id_=1, path=f"/mcp/{TOKEN}"):
    body = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        body["params"] = params
    return client.post(path, json=body, headers=HEADERS)


def build_app(search_fn):
    mcp = create_mcp(search_fn, SearchRequest, TOKEN)

    @contextlib.asynccontextmanager
    async def lifespan(_):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    app.mount("/", mcp.streamable_http_app())
    return app


class MCPServerTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def fake_search(req):
            self.calls.append(req)
            return [StudyResult(identifier="5512", title="Test Study", landingPage="https://example.test/5512")]

        self.client_cm = TestClient(build_app(fake_search))
        self.client = self.client_cm.__enter__()
        init = rpc(self.client, "initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        })
        self.assertEqual(init.status_code, 200, init.text)
        self.init = init.json()

    def tearDown(self):
        self.client_cm.__exit__(None, None, None)

    def test_initialize_returns_instructions(self):
        self.assertIn("ICPSR", self.init["result"]["instructions"])

    def test_tool_listed_as_read_only(self):
        tools = rpc(self.client, "tools/list", {}).json()["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["search_icpsr_studies"])
        ann = tools[0]["annotations"]
        self.assertTrue(ann["readOnlyHint"])
        self.assertFalse(ann["destructiveHint"])
        props = tools[0]["inputSchema"]["properties"]
        for field in ["subject", "geography", "identifiers", "date_from", "date_field", "max_results"]:
            self.assertIn(field, props)

    def test_call_tool_returns_results_and_parameters_used(self):
        resp = rpc(self.client, "tools/call", {
            "name": "search_icpsr_studies",
            "arguments": {"subject": "labor migration", "geography": "Southeast Asia", "date_from": "2015-01-01"},
        }).json()["result"]
        self.assertFalse(resp.get("isError"), resp)
        data = resp["structuredContent"]
        self.assertEqual(data["result_count"], 1)
        self.assertEqual(data["results"][0]["identifier"], "5512")
        self.assertEqual(data["query_parameters"]["date_from"], "2015-01-01")
        self.assertEqual(self.calls[0].subject, "labor migration")

    def test_invalid_dates_are_tool_errors(self):
        resp = rpc(self.client, "tools/call", {
            "name": "search_icpsr_studies",
            "arguments": {"subject": "x", "date_from": "2022-01-01", "date_to": "2021-01-01"},
        }).json()["result"]
        self.assertTrue(resp["isError"])
        self.assertEqual(self.calls, [])

    def test_wrong_path_is_not_found(self):
        self.assertEqual(rpc(self.client, "tools/list", {}, path="/mcp/wrong").status_code, 404)
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})


class MCPUpstreamErrorTests(unittest.TestCase):
    def test_upstream_error_becomes_tool_error(self):
        def failing(_req):
            raise HTTPException(status_code=502, detail="ICPSR API error: boom")

        with TestClient(build_app(failing)) as client:
            rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "t", "version": "0"}})
            resp = rpc(client, "tools/call", {"name": "search_icpsr_studies",
                                              "arguments": {"identifiers": ["5512"]}}).json()["result"]
        self.assertTrue(resp["isError"])
        self.assertIn("502", resp["content"][0]["text"])


class TokenConfigTests(unittest.TestCase):
    def test_short_or_missing_token_disables_mcp(self):
        with patch.dict(os.environ, {"MCP_PATH_TOKEN": "short"}):
            self.assertIsNone(get_mcp_path_token())
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(get_mcp_path_token())

    def test_bridge_app_mounts_mcp_only_with_token(self):
        with patch.dict(os.environ, {"MCP_PATH_TOKEN": ""}):
            module = importlib.reload(bridge_api)
            self.assertIsNone(module._mcp_server)
        with patch.dict(os.environ, {"MCP_PATH_TOKEN": TOKEN}):
            module = importlib.reload(bridge_api)
            self.assertIsNotNone(module._mcp_server)
            with TestClient(module.app) as client:
                self.assertEqual(client.get("/health").json(), {"status": "ok"})
                init = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                  "clientInfo": {"name": "t", "version": "0"}})
                self.assertEqual(init.status_code, 200, init.text)
                # REST route for the old GPT Action still requires the bridge key.
                self.assertEqual(client.post("/search", json={"subject": "x"}).status_code, 422)
        importlib.reload(bridge_api)


if __name__ == "__main__":
    unittest.main()
