import base64
import json
import logging
import math
import os
import re
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Mapping

import boto3
from botocore.exceptions import BotoCoreError, ClientError


logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")

MAX_BODY_BYTES = 16_000
MAX_TEXT_LENGTH = 500
MAX_PROPERTIES_JSON_BYTES = 4_000
MAX_PROPERTY_DEPTH = 3
MAX_PROPERTY_ITEMS = 24
EVENT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_:. -]{0,79}$")


class AnalyticsValidationError(ValueError):
    """Raised when a browser analytics event fails validation."""


def lambda_handler(event: Mapping[str, Any], context: Any) -> Dict[str, Any]:
    try:
        table_name = require_env("ANALYTICS_TABLE_NAME")
        analytics_ttl_days = parse_non_negative_int(os.environ.get("ANALYTICS_TTL_DAYS", "90"))
        payload = parse_and_validate_body(event)
        event_id = str(uuid.uuid4())
        received_at = utc_now_iso()

        item = {
            "event_id": event_id,
            "event_name": payload["event_name"],
            "received_at": received_at,
            "source": "grantstack-pages",
            "page_path": payload.get("page_path", ""),
            "page_title": payload.get("page_title", ""),
            "referrer": payload.get("referrer", ""),
            "session_id": payload.get("session_id", ""),
            "properties": payload.get("properties", {}),
            **ttl_attribute(analytics_ttl_days),
        }

        dynamodb.Table(table_name).put_item(Item=to_dynamodb_json(item))
        logger.info(
            "analytics_event_recorded event_name=%s page_path=%s",
            item["event_name"],
            item["page_path"],
        )
        return response(202, {"status": "ACCEPTED"})
    except AnalyticsValidationError as exc:
        logger.info("invalid_analytics_event error=%s", exc)
        return response(400, {"error": "INVALID_ANALYTICS_EVENT", "message": str(exc)})
    except (BotoCoreError, ClientError):
        logger.exception("failed_to_record_analytics_event")
        return response(503, {"error": "ANALYTICS_UNAVAILABLE", "message": "Analytics event could not be recorded."})
    except Exception:
        logger.exception("unexpected_analytics_error")
        return response(500, {"error": "INTERNAL_ERROR", "message": "Unexpected analytics failure."})


def parse_and_validate_body(event: Mapping[str, Any]) -> Dict[str, Any]:
    body = event.get("body")
    if body is None:
        raise AnalyticsValidationError("Request body is required.")

    if event.get("isBase64Encoded"):
        try:
            body = base64.b64decode(body).decode("utf-8")
        except Exception as exc:
            raise AnalyticsValidationError("Request body is not valid base64-encoded UTF-8.") from exc

    if isinstance(body, str):
        if len(body.encode("utf-8")) > MAX_BODY_BYTES:
            raise AnalyticsValidationError("Request body is too large.")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise AnalyticsValidationError("Request body must be valid JSON.") from exc
    elif isinstance(body, dict):
        payload = body
    else:
        raise AnalyticsValidationError("Request body must be a JSON object.")

    if not isinstance(payload, dict):
        raise AnalyticsValidationError("Request body must be a JSON object.")

    event_name = require_event_name(payload.get("event_name"))
    validated = {
        "event_name": event_name,
        "page_path": optional_limited_string(payload.get("page_path"), "page_path"),
        "page_title": optional_limited_string(payload.get("page_title"), "page_title"),
        "referrer": optional_limited_string(payload.get("referrer"), "referrer"),
        "session_id": optional_limited_string(payload.get("session_id"), "session_id"),
        "properties": sanitize_properties(payload.get("properties", {})),
    }

    if len(json.dumps(validated["properties"], separators=(",", ":")).encode("utf-8")) > MAX_PROPERTIES_JSON_BYTES:
        raise AnalyticsValidationError("properties is too large.")

    return validated


def require_event_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnalyticsValidationError("event_name must be a non-empty string.")
    event_name = value.strip().lower()
    if not EVENT_NAME_PATTERN.match(event_name):
        raise AnalyticsValidationError("event_name contains unsupported characters.")
    return event_name


def optional_limited_string(value: Any, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise AnalyticsValidationError(f"{field_name} must be a string when provided.")
    text = value.strip()
    if len(text) > MAX_TEXT_LENGTH:
        raise AnalyticsValidationError(f"{field_name} must be {MAX_TEXT_LENGTH} characters or fewer.")
    return text


def sanitize_properties(value: Any, depth: int = 0) -> Any:
    if depth > MAX_PROPERTY_DEPTH:
        raise AnalyticsValidationError("properties is nested too deeply.")

    if value is None or isinstance(value, bool):
        return value

    if isinstance(value, str):
        return value.strip()[:MAX_TEXT_LENGTH]

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric_value = float(value)
        if not math.isfinite(numeric_value):
            raise AnalyticsValidationError("properties contains a non-finite number.")
        return value

    if isinstance(value, list):
        return [sanitize_properties(item, depth + 1) for item in value[:MAX_PROPERTY_ITEMS]]

    if isinstance(value, dict):
        sanitized = {}
        for raw_key, raw_value in list(value.items())[:MAX_PROPERTY_ITEMS]:
            key = optional_limited_string(raw_key, "property key")[:80]
            if key:
                sanitized[key] = sanitize_properties(raw_value, depth + 1)
        return sanitized

    raise AnalyticsValidationError("properties contains an unsupported value.")


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def parse_non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError("ANALYTICS_TTL_DAYS must be an integer.") from exc
    if parsed < 0:
        raise RuntimeError("ANALYTICS_TTL_DAYS must be non-negative.")
    return parsed


def ttl_attribute(ttl_days: int) -> Dict[str, int]:
    if ttl_days <= 0:
        return {}
    return {"expires_at": int(time.time()) + ttl_days * 86400}


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
