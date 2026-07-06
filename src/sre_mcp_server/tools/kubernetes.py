"""Kubernetes MCP tools — describe pods, deployments, events, and recent changes."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from kubernetes import client as k8s_client, config as k8s_config
from mcp.types import Tool

from sre_mcp_server.tools.base import BaseToolHandler


def _load_k8s():
    kubeconfig = os.environ.get("KUBECONFIG")
    try:
        if kubeconfig:
            k8s_config.load_kube_config(config_file=kubeconfig)
        else:
            k8s_config.load_incluster_config()
    except Exception:
        k8s_config.load_kube_config()


try:
    _load_k8s()
except Exception:
    pass  # Kubernetes tools will fail gracefully at call time if no cluster
_core = k8s_client.CoreV1Api()
_apps = k8s_client.AppsV1Api()


class KubernetesTools(BaseToolHandler):
    TOOL_NAMES = {
        "describe_pod",
        "list_pods",
        "get_pod_logs",
        "list_recent_events",
        "describe_deployment",
        "get_node_status",
    }

    async def get_tools(self) -> list[Tool]:
        return [
            Tool(
                name="describe_pod",
                description=(
                    "Get status, conditions, events, and container states for a Kubernetes pod. "
                    "Useful for diagnosing CrashLoopBackOff, OOMKill, and pending pods."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Pod name"},
                        "namespace": {
                            "type": "string",
                            "description": "Namespace. Default: default",
                            "default": "default",
                        },
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="list_pods",
                description="List pods in a namespace with their status and age.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "namespace": {"type": "string", "default": "default"},
                        "label_selector": {
                            "type": "string",
                            "description": "Label selector (e.g. 'app=payments-api')",
                        },
                        "field_selector": {
                            "type": "string",
                            "description": "Field selector (e.g. 'status.phase=Failed')",
                        },
                    },
                },
            ),
            Tool(
                name="get_pod_logs",
                description="Fetch recent logs from a pod container.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "namespace": {"type": "string", "default": "default"},
                        "container": {
                            "type": "string",
                            "description": "Container name (required for multi-container pods)",
                        },
                        "lines": {
                            "type": "integer",
                            "description": "Number of log lines to return",
                            "default": 50,
                            "maximum": 200,
                        },
                        "previous": {
                            "type": "boolean",
                            "description": "Return logs from previous container instance (useful after crash)",
                            "default": False,
                        },
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="list_recent_events",
                description="List recent Kubernetes warning events in a namespace.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "namespace": {"type": "string", "default": "default"},
                        "limit": {"type": "integer", "default": 20, "maximum": 100},
                        "reason": {
                            "type": "string",
                            "description": "Filter by event reason (e.g. 'OOMKilling', 'BackOff')",
                        },
                    },
                },
            ),
            Tool(
                name="describe_deployment",
                description="Get deployment spec, rollout status, and recent replica set history.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "namespace": {"type": "string", "default": "default"},
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="get_node_status",
                description="Show node resource usage, conditions, and taints.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Node name. Omit to list all nodes.",
                        }
                    },
                },
            ),
        ]

    async def call(self, name: str, args: dict) -> str:
        match name:
            case "describe_pod":
                return _describe_pod(**args)
            case "list_pods":
                return _list_pods(**args)
            case "get_pod_logs":
                return _get_pod_logs(**args)
            case "list_recent_events":
                return _list_recent_events(**args)
            case "describe_deployment":
                return _describe_deployment(**args)
            case "get_node_status":
                return _get_node_status(**args)
            case _:
                raise ValueError(f"Unknown K8s tool: {name}")


def _describe_pod(name: str, namespace: str = "default") -> str:
    pod = _core.read_namespaced_pod(name=name, namespace=namespace)
    lines = [f"POD: {namespace}/{name}", f"{'═' * 50}"]

    lines.append(f"Phase:    {pod.status.phase}")
    lines.append(f"Node:     {pod.spec.node_name or 'unscheduled'}")
    lines.append(f"IP:       {pod.status.pod_ip or 'N/A'}")

    start_time = pod.status.start_time
    if start_time:
        age = datetime.now(timezone.utc) - start_time.replace(tzinfo=timezone.utc)
        lines.append(f"Age:      {_fmt_age(age.total_seconds())}")

    lines.append("\nCONTAINERS:")
    for cs in pod.status.container_statuses or []:
        state = cs.state
        if state.running:
            status = "Running"
        elif state.waiting:
            status = f"Waiting ({state.waiting.reason}: {state.waiting.message or ''})"
        elif state.terminated:
            status = f"Terminated (exit={state.terminated.exit_code}, reason={state.terminated.reason})"
        else:
            status = "Unknown"

        lines.append(f"  {cs.name}: {status} | restarts={cs.restart_count} | ready={cs.ready}")

        if cs.last_state and cs.last_state.terminated:
            lt = cs.last_state.terminated
            lines.append(
                f"    LastState: exit={lt.exit_code}, reason={lt.reason}, "
                f"finished={lt.finished_at}"
            )

    # Conditions
    lines.append("\nCONDITIONS:")
    for cond in pod.status.conditions or []:
        lines.append(f"  {cond.type}: {cond.status} — {cond.message or 'OK'}")

    # Recent events
    events = _core.list_namespaced_event(
        namespace=namespace,
        field_selector=f"involvedObject.name={name}",
    )
    warning_events = [e for e in events.items if e.type == "Warning"][-10:]
    if warning_events:
        lines.append("\nWARNING EVENTS:")
        for e in warning_events:
            lines.append(f"  [{e.reason}] {e.message} (x{e.count})")

    return "\n".join(lines)


def _list_pods(
    namespace: str = "default",
    label_selector: str | None = None,
    field_selector: str | None = None,
) -> str:
    kwargs = {"namespace": namespace}
    if label_selector:
        kwargs["label_selector"] = label_selector
    if field_selector:
        kwargs["field_selector"] = field_selector

    pods = _core.list_namespaced_pod(**kwargs)
    if not pods.items:
        return f"No pods found in {namespace}"

    lines = [f"PODS in {namespace} ({len(pods.items)} total)\n{'─' * 70}"]
    lines.append(f"{'NAME':<45} {'STATUS':<15} {'RESTARTS':<10} {'AGE'}")

    for pod in pods.items:
        cs_list = pod.status.container_statuses or []
        restarts = sum(cs.restart_count for cs in cs_list)
        phase = pod.status.phase or "Unknown"

        age = "?"
        if pod.status.start_time:
            secs = (
                datetime.now(timezone.utc)
                - pod.status.start_time.replace(tzinfo=timezone.utc)
            ).total_seconds()
            age = _fmt_age(secs)

        lines.append(f"{pod.metadata.name:<45} {phase:<15} {restarts:<10} {age}")

    return "\n".join(lines)


def _get_pod_logs(
    name: str,
    namespace: str = "default",
    container: str | None = None,
    lines: int = 50,
    previous: bool = False,
) -> str:
    kwargs = {
        "name": name,
        "namespace": namespace,
        "tail_lines": lines,
        "previous": previous,
    }
    if container:
        kwargs["container"] = container

    logs = _core.read_namespaced_pod_log(**kwargs)
    header = f"LOGS: {namespace}/{name}"
    if container:
        header += f" [{container}]"
    if previous:
        header += " (previous instance)"

    return f"{header}\n{'─' * 60}\n{logs}"


def _list_recent_events(
    namespace: str = "default", limit: int = 20, reason: str | None = None
) -> str:
    events = _core.list_namespaced_event(namespace=namespace)
    items = [e for e in events.items if e.type == "Warning"]

    if reason:
        items = [e for e in items if e.reason == reason]

    items = sorted(items, key=lambda e: e.last_timestamp or e.event_time, reverse=True)[:limit]

    if not items:
        return f"No warning events in {namespace}"

    lines = [f"WARNING EVENTS in {namespace} ({len(items)} shown)\n{'─' * 70}"]
    for e in items:
        ts = (e.last_timestamp or e.event_time or "?")
        obj = f"{e.involved_object.kind}/{e.involved_object.name}"
        lines.append(f"  [{e.reason}] {obj}: {e.message} (x{e.count}) @ {ts}")

    return "\n".join(lines)


def _describe_deployment(name: str, namespace: str = "default") -> str:
    dep = _apps.read_namespaced_deployment(name=name, namespace=namespace)
    spec = dep.spec
    status = dep.status

    lines = [f"DEPLOYMENT: {namespace}/{name}\n{'═' * 50}"]
    lines.append(f"Desired:   {spec.replicas}")
    lines.append(f"Ready:     {status.ready_replicas or 0}")
    lines.append(f"Available: {status.available_replicas or 0}")
    lines.append(f"Updated:   {status.updated_replicas or 0}")

    for container in spec.template.spec.containers:
        lines.append(f"\nContainer: {container.name}")
        lines.append(f"  Image:   {container.image}")
        if container.resources:
            req = container.resources.requests or {}
            lim = container.resources.limits or {}
            lines.append(f"  Requests: cpu={req.get('cpu', 'N/A')}, memory={req.get('memory', 'N/A')}")
            lines.append(f"  Limits:   cpu={lim.get('cpu', 'N/A')}, memory={lim.get('memory', 'N/A')}")

    lines.append("\nCONDITIONS:")
    for cond in status.conditions or []:
        lines.append(f"  {cond.type}: {cond.status} — {cond.message or 'OK'}")

    return "\n".join(lines)


def _get_node_status(name: str | None = None) -> str:
    if name:
        nodes = [_core.read_node(name=name)]
    else:
        nodes = _core.list_node().items

    lines = [f"NODES ({len(nodes)})\n{'─' * 80}"]
    lines.append(f"{'NAME':<40} {'STATUS':<12} {'ROLES':<20} {'VERSION'}")

    for node in nodes:
        labels = node.metadata.labels or {}
        roles = [k.split("/")[-1] for k in labels if k.startswith("node-role.kubernetes.io/")]
        role_str = ",".join(roles) or "worker"

        ready = next(
            (c.status for c in (node.status.conditions or []) if c.type == "Ready"),
            "Unknown",
        )
        status = "Ready" if ready == "True" else "NotReady"
        version = node.status.node_info.kubelet_version if node.status.node_info else "?"

        lines.append(f"{node.metadata.name:<40} {status:<12} {role_str:<20} {version}")

    return "\n".join(lines)


def _fmt_age(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    elif seconds < 86400:
        return f"{int(seconds // 3600)}h"
    else:
        return f"{int(seconds // 86400)}d"
