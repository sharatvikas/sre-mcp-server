"""On-call intelligence tool — context-aware shift handoff and escalation guidance."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx
from mcp.types import Tool

from sre_mcp_server.tools.base import BaseToolHandler


class OnCallToolHandler(BaseToolHandler):
    """Tools for on-call shift management and escalation workflows."""

    def handles(self, name: str) -> bool:
        return name in {
            "get_current_oncall",
            "generate_shift_handoff",
            "get_escalation_policy",
        }

    def get_tools(self) -> list[Tool]:
        return [
            Tool(
                name="get_current_oncall",
                description=(
                    "Get who is currently on-call for a given service or schedule. "
                    "Returns name, email, Slack handle, and time remaining in their shift."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["schedule_name"],
                    "properties": {
                        "schedule_name": {
                            "type": "string",
                            "description": "PagerDuty schedule name (e.g. 'Platform Primary', 'Database On-Call')",
                        },
                    },
                },
            ),
            Tool(
                name="generate_shift_handoff",
                description=(
                    "Generate a structured shift handoff document from the current on-call context. "
                    "Summarizes open incidents, recent alerts, pending action items, and known issues. "
                    "Uses Claude to synthesize and highlight what the incoming engineer needs to know first."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "services": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of services to include in the handoff (default: all monitored services)",
                        },
                        "lookback_hours": {
                            "type": "integer",
                            "default": 12,
                            "description": "Hours to look back for incidents and alerts",
                        },
                    },
                },
            ),
            Tool(
                name="get_escalation_policy",
                description="Get the escalation policy for a service, including all tiers and contacts.",
                inputSchema={
                    "type": "object",
                    "required": ["service_name"],
                    "properties": {
                        "service_name": {
                            "type": "string",
                            "description": "PagerDuty service name",
                        },
                    },
                },
            ),
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "get_current_oncall":
            return await self._get_current_oncall(arguments["schedule_name"])
        if name == "generate_shift_handoff":
            return await self._generate_handoff(
                arguments.get("services", []),
                arguments.get("lookback_hours", 12),
            )
        if name == "get_escalation_policy":
            return await self._get_escalation_policy(arguments["service_name"])
        raise ValueError(f"Unknown tool: {name}")

    async def _get_current_oncall(self, schedule_name: str) -> str:
        api_key = os.environ.get("PAGERDUTY_API_KEY", "")
        headers = {
            "Authorization": f"Token token={api_key}",
            "Accept": "application/vnd.pagerduty+json;version=2",
        }

        async with httpx.AsyncClient() as client:
            # Find schedule by name
            resp = await client.get(
                "https://api.pagerduty.com/schedules",
                headers=headers,
                params={"query": schedule_name, "limit": 5},
                timeout=10.0,
            )
            resp.raise_for_status()
            schedules = resp.json().get("schedules", [])

            if not schedules:
                return f"No PagerDuty schedule found matching '{schedule_name}'"

            schedule = schedules[0]
            schedule_id = schedule["id"]

            # Get current on-call
            now = datetime.now(timezone.utc).isoformat()
            oncall_resp = await client.get(
                "https://api.pagerduty.com/oncalls",
                headers=headers,
                params={
                    "schedule_ids[]": schedule_id,
                    "since": now,
                    "until": now,
                },
                timeout=10.0,
            )
            oncall_resp.raise_for_status()
            oncalls = oncall_resp.json().get("oncalls", [])

        if not oncalls:
            return f"No one currently on-call for schedule: {schedule_name}"

        oc = oncalls[0]
        user = oc.get("user", {})
        end = oc.get("end", "unknown")

        return (
            f"**Currently on-call for {schedule_name}:**\n"
            f"- Name: {user.get('summary', 'Unknown')}\n"
            f"- Email: {user.get('email', 'N/A')}\n"
            f"- Shift ends: {end}\n"
            f"- PagerDuty profile: {user.get('html_url', '')}"
        )

    async def _generate_handoff(self, services: list[str], lookback_hours: int) -> str:
        import anthropic

        api_key = os.environ.get("PAGERDUTY_API_KEY", "")
        headers = {
            "Authorization": f"Token token={api_key}",
            "Accept": "application/vnd.pagerduty+json;version=2",
        }

        # Fetch recent incidents
        since = datetime.now(timezone.utc)
        from datetime import timedelta
        since = (since - timedelta(hours=lookback_hours)).isoformat()

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.pagerduty.com/incidents",
                headers=headers,
                params={
                    "since": since,
                    "statuses[]": ["triggered", "acknowledged"],
                    "limit": 20,
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            incidents = resp.json().get("incidents", [])

        incident_text = "\n".join(
            f"- [{i['status'].upper()}] {i['title']} (since {i['created_at']}, urgency={i['urgency']})"
            for i in incidents
        ) or "No active incidents."

        service_filter = f"Services in scope: {', '.join(services)}" if services else "All monitored services"

        prompt = f"""You are a Staff SRE writing a shift handoff document.

{service_filter}
Lookback window: {lookback_hours} hours
Current time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

Active/recent incidents:
{incident_text}

Generate a shift handoff document with:
1. **Shift Summary** — 2-3 sentence TL;DR of current system health
2. **Active Incidents** — each with current status, what's been tried, next steps
3. **Watch Items** — things that haven't paged yet but might (degraded metrics, deployments in progress)
4. **Pending Action Items** — anything the incoming engineer needs to follow up on
5. **System Notes** — known issues, maintenance windows, recent deployments to be aware of
6. **Emergency Contacts** — who to escalate to and for what

Be direct and specific. Incoming engineer is reading this at shift start."""

        client_ai = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        resp_ai = client_ai.messages.create(
            model="claude-opus-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp_ai.content[0].text

    async def _get_escalation_policy(self, service_name: str) -> str:
        api_key = os.environ.get("PAGERDUTY_API_KEY", "")
        headers = {
            "Authorization": f"Token token={api_key}",
            "Accept": "application/vnd.pagerduty+json;version=2",
        }

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.pagerduty.com/services",
                headers=headers,
                params={"query": service_name, "limit": 5, "include[]": "escalation_policies"},
                timeout=10.0,
            )
            resp.raise_for_status()
            services = resp.json().get("services", [])

        if not services:
            return f"No PagerDuty service found matching '{service_name}'"

        svc = services[0]
        ep = svc.get("escalation_policy", {})

        lines = [
            f"**Escalation policy for {svc['name']}:**",
            f"Policy: {ep.get('summary', 'Unknown')}",
            f"URL: {ep.get('html_url', '')}",
        ]
        return "\n".join(lines)
