import asyncio
import base64
import json
import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

import boto3
from botocore.exceptions import BotoCoreError, ClientError


logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
secretsmanager = boto3.client("secretsmanager")
s3_client = boto3.client("s3")

CATALOG_PATH = Path(__file__).with_name("incentive_catalog.json")
STATE_PATTERN = re.compile(r"(?:,\s*|\b)(A[LKSZR]|C[AOT]|D[CE]|F[LM]|G[AU]|HI|I[ADLN]|K[SY]|LA|M[ADEHINOST]|N[CDEHJMVY]|O[HKR]|P[AWR]|RI|S[CD]|T[NX]|UT|V[AIT]|W[AIVY])\b", re.IGNORECASE)


class ProcessingError(RuntimeError):
    """Raised for project processing failures that should be retried by SQS."""


@dataclass(frozen=True)
class RuntimeConfig:
    table_name: str
    vector_db_provider: str
    vector_db_endpoint: str
    vector_db_api_key: str
    embedding_provider: str
    embedding_api_endpoint: str
    embedding_api_key: str
    embedding_model: str
    llm_provider: str
    llm_api_endpoint: str
    llm_api_key: str
    llm_model: str
    mock_external_calls: bool
    http_timeout_seconds: int


def lambda_handler(event: Mapping[str, Any], context: Any) -> Dict[str, List[Dict[str, str]]]:
    config = load_config()
    table = dynamodb.Table(config.table_name)
    failed_items: List[Dict[str, str]] = []

    for record in event.get("Records", []):
        message_id = record.get("messageId", "unknown-message")
        try:
            asyncio.run(process_record(record, table, config))
        except Exception as exc:
            failed_items.append({"itemIdentifier": message_id})
            logger.exception("project_processing_failed message_id=%s error=%s", message_id, exc)

    return {"batchItemFailures": failed_items}


async def process_record(record: Mapping[str, Any], table: Any, config: RuntimeConfig) -> None:
    message = parse_sqs_message(record)
    project_id = message["project_id"]
    spec = message["spec"]

    await mark_processing(table, project_id, spec, message.get("received_at"))

    try:
        vector_matches = await query_vector_database(spec, config)
        llm_payload = build_llm_payload(project_id, spec, vector_matches, config.llm_model)
        analysis_report = await execute_llm_analysis(llm_payload, spec, vector_matches, config)
        await mark_completed(table, project_id, vector_matches, analysis_report, config)
        logger.info(
            "project_completed project_id=%s source_count=%s external_llm_used=%s",
            project_id,
            len(vector_matches),
            not config.mock_external_calls,
        )
    except Exception as exc:
        await mark_failed(table, project_id, exc)
        raise ProcessingError(f"Project {project_id} failed during processing.") from exc


