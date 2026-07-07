"""MCP Resource: Cluster capacity planning data.

Exposes a structured JSON resource summarizing Kubernetes cluster capacity,
current utilization, and headroom across all namespaces. Claude reads this
to answer capacity planning questions without needing to make multiple
tool calls for individual metrics.

Resource URI: sre://capacity/overview
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from mcp.types import Resource

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")


async def get_capacity_resource() -> Resource:
    """Return MCP Resource metadata for the capacity overview."""
    return Resource(
        uri="sre://capacity/overview",
        name="Cluster Capacity Overview",
        description=(
            "Current Kubernetes cluster capacity, utilization, and headroom. "
            "Read this at the start of any capacity planning or rightsizing "
            "discussion to understand the current state before making recommendations."
        ),
        mimeType="application/json",
    )


async def read_capacity() -> str:
    """Query Prometheus for capacity metrics and return structured JSON."""
    queries = {
        # Node-level capacity
        "node_cpu_capacity_total_cores": "sum(kube_node_status_allocatable{resource='cpu'}) / 1000",
        "node_memory_capacity_total_gib": "sum(kube_node_status_allocatable{resource='memory'}) / 1024 / 1024 / 1024",
        "node_count_total": "count(kube_node_info)",
        "node_count_ready": "count(kube_node_status_condition{condition='Ready',status='true'})",

        # Cluster-level utilization (all pods vs allocatable)
        "cluster_cpu_requested_cores": "sum(kube_pod_container_resource_requests{resource='cpu', node!=''})",
        "cluster_memory_requested_gib": "sum(kube_pod_container_resource_requests{resource='memory', node!=''}) / 1024 / 1024 / 1024",
        "cluster_cpu_used_cores": "sum(rate(container_cpu_usage_seconds_total{container!='',pod!=''}[5m]))",
        "cluster_memory_used_gib": "sum(container_memory_working_set_bytes{container!='',pod!=''}) / 1024 / 1024 / 1024",

        # Per-namespace top consumers
        "top_cpu_namespaces": "topk(5, sum by (namespace) (kube_pod_container_resource_requests{resource='cpu', node!=''}))",
        "top_memory_namespaces": "topk(5, sum by (namespace) (kube_pod_container_resource_requests{resource='memory', node!=''})) / 1024 / 1024 / 1024",

        # Headroom (capacity - requested)
        "cpu_headroom_cores": "sum(kube_node_status_allocatable{resource='cpu'}) / 1000 - sum(kube_pod_container_resource_requests{resource='cpu', node!=''})",
        "memory_headroom_gib": "(sum(kube_node_status_allocatable{resource='memory'}) - sum(kube_pod_container_resource_requests{resource='memory', node!=''})) / 1024 / 1024 / 1024",

        # Containers at risk
        "containers_cpu_over_80pct": "count(container:cpu_utilization:ratio > 0.8)",
        "containers_memory_over_80pct": "count(container:memory_utilization:ratio > 0.8)",
        "containers_oom_last_hour": "count(increase(kube_pod_container_status_restarts_total[1h]) > 0)",

        # Node pressure
        "nodes_with_memory_pressure": "count(kube_node_status_condition{condition='MemoryPressure',status='true'})",
        "nodes_with_disk_pressure": "count(kube_node_status_condition{condition='DiskPressure',status='true'})",
        "nodes_with_cpu_pressure": "count(kube_node_status_condition{condition='PIDPressure',status='true'})",
    }

    results: dict[str, Any] = {}

    async with httpx.AsyncClient(timeout=15) as client:
        for metric_name, query in queries.items():
            try:
                resp = await client.get(
                    f"{PROMETHEUS_URL}/api/v1/query",
                    params={"query": query},
                )
                if resp.status_code != 200:
                    results[metric_name] = None
                    continue

                data = resp.json()
                result = data.get("data", {}).get("result", [])

                if not result:
                    results[metric_name] = None
                    continue

                # For scalar/vector metrics, return the value
                if len(result) == 1 and "metric" not in result[0] or result[0]["metric"] == {}:
                    results[metric_name] = float(result[0]["value"][1])
                else:
                    # For labeled metrics, return dict
                    results[metric_name] = {
                        r["metric"].get("namespace", r["metric"].get("node", str(i))): round(float(r["value"][1]), 2)
                        for i, r in enumerate(result)
                    }
            except Exception:
                results[metric_name] = None

    # Compute derived metrics
    cpu_cap = results.get("node_cpu_capacity_total_cores") or 0
    cpu_req = results.get("cluster_cpu_requested_cores") or 0
    cpu_used = results.get("cluster_cpu_used_cores") or 0
    mem_cap = results.get("node_memory_capacity_total_gib") or 0
    mem_req = results.get("cluster_memory_requested_gib") or 0
    mem_used = results.get("cluster_memory_used_gib") or 0

    summary = {
        "timestamp": "live",
        "cluster": {
            "nodes": {
                "total": results.get("node_count_total"),
                "ready": results.get("node_count_ready"),
                "memory_pressure": results.get("nodes_with_memory_pressure", 0),
                "disk_pressure": results.get("nodes_with_disk_pressure", 0),
            },
            "cpu": {
                "capacity_cores": round(cpu_cap, 1),
                "requested_cores": round(cpu_req, 1),
                "used_cores": round(cpu_used, 1),
                "request_utilization_pct": round(cpu_req / cpu_cap * 100, 1) if cpu_cap > 0 else None,
                "actual_utilization_pct": round(cpu_used / cpu_cap * 100, 1) if cpu_cap > 0 else None,
                "headroom_cores": round(results.get("cpu_headroom_cores") or (cpu_cap - cpu_req), 1),
                "overcommit_ratio": round(cpu_req / cpu_cap, 2) if cpu_cap > 0 else None,
            },
            "memory": {
                "capacity_gib": round(mem_cap, 1),
                "requested_gib": round(mem_req, 1),
                "used_gib": round(mem_used, 1),
                "request_utilization_pct": round(mem_req / mem_cap * 100, 1) if mem_cap > 0 else None,
                "actual_utilization_pct": round(mem_used / mem_cap * 100, 1) if mem_cap > 0 else None,
                "headroom_gib": round(results.get("memory_headroom_gib") or (mem_cap - mem_req), 1),
                "overcommit_ratio": round(mem_req / mem_cap, 2) if mem_cap > 0 else None,
            },
        },
        "top_consumers": {
            "cpu_by_namespace": results.get("top_cpu_namespaces"),
            "memory_by_namespace_gib": results.get("top_memory_namespaces"),
        },
        "risk_indicators": {
            "containers_cpu_over_80pct": results.get("containers_cpu_over_80pct", 0),
            "containers_memory_over_80pct": results.get("containers_memory_over_80pct", 0),
            "containers_oom_last_hour": results.get("containers_oom_last_hour", 0),
        },
        "recommendations": _generate_recommendations(results, cpu_cap, cpu_req, mem_cap, mem_req),
    }

    return json.dumps(summary, indent=2)


def _generate_recommendations(
    results: dict,
    cpu_cap: float,
    cpu_req: float,
    mem_cap: float,
    mem_req: float,
) -> list[str]:
    """Generate actionable capacity recommendations based on current state."""
    recs = []

    if cpu_cap > 0:
        cpu_pct = cpu_req / cpu_cap * 100
        if cpu_pct > 90:
            recs.append(
                f"CRITICAL: CPU requests at {cpu_pct:.0f}% of capacity. "
                "Add nodes or reduce CPU requests immediately."
            )
        elif cpu_pct > 75:
            recs.append(
                f"WARNING: CPU requests at {cpu_pct:.0f}% of capacity. "
                "Plan to add nodes within 1 week."
            )

    if mem_cap > 0:
        mem_pct = mem_req / mem_cap * 100
        if mem_pct > 85:
            recs.append(
                f"CRITICAL: Memory requests at {mem_pct:.0f}% of capacity. "
                "Risk of pod eviction if any node is lost."
            )
        elif mem_pct > 70:
            recs.append(
                f"WARNING: Memory requests at {mem_pct:.0f}% of capacity. "
                "Less than one node's worth of headroom."
            )

    oom_count = results.get("containers_oom_last_hour", 0)
    if oom_count and oom_count > 0:
        recs.append(
            f"{int(oom_count)} container(s) OOM-killed in the last hour. "
            "Review memory limits with 'k8sai recommend'."
        )

    mem_pressure = results.get("nodes_with_memory_pressure", 0)
    if mem_pressure and mem_pressure > 0:
        recs.append(
            f"{int(mem_pressure)} node(s) under memory pressure. "
            "Kubelet may evict pods — investigate immediately."
        )

    over_cpu = results.get("containers_cpu_over_80pct", 0)
    over_mem = results.get("containers_memory_over_80pct", 0)
    if over_cpu and over_cpu > 5:
        recs.append(
            f"{int(over_cpu)} containers above 80% CPU utilization. "
            "Run 'k8sai recommend --under-provisioned-only' for patch suggestions."
        )
    if over_mem and over_mem > 5:
        recs.append(
            f"{int(over_mem)} containers above 80% memory utilization. "
            "Memory limits may be too low — check for OOMKill risk."
        )

    if not recs:
        recs.append("Cluster capacity looks healthy. No immediate action required.")

    return recs
