import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

import boto3
from botocore.exceptions import BotoCoreError, ClientError


logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")

CATALOG_PATH = Path(__file__).with_name("incentive_catalog.json")
MAX_SOURCE_BYTES = 262_144


def lambda_handler(event: Mapping[str, Any], context: Any) -> Dict[str, Any]:
    bucket = require_env("SOURCE_CATALOG_BUCKET")
    key = require_env("SOURCE_CATALOG_KEY")
    catalog = load_catalog(bucket, key)
    checked_at = utc_now_iso()
    ok_count = 0
    failed_count = 0

    for source in catalog.get("sources", []):
        if not isinstance(source, dict):
            continue
        result = verify_source_url(str(source.get("source_url", "")))
        source["last_checked_at"] = checked_at
        source["retrieval_status"] = result["status"]
        source["retrieval_http_status"] = result.get("http_status")
        source["retrieval_error"] = result.get("error")
        source["content_sha256"] = result.get("content_sha256")
        if result["status"] == "ok":
            ok_count += 1
        else:
            failed_count += 1

    catalog["version"] = checked_at[:10]
    catalog["last_refreshed_at"] = checked_at
    catalog["refresh_summary"] = {
        "ok": ok_count,
        "failed": failed_count,
        "source_count": ok_count + failed_count,
    }

    put_catalog(bucket, key, catalog)
    logger.info("source_catalog_refreshed bucket=%s key=%s ok=%s failed=%s", bucket, key, ok_count, failed_count)
    return catalog["refresh_summary"]


def load_catalog(bucket: str, key: str) -> Dict[str, Any]:
    local_catalog = load_local_catalog()
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        payload = response["Body"].read().decode("utf-8")
        s3_catalog = json.loads(payload)
    except s3_client.exceptions.NoSuchKey:
        return local_catalog
    except (BotoCoreError, ClientError, KeyError, json.JSONDecodeError):
        logger.exception("failed_to_load_s3_catalog using_local_fallback bucket=%s key=%s", bucket, key)
        return local_catalog

    if not isinstance(s3_catalog, dict) or not isinstance(s3_catalog.get("sources"), list):
        raise RuntimeError("Source catalog must be an object containing a sources list.")
    return merge_catalog_status(local_catalog, s3_catalog)


def load_local_catalog() -> Dict[str, Any]:
    with CATALOG_PATH.open("r", encoding="utf-8") as handle:
        catalog = json.load(handle)
    if not isinstance(catalog, dict):
        raise RuntimeError("Local source catalog must be a JSON object.")
    return catalog


def merge_catalog_status(local_catalog: Dict[str, Any], s3_catalog: Mapping[str, Any]) -> Dict[str, Any]:
    status_by_id = {
        source.get("id"): source
        for source in s3_catalog.get("sources", [])
        if isinstance(source, dict) and source.get("id")
    }
    status_fields = {
        "last_checked_at",
        "retrieval_status",
        "retrieval_http_status",
        "retrieval_error",
        "content_sha256",
    }

    for source in local_catalog.get("sources", []):
        if not isinstance(source, dict):
            continue
        previous = status_by_id.get(source.get("id"))
        if not isinstance(previous, dict):
            continue
        for field_name in status_fields:
            if field_name in previous:
                source[field_name] = previous[field_name]

    return local_catalog


def verify_source_url(source_url: str) -> Dict[str, Any]:
    if not source_url.startswith("https://"):
        return {"status": "failed", "error": "source_url must use https"}

    request = urllib.request.Request(
        source_url,
        headers={
            "user-agent": "GrantStackSourceRefresh/1.0",
            "accept": "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.8",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            content = response.read(MAX_SOURCE_BYTES)
            http_status = int(response.status)
    except urllib.error.HTTPError as exc:
        return {"status": "failed", "http_status": exc.code, "error": f"http_error_{exc.code}"}
    except urllib.error.URLError as exc:
        return {"status": "failed", "error": str(exc.reason)[:240]}

    if http_status < 200 or http_status >= 400:
        return {"status": "failed", "http_status": http_status, "error": f"http_status_{http_status}"}

    return {
        "status": "ok",
        "http_status": http_status,
        "content_sha256": hashlib.sha256(content).hexdigest(),
    }


def put_catalog(bucket: str, key: str, catalog: Mapping[str, Any]) -> None:
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(catalog, separators=(",", ":"), sort_keys=True).encode("utf-8"),
        ContentType="application/json",
        ServerSideEncryption="AES256",
    )


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