def parse_sqs_message(record: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        message = json.loads(record["body"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise ProcessingError("SQS record body must be valid JSON.") from exc

    if not isinstance(message, dict):
        raise ProcessingError("SQS message body must be a JSON object.")

    if not isinstance(message.get("project_id"), str) or not message["project_id"]:
        raise ProcessingError("SQS message is missing project_id.")

    spec = message.get("spec")
    if not isinstance(spec, dict):
        raise ProcessingError("SQS message is missing spec object.")

    return message


async def mark_processing(table: Any, project_id: str, spec: Mapping[str, Any], received_at: Optional[str]) -> None:
    now = utc_now_iso()
    await asyncio.to_thread(
        table.update_item,
        Key={"project_id": project_id},
        UpdateExpression=(
            "SET #status = :status, "
            "input_spec = :spec, "
            "received_at = if_not_exists(received_at, :received_at), "
            "created_at = if_not_exists(created_at, :created_at), "
            "updated_at = :updated_at, "
            "attempt_count = if_not_exists(attempt_count, :zero) + :one"
        ),
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues=to_dynamodb_json(
            {
                ":status": "PROCESSING",
                ":spec": dict(spec),
                ":received_at": received_at or now,
                ":created_at": now,
                ":updated_at": now,
                ":zero": 0,
                ":one": 1,
            }
        ),
    )


async def mark_completed(
    table: Any,
    project_id: str,
    vector_matches: List[Dict[str, Any]],
    analysis_report: Mapping[str, Any],
    config: RuntimeConfig,
) -> None:
    now = utc_now_iso()
    await asyncio.to_thread(
        table.update_item,
        Key={"project_id": project_id},
        UpdateExpression=(
            "SET #status = :status, "
            "analysis_report = :analysis_report, "
            "vector_matches = :vector_matches, "
            "llm_metadata = :llm_metadata, "
            "updated_at = :updated_at, "
            "completed_at = :completed_at REMOVE failure"
        ),
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues=to_dynamodb_json(
            {
                ":status": "COMPLETED",
                ":analysis_report": dict(analysis_report),
                ":vector_matches": vector_matches,
                ":llm_metadata": {
                    "model": config.llm_model,
                    "completed_at": now,
                    "response_format": "structured_json",
                    "analysis_mode": analysis_report.get("analysis_mode", "source_backed"),
                    "catalog_version": analysis_report.get("catalog_version"),
                    "external_llm_used": not config.mock_external_calls,
                    "source_count": len(vector_matches),
                },
                ":updated_at": now,
                ":completed_at": now,
            }
        ),
    )


async def mark_failed(table: Any, project_id: str, exc: Exception) -> None:
    now = utc_now_iso()
    sanitized_message = str(exc)[:500] or exc.__class__.__name__
    try:
        await asyncio.to_thread(
            table.update_item,
            Key={"project_id": project_id},
            UpdateExpression=(
                "SET #status = :status, "
                "failure = :failure, "
                "updated_at = :updated_at, "
                "failed_at = :failed_at"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues=to_dynamodb_json(
                {
                    ":status": "FAILED",
                    ":failure": {
                        "message": sanitized_message,
                        "type": exc.__class__.__name__,
                    },
                    ":updated_at": now,
                    ":failed_at": now,
                }
            ),
        )
    except (BotoCoreError, ClientError):
        logger.exception("failed_to_record_project_failure project_id=%s", project_id)


async def query_vector_database(spec: Mapping[str, Any], config: RuntimeConfig) -> List[Dict[str, Any]]:
    local_matches = query_local_incentive_catalog(spec)
    if config.mock_external_calls:
        await asyncio.sleep(0)
        return local_matches

    external_matches = await query_external_vector_database(spec, config)
    return merge_matches(local_matches, external_matches)


def query_local_incentive_catalog(spec: Mapping[str, Any]) -> List[Dict[str, Any]]:
    catalog = load_catalog()
    state_code = infer_state_code(str(spec.get("location", "")))
    scored: List[Dict[str, Any]] = []

    for source in catalog["sources"]:
        score = score_catalog_source(source, spec, state_code)
        if score <= 0:
            continue
        scored.append(to_vector_match(source, score, spec, state_code))

    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:6]


async def query_external_vector_database(spec: Mapping[str, Any], config: RuntimeConfig) -> List[Dict[str, Any]]:
    if not config.vector_db_endpoint:
        raise ProcessingError("VECTOR_DB_ENDPOINT is required when MOCK_EXTERNAL_CALLS is false.")
    if is_unset_secret(config.vector_db_api_key):
        raise ProcessingError("VECTOR_DB_API_KEY is required when MOCK_EXTERNAL_CALLS is false.")

    query_text = (
        f"Grant incentives for {spec['facility_type']} projects in {spec['location']} "
        f"with capex {spec['capex']} and {spec['jobs']} jobs."
    )
    if config.vector_db_provider == "pinecone":
        embedding = await create_embedding(query_text, config)
        response = await post_json(
            pinecone_query_url(config.vector_db_endpoint),
            {
                "vector": embedding,
                "topK": 5,
                "includeMetadata": True,
            },
            headers={
                "api-key": config.vector_db_api_key,
                "content-type": "application/json",
            },
            timeout_seconds=config.http_timeout_seconds,
        )
        matches = response.get("matches", [])
        if not isinstance(matches, list):
            raise ProcessingError("Pinecone response did not include a matches list.")
        return [normalize_external_match(match) for match in matches if isinstance(match, dict)]

    if config.vector_db_provider != "generic_json":
        raise ProcessingError(f"Unsupported VECTOR_DB_PROVIDER: {config.vector_db_provider}")

    request_payload = {
        "query": query_text,
        "top_k": 5,
        "include_metadata": True,
    }
    response = await post_json(
        config.vector_db_endpoint,
        request_payload,
        headers={
            "authorization": f"Bearer {config.vector_db_api_key}",
            "content-type": "application/json",
        },
        timeout_seconds=config.http_timeout_seconds,
    )
    matches = response.get("matches", [])
    if not isinstance(matches, list):
        raise ProcessingError("Vector DB response did not include a matches list.")
    return [normalize_external_match(match) for match in matches if isinstance(match, dict)]


async def create_embedding(query_text: str, config: RuntimeConfig) -> List[float]:
    if config.embedding_provider != "openai":
        raise ProcessingError(f"Unsupported EMBEDDING_PROVIDER: {config.embedding_provider}")
    if not config.embedding_api_endpoint:
        raise ProcessingError("EMBEDDING_API_ENDPOINT is required for Pinecone retrieval.")
    if is_unset_secret(config.embedding_api_key):
        raise ProcessingError("EMBEDDING_API_KEY is required for Pinecone retrieval.")

    response = await post_json(
        config.embedding_api_endpoint,
        {
            "model": config.embedding_model,
            "input": query_text,
        },
        headers={
            "authorization": f"Bearer {config.embedding_api_key}",
            "content-type": "application/json",
        },
        timeout_seconds=config.http_timeout_seconds,
    )
    try:
        embedding = response["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProcessingError("Embedding response had an unexpected structure.") from exc

    if not isinstance(embedding, list) or not all(isinstance(value, (int, float)) for value in embedding):
        raise ProcessingError("Embedding response did not contain a numeric vector.")
    return [float(value) for value in embedding]


def pinecone_query_url(endpoint: str) -> str:
    normalized = endpoint.rstrip("/")
    if normalized.endswith("/query"):
        return normalized
    return f"{normalized}/query"


def merge_matches(local_matches: Sequence[Dict[str, Any]], external_matches: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for match in [*local_matches, *external_matches]:
        match_id = str(match.get("id") or match.get("metadata", {}).get("program_name") or len(merged))
        if match_id not in merged or float(match.get("score", 0)) > float(merged[match_id].get("score", 0)):
            merged[match_id] = match
    return sorted(merged.values(), key=lambda item: float(item.get("score", 0)), reverse=True)[:8]


def normalize_external_match(match: Mapping[str, Any]) -> Dict[str, Any]:
    metadata = match.get("metadata") if isinstance(match.get("metadata"), dict) else {}
    return {
        "id": str(match.get("id") or metadata.get("id") or "external-context"),
        "score": float(match.get("score") or 0.5),
        "metadata": dict(metadata),
    }


def build_llm_payload(
    project_id: str,
    spec: Mapping[str, Any],
    vector_matches: List[Mapping[str, Any]],
    model: str,
) -> Dict[str, Any]:
    system_prompt = (
        "You are GrantStack's grant strategy analyst. Return concise structured JSON only. "
        "Use only the supplied retrieved_context for program claims. Every recommended program must include "
        "a source_url from retrieved_context. Do not invent award amounts, deadlines, or eligibility rules."
    )
    user_context = {
        "project_id": project_id,
        "project_spec": spec,
        "retrieved_context": vector_matches,
        "required_schema": {
            "summary": "string",
            "eligibility_score": "integer 0-100",
            "recommended_programs": [
                {
                    "name": "string",
                    "fit": "High | Medium | Exploratory",
                    "category": "string",
                    "why_it_matters": "string",
                    "source_url": "string",
                    "diligence_questions": ["string"],
                }
            ],
            "strengths": ["string"],
            "risk_flags": ["string"],
            "next_actions": ["string"],
        },
    }
    return {
        "model": model,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_context, separators=(",", ":"))},
        ],
    }


async def execute_llm_analysis(
    llm_payload: Mapping[str, Any],
    spec: Mapping[str, Any],
    vector_matches: List[Mapping[str, Any]],
    config: RuntimeConfig,
) -> Dict[str, Any]:
    deterministic_report = build_source_backed_report(spec, vector_matches, analysis_mode="source_backed")
    if config.mock_external_calls:
        await asyncio.sleep(0)
        return deterministic_report

    if not config.llm_api_endpoint:
        raise ProcessingError("LLM_API_ENDPOINT is required when MOCK_EXTERNAL_CALLS is false.")
    if is_unset_secret(config.llm_api_key):
        raise ProcessingError("LLM_API_KEY is required when MOCK_EXTERNAL_CALLS is false.")

    response = await call_llm_provider(llm_payload, config)
    llm_report = normalize_llm_response(response)
    return merge_report_defaults(deterministic_report, llm_report)


async def call_llm_provider(llm_payload: Mapping[str, Any], config: RuntimeConfig) -> Dict[str, Any]:
    request = build_llm_request(llm_payload, config)
    return await post_json(
        request["url"],
        request["payload"],
        headers=request["headers"],
        timeout_seconds=config.http_timeout_seconds,
    )


def build_llm_request(llm_payload: Mapping[str, Any], config: RuntimeConfig) -> Dict[str, Any]:
    if config.llm_provider == "generic_json":
        return {
            "url": config.llm_api_endpoint,
            "payload": dict(llm_payload),
            "headers": {
                "authorization": f"Bearer {config.llm_api_key}",
                "content-type": "application/json",
            },
        }

    if config.llm_provider == "openai":
        return {
            "url": config.llm_api_endpoint,
            "payload": dict(llm_payload),
            "headers": {
                "authorization": f"Bearer {config.llm_api_key}",
                "content-type": "application/json",
            },
        }

    if config.llm_provider == "anthropic":
        system_prompt = ""
        anthropic_messages: List[Dict[str, str]] = []
        for message in llm_payload.get("messages", []):
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            content = message.get("content")
            if not isinstance(content, str):
                continue
            if role == "system":
                system_prompt = content
            elif role in {"user", "assistant"}:
                anthropic_messages.append({"role": role, "content": content})

        return {
            "url": config.llm_api_endpoint,
            "payload": {
                "model": config.llm_model,
                "max_tokens": 1800,
                "temperature": llm_payload.get("temperature", 0.1),
                "system": system_prompt,
                "messages": anthropic_messages,
            },
            "headers": {
                "x-api-key": config.llm_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        }

    raise ProcessingError(f"Unsupported LLM_PROVIDER: {config.llm_provider}")


def build_source_backed_report(
    spec: Mapping[str, Any],
    vector_matches: Sequence[Mapping[str, Any]],
    analysis_mode: str,
) -> Dict[str, Any]:
    capex = float(spec["capex"])
    jobs = int(spec["jobs"])
    facility_type = str(spec["facility_type"])
    location = str(spec["location"])
    state_code = infer_state_code(location)
    recommended_programs = [program_recommendation(match) for match in vector_matches[:5]]
    score = eligibility_score(capex, jobs, vector_matches, state_code)
    categories = unique_preserve_order(program["category"] for program in recommended_programs)

    return {
        "report_version": "2026-05-21",
        "catalog_version": load_catalog()["version"],
        "analysis_mode": analysis_mode,
        "summary": (
            f"{facility_type} project in {location} with {format_usd(capex)} in capital investment "
            f"and {jobs:,} planned jobs. The strongest first-pass angles are "
            f"{human_join(categories[:3]) or 'state/local incentives, workforce training, and infrastructure support'}."
        ),
        "eligibility_score": score,
        "confidence": confidence_label(vector_matches, state_code),
        "investment_profile": {
            "location": location,
            "state": state_code or "UNCONFIRMED",
            "facility_type": facility_type,
            "capex": capex,
            "capex_band": capex_band(capex),
            "jobs": jobs,
            "jobs_band": jobs_band(jobs),
        },
        "recommended_program_categories": categories,
        "recommended_programs": recommended_programs,
        "strengths": build_strengths(capex, jobs, facility_type, vector_matches),
        "risk_flags": build_risk_flags(capex, jobs, state_code, vector_matches),
        "next_actions": build_next_actions(state_code, recommended_programs),
        "buyer_questions": build_buyer_questions(state_code),
        "evidence_summary": [evidence_line(match) for match in vector_matches[:6]],
        "assumptions": [
            "Project values are treated as preliminary screening inputs, not certified commitments.",
            "State and local awards are often discretionary and may depend on competition, wages, timing, and site specifics.",
            "Program availability, application windows, and award terms must be confirmed with the issuing agency.",
        ],
        "validation_note": (
            "This report is a first-pass incentive screen. It is not legal, tax, accounting, or site-selection advice; "
            "validate program rules and negotiate terms with qualified advisors and public agencies before relying on it."
        ),
    }


def program_recommendation(match: Mapping[str, Any]) -> Dict[str, Any]:
    metadata = safe_metadata(match)
    source_url = str(metadata.get("source_url", ""))
    score = float(match.get("score", 0))
    return {
        "name": metadata.get("program_name", "Unspecified program"),
        "category": metadata.get("category", "Incentive strategy"),
        "jurisdiction": metadata.get("jurisdiction", "Unspecified"),
        "fit": fit_label(score),
        "score": round(score * 100),
        "why_it_matters": metadata.get("fit_reason") or metadata.get("evidence_summary", ""),
        "source_url": source_url,
        "source_note": metadata.get("source_note", ""),
        "diligence_questions": metadata.get("diligence_questions", []),
    }


def evidence_line(match: Mapping[str, Any]) -> Dict[str, Any]:
    metadata = safe_metadata(match)
    return {
        "program": metadata.get("program_name", "Unspecified program"),
        "jurisdiction": metadata.get("jurisdiction", "Unspecified"),
        "evidence": metadata.get("evidence_summary", ""),
        "source_url": metadata.get("source_url", ""),
    }


def build_strengths(
    capex: float,
    jobs: int,
    facility_type: str,
    vector_matches: Sequence[Mapping[str, Any]],
) -> List[str]:
    strengths: List[str] = []
    if capex >= 10_000_000:
        strengths.append(f"{format_usd(capex)} capex gives the project a material investment story for discretionary review.")
    if jobs >= 50:
        strengths.append(f"{jobs:,} planned jobs can support workforce and job-creation incentive narratives.")
    if keyword_overlap(facility_type, {"manufacturing", "industrial", "advanced", "assembly", "distribution", "logistics"}):
        strengths.append("The facility type aligns with common economic-development and workforce-training program categories.")
    if any(safe_metadata(match).get("state_match") for match in vector_matches):
        strengths.append("The screen found state-specific programs to validate before broader national or local options.")
    return strengths or ["The project has enough basic information to begin a structured incentive screen."]


def build_risk_flags(
    capex: float,
    jobs: int,
    state_code: Optional[str],
    vector_matches: Sequence[Mapping[str, Any]],
) -> List[str]:
    risks: List[str] = []
    if not state_code:
        risks.append("Location does not include a parseable state; state-specific screening may be incomplete.")
    if jobs < 25:
        risks.append("Low job count may limit eligibility for job-creation incentives or shift the case toward tax, utility, or local support.")
    if capex < 5_000_000:
        risks.append("Lower capital investment may reduce leverage for discretionary programs unless strategic public benefits are clear.")
    if not any(safe_metadata(match).get("source_url") for match in vector_matches):
        risks.append("No source URLs were attached to retrieved context; human validation is required before buyer-facing use.")
    risks.append("Wage levels, county tier, site control, competing locations, and project timing can materially change award fit.")
    return risks


def build_next_actions(state_code: Optional[str], programs: Sequence[Mapping[str, Any]]) -> List[str]:
    actions = [
        "Confirm exact site address, county, wage bands, hiring schedule, and whether roles are net-new.",
        "Build a one-page economic impact narrative covering capex, jobs, wages, training needs, and public benefit.",
    ]
    if state_code:
        actions.append(f"Validate {state_code} program fit with the state economic development agency and local development authority.")
    if programs:
        actions.append(f"Start diligence with {programs[0]['name']} and document eligibility gaps before outreach.")
    actions.append("Do not promise incentives in internal forecasts until award terms are validated by the issuing agency.")
    return actions


def build_buyer_questions(state_code: Optional[str]) -> List[str]:
    questions = [
        "Is this project competitive with another state or site?",
        "What wage level and benefits package will be committed in writing?",
        "What is the latest date by which incentives must influence the location decision?",
        "Which infrastructure, training, permitting, or utility constraints could public partners help remove?",
    ]
    if state_code == "GA":
        questions.append("Which Georgia county tier or special zone applies to the site?")
    if state_code == "NC":
        questions.append("Does the project meet competitive-location requirements for discretionary North Carolina incentives?")
    return questions


def merge_report_defaults(base_report: Dict[str, Any], llm_report: Mapping[str, Any]) -> Dict[str, Any]:
    merged = dict(base_report)
    for key in ("summary", "eligibility_score", "strengths", "risk_flags", "next_actions", "buyer_questions"):
        if key in llm_report and llm_report[key]:
            merged[key] = llm_report[key]

    if isinstance(llm_report.get("recommended_programs"), list) and llm_report["recommended_programs"]:
        merged["recommended_programs"] = ensure_program_sources(
            llm_report["recommended_programs"],
            base_report["recommended_programs"],
        )
        merged["recommended_program_categories"] = unique_preserve_order(
            program.get("category", "Incentive strategy") for program in merged["recommended_programs"]
        )

    merged["analysis_mode"] = "llm_augmented_source_backed"
    merged["validation_note"] = base_report["validation_note"]
    merged["evidence_summary"] = base_report["evidence_summary"]
    merged["assumptions"] = base_report["assumptions"]
    return merged


def ensure_program_sources(
    llm_programs: Sequence[Any],
    base_programs: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    source_by_name = {str(program.get("name", "")).lower(): dict(program) for program in base_programs}
    source_urls = {program.get("source_url") for program in base_programs}
    cleaned: List[Dict[str, Any]] = []

    for candidate in llm_programs:
        if not isinstance(candidate, dict):
            continue
        program = dict(candidate)
        name = str(program.get("name", "")).lower()
        fallback = source_by_name.get(name)
        if program.get("source_url") not in source_urls and fallback:
            program["source_url"] = fallback.get("source_url", "")
            program["source_note"] = fallback.get("source_note", "")
        if program.get("source_url"):
            cleaned.append(program)

    return cleaned or [dict(program) for program in base_programs]


def normalize_llm_response(response: Mapping[str, Any]) -> Dict[str, Any]:
    if "content" in response and isinstance(response["content"], list):
        text_blocks = [
            block.get("text")
            for block in response["content"]
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        ]
        if text_blocks:
            content = "\n".join(text_blocks)
            try:
                parsed_content = json.loads(content)
            except json.JSONDecodeError:
                parsed_content = {"summary": content}
            if isinstance(parsed_content, dict):
                return parsed_content

    if "choices" in response:
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProcessingError("LLM response had an unexpected choices structure.") from exc

        if isinstance(content, str):
            try:
                parsed_content = json.loads(content)
            except json.JSONDecodeError:
                parsed_content = {"summary": content}
            if isinstance(parsed_content, dict):
                return parsed_content

    if isinstance(response, dict):
        return dict(response)

    raise ProcessingError("LLM response could not be normalized to a JSON object.")


async def post_json(
    url: str,
    payload: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout_seconds: int,
) -> Dict[str, Any]:
    async def send_request() -> Dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url=url, data=body, headers=dict(headers), method="POST")

        def do_post() -> Dict[str, Any]:
            try:
                with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                    raw_body = response.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")[:500]
                raise ProcessingError(f"HTTP {exc.code} from external service: {error_body}") from exc
            except urllib.error.URLError as exc:
                raise ProcessingError(f"External service request failed: {exc.reason}") from exc

            try:
                parsed = json.loads(raw_body)
            except json.JSONDecodeError as exc:
                raise ProcessingError("External service returned non-JSON response.") from exc

            if not isinstance(parsed, dict):
                raise ProcessingError("External service response must be a JSON object.")
            return parsed

        return await asyncio.to_thread(do_post)

    return await asyncio.wait_for(send_request(), timeout=timeout_seconds + 1)


def to_vector_match(
    source: Mapping[str, Any],
    score: float,
    spec: Mapping[str, Any],
    state_code: Optional[str],
) -> Dict[str, Any]:
    metadata = {
        "program_name": source["program_name"],
        "category": source["category"],
        "jurisdiction": source["jurisdiction"],
        "states": source.get("states", []),
        "state_match": state_code in source.get("states", []),
        "evidence_summary": source["evidence_summary"],
        "source_url": source["source_url"],
        "source_note": source["source_note"],
        "diligence_questions": source.get("diligence_questions", []),
        "fit_reason": fit_reason(source, spec, state_code),
        "last_verified": source.get("last_verified"),
    }
    return {
        "id": source["id"],
        "score": round(score, 4),
        "metadata": metadata,
    }


def score_catalog_source(source: Mapping[str, Any], spec: Mapping[str, Any], state_code: Optional[str]) -> float:
    states = set(source.get("states", []))
    jurisdiction = str(source.get("jurisdiction", "")).lower()
    if states and state_code and state_code not in states:
        return 0.0
    if states and not state_code:
        return 0.0

    capex = float(spec["capex"])
    jobs = int(spec["jobs"])
    facility_type = str(spec["facility_type"])
    score = 0.25

    if state_code and state_code in states:
        score += 0.28
    elif jurisdiction == "federal":
        score += 0.08

    min_capex = float(source.get("min_capex", 0))
    min_jobs = int(source.get("min_jobs", 0))
    if min_capex and capex >= min_capex:
        score += 0.12
    elif min_capex:
        score -= 0.08
    if min_jobs and jobs >= min_jobs:
        score += 0.16
    elif min_jobs:
        score -= 0.10

    if keyword_overlap(facility_type, set(source.get("facility_keywords", []))):
        score += 0.16
    if keyword_overlap(str(spec.get("location", "")), set(source.get("location_keywords", []))):
        score += 0.06
    if source.get("category") in {"Workforce training", "Infrastructure"} and jobs >= 25:
        score += 0.05
    if source.get("category") in {"Tax credit", "Discretionary cash grant"} and capex >= 10_000_000:
        score += 0.05

    return max(0.0, min(score, 0.97))


def fit_reason(source: Mapping[str, Any], spec: Mapping[str, Any], state_code: Optional[str]) -> str:
    reasons: List[str] = []
    if state_code and state_code in source.get("states", []):
        reasons.append(f"state-specific match for {state_code}")
    if int(spec["jobs"]) >= int(source.get("min_jobs", 0)):
        reasons.append("job creation clears the catalog screening threshold")
    if float(spec["capex"]) >= float(source.get("min_capex", 0)):
        reasons.append("capital investment clears the catalog screening threshold")
    if keyword_overlap(str(spec["facility_type"]), set(source.get("facility_keywords", []))):
        reasons.append("facility type aligns with the program category")
    return f"{source['program_name']} matches because {human_join(reasons)}." if reasons else str(source["evidence_summary"])


def eligibility_score(
    capex: float,
    jobs: int,
    vector_matches: Sequence[Mapping[str, Any]],
    state_code: Optional[str],
) -> int:
    source_score = 0
    if vector_matches:
        source_score = int(sum(float(match.get("score", 0)) for match in vector_matches[:5]) / min(len(vector_matches), 5) * 35)
    capex_score = min(25, int(capex / 1_000_000))
    jobs_score = min(25, int(jobs / 4))
    state_score = 15 if state_code and any(safe_metadata(match).get("state_match") for match in vector_matches) else 5
    return max(0, min(100, source_score + capex_score + jobs_score + state_score))


def confidence_label(vector_matches: Sequence[Mapping[str, Any]], state_code: Optional[str]) -> str:
    state_specific = sum(1 for match in vector_matches if safe_metadata(match).get("state_match"))
    if state_code and state_specific >= 3:
        return "High for first-pass screening"
    if vector_matches:
        return "Medium for first-pass screening"
    return "Low; needs source enrichment"


def capex_band(capex: float) -> str:
    if capex >= 100_000_000:
        return "$100M+"
    if capex >= 25_000_000:
        return "$25M-$100M"
    if capex >= 5_000_000:
        return "$5M-$25M"
    return "Under $5M"


def jobs_band(jobs: int) -> str:
    if jobs >= 500:
        return "500+ jobs"
    if jobs >= 100:
        return "100-499 jobs"
    if jobs >= 25:
        return "25-99 jobs"
    return "Under 25 jobs"


def fit_label(score: float) -> str:
    if score >= 0.78:
        return "High"
    if score >= 0.58:
        return "Medium"
    return "Exploratory"


def infer_state_code(location: str) -> Optional[str]:
    match = STATE_PATTERN.search(location)
    if match:
        return match.group(1).upper()

    lowered = location.lower()
    for name, code in state_names().items():
        if name in lowered:
            return code
    return None


def keyword_overlap(value: str, keywords: Set[str]) -> bool:
    if not keywords:
        return False
    lowered = value.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def safe_metadata(match: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = match.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def unique_preserve_order(values: Iterable[Any]) -> List[str]:
    seen: Set[str] = set()
    ordered: List[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            ordered.append(text)
    return ordered


def human_join(values: Sequence[str]) -> str:
    cleaned = [value for value in values if value]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    return f"{', '.join(cleaned[:-1])}, and {cleaned[-1]}"


def format_usd(value: float) -> str:
    return f"${value:,.0f}"


@lru_cache(maxsize=1)
def load_catalog() -> Dict[str, Any]:
    bucket = os.environ.get("SOURCE_CATALOG_BUCKET", "").strip()
    key = os.environ.get("SOURCE_CATALOG_KEY", "").strip()
    if bucket and key:
        try:
            response = s3_client.get_object(Bucket=bucket, Key=key)
            catalog = json.loads(response["Body"].read().decode("utf-8"))
            if isinstance(catalog, dict) and isinstance(catalog.get("sources"), list):
                return catalog
            logger.warning("s3_catalog_invalid_shape bucket=%s key=%s using_embedded_catalog=true", bucket, key)
        except (BotoCoreError, ClientError, KeyError, json.JSONDecodeError):
            logger.exception("failed_to_load_s3_catalog bucket=%s key=%s using_embedded_catalog=true", bucket, key)

    try:
        with CATALOG_PATH.open("r", encoding="utf-8") as handle:
            catalog = json.load(handle)
    except FileNotFoundError as exc:
        raise ProcessingError(f"Catalog file not found: {CATALOG_PATH}") from exc
    except json.JSONDecodeError as exc:
        raise ProcessingError("Catalog file is not valid JSON.") from exc

    if not isinstance(catalog, dict) or not isinstance(catalog.get("sources"), list):
        raise ProcessingError("Catalog must contain a sources list.")
    return catalog


@lru_cache(maxsize=1)
def state_names() -> Dict[str, str]:
    return {
        "alabama": "AL",
        "alaska": "AK",
        "arizona": "AZ",
        "arkansas": "AR",
        "california": "CA",
        "colorado": "CO",
        "connecticut": "CT",
        "delaware": "DE",
        "district of columbia": "DC",
        "florida": "FL",
        "georgia": "GA",
        "hawaii": "HI",
        "idaho": "ID",
        "illinois": "IL",
        "indiana": "IN",
        "iowa": "IA",
        "kansas": "KS",
        "kentucky": "KY",
        "louisiana": "LA",
        "maine": "ME",
        "maryland": "MD",
        "massachusetts": "MA",
        "michigan": "MI",
        "minnesota": "MN",
        "mississippi": "MS",
        "missouri": "MO",
        "montana": "MT",
        "nebraska": "NE",
        "nevada": "NV",
        "new hampshire": "NH",
        "new jersey": "NJ",
        "new mexico": "NM",
        "new york": "NY",
        "north carolina": "NC",
        "north dakota": "ND",
        "ohio": "OH",
        "oklahoma": "OK",
        "oregon": "OR",
        "pennsylvania": "PA",
        "rhode island": "RI",
        "south carolina": "SC",
        "south dakota": "SD",
        "tennessee": "TN",
        "texas": "TX",
        "utah": "UT",
        "vermont": "VT",
        "virginia": "VA",
        "washington": "WA",
        "west virginia": "WV",
        "wisconsin": "WI",
        "wyoming": "WY",
    }


def load_config() -> RuntimeConfig:
    mock_external_calls = parse_bool(os.environ.get("MOCK_EXTERNAL_CALLS", "true"))

    return RuntimeConfig(
        table_name=require_env("DYNAMODB_TABLE_NAME"),
        vector_db_provider=os.environ.get("VECTOR_DB_PROVIDER", "pinecone"),
        vector_db_endpoint=optional_env("VECTOR_DB_ENDPOINT"),
        vector_db_api_key=(
            "" if mock_external_calls else resolve_api_key("VECTOR_DB_API_KEY", "VECTOR_DB_API_KEY_SECRET_ARN")
        ),
        embedding_provider=os.environ.get("EMBEDDING_PROVIDER", "openai"),
        embedding_api_endpoint=optional_env("EMBEDDING_API_ENDPOINT"),
        embedding_api_key=(
            "" if mock_external_calls else resolve_api_key("EMBEDDING_API_KEY", "EMBEDDING_API_KEY_SECRET_ARN")
        ),
        embedding_model=os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small"),
        llm_provider=os.environ.get("LLM_PROVIDER", "openai"),
        llm_api_endpoint=optional_env("LLM_API_ENDPOINT"),
        llm_api_key=(
            "" if mock_external_calls else resolve_api_key("LLM_API_KEY", "LLM_API_KEY_SECRET_ARN")
        ),
        llm_model=os.environ.get("LLM_MODEL", "grantstack-evidence-engine-v1"),
        mock_external_calls=mock_external_calls,
        http_timeout_seconds=int(os.environ.get("HTTP_CLIENT_TIMEOUT_SECS", "20")),
    )


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def optional_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or value.startswith("__MOCK_DISABLED__"):
        return ""
    return value


def resolve_api_key(value_env_name: str, secret_arn_env_name: str) -> str:
    direct_value = optional_env(value_env_name)
    if direct_value and not is_unset_secret(direct_value):
        return direct_value

    secret_arn = optional_env(secret_arn_env_name)
    if not secret_arn:
        return ""

    return get_secret_value(secret_arn)


@lru_cache(maxsize=8)
def get_secret_value(secret_arn: str) -> str:
    try:
        response = secretsmanager.get_secret_value(SecretId=secret_arn)
    except (BotoCoreError, ClientError) as exc:
        raise ProcessingError(f"Unable to read secret {secret_arn}.") from exc

    if "SecretString" in response:
        return parse_secret_payload(response["SecretString"], secret_arn)

    secret_binary = response.get("SecretBinary")
    if secret_binary:
        decoded = base64.b64decode(secret_binary).decode("utf-8")
        return parse_secret_payload(decoded, secret_arn)

    raise ProcessingError(f"Secret {secret_arn} did not contain a usable value.")


def parse_secret_payload(secret_payload: str, secret_arn: str) -> str:
    try:
        parsed = json.loads(secret_payload)
    except json.JSONDecodeError:
        return secret_payload

    if isinstance(parsed, str):
        return parsed

    if isinstance(parsed, dict):
        for candidate_key in ("api_key", "token", "value", "secret"):
            candidate_value = parsed.get(candidate_key)
            if isinstance(candidate_value, str) and candidate_value:
                return candidate_value

    raise ProcessingError(
        f"Secret {secret_arn} must be either a raw string or JSON containing api_key, token, value, or secret."
    )


def is_unset_secret(value: str) -> bool:
    return not value or value.startswith("__SET_") or value.startswith("__MOCK_DISABLED__")


def to_dynamodb_json(value: Mapping[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(value), parse_float=Decimal)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
