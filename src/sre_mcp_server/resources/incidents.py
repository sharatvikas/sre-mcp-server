"""MCP Resource: Active PagerDuty incidents.

Exposes the current set of open (triggered or acknowledged) PagerDuty
incidents as a structured JSON resource. Claude can read this at the start
of any incident-response conversation to get immediate situational
awareness without an explicit tool call.

Resource URI: sre://incidents/active
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

INCIDENTS_RESOURCE_URI = "sre://incidents/active"

_PAGERDUTY_API = "https://api.pagerduty.com"


def get_incidents_resource() -> Resource:
    """Return MCP Resource metadata for the active incidents feed."""
    return Resource(
        uri=INCIDENTS_RESOURCE_URI,
        name="Active Incidents — PagerDuty",
        description=(
            "All currently open (triggered or acknowledged) PagerDuty incidents "
            "with urgency, affected service, assignee, and age. Read this first "
            "in any incident-response or on-call conversation."
        ),
        mimeType="application/json",
    )


async def read_active_incidents() -> str:
    """Fetch open incidents from PagerDuty and return them as a JSON string.

    Never raises: on any upstream failure the returned JSON carries an
    ``error`` key so the MCP client always receives valid resource content.
    """
    try:
        data = await _fetch_incidents()
    except httpx.HTTPStatusError as exc:
        log.warning(
            "incidents_resource_http_error",
            status=exc.response.status_code,
            url=str(exc.request.url),
        )
        data = {
            "error": f"PagerDuty API returned HTTP {exc.response.status_code}",
            "timestamp": int(time.time()),
        }
    except httpx.HTTPError as exc:
        log.warning("incidents_resource_transport_error", error=str(exc))
        data = {
            "error": f"Could not reach PagerDuty: {exc}",
            "timestamp": int(time.time()),
        }
    return json.dumps(data, indent=2)


async def _fetch_incidents() -> dict[str, Any]:
    token = os.environ.get("PAGERDUTY_TOKEN", "")
    async with httpx.AsyncClient(
        base_url=_PAGERDUTY_API,
        headers={
            "Authorization": f"Token token={token}",
            "Accept": "application/vnd.pagerduty+json;version=2",
        },
        timeout=15.0,
    ) as client:
        resp = await client.get(
            "/incidents",
            params={
                "statuses[]": ["triggered", "acknowledged"],
                "sort_by": "created_at:desc",
                "limit": 25,
            },
        )
        resp.raise_for_status()
        raw = resp.json().get("incidents", [])

    incidents = [
        {
            "id": inc.get("id"),
            "title": inc.get("title"),
            "status": inc.get("status"),
            "urgency": inc.get("urgency"),
            "service": inc.get("service", {}).get("summary"),
            "assignees": [
                a.get("assignee", {}).get("summary")
                for a in inc.get("assignments", [])
            ],
            "created_at": inc.get("created_at"),
            "url": inc.get("html_url"),
        }
        for inc in raw
    ]

    log.info("incidents_resource_read", count=len(incidents))
    return {
        "timestamp": int(time.time()),
        "summary": {
            "total": len(incidents),
            "triggered": sum(1 for i in incidents if i["status"] == "triggered"),
            "acknowledged": sum(1 for i in incidents if i["status"] == "acknowledged"),
            "high_urgency": sum(1 for i in incidents if i["urgency"] == "high"),
        },
        "incidents": incidents,
    }
