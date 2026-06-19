"""Render the GrantStack AWS architecture diagram.

Install Graphviz locally first, then run from the repo root:

    pip install -r requirements.txt
    python docs/architecture_aws.py

The script writes docs/architecture_aws.png and docs/architecture_aws.svg.
"""

from __future__ import annotations

from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import Lambda
from diagrams.aws.database import Dynamodb
from diagrams.aws.integration import Eventbridge, SQS
from diagrams.aws.management import Cloudwatch, XRay
from diagrams.aws.network import APIGateway
from diagrams.aws.security import IAM, SecretsManager
from diagrams.aws.storage import S3
from diagrams.onprem.client import Users
from diagrams.saas.cdn import Cloudflare

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "architecture_aws"

graph_attr = {
    "bgcolor": "white",
    "pad": "0.45",
    "rankdir": "LR",
    "splines": "ortho",
    "nodesep": "0.8",
    "ranksep": "1.0",
    "fontname": "Inter",
}

node_attr = {
    "fontname": "Inter",
    "fontsize": "12",
    "margin": "0.08",
}

edge_attr = {
    "fontname": "Inter",
    "fontsize": "10",
    "color": "#4b5563",
    "arrowsize": "0.7",
}

with Diagram(
    "GrantStack AWS Architecture",
    filename=str(OUT),
    direction="LR",
    show=False,
    outformat=["png", "svg"],
    graph_attr=graph_attr,
    node_attr=node_attr,
    edge_attr=edge_attr,
):
    user = Users("Expansion team")
    site = Cloudflare("Cloudflare Pages\nstatic frontend")

    with Cluster("AWS serverless API"):
        api = APIGateway("HTTP API\n/projects, /analytics")

        with Cluster("Request handlers"):
            ingest = Lambda("Ingest\nvalidate + accept")
            report = Lambda("Report\ntokenized read")
            analytics = Lambda("Analytics\nevent capture")

        queue = SQS("Processing queue")
        dlq = SQS("DLQ")
        processor = Lambda("Processor\nasync report worker")

    with Cluster("Data and catalog"):
        projects = Dynamodb("Projects table\nreports + status")
        analytics_table = Dynamodb("Analytics table\nTTL events")
        catalog = S3("Source catalog\nversioned/private")

    with Cluster("Scheduled refresh"):
        schedule = Eventbridge("EventBridge\nrate schedule")
        refresh = Lambda("Source refresh")

    with Cluster("Security + operations"):
        iam = IAM("Least-privilege IAM")
        secrets = SecretsManager("Provider secrets\noptional")
        logs = Cloudwatch("Logs, alarms,\ndashboard")
        xray = XRay("X-Ray tracing")

    user >> site
    site >> Edge(label="POST /projects") >> api >> ingest
    ingest >> Edge(label="accepted record") >> projects
    ingest >> Edge(label="enqueue") >> queue >> processor
    processor >> Edge(label="read catalog") >> catalog
    processor >> Edge(label="write report") >> projects
    processor >> Edge(label="failures") >> dlq

    site >> Edge(label="poll private report") >> api >> report >> projects
    site >> Edge(label="POST /analytics") >> api >> analytics >> analytics_table

    schedule >> refresh >> catalog

    processor >> Edge(label="optional provider keys") >> secrets
    [ingest, processor, report, analytics, refresh] >> logs
    [ingest, processor, report, analytics, refresh] >> xray
    iam >> [ingest, processor, report, analytics, refresh]
