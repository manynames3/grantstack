import json
import logging
import os
from decimal import Decimal
from typing import Any, Dict, Mapping, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError


logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")


def lambda_handler(event: Mapping[str, Any], context: Any) -> Dict[str, Any]:
    try:
        project_id = get_project_id(event)
        if project_id is None:
            return response(200, service_index())

        table = dynamodb.Table(require_env("DYNAMODB_TABLE_NAME"))
        access_token = get_access_token(event)

        item = get_project(table, project_id)
        if not item:
            return response(404, {"error": "NOT_FOUND", "message": "Project was not found."})

        if item.get("access_token") != access_token:
            logger.info("invalid_report_token project_id=%s", project_id)
            return response(403, {"error": "FORBIDDEN", "message": "Invalid report access token."})

        return response(200, public_project_view(item))
    except RequestError as exc:
        return response(exc.status_code, {"error": exc.error_code, "message": str(exc)})
    except (BotoCoreError, ClientError):
        logger.exception("failed_to_fetch_project_report")
        return response(503, {"error": "REPORT_UNAVAILABLE", "message": "Report is temporarily unavailable."})
    except Exception:
        logger.exception("unexpected_report_error")
        return response(500, {"error": "INTERNAL_ERROR", "message": "Unexpected report retrieval failure."})


class RequestError(ValueError):
    def __init__(self, status_code: int, error_code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


def get_project_id(event: Mapping[str, Any]) -> Optional[str]:
    path_parameters = event.get("pathParameters") or {}
    project_id = path_parameters.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip():
        return None
    return project_id.strip()


def get_access_token(event: Mapping[str, Any]) -> str:
    query_parameters = event.get("queryStringParameters") or {}
    access_token = query_parameters.get("token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise RequestError(401, "TOKEN_REQUIRED", "Report access token is required.")
    return access_token.strip()


def get_project(table: Any, project_id: str) -> Dict[str, Any]:
    response = table.get_item(Key={"project_id": project_id}, ConsistentRead=True)
    return response.get("Item", {})


def service_index() -> Dict[str, Any]:
    return {
        "service": "GrantStack Projects API",
        "status": "OK",
        "landing_page": "https://grantstack.pages.dev",
        "endpoints": {
            "submit_project": "POST /projects",
            "read_private_report": "GET /projects/{project_id}?token={access_token}",
        },
    }


def public_project_view(item: Mapping[str, Any]) -> Dict[str, Any]:
    status = item.get("status", "UNKNOWN")
    view: Dict[str, Any] = {
        "project_id": item.get("project_id"),
        "status": status,
        "input_spec": item.get("input_spec", {}),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "received_at": item.get("received_at"),
        "completed_at": item.get("completed_at"),
        "failed_at": item.get("failed_at"),
    }

    if status == "COMPLETED":
        view["analysis_report"] = item.get("analysis_report", {})
        view["llm_metadata"] = item.get("llm_metadata", {})
        view["vector_matches"] = item.get("vector_matches", [])

    if status == "FAILED":
        view["failure"] = item.get("failure", {"message": "Project processing failed."})

    return view


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def response(status_code: int, payload: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "content-type": "application/json",
            "cache-control": "no-store",
        },
        "body": json.dumps(payload, default=json_default, separators=(",", ":")),
    }


def json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
