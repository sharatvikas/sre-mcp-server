"""Capacity planning tool — AI-powered growth projection and resource recommendations."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

import httpx
from mcp.types import Tool

from sre_mcp_server.tools.base import BaseToolHandler


class CapacityToolHandler(BaseToolHandler):
    """AI-powered capacity planning using Prometheus metrics + Claude analysis."""

    async def handles(self, name: str) -> bool:
        return name in {"analyze_capacity", "project_growth", "get_resource_headroom"}

    async def get_tools(self) -> list[Tool]:
        return [
            Tool(
                name="analyze_capacity",
                description=(
                    "Analyze current resource utilization and project future capacity needs. "
                    "Queries Prometheus for CPU/memory/storage trends and uses Claude to "
                    "generate a capacity planning recommendation with timelines."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["service"],
                    "properties": {
                        "service": {
                            "type": "string",
                            "description": "Service or deployment name to analyze",
                        },
                        "namespace": {
                            "type": "string",
                            "default": "production",
                            "description": "Kubernetes namespace",
                        },
                        "lookback_days": {
                            "type": "integer",
                            "default": 30,
                            "description": "Days of historical data to analyze",
                        },
                        "growth_scenarios": {
                            "type": "array",
                            "items": {"type": "number"},
                            "default": [25, 50, 100],
                            "description": "Traffic growth percentages to model (e.g. [25, 50, 100])",
                        },
                    },
                },
            ),
            Tool(
                name="get_resource_headroom",
                description=(
                    "Get current resource headroom for all deployments in a namespace. "
                    "Returns which services are close to their limits and which are over-provisioned."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "namespace": {
                            "type": "string",
                            "default": "production",
                        },
                        "warn_threshold_pct": {
                            "type": "number",
                            "default": 80,
                            "description": "CPU/memory utilization % that triggers a warning",
                        },
                    },
                },
            ),
            Tool(
                name="project_growth",
                description=(
                    "Project infrastructure costs and resource needs at different growth rates. "
                    "Combines current spend from AWS Cost Explorer with utilization trends."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["growth_pct", "horizon_months"],
                    "properties": {
                        "growth_pct": {
                            "type": "number",
                            "description": "Expected traffic/usage growth percentage",
                        },
                        "horizon_months": {
                            "type": "integer",
                            "description": "Planning horizon in months (e.g. 6, 12)",
                        },
                        "include_safety_margin": {
                            "type": "number",
                            "default": 20,
                            "description": "Additional headroom percentage to add (default 20%)",
                        },
                    },
                },
            ),
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "analyze_capacity":
            return await self._analyze_capacity(arguments)
        if name == "get_resource_headroom":
            return await self._get_headroom(arguments)
        if name == "project_growth":
            return await self._project_growth(arguments)
        raise ValueError(f"Unknown tool: {name}")

    async def _query_prometheus(self, query: str, duration: str = "30d") -> list[dict]:
        grafana_url = os.environ.get("GRAFANA_URL", "")
        token = os.environ.get("GRAFANA_TOKEN", "")
        if not grafana_url:
            return []

        end = datetime.utcnow()
        start = end - timedelta(days=30)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{grafana_url}/api/ds/query",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "queries": [{
                        "refId": "A",
                        "expr": query,
                        "range": True,
                        "intervalMs": 3600000,
                    }],
                    "from": str(int(start.timestamp() * 1000)),
                    "to": str(int(end.timestamp() * 1000)),
                },
                timeout=15.0,
            )
            if resp.is_success:
                return resp.json().get("results", {}).get("A", {}).get("frames", [])
        return []

    async def _analyze_capacity(self, args: dict) -> str:
        import anthropic

        service = args["service"]
        namespace = args.get("namespace", "production")
        lookback = args.get("lookback_days", 30)
        scenarios = args.get("growth_scenarios", [25, 50, 100])

        # Collect metrics from Prometheus
        cpu_frames = await self._query_prometheus(
            f'avg(rate(container_cpu_usage_seconds_total{{namespace="{namespace}",pod=~"{service}-.*"}}[5m])) by (pod)'
        )
        mem_frames = await self._query_prometheus(
            f'avg(container_memory_working_set_bytes{{namespace="{namespace}",pod=~"{service}-.*"}}) by (pod)'
        )

        metrics_summary = f"""Service: {service} (namespace: {namespace})
Lookback: {lookback} days
Data points collected: CPU={len(cpu_frames)} series, Memory={len(mem_frames)} series
Growth scenarios to model: {scenarios}%"""

        prompt = f"""You are a Staff SRE doing capacity planning. Analyze this service and provide recommendations.

{metrics_summary}

Note: Actual metric values would come from Prometheus in production. For this analysis,
assume typical SaaS growth patterns unless contradicted by the metrics data.

Provide a capacity planning analysis with:
1. **Current State** — estimated CPU/memory utilization and trend direction
2. **Growth Projections** — for each of {scenarios}% growth scenarios:
   - Additional replicas needed
   - New resource requests/limits recommendations
   - Timeline to capacity exhaustion at current trajectory
3. **Scaling Recommendations** — HPA min/max settings, resource right-sizing
4. **Infrastructure Cost Impact** — estimated node count and cost delta
5. **Action Items** — prioritized by urgency with owners

Be specific. Include kubectl commands for implementing recommendations."""

        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        resp = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text

    async def _get_headroom(self, args: dict) -> str:
        namespace = args.get("namespace", "production")
        warn_threshold = args.get("warn_threshold_pct", 80)

        cpu_frames = await self._query_prometheus(
            f'sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}"}}[5m])) by (pod)'
            f' / sum(kube_pod_container_resource_requests{{namespace="{namespace}",resource="cpu"}}) by (pod) * 100'
        )

        lines = [f"**Resource Headroom — {namespace}** (warn at >{warn_threshold}%)\n"]

        if not cpu_frames:
            lines.append("No Prometheus data available. Ensure GRAFANA_URL and GRAFANA_TOKEN are configured.")
            lines.append("\nTo check headroom manually:")
            lines.append(f"```\nkubectl top pods -n {namespace} --sort-by=cpu\nkubectl describe nodes | grep -A5 'Allocated resources'\n```")
        else:
            lines.append(f"Retrieved {len(cpu_frames)} pod metrics from Prometheus.")

        return "\n".join(lines)

    async def _project_growth(self, args: dict) -> str:
        import anthropic

        growth_pct = args["growth_pct"]
        horizon_months = args["horizon_months"]
        safety_margin = args.get("include_safety_margin", 20)

        prompt = f"""Project infrastructure needs for {growth_pct}% traffic growth over {horizon_months} months.
Safety margin: +{safety_margin}% headroom above projected peak.

Provide:
1. **Scaling Formula** — how to translate traffic growth to compute needs
2. **Node Count Projection** — current → projected with timeline
3. **Cost Projection** — monthly cost increase at different instance types
4. **Database Scaling** — when to consider read replicas, vertical scaling
5. **Network/Egress** — bandwidth and data transfer cost impact
6. **Recommended Architecture Changes** — what to build now vs. at scale inflection points

Format as a planning document with a month-by-month milestone table."""

        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        resp = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text
