"""MCP Resources — expose SLO status, runbooks, and on-call as readable resources."""

from __future__ import annotations

import os
import httpx
from mcp.types import Resource


async def get_slo_resource(service: str) -> str:
    """Return current SLO burn rate and error budget for a service."""
    grafana_url = os.environ.get("GRAFANA_URL", "http://localhost:3000")
    token = os.environ.get("GRAFANA_TOKEN", "")

    async with httpx.AsyncClient(
        base_url=grafana_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    ) as client:
        datasource = os.environ.get("GRAFANA_DATASOURCE_UID", "prometheus")

        # Burn rate query (1h window, 99.9% SLO = 0.001 error budget)
        burn_query = f'job:request_error_rate:ratio_rate1h{{job="{service}"}} / 0.001'
        budget_query = f'job:error_budget_remaining:ratio_rate30d{{job="{service}"}}'

        async def instant_query(q: str) -> str:
            resp = await client.get(
                f"/api/datasources/proxy/uid/{datasource}/api/v1/query",
                params={"query": q},
            )
            if resp.is_success:
                result = resp.json().get("data", {}).get("result", [])
                if result:
                    return result[0]["value"][1]
            return "N/A"

        burn_rate = await instant_query(burn_query)
        budget_remaining = await instant_query(budget_query)

    try:
        burn_float = float(burn_rate)
        if burn_float >= 14.4:
            status = "🔴 CRITICAL — fast burn, page immediately"
        elif burn_float >= 6.0:
            status = "🟠 HIGH — slow burn, investigate urgently"
        elif burn_float >= 1.0:
            status = "🟡 ELEVATED — consuming budget above baseline"
        else:
            status = "✅ HEALTHY"
    except (ValueError, TypeError):
        status = "⚪ UNKNOWN"

    try:
        budget_pct = float(budget_remaining) * 100
        budget_str = f"{budget_pct:.1f}%"
    except (ValueError, TypeError):
        budget_str = "N/A"

    return f"""SLO STATUS: {service}
{'═' * 50}
Burn Rate (1h):       {burn_rate}x
Error Budget Left:    {budget_str}
Status:               {status}

Thresholds:
  Fast burn (1h):  ≥ 14.4x → page immediately
  Slow burn (6h):  ≥ 6.0x  → investigate urgently
  Baseline:        1.0x     → sustainable rate

Dashboard: {os.environ.get('GRAFANA_URL', '')}/d/slo-overview?var-job={service}
"""


def list_slo_resources(services: list[str]) -> list[Resource]:
    """Return MCP Resource objects for each service's SLO."""
    return [
        Resource(
            uri=f"sre://slos/{service}",
            name=f"SLO Status: {service}",
            description=f"Current SLO burn rate and error budget for {service}",
            mimeType="text/plain",
        )
        for service in services
    ]
