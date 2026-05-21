import json
import os
import sys
import unittest
from pathlib import Path


LAMBDA_DIR = Path(__file__).resolve().parents[1] / "lambda"
sys.path.insert(0, str(LAMBDA_DIR))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import ingest_handler  # noqa: E402
import processor_handler  # noqa: E402
import source_refresh_handler  # noqa: E402


class IngestValidationTests(unittest.TestCase):
    def test_valid_project_with_optional_buyer_context(self) -> None:
        payload = {
            "location": "Raleigh, NC",
            "capex": 12500000,
            "jobs": 82,
            "facility_type": "advanced manufacturing",
            "contact_email": "buyer@example.com",
            "average_wage": 72000,
            "project_timeline": "Decision inside 120 days",
            "competing_locations": "SC, TN",
        }

        parsed = ingest_handler.parse_and_validate_body({"body": json.dumps(payload)})

        self.assertEqual(parsed["location"], "Raleigh, NC")
        self.assertEqual(parsed["contact_email"], "buyer@example.com")
        self.assertEqual(parsed["average_wage"], 72000)

    def test_rejects_unsupported_fields(self) -> None:
        payload = {
            "location": "Raleigh, NC",
            "capex": 12500000,
            "jobs": 82,
            "facility_type": "advanced manufacturing",
            "unexpected": "nope",
        }

        with self.assertRaises(ingest_handler.RequestValidationError):
            ingest_handler.parse_and_validate_body({"body": json.dumps(payload)})


class ProcessorEvidenceTests(unittest.TestCase):
    def test_state_specific_catalog_matches_have_sources(self) -> None:
        spec = {
            "location": "Raleigh, NC",
            "capex": 12500000,
            "jobs": 82,
            "facility_type": "advanced manufacturing",
        }

        matches = processor_handler.query_local_incentive_catalog(spec)

        self.assertGreaterEqual(len(matches), 3)
        self.assertTrue(any(match["metadata"]["state_match"] for match in matches))
        self.assertTrue(all(match["metadata"]["source_url"].startswith("https://") for match in matches))

    def test_report_contains_cited_recommendations(self) -> None:
        spec = {
            "location": "Augusta, GA",
            "capex": 42000000,
            "jobs": 140,
            "facility_type": "advanced manufacturing",
        }
        matches = processor_handler.query_local_incentive_catalog(spec)

        report = processor_handler.build_source_backed_report(spec, matches, "source_backed")

        self.assertGreaterEqual(report["eligibility_score"], 70)
        self.assertGreaterEqual(len(report["recommended_programs"]), 3)
        self.assertTrue(all(program["source_url"].startswith("https://") for program in report["recommended_programs"]))
        self.assertIn("validation_note", report)


class SourceRefreshTests(unittest.TestCase):
    def test_source_refresh_merges_status_without_overwriting_local_catalog(self) -> None:
        local_catalog = {
            "version": "2026-05-21",
            "sources": [
                {
                    "id": "program-a",
                    "program_name": "Program A",
                    "source_url": "https://new.example.gov/program-a",
                }
            ],
        }
        s3_catalog = {
            "version": "2026-05-14",
            "sources": [
                {
                    "id": "program-a",
                    "program_name": "Old Program A",
                    "source_url": "https://old.example.gov/program-a",
                    "last_checked_at": "2026-05-14T00:00:00Z",
                    "retrieval_status": "ok",
                    "retrieval_http_status": 200,
                    "retrieval_error": None,
                    "content_sha256": "abc123",
                }
            ],
        }

        merged = source_refresh_handler.merge_catalog_status(local_catalog, s3_catalog)
        source = merged["sources"][0]

        self.assertEqual(source["source_url"], "https://new.example.gov/program-a")
        self.assertEqual(source["program_name"], "Program A")
        self.assertEqual(source["retrieval_status"], "ok")
        self.assertEqual(source["content_sha256"], "abc123")

    def test_source_refresh_rejects_non_https_sources(self) -> None:
        result = source_refresh_handler.verify_source_url("http://example.gov/insecure")

        self.assertEqual(result["status"], "failed")
        self.assertIn("https", result["error"])


if __name__ == "__main__":
    unittest.main()
