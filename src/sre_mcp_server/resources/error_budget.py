"""MCP Resource: Error budget tracking across all services.

Exposes a structured JSON resource that aggregates SLO burn rates
and error budget status for every service registered in Prometheus.
Claude can read this resource at conversation start to get an immediate
picture of which services are at risk without explicit tool calls.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.types import Resource


GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://localhost:3000")
GRAFANA_TOKEN = os.environ.get("GRAFANA_TOKEN", "")
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")

_HEADERS = {"Authorization": f"Bearer {GRAFANA_TOKEN}"} if GRAFANA_TOKEN else {}


async def get_error_budget_resource() -> Resource:
    """Return MCP Resource metadata for the error budget report."""
    return Resource(
        uri="sre://error-budget/all",
        name="Error Budget Status — All Services",
        description=(
            "Real-time error budget consumption and SLO burn rates across all "
            "services. Read this at the start of any capacity or SLO review to "
            "identify services at risk of burning their 30-day error budget."
        ),
        mimeType="application/json",
    )


async def read_error_budget() -> str:
    """Fetch error budget data from Prometheus and return as JSON string."""
    import json
    import time

    try:
        data = await _fetch_from_prometheus()
    except Exception as e:
        return json.dumps({"error": str(e), "timestamp": int(time.time())})

    return json.dumps(data, indent=2)


async def _fetch_from_prometheus() -> dict[str, Any]:
    """Query Prometheus for SLO metrics and build budget report."""
    import time

    prometheus_base = PROMETHEUS_URL.rstrip("/")

    async with httpx.AsyncClient(timeout=15) as client:
        # Multi-window burn rates
        fast_burn = await _query(client, prometheus_base,
            'sum by (job) (job:request_error_rate:ratio_rate5m) * 14.4')
        slow_burn = await _query(client, prometheus_base,
            'sum by (job) (job:request_error_rate:ratio_rate6h) * 6')
        error_rate_1h = await _query(client, prometheus_base,
            'sum by (job) (job:request_error_rate:ratio_rate1h)')

    services: dict[str, dict] = {}

    def _add(metric_name: str, results: list[dict]) -> None:
        for r in results:
            job = r.get("metric", {}).get("job", "unknown")
            val = float(r.get("value", [0, "0"])[1])
            if job not in services:
                services[job] = {}
            services[job][metric_name] = round(val, 6)

    _add("fast_burn_rate", fast_burn)
    _add("slow_burn_rate", slow_burn)
    _add("error_rate_1h", error_rate_1h)

    # Classify budget status
    for svc, metrics in services.items():
        fast = metrics.get("fast_burn_rate", 0)
        slow = metrics.get("slow_burn_rate", 0)
        err_1h = metrics.get("error_rate_1h", 0)

        if fast > 14.4 or slow > 6.0:
            status = "CRITICAL"
            alert = "SLO burn rate alert firing — error budget will be exhausted"
        elif fast > 5.0 or slow > 2.0:
            status = "WARNING"
            alert = "Elevated burn rate — monitor closely"
        elif err_1h > 0.001:
            status = "DEGRADED"
            alert = "Non-zero error rate — below SLO threshold but worth watching"
        else:
            status = "OK"
            alert = None

        # Estimate 30-day budget remaining assuming current 1h rate
        # SLO target = 99.9% → error budget = 0.1% = 43.2 min/month
        error_budget_used_pct = round(err_1h * 100 / 0.001, 1) if err_1h > 0 else 0.0
        remaining_pct = max(0.0, 100.0 - error_budget_used_pct)

        metrics["status"] = status
        metrics["error_budget_used_pct"] = error_budget_used_pct
        metrics["error_budget_remaining_pct"] = remaining_pct
        if alert:
            metrics["alert"] = alert

    # Sort: CRITICAL first, then WARNING, DEGRADED, OK
    order = {"CRITICAL": 0, "WARNING": 1, "DEGRADED": 2, "OK": 3}
    sorted_services = dict(
        sorted(services.items(), key=lambda x: order.get(x[1].get("status", "OK"), 9))
    )

    critical_count = sum(1 for s in services.values() if s.get("status") == "CRITICAL")
    warning_count = sum(1 for s in services.values() if s.get("status") == "WARNING")

    return {
        "timestamp": int(time.time()),
        "summary": {
            "total_services": len(services),
            "critical": critical_count,
            "warning": warning_count,
            "healthy": len(services) - critical_count - warning_count,
        },
        "services": sorted_services,
    }


async def _query(client: httpx.AsyncClient, base: str, promql: str) -> list[dict]:
    """Execute a PromQL instant query."""
    try:
        r = await client.get(
            f"{base}/api/v1/query",
            params={"query": promql},
        )
        r.raise_for_status()
        result = r.json()
        return result.get("data", {}).get("result", [])
    except Exception:
        return []
