import json
import os
import sys
import unittest
from pathlib import Path


LAMBDA_DIR = Path(__file__).resolve().parents[1] / "lambda"
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(LAMBDA_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import ingest_handler  # noqa: E402
import analytics_handler  # noqa: E402
import partner_portal_service  # noqa: E402
import processor_handler  # noqa: E402
import source_refresh_handler  # noqa: E402
import sync_vector_index  # noqa: E402


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


class AnalyticsValidationTests(unittest.TestCase):
    def test_valid_analytics_event_is_sanitized(self) -> None:
        payload = {
            "event_name": "cta_click",
            "page_path": "/",
            "page_title": "GrantStack",
            "session_id": "session-123",
            "properties": {
                "target": "sample_report",
                "nested": {"ok": True},
            },
        }

        parsed = analytics_handler.parse_and_validate_body({"body": json.dumps(payload)})

        self.assertEqual(parsed["event_name"], "cta_click")
        self.assertEqual(parsed["properties"]["target"], "sample_report")
        self.assertTrue(parsed["properties"]["nested"]["ok"])

    def test_rejects_invalid_analytics_event_name(self) -> None:
        payload = {"event_name": "<script>", "properties": {}}

        with self.assertRaises(analytics_handler.AnalyticsValidationError):
            analytics_handler.parse_and_validate_body({"body": json.dumps(payload)})


class ProcessorEvidenceTests(unittest.TestCase):
    def test_source_catalog_is_expanded_for_provider_mode(self) -> None:
        catalog = processor_handler.load_catalog()
        states = {
            state
            for source in catalog["sources"]
            for state in source.get("states", [])
        }

        self.assertGreaterEqual(len(catalog["sources"]), 24)
        self.assertTrue({"NC", "GA", "SC", "TN", "TX", "OH", "IN", "AL", "KY"}.issubset(states))

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

    def test_texas_project_returns_jurisdiction_specific_sources(self) -> None:
        spec = {
            "location": "Austin, TX",
            "capex": 85000000,
            "jobs": 120,
            "facility_type": "advanced manufacturing",
            "average_wage": 89000,
            "competing_locations": "OK, AZ",
        }

        matches = processor_handler.query_local_incentive_catalog(spec)
        ids = {match["id"] for match in matches}

        self.assertIn("tx-enterprise-fund", ids)
        self.assertIn("tx-skills-development-fund", ids)
        self.assertTrue(all(match["metadata"]["jurisdiction"] in {"Texas", "Federal"} for match in matches))

    def test_eligibility_rules_surface_failures_and_unknowns(self) -> None:
        spec = {
            "location": "Austin, TX",
            "capex": 85000000,
            "jobs": 50,
            "facility_type": "advanced manufacturing",
        }
        matches = processor_handler.query_local_incentive_catalog(spec)

        checks = processor_handler.evaluate_eligibility_rules(spec, matches)
        tx_tef = next(item for item in checks if item["program_id"] == "tx-enterprise-fund")
        statuses = {check["rule_id"]: check["status"] for check in tx_tef["checks"]}

        self.assertEqual(statuses["tx-tef-jobs"], "FAIL")
        self.assertEqual(statuses["tx-tef-competition"], "UNKNOWN")
        self.assertEqual(statuses["tx-tef-average-wage"], "UNKNOWN")

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
        self.assertGreaterEqual(report["rule_summary"]["programs_checked"], 1)
        self.assertIn("eligibility_checks", report)
        self.assertIn("validation_note", report)

    def test_pinecone_query_payload_uses_provider_config(self) -> None:
        config = processor_handler.RuntimeConfig(
            table_name="projects",
            vector_db_provider="pinecone",
            vector_db_endpoint="https://example-index.pinecone.io",
            vector_db_api_key="secret",
            vector_db_namespace="grantstack-tests",
            vector_db_top_k=12,
            vector_db_min_score=0.3,
            vector_db_api_version="2026-01",
            embedding_provider="openai",
            embedding_api_endpoint="https://api.openai.com/v1/embeddings",
            embedding_api_key="secret",
            embedding_model="text-embedding-3-small",
            llm_provider="openai",
            llm_api_endpoint="https://api.openai.com/v1/chat/completions",
            llm_api_key="secret",
            llm_model="gpt-4.1-mini",
            mock_external_calls=False,
            http_timeout_seconds=20,
        )

        payload = processor_handler.build_pinecone_query_payload([0.1, 0.2], config)

        self.assertEqual(payload["topK"], 12)
        self.assertEqual(payload["namespace"], "grantstack-tests")
        self.assertFalse(payload["includeValues"])

    def test_vector_sync_dry_run_records_include_rules(self) -> None:
        catalog = processor_handler.load_catalog()
        rules = processor_handler.load_eligibility_rules()

        records = sync_vector_index.build_index_records(catalog, rules, limit=1)

        self.assertEqual(len(records), 1)
        self.assertIn("Eligibility rules:", records[0]["text"])
        self.assertGreaterEqual(records[0]["metadata"]["rule_count"], 1)


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


class InMemoryPartnerPortalRepository:
    def __init__(self) -> None:
        self.projects = {}
        self.partners = {}
        self.routings = {}
        self.payout_ledgers = []
        self.audit_entries = []

    def get_project(self, project_id: str):
        return self.projects.get(project_id)

    def update_project_status(self, project_id: str, status: str) -> None:
        self.projects[project_id]["status"] = status

    def get_partner(self, partner_id: str):
        return self.partners.get(partner_id)

    def put_partner(self, partner) -> None:
        self.partners[partner.partner_id] = partner

    def get_routing(self, project_id: str):
        return self.routings.get(project_id)

    def put_routing(self, routing) -> None:
        self.routings[routing.project_id] = routing

    def put_payout_ledger(self, ledger) -> None:
        self.payout_ledgers.append(ledger)

    def put_audit_entry(self, entry) -> None:
        self.audit_entries.append(entry)


class PartnerPortalServiceTests(unittest.TestCase):
    def build_repository(self) -> InMemoryPartnerPortalRepository:
        repository = InMemoryPartnerPortalRepository()
        repository.projects["project-oh"] = {
            "project_id": "project-oh",
            "status": "COMPLETED",
            "input_spec": {
                "location": "Columbus, OH",
                "capex": 50000000,
                "jobs": 180,
                "facility_type": "semiconductor supplier",
            },
            "analysis_report": {
                "summary": "Ohio semiconductor supplier expansion has several first-pass incentive paths.",
                "recommended_programs": [
                    {
                        "name": "Ohio Job Creation Tax Credit",
                        "jurisdiction": "Ohio",
                        "source_url": "https://development.ohio.gov/business/state-incentives/job-creation-tax-credit",
                    }
                ],
                "eligibility_checks": [{"program_id": "oh-jctc", "status": "UNKNOWN"}],
                "system_prompt": "internal prompt must not leave backend",
                "embedding_history": [0.1, 0.2],
            },
            "llm_metadata": {"provider": "internal"},
            "vector_matches": [{"id": "internal-vector-match"}],
            "access_token": "private-token",
        }
        repository.put_partner(
            partner_portal_service.Partner(
                partner_id="oh-cpa",
                firm_name="Ohio Incentive Review LLP",
                assigned_cpa_name="Jordan Lee",
                cpa_license_number="OH-CPA-12345",
                state_jurisdictions=["OH"],
                contracted_rev_share_percentage=partner_portal_service.Decimal("30"),
            )
        )
        repository.put_partner(
            partner_portal_service.Partner(
                partner_id="mi-cpa",
                firm_name="Michigan Review LLP",
                assigned_cpa_name="Taylor Morgan",
                cpa_license_number="MI-CPA-12345",
                state_jurisdictions=["MI"],
                contracted_rev_share_percentage=partner_portal_service.Decimal("25"),
            )
        )
        repository.put_partner(
            partner_portal_service.Partner(
                partner_id="federal-cpa",
                firm_name="Federal Incentive Review LLP",
                assigned_cpa_name="Avery Patel",
                cpa_license_number="US-CPA-12345",
                state_jurisdictions=["FEDERAL"],
                contracted_rev_share_percentage=partner_portal_service.Decimal("35"),
            )
        )
        return repository

    def test_assign_project_requires_matching_state_or_federal_oversight(self) -> None:
        repository = self.build_repository()
        service = partner_portal_service.PartnerPortalService(repository)

        with self.assertRaises(partner_portal_service.PartnerRoutingEligibilityError):
            service.assign_project_to_partner("project-oh", "mi-cpa")

        routing = service.assign_project_to_partner("project-oh", "oh-cpa")

        self.assertEqual(routing.routing_status, partner_portal_service.RoutingStatus.PENDING_PARTNER_ACCEPTANCE)
        self.assertEqual(repository.projects["project-oh"]["status"], "PENDING_PARTNER_ACCEPTANCE")
        self.assertEqual(repository.audit_entries[-1].event_type, "PROJECT_ROUTED_TO_PARTNER")

    def test_federal_oversight_partner_can_review_ohio_project(self) -> None:
        repository = self.build_repository()
        service = partner_portal_service.PartnerPortalService(repository)

        routing = service.assign_project_to_partner("project-oh", "federal-cpa")

        self.assertEqual(routing.assigned_partner_id, "federal-cpa")

    def test_secure_review_payload_excludes_internal_ai_material(self) -> None:
        repository = self.build_repository()
        service = partner_portal_service.PartnerPortalService(repository)
        service.assign_project_to_partner("project-oh", "oh-cpa")

        payload = service.generate_secure_review_payload("project-oh")
        encoded_payload = json.dumps(payload)

        self.assertTrue(payload["read_only"])
        self.assertEqual(payload["review_document"]["estimated_page_count"], 50)
        self.assertIn("source_citations", payload)
        self.assertNotIn("system_prompt", encoded_payload)
        self.assertNotIn("embedding_history", encoded_payload)
        self.assertNotIn("vector_matches", encoded_payload)
        self.assertNotIn("access_token", encoded_payload)
        self.assertNotIn("llm_metadata", encoded_payload)

    def test_cpa_approval_moves_project_to_audit_ready_and_logs_entry(self) -> None:
        repository = self.build_repository()
        service = partner_portal_service.PartnerPortalService(repository)
        service.assign_project_to_partner("project-oh", "oh-cpa")

        routing = service.submit_cpa_review_action("project-oh", "APPROVE", "Reviewed and signed off.")

        self.assertEqual(routing.routing_status, partner_portal_service.RoutingStatus.PARTNER_SIGNED_OFF)
        self.assertEqual(repository.projects["project-oh"]["status"], "AUDIT_READY")
        self.assertEqual(repository.audit_entries[-1].event_type, "PARTNER_SIGNED_OFF")
        self.assertTrue(repository.audit_entries[-1].to_item()["immutable"])

    def test_request_changes_requires_notes(self) -> None:
        repository = self.build_repository()
        service = partner_portal_service.PartnerPortalService(repository)
        service.assign_project_to_partner("project-oh", "oh-cpa")

        with self.assertRaises(partner_portal_service.PartnerPortalValidationError):
            service.submit_cpa_review_action("project-oh", "REQUEST_CHANGES", "")

    def test_payout_ledger_uses_subcontracted_professional_service_fee_label(self) -> None:
        repository = self.build_repository()
        service = partner_portal_service.PartnerPortalService(repository)
        service.assign_project_to_partner("project-oh", "oh-cpa")

        ledger = service.record_payout_ledger("project-oh", "100000.00", ledger_id="ledger-1")

        self.assertEqual(ledger.partner_fee_type, "SubcontractedProfessionalServiceFees")
        self.assertEqual(ledger.partner_share, partner_portal_service.Decimal("30000.00"))
        self.assertEqual(ledger.platform_share, partner_portal_service.Decimal("70000.00"))
        self.assertEqual(repository.payout_ledgers[0].ledger_id, "ledger-1")


if __name__ == "__main__":
    unittest.main()
