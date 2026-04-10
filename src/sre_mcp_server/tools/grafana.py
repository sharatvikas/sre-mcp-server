"""Grafana MCP tools — query metrics and fetch dashboard links."""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

import httpx
from mcp.types import Tool

from sre_mcp_server.tools.base import BaseToolHandler


class GrafanaTools(BaseToolHandler):
    TOOL_NAMES = {"query_metrics", "get_dashboard_url", "list_firing_alerts"}

    def __init__(self) -> None:
        url = os.environ.get("GRAFANA_URL", "http://localhost:3000").rstrip("/")
        token = os.environ.get("GRAFANA_TOKEN", "")
        self._client = httpx.AsyncClient(
            base_url=url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
        self._datasource_uid = os.environ.get("GRAFANA_DATASOURCE_UID", "prometheus")

    async def get_tools(self) -> list[Tool]:
        return [
            Tool(
                name="query_metrics",
                description=(
                    "Execute a PromQL query against the Grafana-connected Prometheus datasource. "
                    "Returns time-series data or an instant vector."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "PromQL query string",
                        },
                        "time_range": {
                            "type": "string",
                            "enum": ["5m", "15m", "1h", "6h", "24h", "7d"],
                            "description": "Time range to query. Default: 1h",
                            "default": "1h",
                        },
                        "step": {
                            "type": "string",
                            "description": "Query step interval (e.g. '1m', '5m'). Default: auto",
                            "default": "auto",
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="get_dashboard_url",
                description="Search for a Grafana dashboard by name and return its URL.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Dashboard name or partial name to search for",
                        }
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="list_firing_alerts",
                description="List all currently firing Grafana-managed alerts.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "state": {
                            "type": "string",
                            "enum": ["firing", "pending", "normal", "any"],
                            "default": "firing",
                        }
                    },
                },
            ),
        ]

    async def call(self, name: str, args: dict) -> str:
        match name:
            case "query_metrics":
                return await self._query_metrics(**args)
            case "get_dashboard_url":
                return await self._get_dashboard_url(**args)
            case "list_firing_alerts":
                return await self._list_firing_alerts(**args)
            case _:
                raise ValueError(f"Unknown Grafana tool: {name}")

    async def _query_metrics(
        self, query: str, time_range: str = "1h", step: str = "auto"
    ) -> str:
        now = datetime.now(timezone.utc)
        range_map = {"5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800}
        seconds = range_map.get(time_range, 3600)
        start = now - timedelta(seconds=seconds)

        if step == "auto":
            step = f"{max(seconds // 60, 15)}s"

        resp = await self._client.get(
            f"/api/datasources/proxy/uid/{self._datasource_uid}/api/v1/query_range",
            params={
                "query": query,
                "start": int(start.timestamp()),
                "end": int(now.timestamp()),
                "step": step,
            },
        )

        if resp.status_code == 404:
            # Fall back to instant query
            resp = await self._client.get(
                f"/api/datasources/proxy/uid/{self._datasource_uid}/api/v1/query",
                params={"query": query, "time": int(now.timestamp())},
            )

        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "success":
            return f"Query failed: {data.get('error', 'unknown error')}"

        result = data["data"]["result"]
        if not result:
            return f"No data returned for query: {query}"

        lines = [f"Query: {query}", f"Range: last {time_range}", f"Results: {len(result)} series\n"]

        for series in result[:10]:  # Cap at 10 series
            metric = series.get("metric", {})
            label_str = ", ".join(f'{k}="{v}"' for k, v in metric.items() if k != "__name__")
            name = metric.get("__name__", "metric")
            header = f"{name}{{{label_str}}}" if label_str else name

            values = series.get("values", [series.get("value")])
            if values and isinstance(values[-1], (list, tuple)):
                ts, val = values[-1]
                lines.append(f"  {header}: {val} (latest)")
            elif series.get("value"):
                ts, val = series["value"]
                lines.append(f"  {header}: {val}")

        if len(result) > 10:
            lines.append(f"  ... and {len(result) - 10} more series")

        return "\n".join(lines)

    async def _get_dashboard_url(self, name: str) -> str:
        resp = await self._client.get("/api/search", params={"query": name, "type": "dash-db"})
        resp.raise_for_status()
        results = resp.json()

        if not results:
            return f"No dashboard found matching '{name}'"

        base_url = str(self._client.base_url).rstrip("/")
        lines = [f"Dashboards matching '{name}':"]
        for d in results[:5]:
            lines.append(f"  {d['title']}: {base_url}{d['url']}")
        return "\n".join(lines)

    async def _list_firing_alerts(self, state: str = "firing") -> str:
        resp = await self._client.get("/api/alertmanager/grafana/api/v2/alerts")
        resp.raise_for_status()
        alerts = resp.json()

        if state != "any":
            alerts = [a for a in alerts if a.get("status", {}).get("state") == state]

        if not alerts:
            return f"No alerts in state '{state}'."

        lines = [f"ALERTS ({state.upper()}) — {len(alerts)} total\n{'═' * 40}"]
        for alert in alerts[:20]:
            labels = alert.get("labels", {})
            annotations = alert.get("annotations", {})
            lines.append(
                f"\n[{labels.get('severity', 'unknown').upper()}] {labels.get('alertname', 'Unknown')}\n"
                f"  Summary: {annotations.get('summary', 'N/A')}\n"
                f"  Labels: {', '.join(f'{k}={v}' for k, v in labels.items() if k not in ('alertname', 'severity'))}"
            )
        return "\n".join(lines)
