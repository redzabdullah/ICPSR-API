import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from bridge_api import (
    SearchRequest,
    build_where_clauses,
    extract_documentation_links,
    verify_key,
)


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
            clauses[2]["value"],
            {"start": "2020-01-01", "end": "2021-12-31"},
        )

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


if __name__ == "__main__":
    unittest.main()
