"""MCP Resource: Current on-call schedule.

Exposes who is currently on-call for every PagerDuty escalation policy as
a structured JSON resource. Useful ambient context for handoffs,
escalations, and "who do I page?" questions.

Resource URI: sre://oncall/schedule
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx
import structlog
from mcp.types import Resource

log = structlog.get_logger()

ONCALL_RESOURCE_URI = "sre://oncall/schedule"

_PAGERDUTY_API = "https://api.pagerduty.com"


def get_oncall_resource() -> Resource:
    """Return MCP Resource metadata for the on-call schedule."""
    return Resource(
        uri=ONCALL_RESOURCE_URI,
        name="On-Call Schedule — Current Rotation",
        description=(
            "Who is currently on-call for each PagerDuty escalation policy, "
            "including escalation level and shift end time. Read this before "
            "recommending an escalation or drafting a shift handoff."
        ),
        mimeType="application/json",
    )


async def read_oncall_schedule() -> str:
    """Fetch the current on-call rotation and return it as a JSON string.

    Never raises: upstream failures are converted into an ``error`` key in
    the returned JSON payload.
    """
    try:
        data = await _fetch_oncalls()
    except httpx.HTTPStatusError as exc:
        log.warning(
            "oncall_resource_http_error",
            status=exc.response.status_code,
            url=str(exc.request.url),
        )
        data = {
            "error": f"PagerDuty API returned HTTP {exc.response.status_code}",
            "timestamp": int(time.time()),
        }
    except httpx.HTTPError as exc:
        log.warning("oncall_resource_transport_error", error=str(exc))
        data = {
            "error": f"Could not reach PagerDuty: {exc}",
            "timestamp": int(time.time()),
        }
    return json.dumps(data, indent=2)


async def _fetch_oncalls() -> dict[str, Any]:
    token = os.environ.get("PAGERDUTY_TOKEN", "")
    async with httpx.AsyncClient(
        base_url=_PAGERDUTY_API,
        headers={
            "Authorization": f"Token token={token}",
            "Accept": "application/vnd.pagerduty+json;version=2",
        },
        timeout=15.0,
    ) as client:
        resp = await client.get("/oncalls", params={"limit": 100, "earliest": "true"})
        resp.raise_for_status()
        raw = resp.json().get("oncalls", [])

    policies: dict[str, list[dict[str, Any]]] = {}
    for oc in raw:
        policy = oc.get("escalation_policy", {}).get("summary", "Unknown policy")
        entry = {
            "level": oc.get("escalation_level"),
            "engineer": oc.get("user", {}).get("summary", "Unknown"),
            "schedule": (oc.get("schedule") or {}).get("summary"),
            "shift_start": oc.get("start"),
            "shift_end": oc.get("end"),
        }
        policies.setdefault(policy, []).append(entry)

    # Sort escalation chain within each policy by level (primary first).
    for chain in policies.values():
        chain.sort(key=lambda e: e["level"] if e["level"] is not None else 99)

    log.info("oncall_resource_read", policies=len(policies))
    return {
        "timestamp": int(time.time()),
        "summary": {"escalation_policies": len(policies)},
        "policies": policies,
    }
