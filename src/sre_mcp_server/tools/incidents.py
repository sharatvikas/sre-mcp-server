"""Incident management and AlertManager MCP tools.

Provides:
- list_alertmanager_alerts —  query AlertManager for currently firing alerts
- silence_alert         — create an AlertManager silence
- create_incident       — open a PagerDuty incident programmatically
- get_incident_timeline — retrieve a PagerDuty incident + timeline
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx
from mcp.types import Tool

from sre_mcp_server.tools.base import BaseToolHandler

ALERTMANAGER_URL = os.environ.get("ALERTMANAGER_URL", "http://alertmanager:9093")
PAGERDUTY_API_KEY = os.environ.get("PAGERDUTY_API_KEY", "")
PAGERDUTY_FROM = os.environ.get("PAGERDUTY_FROM_EMAIL", "sre-bot@example.com")


class IncidentToolHandler(BaseToolHandler):
    """Tools for incident management, alert silencing, and PagerDuty integration."""

    async def handles(self, name: str) -> bool:
        return name in {
            "list_alertmanager_alerts",
            "silence_alert",
            "create_incident",
            "get_incident_timeline",
        }

    async def get_tools(self) -> list[Tool]:
        return [
            Tool(
                name="list_alertmanager_alerts",
                description=(
                    "List all currently firing alerts from AlertManager. "
                    "Optionally filter by label (e.g. severity=critical, service=payments-api). "
                    "Returns alert name, labels, annotations, and how long it has been firing."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "filter_label": {
                            "type": "string",
                            "description": "Label filter in key=value format (e.g. 'severity=critical'). Optional.",
                        },
                        "include_silenced": {
                            "type": "boolean",
                            "description": "Include silenced alerts (default false).",
                            "default": False,
                        },
                    },
                },
            ),
            Tool(
                name="silence_alert",
                description=(
                    "Create an AlertManager silence for a given alert or label set. "
                    "Use this to suppress noisy alerts during maintenance windows or known incidents. "
                    "Returns the silence ID."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["matchers", "duration_minutes", "comment"],
                    "properties": {
                        "matchers": {
                            "type": "array",
                            "description": "List of label matchers, e.g. [{\"name\": \"alertname\", \"value\": \"PodCrashLooping\", \"isRegex\": false}]",
                            "items": {
                                "type": "object",
                                "required": ["name", "value"],
                                "properties": {
                                    "name": {"type": "string"},
                                    "value": {"type": "string"},
                                    "isRegex": {"type": "boolean", "default": False},
                                },
                            },
                        },
                        "duration_minutes": {
                            "type": "integer",
                            "description": "How long to silence for, in minutes.",
                            "minimum": 1,
                            "maximum": 10080,
                        },
                        "comment": {
                            "type": "string",
                            "description": "Human-readable reason for the silence (required by AlertManager).",
                        },
                        "created_by": {
                            "type": "string",
                            "description": "Who is creating this silence (default: sre-mcp-bot).",
                            "default": "sre-mcp-bot",
                        },
                    },
                },
            ),
            Tool(
                name="create_incident",
                description=(
                    "Open a new PagerDuty incident for a given service. "
                    "Use this when an automated system detects an issue that needs human response "
                    "and AlertManager has not already triggered the escalation."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["title", "service_id", "severity"],
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Short description of the incident.",
                        },
                        "service_id": {
                            "type": "string",
                            "description": "PagerDuty service ID (e.g. P12ABCD).",
                        },
                        "severity": {
                            "type": "string",
                            "enum": ["critical", "error", "warning", "info"],
                            "description": "Incident severity.",
                        },
                        "body": {
                            "type": "string",
                            "description": "Detailed description / runbook excerpt.",
                        },
                        "escalation_policy_id": {
                            "type": "string",
                            "description": "Override escalation policy ID. Optional.",
                        },
                    },
                },
            ),
            Tool(
                name="get_incident_timeline",
                description=(
                    "Retrieve a PagerDuty incident and its log entries / timeline. "
                    "Useful for writing postmortems or understanding response gaps."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["incident_id"],
                    "properties": {
                        "incident_id": {
                            "type": "string",
                            "description": "PagerDuty incident ID (e.g. P1234567).",
                        },
                    },
                },
            ),
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        return await self.run_tool(name, arguments)

    async def run_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "list_alertmanager_alerts":
            return await self._list_firing_alerts(arguments)
        if name == "silence_alert":
            return await self._silence_alert(arguments)
        if name == "create_incident":
            return await self._create_incident(arguments)
        if name == "get_incident_timeline":
            return await self._get_incident_timeline(arguments)
        raise ValueError(f"Unknown tool: {name}")

    # ── AlertManager ──────────────────────────────────────────────────────────

    async def _list_firing_alerts(self, args: dict[str, Any]) -> str:
        filter_label = args.get("filter_label", "")
        include_silenced = args.get("include_silenced", False)

        params: dict[str, Any] = {"active": "true", "silenced": str(include_silenced).lower()}
        if filter_label and "=" in filter_label:
            params["filter"] = filter_label

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{ALERTMANAGER_URL}/api/v2/alerts", params=params)
                resp.raise_for_status()
                alerts = resp.json()
        except Exception as exc:
            return self._mock_alerts(filter_label, str(exc))

        if not alerts:
            return "No firing alerts found."

        lines = [f"## Firing Alerts ({len(alerts)} total)\n"]
        for alert in alerts:
            labels = alert.get("labels", {})
            annotations = alert.get("annotations", {})
            starts_at = alert.get("startsAt", "")
            fingerprint = alert.get("fingerprint", "")[:8]

            firing_since = ""
            if starts_at:
                try:
                    t = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
                    delta = datetime.now(timezone.utc) - t
                    minutes = int(delta.total_seconds() / 60)
                    firing_since = f" (firing {minutes}m)"
                except Exception:
                    firing_since = f" (since {starts_at})"

            lines.append(f"### {labels.get('alertname', 'Unknown')}{firing_since}")
            lines.append(f"- **Fingerprint**: `{fingerprint}`")
            lines.append(f"- **Severity**: {labels.get('severity', 'n/a')}")
            lines.append(f"- **Namespace**: {labels.get('namespace', 'n/a')}")
            if summary := annotations.get("summary"):
                lines.append(f"- **Summary**: {summary}")
            if runbook := annotations.get("runbook_url"):
                lines.append(f"- **Runbook**: {runbook}")
            lines.append("")

        return "\n".join(lines)

    async def _silence_alert(self, args: dict[str, Any]) -> str:
        from datetime import timedelta

        matchers = args["matchers"]
        duration_minutes = args["duration_minutes"]
        comment = args["comment"]
        created_by = args.get("created_by", "sre-mcp-bot")

        now = datetime.now(timezone.utc)
        ends_at = now + timedelta(minutes=duration_minutes)

        payload = {
            "matchers": [
                {
                    "name": m["name"],
                    "value": m["value"],
                    "isRegex": m.get("isRegex", False),
                    "isEqual": True,
                }
                for m in matchers
            ],
            "startsAt": now.isoformat(),
            "endsAt": ends_at.isoformat(),
            "comment": comment,
            "createdBy": created_by,
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{ALERTMANAGER_URL}/api/v2/silences",
                    json=payload,
                )
                resp.raise_for_status()
                silence_id = resp.json().get("silenceID", "unknown")
            return (
                f"Silence created successfully.\n"
                f"- **ID**: `{silence_id}`\n"
                f"- **Duration**: {duration_minutes} minutes (until {ends_at.strftime('%H:%M UTC')})\n"
                f"- **Reason**: {comment}"
            )
        except Exception as exc:
            return (
                f"AlertManager unreachable ({exc}). Silence payload (apply manually):\n"
                f"```json\n{_json(payload)}\n```"
            )

    # ── PagerDuty ─────────────────────────────────────────────────────────────

    async def _create_incident(self, args: dict[str, Any]) -> str:
        if not PAGERDUTY_API_KEY:
            return (
                "PAGERDUTY_API_KEY not configured. Set it as an environment variable "
                "to enable programmatic incident creation."
            )

        payload: dict[str, Any] = {
            "incident": {
                "type": "incident",
                "title": args["title"],
                "service": {"id": args["service_id"], "type": "service_reference"},
                "urgency": "high" if args["severity"] in ("critical", "error") else "low",
                "body": {
                    "type": "incident_body",
                    "details": args.get("body", args["title"]),
                },
            }
        }
        if ep := args.get("escalation_policy_id"):
            payload["incident"]["escalation_policy"] = {
                "id": ep,
                "type": "escalation_policy_reference",
            }

        headers = {
            "Authorization": f"Token token={PAGERDUTY_API_KEY}",
            "Accept": "application/vnd.pagerduty+json;version=2",
            "From": PAGERDUTY_FROM,
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    "https://api.pagerduty.com/incidents",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                inc = resp.json()["incident"]
            return (
                f"Incident created.\n"
                f"- **ID**: {inc['id']}\n"
                f"- **Number**: #{inc['incident_number']}\n"
                f"- **URL**: {inc['html_url']}\n"
                f"- **Status**: {inc['status']}\n"
                f"- **Urgency**: {inc['urgency']}"
            )
        except httpx.HTTPStatusError as exc:
            return f"PagerDuty API error {exc.response.status_code}: {exc.response.text}"
        except Exception as exc:
            return f"Failed to create PagerDuty incident: {exc}"

    async def _get_incident_timeline(self, args: dict[str, Any]) -> str:
        if not PAGERDUTY_API_KEY:
            return "PAGERDUTY_API_KEY not configured."

        incident_id = args["incident_id"]
        headers = {
            "Authorization": f"Token token={PAGERDUTY_API_KEY}",
            "Accept": "application/vnd.pagerduty+json;version=2",
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                inc_resp = await client.get(
                    f"https://api.pagerduty.com/incidents/{incident_id}",
                    headers=headers,
                )
                inc_resp.raise_for_status()
                inc = inc_resp.json()["incident"]

                log_resp = await client.get(
                    f"https://api.pagerduty.com/incidents/{incident_id}/log_entries",
                    headers=headers,
                    params={"is_overview": "true"},
                )
                log_resp.raise_for_status()
                entries = log_resp.json().get("log_entries", [])
        except httpx.HTTPStatusError as exc:
            return f"PagerDuty API error {exc.response.status_code}: {exc.response.text}"
        except Exception as exc:
            return f"Failed to retrieve incident: {exc}"

        lines = [
            f"## Incident #{inc['incident_number']}: {inc['title']}",
            f"- **Status**: {inc['status']}",
            f"- **Urgency**: {inc['urgency']}",
            f"- **Created**: {inc.get('created_at', 'n/a')}",
            f"- **URL**: {inc.get('html_url', 'n/a')}",
            "",
            f"## Timeline ({len(entries)} events)",
            "",
        ]
        for entry in entries:
            at = entry.get("created_at", "")
            etype = entry.get("type", "").replace("_log_entry", "").replace("_", " ")
            summary = entry.get("summary", "")
            lines.append(f"- `{at}` **{etype}** — {summary}")

        return "\n".join(lines)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _mock_alerts(self, filter_label: str, error: str) -> str:
        return (
            f"AlertManager at {ALERTMANAGER_URL} unreachable ({error}).\n\n"
            f"**Mock response** (configure ALERTMANAGER_URL to get live data):\n\n"
            f"### HighErrorRate (firing 12m)\n"
            f"- **Severity**: critical\n"
            f"- **Namespace**: payments\n"
            f"- **Summary**: Error rate 8.2% exceeds SLO threshold 1%\n"
            f"- **Runbook**: https://runbooks.internal/high-error-rate\n"
        )


def _json(obj: Any) -> str:
    import json
    return json.dumps(obj, indent=2, default=str)
