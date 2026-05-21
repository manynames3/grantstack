import base64
import json
import logging
import math
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Mapping

import boto3
from botocore.exceptions import BotoCoreError, ClientError


logger = logging.getLogger()
logger.setLevel(logging.INFO)

sqs_client = boto3.client("sqs")
dynamodb = boto3.resource("dynamodb")

REQUIRED_FIELDS = {"location", "capex", "jobs", "facility_type"}
OPTIONAL_FIELDS = {
    "metadata",
    "request_id",
    "contact_email",
    "company_name",
    "project_timeline",
    "average_wage",
    "competing_locations",
    "site_control",
}
ALLOWED_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_BODY_BYTES = 64_000
MAX_TEXT_LENGTH = 300
MAX_METADATA_JSON_BYTES = 8_000
MAX_CAPEX = 10_000_000_000
MAX_JOBS = 100_000


class RequestValidationError(ValueError):
    """Raised when an incoming project spec fails validation."""


def lambda_handler(event: Mapping[str, Any], context: Any) -> Dict[str, Any]:
    try:
        queue_url = require_env("SQS_QUEUE_URL")
        table_name = require_env("DYNAMODB_TABLE_NAME")
        project_ttl_days = parse_non_negative_int(os.environ.get("PROJECT_TTL_DAYS", "30"))
        project_spec = parse_and_validate_body(event)
        project_id = str(uuid.uuid4())
        access_token = secrets.token_urlsafe(32)
        received_at = utc_now_iso()
        table = dynamodb.Table(table_name)

        queue_message = {
            "project_id": project_id,
            "status": "ACCEPTED",
            "received_at": received_at,
            "spec": project_spec,
        }

        table.put_item(
            Item=to_dynamodb_json(
                {
                    "project_id": project_id,
                    "access_token": access_token,
                    "status": "ACCEPTED",
                    "input_spec": project_spec,
                    "created_at": received_at,
                    "updated_at": received_at,
                    "received_at": received_at,
                    "source": "api",
                    **ttl_attribute(project_ttl_days),
                }
            ),
            ConditionExpression="attribute_not_exists(project_id)",
        )

        try:
            sqs_client.send_message(
                QueueUrl=queue_url,
                MessageBody=json.dumps(queue_message, separators=(",", ":")),
                MessageAttributes={
                    "project_id": {
                        "DataType": "String",
                        "StringValue": project_id,
                    }
                },
            )
        except (BotoCoreError, ClientError):
            mark_queue_failed(table, project_id)
            raise

        logger.info("queued_project project_id=%s", project_id)

        return response(
            202,
            {
                "project_id": project_id,
                "access_token": access_token,
                "status": "ACCEPTED",
            },
        )
    except RequestValidationError as exc:
        logger.info("invalid_project_request error=%s", exc)
        return response(400, {"error": "INVALID_REQUEST", "message": str(exc)})
    except (BotoCoreError, ClientError):
        logger.exception("failed_to_enqueue_project")
        return response(
            503,
            {
                "error": "QUEUE_UNAVAILABLE",
                "message": "Project was validated but could not be queued. Retry the request.",
            },
        )
    except Exception:
        logger.exception("unexpected_ingestion_error")
        return response(500, {"error": "INTERNAL_ERROR", "message": "Unexpected ingestion failure."})


