#!/usr/bin/env python3
"""Runnable local demo for the SRE MCP server.

Boots the MCP server in-process and exercises its real surface:

  1. Prints every registered tool / resource / prompt (the true, live registry).
  2. LIVE read against a local Grafana (http://localhost:3000, admin/admin)
     via the repo's own `sre://alerts/rules` resource handler.
  3. LIVE read of metrics via the repo's `query_metrics` Grafana tool
     (Grafana proxies the PromQL to the in-cluster Prometheus) PLUS a direct
     raw read against Prometheus on :9090 so the real JSON is visible.
  4. Renders a real prompt (`explain_alert`) through the prompt registry.
  5. Exercises the PagerDuty + CloudWatch handlers through the repo's OWN
     mocking patterns (httpx MockTransport for PagerDuty, patched boto3 for
     CloudWatch) because no real credentials exist in this environment.

Nothing here mutates Grafana, Prometheus, or the Kubernetes cluster — every
external call is either a read or a mock.
"""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import MagicMock, patch

import httpx

# ── Configure external services BEFORE importing the server ────────────────────
# The Grafana handlers read these at construction time. We embed admin:admin in
# the URL (HTTP basic auth) and leave the bearer token empty; that is the auth
# this local Grafana accepts. Prometheus on :9090 needs no auth.
GRAFANA_BASE = "http://localhost:3000"
GRAFANA_AUTH_URL = "http://admin:admin@localhost:3000"
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")

os.environ.setdefault("GRAFANA_URL", GRAFANA_AUTH_URL)
os.environ.setdefault("GRAFANA_TOKEN", "")            # force HTTP-basic-via-URL path
os.environ.setdefault("GRAFANA_DATASOURCE_UID", "prometheus")
os.environ.setdefault("SLO_SERVICES", "payments-api,checkout-api,auth-api")

# Import the real server module. This constructs every tool handler and wires
# the MCP callbacks exactly as the production entrypoint does.
from sre_mcp_server import server  # noqa: E402


def rule(title: str) -> None:
    print("\n" + "═" * 78)
    print(title)
    print("═" * 78)


def sub(title: str) -> None:
    print("\n" + "─" * 78)
    print(title)
    print("─" * 78)


async def show_surface() -> None:
    rule("1. REGISTERED MCP SURFACE  (booted in-process from sre_mcp_server.server)")

    tools = await server.list_tools()
    resources = await server.list_resources()
    prompts = await server.list_prompts()

    sub(f"TOOLS — {len(tools)} registered")
    for t in sorted(tools, key=lambda x: x.name):
        print(f"  • {t.name}")

    sub(f"RESOURCES — {len(resources)} registered")
    for r in resources:
        print(f"  • {str(r.uri):<28} {r.name}")

    sub(f"PROMPTS — {len(prompts)} registered")
    for p in prompts:
        args = ", ".join(
            f"{a.name}{'*' if a.required else ''}" for a in (p.arguments or [])
        )
        print(f"  • {p.name:<20} ({args})")

    print(f"\n  SUMMARY: {len(tools)} tools · {len(resources)} resources · {len(prompts)} prompts")


async def live_grafana_resource() -> None:
    rule("2. LIVE READ — Grafana alert-rule catalog  [REAL, admin/admin @ :3000]")
    print(f"Resource URI : sre://alerts/rules")
    print(f"Grafana      : {GRAFANA_BASE}  (auth: HTTP basic admin/admin)")
    print("Handler      : sre_mcp_server.resources.alert_rules.read_alert_rules()\n")

    payload = await server.read_resource("sre://alerts/rules")
    print(payload)
    data = json.loads(payload)
    if "error" in data:
        print("\n  NOTE: Grafana returned an error above (see 'error' key).")
    else:
        n = data.get("summary", {}).get("total_rules", "?")
        print(f"\n  → Live Grafana provisioning API answered: {n} Grafana-managed alert rule(s).")
        print("    (kube-prometheus ships its alerts as Prometheus rules, so this")
        print("     Grafana-managed catalog is legitimately empty — the 200 OK proves")
        print("     the authenticated live read worked.)")


async def live_grafana_metrics() -> None:
    rule("3a. LIVE READ — metrics via Grafana `query_metrics` tool  [REAL]")
    print("Tool    : query_metrics   (Grafana proxies PromQL → in-cluster Prometheus)")
    print("PromQL  : sum by (job) (up)\n")
    try:
        out = await server.call_tool(
            "query_metrics", {"query": "sum by (job) (up)", "time_range": "5m"}
        )
        print(out[0].text)
    except Exception as exc:  # pragma: no cover - visibility only
        print(f"  query_metrics failed: {exc!r}")


