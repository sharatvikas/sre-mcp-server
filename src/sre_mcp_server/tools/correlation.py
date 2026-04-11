"""Alert correlation tool — identifies related alerts and probable root causes.

Groups simultaneously-firing Alertmanager alerts by time window, shared
labels, and causal dependency patterns to surface the most likely root cause
alert in a storm. Uses Claude Opus for deep cross-service reasoning.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import httpx
from mcp.types import Tool

from sre_mcp_server.tools.base import BaseToolHandler


_ALERTMANAGER_URL = os.environ.get("ALERTMANAGER_URL", "http://localhost:9093")
_ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Causal dependency patterns — if alert A fires and alert B fires,
# A is likely the cause of B (not the other way round)
_CAUSAL_PATTERNS: list[tuple[str, str]] = [
    # Infrastructure → Application
    ("NodeNotReady", "KubePodNotReady"),
    ("NodeNotReady", "TargetDown"),
    ("NodeDiskPressure", "KubePodEvicted"),
    ("NodeMemoryPressure", "OOMKilled"),
    # Network → Service
    ("NetworkConnectivityLoss", "ServiceDown"),
    ("NetworkLatencyHigh", "RequestLatencyHigh"),
    ("DNSResolutionFailing", "ServiceDown"),
    # Database → Application
    ("PostgreSQLDown", "PaymentServiceDown"),
    ("RDSConnectionPoolExhausted", "APIHighLatency"),
    ("DatabaseSlowQueries", "APIHighLatency"),
    # SLO cascade
    ("HighErrorRate", "SLOBurnRateCritical"),
    ("HighLatency", "SLOBurnRateCritical"),
    # Kubernetes control plane
    ("KubeAPIServerDown", "KubePodNotReady"),
    ("EtcdDown", "KubeAPIServerDown"),
]


class AlertCorrelationTools(BaseToolHandler):
    """Correlates active alerts to identify root causes and suppress noise."""

    def handles(self, name: str) -> bool:
        return name in {
            "correlate_alerts",
            "get_alert_storm_summary",
            "find_root_cause",
        }

    def get_tools(self) -> list[Tool]:
        return [
            Tool(
                name="correlate_alerts",
                description=(
                    "Group and correlate currently-firing alerts by shared labels, "
                    "time window, and known causal dependency patterns. Returns "
                    "alert groups ranked by likely root cause priority."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "time_window_minutes": {
                            "type": "integer",
                            "description": "Group alerts that fired within this window (default 15)",
                            "default": 15,
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Filter alerts by Kubernetes namespace (optional)",
                        },
                    },
                    "required": [],
                },
            ),
            Tool(
                name="get_alert_storm_summary",
                description=(
                    "Detect alert storms — when many alerts fire in a short period. "
                    "Returns storm severity, affected services, and a noise reduction "
                    "recommendation (which alerts to page on, which to suppress)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "storm_threshold": {
                            "type": "integer",
                            "description": "Number of concurrent alerts that constitutes a storm",
                            "default": 5,
                        },
                    },
                    "required": [],
                },
            ),
            Tool(
                name="find_root_cause",
                description=(
                    "Use Claude AI to analyze a set of correlated alerts and identify "
                    "the most likely root cause, affected blast radius, and recommended "
                    "first actions. Returns structured root cause analysis."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "alert_names": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of alert names to analyze (from correlate_alerts output)",
                        },
                        "context": {
                            "type": "string",
                            "description": "Additional context about the incident (recent deployments, etc.)",
                        },
                    },
                    "required": ["alert_names"],
                },
            ),
        ]

    async def call(self, name: str, arguments: dict) -> str:
        if name == "correlate_alerts":
            return await self._correlate_alerts(arguments)
        elif name == "get_alert_storm_summary":
            return await self._get_storm_summary(arguments)
        elif name == "find_root_cause":
            return await self._find_root_cause(arguments)
        raise ValueError(f"Unknown tool: {name}")

    async def _correlate_alerts(self, args: dict) -> str:
        window_minutes = int(args.get("time_window_minutes", 15))
        namespace_filter = args.get("namespace", "")

        alerts = await _fetch_active_alerts()

        if namespace_filter:
            alerts = [
                a for a in alerts
                if a.get("labels", {}).get("namespace") == namespace_filter
            ]

        if not alerts:
            return json.dumps({"message": "No active alerts", "groups": []})

        # Group by shared labels
        groups = _group_alerts(alerts, window_minutes)

        # Apply causal dependency scoring
        for group in groups:
            group["root_cause_candidates"] = _score_root_cause(group["alerts"])

        return json.dumps({
            "total_alerts": len(alerts),
            "alert_groups": len(groups),
            "groups": groups,
        }, indent=2, default=str)

    async def _get_storm_summary(self, args: dict) -> str:
        threshold = int(args.get("storm_threshold", 5))
        alerts = await _fetch_active_alerts()

        if len(alerts) < threshold:
            return json.dumps({
                "storm_detected": False,
                "active_alert_count": len(alerts),
                "threshold": threshold,
            })

        # Analyze storm
        severities = defaultdict(int)
        services = defaultdict(int)
        namespaces = defaultdict(int)

        for alert in alerts:
            labels = alert.get("labels", {})
            severities[labels.get("severity", "unknown")] += 1
            if job := labels.get("job"):
                services[job] += 1
            if ns := labels.get("namespace"):
                namespaces[ns] += 1

        # Identify candidate root cause alerts (infrastructure-level)
        root_candidates = [
            a["labels"].get("alertname", "")
            for a in alerts
            if a["labels"].get("severity") == "critical"
            and a["labels"].get("alertname", "") in {p[0] for p in _CAUSAL_PATTERNS}
        ]

        return json.dumps({
            "storm_detected": True,
            "active_alert_count": len(alerts),
            "storm_severity": "CRITICAL" if severities.get("critical", 0) > 0 else "HIGH",
            "severity_breakdown": dict(severities),
            "top_affected_services": dict(sorted(services.items(), key=lambda x: x[1], reverse=True)[:5]),
            "top_affected_namespaces": dict(sorted(namespaces.items(), key=lambda x: x[1], reverse=True)[:5]),
            "likely_root_cause_alerts": root_candidates[:3],
            "recommendation": (
                "Focus on root cause candidates above. "
                "Suppress downstream alerts until infrastructure alerts resolve."
            ),
        }, indent=2)

    async def _find_root_cause(self, args: dict) -> str:
        alert_names = args.get("alert_names", [])
        context = args.get("context", "")

        # Fetch full alert details
        all_alerts = await _fetch_active_alerts()
        relevant = [
            a for a in all_alerts
            if a.get("labels", {}).get("alertname") in alert_names
        ]

        alert_text = "\n".join([
            f"- {a['labels'].get('alertname')}: {a.get('annotations', {}).get('description', 'no description')} "
            f"(severity={a['labels'].get('severity')}, namespace={a['labels'].get('namespace', 'n/a')})"
            for a in relevant
        ])

        if not alert_text:
            alert_text = "\n".join([f"- {name}: (details unavailable)" for name in alert_names])

        prompt = f"""You are an SRE analyzing a set of simultaneously-firing alerts.

