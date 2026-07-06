"""MCP Resource: Recent CloudWatch alarm activity.

Exposes CloudWatch alarms — those currently in ALARM state plus the most
recently updated ones — as a structured JSON resource. Gives Claude an
immediate view of AWS-side alerting without an explicit tool call.

Resource URI: sre://cloudwatch/alarms
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import boto3
import structlog
from botocore.exceptions import BotoCoreError, ClientError
from mcp.types import Resource

log = structlog.get_logger()

CLOUDWATCH_RESOURCE_URI = "sre://cloudwatch/alarms"


def get_cloudwatch_resource() -> Resource:
    """Return MCP Resource metadata for the CloudWatch alarm feed."""
    return Resource(
        uri=CLOUDWATCH_RESOURCE_URI,
        name="CloudWatch Alarms — Recent Activity",
        description=(
            "CloudWatch alarms currently in ALARM state plus recently updated "
            "alarms, with state reason and the metric each alarm watches. Read "
            "this when triaging AWS-side symptoms."
        ),
        mimeType="application/json",
    )


async def read_cloudwatch_alarms() -> str:
    """Fetch recent CloudWatch alarm state and return it as a JSON string.

    boto3 is synchronous, so the fetch runs in a worker thread to avoid
    blocking the MCP event loop. Never raises: AWS failures are converted
    into an ``error`` key in the returned JSON payload.
    """
    try:
        data = await asyncio.to_thread(_fetch_alarms)
    except (BotoCoreError, ClientError) as exc:
        log.warning("cloudwatch_resource_aws_error", error=str(exc))
        data = {
            "error": f"CloudWatch query failed: {exc}",
            "timestamp": int(time.time()),
        }
    return json.dumps(data, indent=2, default=str)


def _fetch_alarms() -> dict[str, Any]:
    region = os.environ.get("AWS_REGION", "us-east-1")
    profile = os.environ.get("AWS_PROFILE")
    session = boto3.Session(profile_name=profile, region_name=region)
    cw = session.client("cloudwatch")

    firing = cw.describe_alarms(StateValue="ALARM", MaxRecords=50).get(
        "MetricAlarms", []
    )
    all_recent = cw.describe_alarms(MaxRecords=100).get("MetricAlarms", [])
    all_recent.sort(
        key=lambda a: a.get("StateUpdatedTimestamp") or 0, reverse=True
    )

    def _summarize(alarm: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": alarm.get("AlarmName"),
            "state": alarm.get("StateValue"),
            "state_reason": (alarm.get("StateReason") or "")[:200],
            "metric": f"{alarm.get('Namespace', '')}/{alarm.get('MetricName', '')}",
            "threshold": alarm.get("Threshold"),
            "comparison": alarm.get("ComparisonOperator"),
            "state_updated": alarm.get("StateUpdatedTimestamp"),
        }

    log.info(
        "cloudwatch_resource_read",
        region=region,
        firing=len(firing),
        recent=len(all_recent),
    )
    return {
        "timestamp": int(time.time()),
        "region": region,
        "summary": {
            "in_alarm": len(firing),
            "total_alarms": len(all_recent),
        },
        "firing": [_summarize(a) for a in firing],
        "recently_updated": [_summarize(a) for a in all_recent[:20]],
    }
