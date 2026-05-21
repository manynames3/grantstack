#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_PAYLOAD = {
    "location": "Raleigh, NC",
    "capex": 12500000,
    "jobs": 82,
    "facility_type": "advanced manufacturing",
    "contact_email": "pilot@example.com",
    "company_name": "GrantStack Smoke Test Co.",
    "average_wage": 72000,
    "project_timeline": "Site decision inside 120 days",
    "competing_locations": "SC, TN",
    "metadata": {
        "smoke_test": True,
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test a deployed GrantStack backend.")
    parser.add_argument(
        "--terraform-dir",
        default=str(Path(__file__).resolve().parents[1] / "terraform"),
        help="Path to the Terraform directory containing applied state.",
    )
    parser.add_argument("--payload-file", help="Optional JSON file to POST instead of the built-in sample payload.")
    parser.add_argument("--timeout", type=int, default=120, help="Seconds to wait for DynamoDB status completion.")
    parser.add_argument("--interval", type=int, default=5, help="Polling interval in seconds.")
    parser.add_argument("--skip-dlq-check", action="store_true", help="Skip the DLQ empty check.")
    args = parser.parse_args()

    outputs = terraform_outputs(Path(args.terraform_dir))
    endpoint = required_output(outputs, "api_endpoint").rstrip("/")
    region = required_output(outputs, "aws_region")
    dlq_url = outputs.get("processing_dlq_url", {}).get("value")
    payload = load_payload(args.payload_file)

    accepted_project = post_project(endpoint, payload)
    project_id = accepted_project["project_id"]
    access_token = accepted_project["access_token"]
    print(f"accepted project_id={project_id}")

    item = wait_for_project(endpoint, project_id, access_token, args.timeout, args.interval)
    status = item.get("status", "UNKNOWN")

    if status != "COMPLETED":
        print(json.dumps(item, indent=2))
        return 1

    print(f"completed project_id={project_id}")
    assert_report_quality(item)

    if not args.skip_dlq_check and dlq_url:
        assert_dlq_empty(dlq_url, region)
        print("dlq_empty=true")

    return 0


def terraform_outputs(terraform_dir: Path) -> Dict[str, Any]:
    result = run_command(["terraform", f"-chdir={terraform_dir}", "output", "-json"])
    return json.loads(result.stdout)


def required_output(outputs: Dict[str, Any], name: str) -> str:
    value = outputs.get(name, {}).get("value")
    if not value:
        raise SystemExit(f"Missing Terraform output: {name}")
    return str(value)


def load_payload(payload_file: Optional[str]) -> Dict[str, Any]:
    if not payload_file:
        return DEFAULT_PAYLOAD

    with open(payload_file, "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if not isinstance(payload, dict):
        raise SystemExit("Payload file must contain a JSON object.")
    return payload


def post_project(endpoint: str, payload: Dict[str, Any]) -> Dict[str, str]:
    request_body = json.dumps(payload).encode("utf-8")
    headers = {
        "content-type": "application/json",
    }

    auth_token = os.environ.get("GRANTSTACK_AUTH_TOKEN")
    if auth_token:
        headers["authorization"] = f"Bearer {auth_token}"

    request = urllib.request.Request(
        url=f"{endpoint}/projects",
        data=request_body,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            response_body = response.read().decode("utf-8")
            status_code = response.status
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"POST /projects failed: HTTP {exc.code} {error_body}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"POST /projects failed: {exc.reason}") from exc

    if status_code != 202:
        raise SystemExit(f"Expected HTTP 202, got HTTP {status_code}: {response_body}")

    parsed = json.loads(response_body)
    project_id = parsed.get("project_id")
    if not project_id:
        raise SystemExit(f"Response did not contain project_id: {response_body}")
    access_token = parsed.get("access_token")
    if not access_token:
        raise SystemExit(f"Response did not contain access_token: {response_body}")

    return {
        "project_id": str(project_id),
        "access_token": str(access_token),
    }


def wait_for_project(
    endpoint: str,
    project_id: str,
    access_token: str,
    timeout_seconds: int,
    interval_seconds: int,
) -> Dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last_item: Dict[str, Any] = {}

    while time.time() < deadline:
        last_item = get_project(endpoint, project_id, access_token)
        status = last_item.get("status")

        if status in {"COMPLETED", "FAILED"}:
            return last_item

        print(f"waiting project_id={project_id} status={status or 'NOT_FOUND'}")
        time.sleep(interval_seconds)

    raise SystemExit(f"Timed out waiting for project {project_id}. Last item: {json.dumps(last_item)}")


def get_project(endpoint: str, project_id: str, access_token: str) -> Dict[str, Any]:
    url = f"{endpoint}/projects/{project_id}?token={access_token}"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"GET /projects/{project_id} failed: HTTP {exc.code} {error_body}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"GET /projects/{project_id} failed: {exc.reason}") from exc


def assert_report_quality(item: Dict[str, Any]) -> None:
    report = item.get("analysis_report")
    if not isinstance(report, dict):
        raise SystemExit("Completed project did not include analysis_report.")

    recommended_programs = report.get("recommended_programs")
    if not isinstance(recommended_programs, list) or not recommended_programs:
        raise SystemExit("analysis_report did not include recommended_programs.")

    sourced_programs = [
        program
        for program in recommended_programs
        if isinstance(program, dict) and str(program.get("source_url", "")).startswith("https://")
    ]
    if len(sourced_programs) < 2:
        raise SystemExit("analysis_report did not include enough sourced program recommendations.")

    evidence = report.get("evidence_summary")
    if not isinstance(evidence, list) or not evidence:
        raise SystemExit("analysis_report did not include evidence_summary.")

    score = report.get("eligibility_score")
    if not isinstance(score, int) or score < 0 or score > 100:
        raise SystemExit("analysis_report eligibility_score must be an integer between 0 and 100.")


def assert_dlq_empty(dlq_url: str, region: str) -> None:
    result = aws_json(
        [
            "sqs",
            "get-queue-attributes",
            "--region",
            region,
            "--queue-url",
            dlq_url,
            "--attribute-names",
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
        ]
    )
    attributes = result.get("Attributes", {})
    visible = int(attributes.get("ApproximateNumberOfMessages", "0"))
    in_flight = int(attributes.get("ApproximateNumberOfMessagesNotVisible", "0"))
    if visible or in_flight:
        raise SystemExit(f"DLQ is not empty: visible={visible} in_flight={in_flight}")


def aws_json(args: List[str]) -> Dict[str, Any]:
    result = run_command(["aws", *args, "--output", "json"])
    if not result.stdout.strip():
        return {}
    return json.loads(result.stdout)


def run_command(command: List[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise SystemExit(f"Required command not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip()
        stdout = exc.stdout.strip()
        details = stderr or stdout or f"exit_code={exc.returncode}"
        raise SystemExit(f"Command failed: {' '.join(command)}\n{details}") from exc


if __name__ == "__main__":
    sys.exit(main())
