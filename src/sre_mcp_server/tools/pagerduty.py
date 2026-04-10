"""PagerDuty MCP tools — query incidents, escalation policies, and on-call schedules."""

from __future__ import annotations

import os
import json
from datetime import datetime, timezone, timedelta

import httpx
from mcp.types import Tool

from sre_mcp_server.tools.base import BaseToolHandler


class PagerDutyTools(BaseToolHandler):
    """Tools for interacting with PagerDuty."""

    TOOL_NAMES = {
        "get_active_incidents",
        "get_incident_details",
        "get_oncall_schedule",
        "acknowledge_incident",
    }

    def __init__(self) -> None:
        token = os.environ.get("PAGERDUTY_TOKEN", "")
        self._client = httpx.AsyncClient(
            base_url="https://api.pagerduty.com",
            headers={
                "Authorization": f"Token token={token}",
                "Accept": "application/vnd.pagerduty+json;version=2",
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )

    async def get_tools(self) -> list[Tool]:
        return [
            Tool(
                name="get_active_incidents",
                description=(
                    "Fetch currently open/acknowledged PagerDuty incidents. "
                    "Returns severity, title, affected service, assignee, and duration."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "urgency": {
                            "type": "string",
                            "enum": ["high", "low", "any"],
                            "description": "Filter by urgency. Default: any",
                            "default": "any",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max incidents to return (1-25)",
                            "default": 10,
                            "minimum": 1,
                            "maximum": 25,
                        },
                    },
                },
            ),
            Tool(
                name="get_incident_details",
                description=(
                    "Get full details for a specific PagerDuty incident including "
                    "timeline, notes, and log entries."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "incident_id": {
                            "type": "string",
                            "description": "PagerDuty incident ID (e.g. P1A2B3C)",
                        }
                    },
                    "required": ["incident_id"],
                },
            ),
            Tool(
                name="get_oncall_schedule",
                description="Show who is currently on-call for each escalation policy.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "schedule_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional list of specific schedule IDs to query",
                        }
                    },
                },
            ),
            Tool(
                name="acknowledge_incident",
                description="Acknowledge a PagerDuty incident on behalf of the on-call engineer.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "incident_id": {
                            "type": "string",
                            "description": "PagerDuty incident ID to acknowledge",
                        },
                        "from_email": {
                            "type": "string",
                            "description": "Email address of the acknowledging user",
                        },
                    },
                    "required": ["incident_id", "from_email"],
                },
            ),
        ]

    async def call(self, name: str, args: dict) -> str:
        match name:
            case "get_active_incidents":
                return await self._get_active_incidents(**args)
            case "get_incident_details":
                return await self._get_incident_details(**args)
            case "get_oncall_schedule":
                return await self._get_oncall_schedule(**args)
            case "acknowledge_incident":
                return await self._acknowledge_incident(**args)
            case _:
                raise ValueError(f"Unknown PagerDuty tool: {name}")

    async def _get_active_incidents(
        self, urgency: str = "any", limit: int = 10
    ) -> str:
        params: dict = {
            "statuses[]": ["triggered", "acknowledged"],
            "limit": limit,
            "sort_by": "created_at:desc",
        }
        if urgency != "any":
            params["urgencies[]"] = [urgency]

        resp = await self._client.get("/incidents", params=params)
        resp.raise_for_status()
        data = resp.json()

        incidents = data.get("incidents", [])
        if not incidents:
            return "No active incidents. All clear."

        lines = [f"ACTIVE INCIDENTS ({len(incidents)} total)\n{'═' * 50}"]
        for inc in incidents:
            created = datetime.fromisoformat(inc["created_at"].replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - created
            age_str = _format_duration(age)

            assignees = ", ".join(
                a["assignee"]["summary"] for a in inc.get("assignments", [])
            ) or "Unassigned"

            lines.append(
                f"\n[{inc['urgency'].upper()}] {inc['title']}\n"
                f"  ID:       {inc['id']}\n"
                f"  Service:  {inc['service']['summary']}\n"
                f"  Status:   {inc['status']}\n"
                f"  Assignee: {assignees}\n"
                f"  Age:      {age_str}\n"
                f"  URL:      {inc['html_url']}"
            )

        return "\n".join(lines)

    async def _get_incident_details(self, incident_id: str) -> str:
        resp = await self._client.get(f"/incidents/{incident_id}")
        resp.raise_for_status()
        inc = resp.json()["incident"]

        # Fetch recent log entries
        logs_resp = await self._client.get(
            f"/incidents/{incident_id}/log_entries",
            params={"limit": 10, "is_overview": True},
        )
        logs = logs_resp.json().get("log_entries", []) if logs_resp.is_success else []

        lines = [
            f"INCIDENT {incident_id}\n{'═' * 50}",
            f"Title:    {inc['title']}",
            f"Status:   {inc['status']}",
            f"Urgency:  {inc['urgency']}",
            f"Service:  {inc['service']['summary']}",
            f"Created:  {inc['created_at']}",
            f"URL:      {inc['html_url']}",
        ]

        if inc.get("body", {}).get("details"):
            lines.append(f"\nDetails:\n{inc['body']['details']}")

        if logs:
            lines.append(f"\nRECENT ACTIVITY")
            for entry in reversed(logs[-5:]):
                lines.append(f"  [{entry['created_at']}] {entry['summary']}")

        return "\n".join(lines)

    async def _get_oncall_schedule(self, schedule_ids: list | None = None) -> str:
        params: dict = {"limit": 25}
        resp = await self._client.get("/oncalls", params=params)
        resp.raise_for_status()
        oncalls = resp.json().get("oncalls", [])

        seen = set()
        lines = [f"CURRENT ON-CALL\n{'═' * 50}"]
        for oc in oncalls:
            policy = oc["escalation_policy"]["summary"]
            if policy in seen:
                continue
            seen.add(policy)
            user = oc.get("user", {}).get("summary", "Unknown")
            lines.append(f"  {policy}: {user}")

        return "\n".join(lines) if len(lines) > 1 else "No on-call data available."

    async def _acknowledge_incident(self, incident_id: str, from_email: str) -> str:
        resp = await self._client.put(
            f"/incidents/{incident_id}",
            headers={"From": from_email},
            json={"incident": {"type": "incident_reference", "status": "acknowledged"}},
        )
        resp.raise_for_status()
        return f"Incident {incident_id} acknowledged successfully."


def _format_duration(delta: timedelta) -> str:
    total_seconds = int(delta.total_seconds())
    if total_seconds < 3600:
        return f"{total_seconds // 60}m"
    elif total_seconds < 86400:
        return f"{total_seconds // 3600}h {(total_seconds % 3600) // 60}m"
    else:
        return f"{total_seconds // 86400}d {(total_seconds % 86400) // 3600}h"
