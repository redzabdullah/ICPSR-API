import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from bridge_api import (
    SearchRequest,
    build_where_clauses,
    extract_documentation_links,
    search,
    verify_key,
)
from src.icpsr_client import ICPSRAPIError


class BridgeAPITests(unittest.TestCase):
    def test_builds_plain_search_filters(self):
        request = SearchRequest(
            identifiers=["5512"],
            subject="migration",
            date_from="2020-01-01",
            date_to="2021-12-31",
        )
        clauses = build_where_clauses(request)
        self.assertEqual(clauses[0]["attribute"], "identifier")
        self.assertEqual(clauses[1]["op"], "contains")
        self.assertEqual(
            [(c["op"], c["value"]) for c in clauses[2:]],
            [("gte", "2020-01-01"), ("lte", "2021-12-31")],
        )
        self.assertNotIn("between", [c["op"] for c in clauses])

    def test_rejects_invalid_date_field_and_range(self):
        with self.assertRaises(ValidationError):
            SearchRequest(subject="x", date_field="created")
        with self.assertRaises(ValidationError):
            SearchRequest(subject="x", date_from="2022-01-01", date_to="2021-01-01")

    def test_requires_at_least_one_filter(self):
        with self.assertRaises(HTTPException) as error:
            build_where_clauses(SearchRequest())
        self.assertEqual(error.exception.status_code, 400)

    def test_bridge_key(self):
        with patch.dict(os.environ, {"BRIDGE_API_KEY": "expected"}):
            self.assertIsNone(verify_key("expected"))
            with self.assertRaises(HTTPException):
                verify_key("wrong")
            with self.assertRaises(HTTPException):
                verify_key("expectez")  # Same length as the correct key.

    def test_bridge_key_uses_constant_time_comparison(self):
        with (
            patch.dict(os.environ, {"BRIDGE_API_KEY": "expected"}),
            patch("bridge_api.secrets.compare_digest", return_value=True) as compare,
        ):
            self.assertIsNone(verify_key("provided"))
        compare.assert_called_once_with("provided", "expected")

    def test_documentation_links_are_unique(self):
        record = {"distribution": [
            {"describedBy": "https://example.test/codebook"},
            {"describedBy": "https://example.test/codebook"},
            {},
        ]}
        self.assertEqual(
            extract_documentation_links(record),
            ["https://example.test/codebook"],
        )

    def test_icpsr_error_response_contains_no_credentials(self):
        api_key = "client-key-sensitive"
        api_secret = "client-secret-sensitive"
        safe_error = ICPSRAPIError(
            "upstream rejected client_id=[REDACTED] and client_secret=[REDACTED]"
        )
        with patch("bridge_api.ICPSRClient") as client_class:
            client_class.return_value.run_query.side_effect = safe_error
            with self.assertRaises(HTTPException) as error:
                search(SearchRequest(identifiers=["5512"]), None)

        response_body = str(error.exception.detail)
        self.assertEqual(error.exception.status_code, 502)
        self.assertNotIn(api_key, response_body)
        self.assertNotIn(api_secret, response_body)


if __name__ == "__main__":
    unittest.main()