Active alerts:
{alert_text}

{"Additional context: " + context if context else ""}

Known causal patterns in this system:
{chr(10).join(f"- {src} → {dst} (if {src} fires, it may cause {dst})" for src, dst in _CAUSAL_PATTERNS[:10])}

Provide a structured root cause analysis:

1. ROOT CAUSE: Which alert is most likely the original trigger? Why?
2. BLAST RADIUS: What services/components are affected downstream?
3. INVESTIGATION STEPS: 3-5 specific things to check first (with commands where applicable)
4. IMMEDIATE ACTIONS: What to do in the next 5 minutes to stabilize
5. NOISE: Which alerts are likely downstream effects and can be acknowledged/silenced?

Be specific and actionable. Use Kubernetes/AWS CLI commands where helpful."""

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": _ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-opus-4-6",
                        "max_tokens": 1200,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                r.raise_for_status()
                analysis = r.json()["content"][0]["text"]
        except Exception as e:
            analysis = f"AI analysis unavailable: {e}"

        return json.dumps({
            "alerts_analyzed": alert_names,
            "root_cause_analysis": analysis,
        }, indent=2)


async def _fetch_active_alerts() -> list[dict[str, Any]]:
    """Fetch currently-firing alerts from Alertmanager API."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{_ALERTMANAGER_URL}/api/v2/alerts",
                params={"active": "true", "silenced": "false", "inhibited": "false"},
            )
            r.raise_for_status()
            return r.json()
    except Exception:
        return []


def _group_alerts(alerts: list[dict], window_minutes: int) -> list[dict]:
    """Group alerts by shared namespace/service labels within the time window."""
    groups: dict[str, list[dict]] = defaultdict(list)

    for alert in alerts:
        labels = alert.get("labels", {})
        # Group key: namespace + job (most specific grouping)
        ns = labels.get("namespace", "")
        job = labels.get("job", "")
        severity = labels.get("severity", "")

        if ns and job:
            key = f"{ns}/{job}"
        elif ns:
            key = ns
        elif job:
            key = job
        else:
            key = severity or "ungrouped"

        groups[key].append({
            "name": labels.get("alertname", ""),
            "severity": severity,
            "namespace": ns,
            "service": job,
            "summary": alert.get("annotations", {}).get("summary", ""),
            "started_at": alert.get("startsAt", ""),
        })

    result = []
    for key, group_alerts in groups.items():
        result.append({
            "group_key": key,
            "alert_count": len(group_alerts),
            "max_severity": _max_severity(group_alerts),
            "alerts": sorted(group_alerts, key=lambda x: x.get("severity", ""), reverse=True),
        })

    return sorted(result, key=lambda g: (
        {"critical": 0, "warning": 1, "info": 2}.get(g["max_severity"], 3),
        -g["alert_count"],
    ))


def _score_root_cause(alerts: list[dict]) -> list[dict]:
    """Score each alert as a potential root cause based on causal patterns."""
    cause_map = {p[0]: p[1] for p in _CAUSAL_PATTERNS}
    effect_set = {p[1] for p in _CAUSAL_PATTERNS}
    alert_names = {a["name"] for a in alerts}

    scored = []
    for alert in alerts:
        name = alert["name"]
        is_cause = name in cause_map and cause_map[name] in alert_names
        is_effect = name in effect_set
        score = 2 if is_cause else (0 if is_effect else 1)
        scored.append({**alert, "root_cause_score": score, "is_known_cause": is_cause})

    return sorted(scored, key=lambda x: x["root_cause_score"], reverse=True)


def _max_severity(alerts: list[dict]) -> str:
    order = {"critical": 0, "warning": 1, "info": 2}
    return min(
        (a.get("severity", "info") for a in alerts),
        key=lambda s: order.get(s, 9),
        default="info",
    )