def parse_and_validate_body(event: Mapping[str, Any]) -> Dict[str, Any]:
    body = event.get("body")
    if body is None:
        raise RequestValidationError("Request body is required.")

    if event.get("isBase64Encoded"):
        try:
            body = base64.b64decode(body).decode("utf-8")
        except Exception as exc:
            raise RequestValidationError("Request body is not valid base64-encoded UTF-8.") from exc

    if isinstance(body, str):
        if len(body.encode("utf-8")) > MAX_BODY_BYTES:
            raise RequestValidationError("Request body is too large.")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RequestValidationError("Request body must be valid JSON.") from exc
    elif isinstance(body, dict):
        payload = body
    else:
        raise RequestValidationError("Request body must be a JSON object.")

    if not isinstance(payload, dict):
        raise RequestValidationError("Request body must be a JSON object.")

    unknown_fields = sorted(set(payload) - ALLOWED_FIELDS)
    if unknown_fields:
        raise RequestValidationError(f"Unsupported fields: {', '.join(unknown_fields)}.")

    missing_fields = sorted(REQUIRED_FIELDS - set(payload))
    if missing_fields:
        raise RequestValidationError(f"Missing required fields: {', '.join(missing_fields)}.")

    validated = {
        "location": require_limited_string(payload["location"], "location"),
        "capex": require_positive_number(payload["capex"], "capex"),
        "jobs": require_non_negative_integer(payload["jobs"], "jobs"),
        "facility_type": require_limited_string(payload["facility_type"], "facility_type"),
    }

    if "metadata" in payload:
        if not isinstance(payload["metadata"], dict):
            raise RequestValidationError("metadata must be an object when provided.")
        if len(json.dumps(payload["metadata"], separators=(",", ":")).encode("utf-8")) > MAX_METADATA_JSON_BYTES:
            raise RequestValidationError("metadata is too large.")
        validated["metadata"] = payload["metadata"]

    if "request_id" in payload:
        validated["request_id"] = require_limited_string(payload["request_id"], "request_id")

    if "contact_email" in payload:
        validated["contact_email"] = require_email(payload["contact_email"], "contact_email")

    for field_name in ("company_name", "project_timeline", "competing_locations", "site_control"):
        if field_name in payload:
            validated[field_name] = require_limited_string(payload[field_name], field_name)

    if "average_wage" in payload:
        validated["average_wage"] = require_positive_number(payload["average_wage"], "average_wage", max_value=1_000_000)

    return validated


def require_non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RequestValidationError(f"{field_name} must be a non-empty string.")
    return value.strip()


def require_limited_string(value: Any, field_name: str) -> str:
    text = require_non_empty_string(value, field_name)
    if len(text) > MAX_TEXT_LENGTH:
        raise RequestValidationError(f"{field_name} must be {MAX_TEXT_LENGTH} characters or fewer.")
    return text


def require_positive_number(value: Any, field_name: str, max_value: float = MAX_CAPEX) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RequestValidationError(f"{field_name} must be a positive number.")
    numeric_value = float(value)
    if not math.isfinite(numeric_value) or numeric_value <= 0:
        raise RequestValidationError(f"{field_name} must be a positive number.")
    if numeric_value > max_value:
        raise RequestValidationError(f"{field_name} is outside the supported screening range.")
    return numeric_value


def require_non_negative_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RequestValidationError(f"{field_name} must be a non-negative integer.")
    if value < 0:
        raise RequestValidationError(f"{field_name} must be a non-negative integer.")
    if value > MAX_JOBS:
        raise RequestValidationError(f"{field_name} is outside the supported screening range.")
    return value


def require_email(value: Any, field_name: str) -> str:
    email = require_non_empty_string(value, field_name).lower()
    if not EMAIL_PATTERN.match(email):
        raise RequestValidationError(f"{field_name} must be a valid email address.")
    return email


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def parse_non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError("PROJECT_TTL_DAYS must be an integer.") from exc
    if parsed < 0:
        raise RuntimeError("PROJECT_TTL_DAYS must be non-negative.")
    return parsed


def ttl_attribute(project_ttl_days: int) -> Dict[str, int]:
    if project_ttl_days <= 0:
        return {}
    return {"expires_at": int(time.time()) + project_ttl_days * 86400}


def mark_queue_failed(table: Any, project_id: str) -> None:
    try:
        now = utc_now_iso()
        table.update_item(
            Key={"project_id": project_id},
            UpdateExpression="SET #status = :status, failure = :failure, updated_at = :updated_at",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues=to_dynamodb_json(
                {
                    ":status": "QUEUE_FAILED",
                    ":failure": {
                        "message": "Project was accepted but could not be queued for processing.",
                        "type": "QueueUnavailable",
                    },
                    ":updated_at": now,
                }
            ),
        )
    except (BotoCoreError, ClientError):
        logger.exception("failed_to_record_queue_failure project_id=%s", project_id)


def to_dynamodb_json(value: Mapping[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(value), parse_float=Decimal)


def response(status_code: int, payload: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "content-type": "application/json",
            "cache-control": "no-store",
        },
        "body": json.dumps(payload, separators=(",", ":")),
    }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
