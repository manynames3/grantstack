from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence

import boto3
from botocore.exceptions import BotoCoreError, ClientError


CENT = Decimal("0.01")
FEDERAL_JURISDICTION = "FEDERAL"
MAX_NOTES_LENGTH = 4_000
STATE_PATTERN = re.compile(
    r"(?:,\s*|\b)(A[LKSZR]|C[AOT]|D[CE]|F[LM]|G[AU]|HI|I[ADLN]|K[SY]|LA|M[ADEHINOST]|N[CDEHJMVY]|O[HKR]|P[AWR]|RI|S[CD]|T[NX]|UT|V[AIT]|W[AIVY])\b",
    re.IGNORECASE,
)
STATE_NAME_TO_CODE = {
    "ALABAMA": "AL",
    "ALASKA": "AK",
    "ARIZONA": "AZ",
    "ARKANSAS": "AR",
    "CALIFORNIA": "CA",
    "COLORADO": "CO",
    "CONNECTICUT": "CT",
    "DELAWARE": "DE",
    "DISTRICT OF COLUMBIA": "DC",
    "FLORIDA": "FL",
    "GEORGIA": "GA",
    "HAWAII": "HI",
    "IDAHO": "ID",
    "ILLINOIS": "IL",
    "INDIANA": "IN",
    "IOWA": "IA",
    "KANSAS": "KS",
    "KENTUCKY": "KY",
    "LOUISIANA": "LA",
    "MAINE": "ME",
    "MARYLAND": "MD",
    "MASSACHUSETTS": "MA",
    "MICHIGAN": "MI",
    "MINNESOTA": "MN",
    "MISSISSIPPI": "MS",
    "MISSOURI": "MO",
    "MONTANA": "MT",
    "NEBRASKA": "NE",
    "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH",
    "NEW JERSEY": "NJ",
    "NEW MEXICO": "NM",
    "NEW YORK": "NY",
    "NORTH CAROLINA": "NC",
    "NORTH DAKOTA": "ND",
    "OHIO": "OH",
    "OKLAHOMA": "OK",
    "OREGON": "OR",
    "PENNSYLVANIA": "PA",
    "RHODE ISLAND": "RI",
    "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN",
    "TEXAS": "TX",
    "UTAH": "UT",
    "VERMONT": "VT",
    "VIRGINIA": "VA",
    "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI",
    "WYOMING": "WY",
}
INTERNAL_PAYLOAD_KEY_FRAGMENTS = (
    "api_key",
    "embedding",
    "index",
    "llm",
    "prompt",
    "retrieved_context",
    "secret",
    "system_message",
    "token",
    "vector",
)
INTERNAL_PAYLOAD_VALUE_FRAGMENTS = (
    "api key",
    "embedding history",
    "retrieved_context",
    "secret key",
    "system prompt",
    "vector index",
)


class PartnerPortalError(RuntimeError):
    """Base error for partner portal workflow failures."""


class PartnerPortalValidationError(PartnerPortalError):
    """Raised when partner portal input is invalid."""


class PartnerPortalNotFoundError(PartnerPortalError):
    """Raised when a project, partner, or routing record cannot be found."""


class PartnerRoutingEligibilityError(PartnerPortalError):
    """Raised when a partner is not licensed for the project jurisdiction."""


class PartnerPortalStateError(PartnerPortalError):
    """Raised when an action is invalid for the current workflow state."""


class RoutingStatus(str, Enum):
    UNASSIGNED = "UNASSIGNED"
    PENDING_PARTNER_ACCEPTANCE = "PENDING_PARTNER_ACCEPTANCE"
    UNDER_HUMAN_REVIEW = "UNDER_HUMAN_REVIEW"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    PARTNER_SIGNED_OFF = "PARTNER_SIGNED_OFF"


class PartnerReviewAction(str, Enum):
    ACCEPT_REVIEW = "ACCEPT_REVIEW"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    APPROVE = "APPROVE"