async def live_prometheus_raw() -> None:
    rule("3b. LIVE READ — raw Prometheus JSON  [REAL, direct @ :9090]")
    print(f"Prometheus : {PROMETHEUS_URL}/api/v1/query?query=up   (no auth)")
    print("Purpose    : show the real JSON the Grafana datasource proxies to.\n")
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": "up"})
    data = resp.json()
    result = data.get("data", {}).get("result", [])
    print(f"HTTP {resp.status_code} · status={data.get('status')} · {len(result)} series returned")
    print("First 3 series (trimmed):\n")
    print(json.dumps({"status": data.get("status"),
                      "data": {"resultType": data.get("data", {}).get("resultType"),
                               "result": result[:3]}}, indent=2))


async def render_prompt() -> None:
    rule("4. RENDER PROMPT — `explain_alert`  [REAL prompt registry]")
    args = {
        "alert_name": "KubePodCrashLooping",
        "labels": "namespace=monitoring, severity=warning",
        "audience": "engineer",
    }
    print(f"dispatch_prompt('explain_alert', {json.dumps(args)})\n")
    result = await server.handle_get_prompt("explain_alert", args)
    print(f"description: {result.description}\n")
    for msg in result.messages:
        print(f"[{msg.role}]")
        print(msg.content.text)


async def mocked_pagerduty() -> None:
    rule("5a. MOCKED — PagerDuty `get_active_incidents`  [NO CREDS → MOCK]")
    print("No PAGERDUTY_TOKEN in this environment. We exercise the REAL")
    print("PagerDutyTools handler + formatter, but swap its httpx client for an")
    print("httpx.MockTransport (the same HTTP-layer mocking the test suite uses).\n")

    def _pd_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "incidents": [
                    {
                        "id": "PABC123",
                        "title": "High error rate on payments-api",
                        "status": "triggered",
                        "urgency": "high",
                        "service": {"summary": "payments-api"},
                        "assignments": [{"assignee": {"summary": "Alice Chen"}}],
                        "created_at": "2026-07-05T10:15:00Z",
                        "html_url": "https://acme.pagerduty.com/incidents/PABC123",
                    }
                ]
            },
        )

    handler = server._pd
    original = handler._client
    handler._client = httpx.AsyncClient(
        base_url="https://api.pagerduty.com",
        transport=httpx.MockTransport(_pd_handler),
    )
    try:
        out = await handler.call("get_active_incidents", {"urgency": "high", "limit": 5})
        print(out)
    finally:
        await handler._client.aclose()
        handler._client = original


async def mocked_cloudwatch() -> None:
    rule("5b. MOCKED — CloudWatch `get_cloudwatch_alarms`  [NO CREDS → MOCK]")
    print("No AWS credentials here. We exercise the REAL AWSTools handler +")
    print("formatter with boto3 patched to a MagicMock (identical to the pattern")
    print("in tests/test_tools.py::TestAWSTools).\n")

    mock_cw = MagicMock()
    mock_cw.describe_alarms.return_value = {
        "MetricAlarms": [
            {
                "AlarmName": "payments-rds-cpu-high",
                "StateValue": "ALARM",
                "StateReason": "Threshold Crossed: CPUUtilization > 85% for 3 datapoints",
                "Namespace": "AWS/RDS",
                "MetricName": "CPUUtilization",
                "StateUpdatedTimestamp": "2026-07-05T10:22:00Z",
            },
            {
                "AlarmName": "checkout-alb-5xx",
                "StateValue": "ALARM",
                "StateReason": "Threshold Crossed: HTTPCode_Target_5XX_Count > 50",
                "Namespace": "AWS/ApplicationELB",
                "MetricName": "HTTPCode_Target_5XX_Count",
                "StateUpdatedTimestamp": "2026-07-05T10:18:00Z",
            },
        ]
    }
    mock_session = MagicMock()
    mock_session.client.return_value = mock_cw

    with patch("sre_mcp_server.tools.aws.boto3") as mock_boto3:
        mock_boto3.Session.return_value = mock_session
        out = await server._aws.call("get_cloudwatch_alarms", {"state": "ALARM"})
    print(out)


async def main() -> None:
    print("SRE MCP SERVER — LOCAL DEMO")
    print("LIVE   : Grafana (:3000, admin/admin) + Prometheus (:9090) on kind-sre-platform")
    print("MOCKED : PagerDuty + CloudWatch (no credentials in this environment)")

    await show_surface()
    await live_grafana_resource()
    await live_grafana_metrics()
    await live_prometheus_raw()
    await render_prompt()
    await mocked_pagerduty()
    await mocked_cloudwatch()

    rule("DEMO COMPLETE")
    print("LIVE reads   : Grafana alert-rule catalog, Grafana query_metrics, raw Prometheus.")
    print("MOCKED calls : PagerDuty incidents, CloudWatch alarms (repo mock patterns).")


if __name__ == "__main__":
    asyncio.run(main())
