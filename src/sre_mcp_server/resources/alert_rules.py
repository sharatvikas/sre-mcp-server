"""MCP Resource: Grafana-managed alert rules.

Exposes the full set of Grafana alert rule definitions as a structured JSON
resource. Claude reads this to answer "what alerts do we have for X?",
review alert coverage, and explain why a given alert fired.

Resource URI: sre://alerts/rules
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

ALERT_RULES_RESOURCE_URI = "sre://alerts/rules"


def get_alert_rules_resource() -> Resource:
    """Return MCP Resource metadata for the alert rules catalog."""
    return Resource(
        uri=ALERT_RULES_RESOURCE_URI,
        name="Alert Rules — Grafana",
        description=(
            "All Grafana-managed alert rule definitions: title, rule group, "
            "evaluation interval, labels, and annotations. Read this when "
            "reviewing alert coverage or explaining why an alert fired."
        ),
        mimeType="application/json",
    )


async def read_alert_rules() -> str:
    """Fetch alert rules from the Grafana provisioning API as a JSON string.

    Never raises: upstream failures are converted into an ``error`` key in
    the returned JSON payload.
    """
    try:
        data = await _fetch_alert_rules()
    except httpx.HTTPStatusError as exc:
        log.warning(
            "alert_rules_resource_http_error",
            status=exc.response.status_code,
            url=str(exc.request.url),
        )
        data = {
            "error": f"Grafana API returned HTTP {exc.response.status_code}",
            "timestamp": int(time.time()),
        }
    except httpx.HTTPError as exc:
        log.warning("alert_rules_resource_transport_error", error=str(exc))
        data = {
            "error": f"Could not reach Grafana: {exc}",
            "timestamp": int(time.time()),
        }
    return json.dumps(data, indent=2)


async def _fetch_alert_rules() -> dict[str, Any]:
    grafana_url = os.environ.get("GRAFANA_URL", "http://localhost:3000").rstrip("/")
    token = os.environ.get("GRAFANA_TOKEN", "")

    async with httpx.AsyncClient(
        base_url=grafana_url,
        headers={"Authorization": f"Bearer {token}"} if token else {},
        timeout=15.0,
    ) as client:
        resp = await client.get("/api/v1/provisioning/alert-rules")
        resp.raise_for_status()
        raw = resp.json()

    rules = [
        {
            "uid": rule.get("uid"),
            "title": rule.get("title"),
            "rule_group": rule.get("ruleGroup"),
            "folder_uid": rule.get("folderUID"),
            "paused": rule.get("isPaused", False),
            "for": rule.get("for"),
            "labels": rule.get("labels") or {},
            "annotations": rule.get("annotations") or {},
        }
        for rule in raw
    ]

    groups: dict[str, int] = {}
    for rule in rules:
        group = rule["rule_group"] or "ungrouped"
        groups[group] = groups.get(group, 0) + 1

    log.info("alert_rules_resource_read", count=len(rules))
    return {
        "timestamp": int(time.time()),
        "summary": {
            "total_rules": len(rules),
            "paused": sum(1 for r in rules if r["paused"]),
            "rule_groups": groups,
        },
        "rules": rules,
    }