@dataclass(frozen=True)
class Partner:
    partner_id: str
    firm_name: str
    assigned_cpa_name: str
    cpa_license_number: str
    state_jurisdictions: Sequence[str]
    contracted_rev_share_percentage: Decimal
    created_at: str = field(default_factory=lambda: utc_now_iso())
    updated_at: str = field(default_factory=lambda: utc_now_iso())

    def __post_init__(self) -> None:
        require_non_empty(self.partner_id, "partner_id")
        require_non_empty(self.firm_name, "firm_name")
        require_non_empty(self.assigned_cpa_name, "assigned_cpa_name")
        require_non_empty(self.cpa_license_number, "cpa_license_number")
        if not self.state_jurisdictions:
            raise PartnerPortalValidationError("state_jurisdictions must include at least one state or FEDERAL.")
        validate_percentage(self.contracted_rev_share_percentage, "contracted_rev_share_percentage")

    @property
    def normalized_state_jurisdictions(self) -> List[str]:
        return [normalize_jurisdiction(value) for value in self.state_jurisdictions]

    def has_federal_oversight(self) -> bool:
        return FEDERAL_JURISDICTION in self.normalized_state_jurisdictions

    def can_review_state(self, state_code: str) -> bool:
        normalized_state = normalize_jurisdiction(state_code)
        return self.has_federal_oversight() or normalized_state in self.normalized_state_jurisdictions

    def to_item(self) -> Dict[str, Any]:
        return {
            "project_id": partner_key(self.partner_id),
            "entity_type": "Partners",
            "partner_id": self.partner_id,
            "firm_name": self.firm_name,
            "assigned_cpa_name": self.assigned_cpa_name,
            "cpa_license_number": self.cpa_license_number,
            "state_jurisdictions": self.normalized_state_jurisdictions,
            "contracted_rev_share_percentage": self.contracted_rev_share_percentage,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_item(cls, item: Mapping[str, Any]) -> "Partner":
        return cls(
            partner_id=str(item["partner_id"]),
            firm_name=str(item["firm_name"]),
            assigned_cpa_name=str(item["assigned_cpa_name"]),
            cpa_license_number=str(item["cpa_license_number"]),
            state_jurisdictions=list(item.get("state_jurisdictions", [])),
            contracted_rev_share_percentage=to_decimal(item["contracted_rev_share_percentage"]),
            created_at=str(item.get("created_at") or utc_now_iso()),
            updated_at=str(item.get("updated_at") or utc_now_iso()),
        )


@dataclass(frozen=True)
class ProjectRouting:
    project_id: str
    assigned_partner_id: str
    routing_status: RoutingStatus
    partner_notes: str = ""
    routing_event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=lambda: utc_now_iso())
    updated_at: str = field(default_factory=lambda: utc_now_iso())

    def __post_init__(self) -> None:
        require_non_empty(self.project_id, "project_id")
        require_non_empty(self.assigned_partner_id, "assigned_partner_id")
        if len(self.partner_notes) > MAX_NOTES_LENGTH:
            raise PartnerPortalValidationError(f"partner_notes must be {MAX_NOTES_LENGTH} characters or fewer.")

    def to_item(self) -> Dict[str, Any]:
        return {
            "project_id": routing_key(self.project_id),
            "entity_type": "ProjectRouting",
            "source_project_id": self.project_id,
            "assigned_partner_id": self.assigned_partner_id,
            "routing_status": self.routing_status.value,
            "partner_notes": self.partner_notes,
            "routing_event_id": self.routing_event_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_item(cls, item: Mapping[str, Any]) -> "ProjectRouting":
        return cls(
            project_id=str(item["source_project_id"]),
            assigned_partner_id=str(item["assigned_partner_id"]),
            routing_status=RoutingStatus(str(item["routing_status"])),
            partner_notes=str(item.get("partner_notes") or ""),
            routing_event_id=str(item.get("routing_event_id") or uuid.uuid4()),
            created_at=str(item.get("created_at") or utc_now_iso()),
            updated_at=str(item.get("updated_at") or utc_now_iso()),
        )


@dataclass(frozen=True)
class PayoutLedger:
    ledger_id: str
    project_id: str
    partner_id: str
    total_client_fee_collected: Decimal
    platform_share: Decimal
    partner_share: Decimal
    contracted_rev_share_percentage: Decimal
    partner_fee_type: str = "SubcontractedProfessionalServiceFees"
    architecture: str = "PrimeSubcontractor"
    created_at: str = field(default_factory=lambda: utc_now_iso())

    def __post_init__(self) -> None:
        require_non_empty(self.ledger_id, "ledger_id")
        require_non_empty(self.project_id, "project_id")
        require_non_empty(self.partner_id, "partner_id")
        if self.partner_fee_type != "SubcontractedProfessionalServiceFees":
            raise PartnerPortalValidationError("partner_fee_type must be SubcontractedProfessionalServiceFees.")
        for field_name in ("total_client_fee_collected", "platform_share", "partner_share"):
            if getattr(self, field_name) < 0:
                raise PartnerPortalValidationError(f"{field_name} cannot be negative.")

    def to_item(self) -> Dict[str, Any]:
        return {
            "project_id": payout_key(self.project_id, self.ledger_id),
            "entity_type": "PayoutLedger",
            "ledger_id": self.ledger_id,
            "source_project_id": self.project_id,
            "partner_id": self.partner_id,
            "total_client_fee_collected": self.total_client_fee_collected,
            "platform_share": self.platform_share,
            "partner_share": self.partner_share,
            "contracted_rev_share_percentage": self.contracted_rev_share_percentage,
            "partner_fee_type": self.partner_fee_type,
            "architecture": self.architecture,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class AuditLedgerEntry:
    audit_entry_id: str
    project_id: str
    event_type: str
    actor_partner_id: str
    routing_status_before: Optional[str]
    routing_status_after: str
    project_status_after: Optional[str]
    notes: str
    created_at: str = field(default_factory=lambda: utc_now_iso())

    def to_item(self) -> Dict[str, Any]:
        return {
            "project_id": audit_key(self.project_id, self.audit_entry_id),
            "entity_type": "AuditLedger",
            "audit_entry_id": self.audit_entry_id,
            "source_project_id": self.project_id,
            "event_type": self.event_type,
            "actor_partner_id": self.actor_partner_id,
            "routing_status_before": self.routing_status_before,
            "routing_status_after": self.routing_status_after,
            "project_status_after": self.project_status_after,
            "notes": self.notes,
            "created_at": self.created_at,
            "immutable": True,
        }


class PartnerPortalRepository(Protocol):
    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        ...

    def update_project_status(self, project_id: str, status: str) -> None:
        ...

    def get_partner(self, partner_id: str) -> Optional[Partner]:
        ...

    def put_partner(self, partner: Partner) -> None:
        ...

    def get_routing(self, project_id: str) -> Optional[ProjectRouting]:
        ...

    def put_routing(self, routing: ProjectRouting) -> None:
        ...

    def put_payout_ledger(self, ledger: PayoutLedger) -> None:
        ...

    def put_audit_entry(self, entry: AuditLedgerEntry) -> None:
        ...


class DynamoDbPartnerPortalRepository:
    def __init__(self, table: Any) -> None:
        self.table = table

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        response = self.table.get_item(Key={"project_id": project_id}, ConsistentRead=True)
        item = response.get("Item")
        return dict(item) if item else None

    def update_project_status(self, project_id: str, status: str) -> None:
        self.table.update_item(
            Key={"project_id": project_id},
            UpdateExpression="SET #status = :status, updated_at = :updated_at",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues=to_dynamodb_item(
                {
                    ":status": status,
                    ":updated_at": utc_now_iso(),
                }
            ),
            ConditionExpression="attribute_exists(project_id)",
        )

    def get_partner(self, partner_id: str) -> Optional[Partner]:
        response = self.table.get_item(Key={"project_id": partner_key(partner_id)}, ConsistentRead=True)
        item = response.get("Item")
        return Partner.from_item(item) if item else None

    def put_partner(self, partner: Partner) -> None:
        self.table.put_item(Item=to_dynamodb_item(partner.to_item()))

    def get_routing(self, project_id: str) -> Optional[ProjectRouting]:
        response = self.table.get_item(Key={"project_id": routing_key(project_id)}, ConsistentRead=True)
        item = response.get("Item")
        return ProjectRouting.from_item(item) if item else None

    def put_routing(self, routing: ProjectRouting) -> None:
        self.table.put_item(Item=to_dynamodb_item(routing.to_item()))

    def put_payout_ledger(self, ledger: PayoutLedger) -> None:
        self.table.put_item(
            Item=to_dynamodb_item(ledger.to_item()),
            ConditionExpression="attribute_not_exists(project_id)",
        )

    def put_audit_entry(self, entry: AuditLedgerEntry) -> None:
        self.table.put_item(
            Item=to_dynamodb_item(entry.to_item()),
            ConditionExpression="attribute_not_exists(project_id)",
        )


class PrimeSubcontractorBillingUtility:
    @staticmethod
    def build_payout_ledger(
        project_id: str,
        partner: Partner,
        total_client_fee_collected: Decimal | int | float | str,
        ledger_id: Optional[str] = None,
    ) -> PayoutLedger:
        total_fee = money(total_client_fee_collected, "total_client_fee_collected")
        partner_percentage = validate_percentage(
            partner.contracted_rev_share_percentage,
            "contracted_rev_share_percentage",
        )
        partner_share = (total_fee * partner_percentage / Decimal("100")).quantize(CENT, rounding=ROUND_HALF_UP)
        platform_share = (total_fee - partner_share).quantize(CENT, rounding=ROUND_HALF_UP)
        return PayoutLedger(
            ledger_id=ledger_id or str(uuid.uuid4()),
            project_id=project_id,
            partner_id=partner.partner_id,
            total_client_fee_collected=total_fee,
            platform_share=platform_share,
            partner_share=partner_share,
            contracted_rev_share_percentage=partner_percentage,
        )


class PartnerPortalService:
    def __init__(self, repository: PartnerPortalRepository) -> None:
        self.repository = repository
        self.billing = PrimeSubcontractorBillingUtility()

    def assign_project_to_partner(self, project_id: str, partner_id: str) -> ProjectRouting:
        project = self._require_project(project_id)
        partner = self._require_partner(partner_id)
        project_state = infer_project_state(project)
        if not project_state:
            raise PartnerRoutingEligibilityError("Project jurisdiction could not be inferred from location.")
        if not partner.can_review_state(project_state):
            raise PartnerRoutingEligibilityError(
                f"Partner {partner.partner_id} is not licensed or federally authorized for {project_state}."
            )

        existing = self.repository.get_routing(project_id)
        if existing and existing.routing_status == RoutingStatus.PARTNER_SIGNED_OFF:
            raise PartnerPortalStateError("Project already has partner sign-off and cannot be reassigned.")

        routing = ProjectRouting(
            project_id=project_id,
            assigned_partner_id=partner.partner_id,
            routing_status=RoutingStatus.PENDING_PARTNER_ACCEPTANCE,
        )
        self.repository.put_routing(routing)
        self.repository.update_project_status(project_id, "PENDING_PARTNER_ACCEPTANCE")
        self.repository.put_audit_entry(
            audit_entry(
                project_id=project_id,
                event_type="PROJECT_ROUTED_TO_PARTNER",
                actor_partner_id=partner.partner_id,
                before=existing.routing_status.value if existing else RoutingStatus.UNASSIGNED.value,
                after=routing.routing_status.value,
                project_status_after="PENDING_PARTNER_ACCEPTANCE",
                notes=f"Project routed for {project_state} review.",
            )
        )
        return routing

    def generate_secure_review_payload(self, project_id: str) -> Dict[str, Any]:
        project = self._require_project(project_id)
        routing = self.repository.get_routing(project_id)
        if not routing or routing.routing_status == RoutingStatus.UNASSIGNED:
            raise PartnerPortalStateError("Project must be assigned before a partner payload can be generated.")

        partner = self._require_partner(routing.assigned_partner_id)
        report = project.get("analysis_report")
        if not isinstance(report, Mapping):
            raise PartnerPortalStateError("Project does not have an analysis report ready for partner review.")

        payload = {
            "payload_version": "2026-05-phase-2",
            "project_id": project_id,
            "read_only": True,
            "generated_at": utc_now_iso(),
            "routing": {
                "assigned_partner_id": partner.partner_id,
                "firm_name": partner.firm_name,
                "assigned_cpa_name": partner.assigned_cpa_name,
                "cpa_license_number": partner.cpa_license_number,
                "routing_status": routing.routing_status.value,
            },
            "review_document": {
                "title": "GrantStack Compliance Review Packet",
                "document_type": "AI_GENERATED_COMPLIANCE_DRAFT",
                "estimated_page_count": 50,
                "rendering": {
                    "json": "read_only",
                    "pdf": "render_from_payload_server_side",
                },
                "sections": [
                    "executive_summary",
                    "project_facts",
                    "eligibility_checks",
                    "program_recommendations",
                    "risk_flags",
                    "source_citations",
                    "partner_review_actions",
                ],
            },
            "project_facts": sanitize_partner_payload(project.get("input_spec", {})),
            "analysis_report": sanitize_partner_payload(report),
            "source_citations": extract_source_citations(report),
            "allowed_actions": [PartnerReviewAction.APPROVE.value, PartnerReviewAction.REQUEST_CHANGES.value],
            "internal_material_excluded": True,
        }
        assert_black_box_payload(payload)
        return payload

    def submit_cpa_review_action(self, project_id: str, action: str, notes: str = "") -> ProjectRouting:
        normalized_action = parse_review_action(action)
        sanitized_notes = require_notes(notes, required=normalized_action == PartnerReviewAction.REQUEST_CHANGES)
        routing = self._require_routing(project_id)
        partner = self._require_partner(routing.assigned_partner_id)
        if routing.routing_status == RoutingStatus.PARTNER_SIGNED_OFF:
            raise PartnerPortalStateError("Project already has partner sign-off.")

        if normalized_action == PartnerReviewAction.ACCEPT_REVIEW:
            next_status = RoutingStatus.UNDER_HUMAN_REVIEW
            project_status = "UNDER_HUMAN_REVIEW"
            event_type = "PARTNER_ACCEPTED_REVIEW"
        elif normalized_action == PartnerReviewAction.REQUEST_CHANGES:
            next_status = RoutingStatus.CHANGES_REQUESTED
            project_status = "HUMAN_REVIEW_CHANGES_REQUESTED"
            event_type = "PARTNER_REQUESTED_CHANGES"
        else:
            next_status = RoutingStatus.PARTNER_SIGNED_OFF
            project_status = "AUDIT_READY"
            event_type = "PARTNER_SIGNED_OFF"

        updated = ProjectRouting(
            project_id=project_id,
            assigned_partner_id=partner.partner_id,
            routing_status=next_status,
            partner_notes=sanitized_notes,
            routing_event_id=routing.routing_event_id,
            created_at=routing.created_at,
            updated_at=utc_now_iso(),
        )
        self.repository.put_routing(updated)
        self.repository.update_project_status(project_id, project_status)
        self.repository.put_audit_entry(
            audit_entry(
                project_id=project_id,
                event_type=event_type,
                actor_partner_id=partner.partner_id,
                before=routing.routing_status.value,
                after=next_status.value,
                project_status_after=project_status,
                notes=sanitized_notes,
            )
        )
        return updated

    def record_payout_ledger(
        self,
        project_id: str,
        total_client_fee_collected: Decimal | int | float | str,
        ledger_id: Optional[str] = None,
    ) -> PayoutLedger:
        routing = self._require_routing(project_id)
        partner = self._require_partner(routing.assigned_partner_id)
        ledger = self.billing.build_payout_ledger(
            project_id=project_id,
            partner=partner,
            total_client_fee_collected=total_client_fee_collected,
            ledger_id=ledger_id,
        )
        self.repository.put_payout_ledger(ledger)
        return ledger

    def _require_project(self, project_id: str) -> Dict[str, Any]:
        require_non_empty(project_id, "project_id")
        try:
            project = self.repository.get_project(project_id)
        except (BotoCoreError, ClientError) as exc:
            raise PartnerPortalError("Project lookup failed.") from exc
        if not project:
            raise PartnerPortalNotFoundError(f"Project {project_id} was not found.")
        return project

    def _require_partner(self, partner_id: str) -> Partner:
        require_non_empty(partner_id, "partner_id")
        try:
            partner = self.repository.get_partner(partner_id)
        except (BotoCoreError, ClientError) as exc:
            raise PartnerPortalError("Partner lookup failed.") from exc
        if not partner:
            raise PartnerPortalNotFoundError(f"Partner {partner_id} was not found.")
        return partner

    def _require_routing(self, project_id: str) -> ProjectRouting:
        require_non_empty(project_id, "project_id")
        routing = self.repository.get_routing(project_id)
        if not routing:
            raise PartnerPortalNotFoundError(f"Routing record for project {project_id} was not found.")
        return routing


def service_from_environment(table_name: str) -> PartnerPortalService:
    table = boto3.resource("dynamodb").Table(table_name)
    return PartnerPortalService(DynamoDbPartnerPortalRepository(table))


def audit_entry(
    project_id: str,
    event_type: str,
    actor_partner_id: str,
    before: Optional[str],
    after: str,
    project_status_after: Optional[str],
    notes: str,
) -> AuditLedgerEntry:
    return AuditLedgerEntry(
        audit_entry_id=str(uuid.uuid4()),
        project_id=project_id,
        event_type=event_type,
        actor_partner_id=actor_partner_id,
        routing_status_before=before,
        routing_status_after=after,
        project_status_after=project_status_after,
        notes=notes,
    )


def infer_project_state(project: Mapping[str, Any]) -> Optional[str]:
    spec = project.get("input_spec") if isinstance(project.get("input_spec"), Mapping) else {}
    location = str(spec.get("location", ""))
    return infer_state_code(location)


def infer_state_code(location: str) -> Optional[str]:
    match = STATE_PATTERN.search(location)
    if match:
        return match.group(1).upper()
    normalized_location = re.sub(r"[^A-Za-z ]+", " ", location).upper()
    words = " ".join(normalized_location.split())
    for state_name, code in STATE_NAME_TO_CODE.items():
        if re.search(rf"\b{re.escape(state_name)}\b", words):
            return code
    return None


def normalize_jurisdiction(value: str) -> str:
    normalized = str(value).strip().upper()
    if normalized in {"*", "FED", "FEDERAL", "NATIONAL", "US", "USA", "UNITED STATES"}:
        return FEDERAL_JURISDICTION
    if normalized in STATE_NAME_TO_CODE:
        return STATE_NAME_TO_CODE[normalized]
    if normalized in set(STATE_NAME_TO_CODE.values()):
        return normalized
    raise PartnerPortalValidationError(f"Unsupported jurisdiction: {value}")


def parse_review_action(action: str) -> PartnerReviewAction:
    try:
        return PartnerReviewAction(str(action).strip().upper())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in PartnerReviewAction)
        raise PartnerPortalValidationError(f"Unsupported review action. Allowed actions: {allowed}.") from exc


def require_notes(notes: str, required: bool) -> str:
    if not isinstance(notes, str):
        raise PartnerPortalValidationError("notes must be a string.")
    sanitized = notes.strip()
    if required and not sanitized:
        raise PartnerPortalValidationError("notes are required when requesting changes.")
    if len(sanitized) > MAX_NOTES_LENGTH:
        raise PartnerPortalValidationError(f"notes must be {MAX_NOTES_LENGTH} characters or fewer.")
    return sanitized


def sanitize_partner_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        sanitized: Dict[str, Any] = {}
        for key, nested in value.items():
            key_text = str(key)
            if is_internal_key(key_text):
                continue
            sanitized[key_text] = sanitize_partner_payload(nested)
        return sanitized
    if isinstance(value, list):
        return [sanitize_partner_payload(item) for item in value]
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    return value


def assert_black_box_payload(payload: Mapping[str, Any]) -> None:
    offending_keys = find_internal_keys(payload)
    if offending_keys:
        raise PartnerPortalStateError(f"Partner review payload included internal-only keys: {', '.join(offending_keys)}")


def find_internal_keys(value: Any, path: str = "") -> List[str]:
    findings: List[str] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            if is_internal_key(key_text):
                findings.append(child_path)
            findings.extend(find_internal_keys(nested, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(find_internal_keys(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        normalized = value.lower()
        if any(fragment in normalized for fragment in INTERNAL_PAYLOAD_VALUE_FRAGMENTS):
            findings.append(path or "<root>")
    return findings


def is_internal_key(key: str) -> bool:
    normalized = key.lower()
    return any(fragment in normalized for fragment in INTERNAL_PAYLOAD_KEY_FRAGMENTS)


def extract_source_citations(report: Mapping[str, Any]) -> List[Dict[str, str]]:
    citations: List[Dict[str, str]] = []
    for program in report.get("recommended_programs", []):
        if not isinstance(program, Mapping):
            continue
        source_url = str(program.get("source_url") or "").strip()
        if not source_url:
            continue
        citations.append(
            {
                "name": str(program.get("name") or "Referenced program"),
                "source_url": source_url,
                "jurisdiction": str(program.get("jurisdiction") or ""),
            }
        )
    return citations


def validate_percentage(value: Decimal | int | float | str, field_name: str) -> Decimal:
    percentage = to_decimal(value)
    if percentage < 0 or percentage > 100:
        raise PartnerPortalValidationError(f"{field_name} must be between 0 and 100.")
    return percentage


def money(value: Decimal | int | float | str, field_name: str) -> Decimal:
    amount = to_decimal(value)
    if amount < 0:
        raise PartnerPortalValidationError(f"{field_name} cannot be negative.")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def to_decimal(value: Decimal | int | float | str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PartnerPortalValidationError(f"{value!r} is not a valid decimal value.") from exc


def require_non_empty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PartnerPortalValidationError(f"{field_name} must be a non-empty string.")
    return value.strip()


def partner_key(partner_id: str) -> str:
    return f"PARTNER#{partner_id}"


def routing_key(project_id: str) -> str:
    return f"ROUTING#{project_id}"


def payout_key(project_id: str, ledger_id: str) -> str:
    return f"PAYOUT#{project_id}#{ledger_id}"


def audit_key(project_id: str, audit_entry_id: str) -> str:
    return f"AUDIT#{project_id}#{audit_entry_id}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_dynamodb_item(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, Decimal):
        return value
    if isinstance(value, Mapping):
        return {key: to_dynamodb_item(nested) for key, nested in value.items() if nested is not None}
    if isinstance(value, list):
        return [to_dynamodb_item(item) for item in value]
    return value
