"""Deployment management MCP tools for kubectl rollout operations.

Provides:
- get_rollout_status      — check if a deployment rollout is progressing or stuck
- get_rollout_history     — list recent deployment revisions with change-cause annotations
- rollback_deployment     — roll back a deployment to a previous revision
- list_recent_deployments — find deployments changed in the last N hours across namespaces
- compare_deployment_envs — diff env vars between two Kubernetes Deployment specs

All tools shell out to `kubectl` via subprocess. The kubectl context is
picked up from KUBECONFIG or the in-cluster service account automatically.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from datetime import datetime, timezone
from typing import Any

from mcp.types import Tool

from sre_mcp_server.tools.base import BaseToolHandler

KUBECTL = os.environ.get("KUBECTL_PATH", "kubectl")


def _run_kubectl(*args: str, timeout: int = 30) -> tuple[str, str, int]:
    """Run a kubectl command and return (stdout, stderr, returncode)."""
    cmd = [KUBECTL, *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.stdout.strip(), proc.stderr.strip(), proc.returncode
    except subprocess.TimeoutExpired:
        return "", f"kubectl timed out after {timeout}s", 1
    except FileNotFoundError:
        return "", f"kubectl not found at {KUBECTL!r}", 1


class DeploymentToolHandler(BaseToolHandler):
    """Tools for tracking and managing Kubernetes deployment rollouts."""

    async def handles(self, name: str) -> bool:
        return name in {
            "get_rollout_status",
            "get_rollout_history",
            "rollback_deployment",
            "list_recent_deployments",
            "compare_deployment_envs",
        }

    async def get_tools(self) -> list[Tool]:
        return [
            Tool(
                name="get_rollout_status",
                description=(
                    "Check the rollout status of a Kubernetes Deployment. "
                    "Returns whether the rollout is complete, in-progress, or stuck, "
                    "along with ready/desired replica counts and any failure conditions."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["deployment"],
                    "properties": {
                        "deployment": {"type": "string", "description": "Deployment name"},
                        "namespace": {"type": "string", "description": "Kubernetes namespace (default: default)"},
                    },
                },
            ),
            Tool(
                name="get_rollout_history",
                description=(
                    "List recent revisions of a Kubernetes Deployment. "
                    "Shows revision number, change-cause annotation, and creation time. "
                    "Useful for identifying which commit or CI job triggered a change."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["deployment"],
                    "properties": {
                        "deployment": {"type": "string", "description": "Deployment name"},
                        "namespace": {"type": "string", "description": "Kubernetes namespace (default: default)"},
                        "limit": {"type": "integer", "description": "Max revisions to return (default: 10)"},
                    },
                },
            ),
            Tool(
                name="rollback_deployment",
                description=(
                    "Roll back a Kubernetes Deployment to a previous revision. "
                    "Use revision=0 to roll back to the immediately preceding revision. "
                    "Returns the new rollout status after initiating the rollback."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["deployment"],
                    "properties": {
                        "deployment": {"type": "string", "description": "Deployment name"},
                        "namespace": {"type": "string", "description": "Kubernetes namespace (default: default)"},
                        "revision": {
                            "type": "integer",
                            "description": "Target revision number. 0 = previous revision (default: 0)",
                        },
                        "dry_run": {
                            "type": "boolean",
                            "description": "If true, show what would happen without executing (default: false)",
                        },
                    },
                },
            ),
            Tool(
                name="list_recent_deployments",
                description=(
                    "Find Kubernetes Deployments that were recently updated across one or all namespaces. "
                    "Returns deployment name, namespace, ready/desired replicas, image tag, and age."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "namespace": {
                            "type": "string",
                            "description": "Kubernetes namespace. Use '--all-namespaces' or empty for all (default: all)",
                        },
                        "hours": {
                            "type": "integer",
                            "description": "Show deployments updated in the last N hours (default: 24)",
                        },
                    },
                },
            ),
            Tool(
                name="compare_deployment_envs",
                description=(
                    "Compare environment variables between two Kubernetes Deployments. "
                    "Shows which env vars exist in one but not the other, and values that differ. "
                    "Useful for debugging config drift between prod and staging."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["deployment_a", "deployment_b"],
                    "properties": {
                        "deployment_a": {"type": "string", "description": "First deployment name"},
                        "deployment_b": {"type": "string", "description": "Second deployment name"},
                        "namespace_a": {"type": "string", "description": "Namespace of first deployment (default: default)"},
                        "namespace_b": {"type": "string", "description": "Namespace of second deployment (default: default)"},
                        "container": {"type": "string", "description": "Container name to compare (default: first container)"},
                    },
                },
            ),
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        return await self.handle(name, arguments)

    async def handle(self, name: str, arguments: dict[str, Any]) -> str:
        handlers = {
            "get_rollout_status": self._get_rollout_status,
            "get_rollout_history": self._get_rollout_history,
            "rollback_deployment": self._rollback_deployment,
            "list_recent_deployments": self._list_recent_deployments,
            "compare_deployment_envs": self._compare_deployment_envs,
        }
        return await handlers[name](arguments)

    # ── Tool implementations ───────────────────────────────────────────────

    async def _get_rollout_status(self, args: dict) -> str:
        deployment = args["deployment"]
        ns = args.get("namespace", "default")

        # Rollout status — exits 0 when complete, 1 when in-progress/stuck
        stdout, stderr, rc = _run_kubectl(
            "rollout", "status", f"deployment/{deployment}", "-n", ns, "--timeout=5s"
        )

        # Get replica counts from deployment JSON
        d_stdout, _, d_rc = _run_kubectl(
            "get", "deployment", deployment, "-n", ns,
            "-o", "jsonpath={.status.readyReplicas}/{.status.replicas}/{.status.updatedReplicas}",
        )

        parts = d_stdout.split("/") if d_stdout else ["?", "?", "?"]
        ready, desired, updated = (parts + ["?", "?"])[:3]

        # Get any failed conditions
        cond_stdout, _, _ = _run_kubectl(
            "get", "deployment", deployment, "-n", ns,
            "-o", "jsonpath={range .status.conditions[*]}{.type}={.status}: {.message}\n{end}",
        )

        status_line = "COMPLETE" if rc == 0 else "IN-PROGRESS/STUCK"
        lines = [
            f"Deployment: {deployment} (namespace: {ns})",
            f"Status: {status_line}",
            f"Replicas: {ready} ready / {desired} desired / {updated} updated",
        ]
        if stdout:
            lines.append(f"kubectl: {stdout}")
        if stderr and "timed out" in stderr:
            lines.append(f"Note: {stderr}")
        if cond_stdout:
            lines.append("\nConditions:")
            lines.extend(f"  {line}" for line in cond_stdout.splitlines() if line.strip())
        return "\n".join(lines)

    async def _get_rollout_history(self, args: dict) -> str:
        deployment = args["deployment"]
        ns = args.get("namespace", "default")
        limit = args.get("limit", 10)

        stdout, stderr, rc = _run_kubectl(
            "rollout", "history", f"deployment/{deployment}", "-n", ns
        )
        if rc != 0:
            return f"Error fetching rollout history: {stderr or stdout}"

        lines = stdout.splitlines()
        # kubectl output: REVISION  CHANGE-CAUSE — parse and limit
        header = lines[0] if lines else "REVISION  CHANGE-CAUSE"
        data_lines = [l for l in lines[1:] if l.strip()]
        limited = data_lines[-limit:] if len(data_lines) > limit else data_lines

        return f"Rollout history for {deployment} (namespace: {ns}, last {len(limited)} revisions):\n{header}\n" + "\n".join(limited)

    async def _rollback_deployment(self, args: dict) -> str:
        deployment = args["deployment"]
        ns = args.get("namespace", "default")
        revision = args.get("revision", 0)
        dry_run = args.get("dry_run", False)

        cmd = ["rollout", "undo", f"deployment/{deployment}", "-n", ns]
        if revision:
            cmd.extend(["--to-revision", str(revision)])
        if dry_run:
            cmd.extend(["--dry-run=client"])

        stdout, stderr, rc = _run_kubectl(*cmd)
        if rc != 0:
            return f"Rollback failed: {stderr or stdout}"

        result = [f"{'DRY RUN: ' if dry_run else ''}Rollback initiated for {deployment} (namespace: {ns})"]
        if stdout:
            result.append(stdout)

        if not dry_run:
            # Poll status briefly
            status_out, _, _ = _run_kubectl(
                "rollout", "status", f"deployment/{deployment}", "-n", ns, "--timeout=10s"
            )
            if status_out:
                result.append(f"Status: {status_out}")

        return "\n".join(result)

    async def _list_recent_deployments(self, args: dict) -> str:
        ns = args.get("namespace", "")
        hours = args.get("hours", 24)

        ns_args = ["--all-namespaces"] if not ns else ["-n", ns]
        stdout, stderr, rc = _run_kubectl(
            "get", "deployments", *ns_args,
            "-o", "json",
        )
        if rc != 0:
            return f"Error listing deployments: {stderr}"

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return f"Failed to parse deployment list: {stdout[:200]}"

        now = datetime.now(timezone.utc)
        cutoff_hours = hours
        results = []

        for item in data.get("items", []):
            meta = item.get("metadata", {})
            spec = item.get("spec", {})
            status = item.get("status", {})

            # Parse lastTransitionTime from Available condition
            last_updated = meta.get("creationTimestamp", "")
            for cond in status.get("conditions", []):
                if cond.get("type") == "Available":
                    last_updated = cond.get("lastUpdateTime", last_updated)

            if last_updated:
                try:
                    dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
                    age_hours = (now - dt).total_seconds() / 3600
                    if age_hours > cutoff_hours:
                        continue
                    age_str = f"{age_hours:.1f}h ago"
                except ValueError:
                    age_str = last_updated

                # Extract image tag from first container
                containers = spec.get("template", {}).get("spec", {}).get("containers", [])
                image = containers[0].get("image", "unknown") if containers else "unknown"
                tag = image.split(":")[-1] if ":" in image else "latest"

                results.append({
                    "namespace": meta.get("namespace", ns),
                    "name": meta.get("name", ""),
                    "ready": status.get("readyReplicas", 0),
                    "desired": spec.get("replicas", 1),
                    "image_tag": tag[:40],
                    "updated": age_str,
                })

        if not results:
            return f"No deployments updated in the last {hours}h"

        lines = [f"Deployments updated in the last {hours}h ({len(results)} found):"]
        lines.append(f"{'NAMESPACE':<20} {'NAME':<40} {'READY':<10} {'IMAGE TAG':<45} {'UPDATED'}")
        lines.append("─" * 130)
        for r in sorted(results, key=lambda x: x["updated"]):
            ready_str = f"{r['ready']}/{r['desired']}"
            lines.append(
                f"{r['namespace']:<20} {r['name']:<40} {ready_str:<10} {r['image_tag']:<45} {r['updated']}"
            )
        return "\n".join(lines)

    async def _compare_deployment_envs(self, args: dict) -> str:
        dep_a = args["deployment_a"]
        dep_b = args["deployment_b"]
        ns_a = args.get("namespace_a", "default")
        ns_b = args.get("namespace_b", "default")
        container = args.get("container")

        def get_env(dep: str, ns: str) -> dict[str, str] | str:
            out, err, rc = _run_kubectl("get", "deployment", dep, "-n", ns, "-o", "json")
            if rc != 0:
                return f"Error fetching {dep}: {err}"
            try:
                data = json.loads(out)
            except json.JSONDecodeError:
                return f"Failed to parse {dep} spec"
            containers = data["spec"]["template"]["spec"].get("containers", [])
            target = next((c for c in containers if not container or c["name"] == container), containers[0] if containers else None)
            if not target:
                return {}
            return {e["name"]: e.get("value", e.get("valueFrom", {}).get("secretKeyRef", {}).get("name", "<secret>"))
                    for e in target.get("env", [])}

        env_a = get_env(dep_a, ns_a)
        env_b = get_env(dep_b, ns_b)

        if isinstance(env_a, str):
            return env_a
        if isinstance(env_b, str):
            return env_b

        keys_a = set(env_a)
        keys_b = set(env_b)
        only_a = keys_a - keys_b
        only_b = keys_b - keys_a
        differ = {k for k in keys_a & keys_b if env_a[k] != env_b[k]}

        lines = [f"Env var diff: {dep_a} ({ns_a}) vs {dep_b} ({ns_b})"]
        if not only_a and not only_b and not differ:
            lines.append("✓ No differences found — env vars are identical")
            return "\n".join(lines)

        if only_a:
            lines.append(f"\nOnly in {dep_a}:")
            for k in sorted(only_a):
                lines.append(f"  + {k}={env_a[k]}")
        if only_b:
            lines.append(f"\nOnly in {dep_b}:")
            for k in sorted(only_b):
                lines.append(f"  + {k}={env_b[k]}")
        if differ:
            lines.append(f"\nDifferent values:")
            for k in sorted(differ):
                lines.append(f"  ~ {k}")
                lines.append(f"      {dep_a}: {env_a[k]}")
                lines.append(f"      {dep_b}: {env_b[k]}")

        return "\n".join(lines)
