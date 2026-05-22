#!/usr/bin/env python3
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG_PATH = BACKEND_DIR / "lambda" / "incentive_catalog.json"
DEFAULT_RULES_PATH = BACKEND_DIR / "lambda" / "eligibility_rules.json"
DEFAULT_EMBEDDING_ENDPOINT = "https://api.openai.com/v1/embeddings"


class SyncError(RuntimeError):
    pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Embed GrantStack incentive sources and upsert them to Pinecone.")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH)
    parser.add_argument("--embedding-endpoint", default=os.environ.get("OPENAI_EMBEDDING_ENDPOINT", DEFAULT_EMBEDDING_ENDPOINT))
    parser.add_argument("--embedding-model", default=os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"))
    parser.add_argument("--pinecone-index-host", default=os.environ.get("PINECONE_INDEX_HOST", ""))
    parser.add_argument("--namespace", default=os.environ.get("PINECONE_NAMESPACE", "grantstack-incentives-dev"))
    parser.add_argument("--pinecone-api-version", default=os.environ.get("PINECONE_API_VERSION", ""))
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--limit", type=int, default=0, help="Limit records for a smoke run. 0 means all records.")
    parser.add_argument("--dry-run", action="store_true", help="Build records without calling OpenAI or Pinecone.")
    args = parser.parse_args(argv)

    catalog = load_json(args.catalog)
    rules = load_json(args.rules)
    records = build_index_records(catalog, rules, limit=args.limit or None)

    if args.dry_run:
        print(
            json.dumps(
                {
                    "records_prepared": len(records),
                    "catalog_version": catalog.get("version"),
                    "rules_version": rules.get("version"),
                    "namespace": args.namespace,
                    "sample_record": records[0] if records else None,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    openai_api_key = require_env("OPENAI_API_KEY")
    pinecone_api_key = require_env("PINECONE_API_KEY")
    if not args.pinecone_index_host:
        raise SyncError("PINECONE_INDEX_HOST or --pinecone-index-host is required.")

    total_upserted = 0
    for batch in chunked(records, args.batch_size):
        embeddings = create_embeddings(
            [record["text"] for record in batch],
            api_key=openai_api_key,
            endpoint=args.embedding_endpoint,
            model=args.embedding_model,
            timeout=args.timeout,
        )
        total_upserted += upsert_vectors(
            batch,
            embeddings,
            api_key=pinecone_api_key,
            index_host=args.pinecone_index_host,
            namespace=args.namespace,
            api_version=args.pinecone_api_version,
            timeout=args.timeout,
        )

    print(json.dumps({"records_upserted": total_upserted, "namespace": args.namespace}, sort_keys=True))
    return 0


def load_json(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError as exc:
        raise SyncError(f"File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SyncError(f"Invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise SyncError(f"JSON document must be an object: {path}")
    return value


def build_index_records(catalog: Mapping[str, Any], rules: Mapping[str, Any], limit: Optional[int] = None) -> List[Dict[str, Any]]:
    sources = catalog.get("sources", [])
    rules_by_program = rules.get("rules", {})
    if not isinstance(sources, list):
        raise SyncError("Catalog must contain a sources list.")
    if not isinstance(rules_by_program, dict):
        raise SyncError("Eligibility rules must contain a rules object.")

    records: List[Dict[str, Any]] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("id", "")).strip()
        if not source_id:
            continue
        program_rules = rules_by_program.get(source_id, [])
        records.append(
            {
                "id": source_id,
                "text": build_retrieval_text(source, program_rules if isinstance(program_rules, list) else []),
                "metadata": build_metadata(source, program_rules if isinstance(program_rules, list) else []),
            }
        )
        if limit and len(records) >= limit:
            break
    return records


def build_retrieval_text(source: Mapping[str, Any], rules: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        f"Program: {source.get('program_name', '')}",
        f"Jurisdiction: {source.get('jurisdiction', '')}",
        f"States: {', '.join(str(value) for value in source.get('states', [])) or 'Federal or national'}",
        f"Category: {source.get('category', '')}",
        f"Minimum jobs screen: {source.get('min_jobs', 0)}",
        f"Minimum capex screen: {source.get('min_capex', 0)}",
        f"Facility keywords: {', '.join(str(value) for value in source.get('facility_keywords', []))}",
        f"Evidence: {source.get('evidence_summary', '')}",
        f"Source: {source.get('source_url', '')}",
    ]
    diligence_questions = source.get("diligence_questions", [])
    if isinstance(diligence_questions, list) and diligence_questions:
        lines.append("Diligence questions: " + " | ".join(str(item) for item in diligence_questions))
    if rules:
        rule_lines = []
        for rule in rules:
            if not isinstance(rule, Mapping):
                continue
            rule_lines.append(
                f"{rule.get('label', rule.get('id', 'rule'))}: "
                f"{rule.get('field', '')} {rule.get('operator', '')} {rule.get('value', '')}; "
                f"severity={rule.get('severity', '')}"
            )
        lines.append("Eligibility rules: " + " | ".join(rule_lines))
    return "\n".join(lines)


def build_metadata(source: Mapping[str, Any], rules: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "id": str(source.get("id", "")),
        "program_name": str(source.get("program_name", "")),
        "category": str(source.get("category", "")),
        "jurisdiction": str(source.get("jurisdiction", "")),
        "states": [str(value) for value in source.get("states", [])],
        "source_url": str(source.get("source_url", "")),
        "source_note": str(source.get("source_note", "")),
        "evidence_summary": str(source.get("evidence_summary", "")),
        "min_jobs": int(source.get("min_jobs", 0) or 0),
        "min_capex": float(source.get("min_capex", 0) or 0),
        "facility_keywords": [str(value) for value in source.get("facility_keywords", [])],
        "last_verified": str(source.get("last_verified", "")),
        "rule_count": len([rule for rule in rules if isinstance(rule, Mapping)]),
    }
    diligence_questions = source.get("diligence_questions", [])
    if isinstance(diligence_questions, list):
        metadata["diligence_questions"] = [str(value) for value in diligence_questions[:8]]
    return metadata


def create_embeddings(
    texts: Sequence[str],
    api_key: str,
    endpoint: str,
    model: str,
    timeout: int,
) -> List[List[float]]:
    response = post_json(
        endpoint,
        {"model": model, "input": list(texts)},
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=timeout,
    )
    data = response.get("data")
    if not isinstance(data, list):
        raise SyncError("Embedding response did not include a data list.")
    ordered = sorted((item for item in data if isinstance(item, dict)), key=lambda item: int(item.get("index", 0)))
    embeddings: List[List[float]] = []
    for item in ordered:
        embedding = item.get("embedding")
        if not isinstance(embedding, list) or not all(isinstance(value, (int, float)) for value in embedding):
            raise SyncError("Embedding response contained a non-numeric embedding.")
        embeddings.append([float(value) for value in embedding])
    if len(embeddings) != len(texts):
        raise SyncError("Embedding response count did not match input count.")
    return embeddings


def upsert_vectors(
    records: Sequence[Mapping[str, Any]],
    embeddings: Sequence[Sequence[float]],
    api_key: str,
    index_host: str,
    namespace: str,
    api_version: str,
    timeout: int,
) -> int:
    if len(records) != len(embeddings):
        raise SyncError("Record and embedding counts do not match.")

    vectors = []
    for record, embedding in zip(records, embeddings):
        vectors.append(
            {
                "id": str(record["id"]),
                "values": [float(value) for value in embedding],
                "metadata": dict(record["metadata"]),
            }
        )

    payload: Dict[str, Any] = {"vectors": vectors}
    if namespace:
        payload["namespace"] = namespace
    headers = {
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }
    if api_version:
        headers["X-Pinecone-Api-Version"] = api_version

    response = post_json(pinecone_upsert_url(index_host), payload, headers=headers, timeout=timeout)
    return int(response.get("upsertedCount", len(vectors)))


def post_json(url: str, payload: Mapping[str, Any], headers: Mapping[str, str], timeout: int) -> Dict[str, Any]:
    request = urllib.request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers=dict(headers),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")[:500]
        raise SyncError(f"HTTP {exc.code} from {url}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise SyncError(f"Request failed for {url}: {exc.reason}") from exc

    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise SyncError(f"Non-JSON response from {url}") from exc
    if not isinstance(parsed, dict):
        raise SyncError(f"Expected JSON object from {url}")
    return parsed


def pinecone_upsert_url(index_host: str) -> str:
    normalized = index_host.rstrip("/")
    if normalized.endswith("/vectors/upsert"):
        return normalized
    return f"{normalized}/vectors/upsert"


def chunked(values: Sequence[Dict[str, Any]], size: int) -> Iterable[List[Dict[str, Any]]]:
    if size < 1:
        raise SyncError("--batch-size must be at least 1.")
    for index in range(0, len(values), size):
        yield list(values[index : index + size])


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SyncError(f"Missing required environment variable: {name}")
    return value


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
