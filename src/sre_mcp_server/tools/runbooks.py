"""Runbook MCP tools — search and execute pre-approved runbook actions."""

from __future__ import annotations

import os
from pathlib import Path

from mcp.types import Tool

from sre_mcp_server.tools.base import BaseToolHandler


class RunbookTools(BaseToolHandler):
    TOOL_NAMES = {"search_runbooks", "get_runbook", "list_runbook_categories"}

    def __init__(self) -> None:
        self._runbook_dir = Path(
            os.environ.get("RUNBOOK_DIR", "/etc/sre-mcp/runbooks")
        )

    async def get_tools(self) -> list[Tool]:
        return [
            Tool(
                name="search_runbooks",
                description=(
                    "Search the runbook library by keyword. Returns matching runbook "
                    "titles, categories, and summaries."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search terms (e.g. 'database failover', 'pod crash', 'memory')",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results to return",
                            "default": 5,
                            "maximum": 20,
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="get_runbook",
                description="Retrieve the full content of a specific runbook by name or path.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Runbook name or relative path (e.g. 'k8s/oomkill' or 'database-failover')",
                        }
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="list_runbook_categories",
                description="List all available runbook categories and the number of runbooks in each.",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    async def call(self, name: str, args: dict) -> str:
        match name:
            case "search_runbooks":
                return self._search_runbooks(**args)
            case "get_runbook":
                return self._get_runbook(**args)
            case "list_runbook_categories":
                return self._list_categories()
            case _:
                raise ValueError(f"Unknown runbook tool: {name}")

    def _search_runbooks(self, query: str, limit: int = 5) -> str:
        if not self._runbook_dir.exists():
            return _DEMO_RUNBOOKS_RESPONSE

        terms = query.lower().split()
        matches = []

        for path in self._runbook_dir.rglob("*.md"):
            content = path.read_text(errors="ignore")
            score = sum(1 for term in terms if term in content.lower())
            if score > 0:
                title = _extract_title(content) or path.stem
                summary = _extract_summary(content)
                rel = path.relative_to(self._runbook_dir)
                matches.append((score, str(rel), title, summary))

        matches.sort(key=lambda x: x[0], reverse=True)
        if not matches:
            return f"No runbooks found matching '{query}'"

        lines = [f"RUNBOOKS matching '{query}' ({min(len(matches), limit)} results)\n"]
        for _, rel, title, summary in matches[:limit]:
            lines.append(f"  [{rel}] {title}")
            if summary:
                lines.append(f"    {summary}")
        return "\n".join(lines)

    def _get_runbook(self, name: str) -> str:
        if not self._runbook_dir.exists():
            return _DEMO_RUNBOOK_CONTENT

        # Try exact path first
        candidates = [
            self._runbook_dir / f"{name}.md",
            self._runbook_dir / name,
        ]
        # Also search by filename
        for path in self._runbook_dir.rglob("*.md"):
            if path.stem.lower() == name.lower().replace(" ", "-"):
                candidates.insert(0, path)

        for path in candidates:
            if path.exists():
                return path.read_text()

        return f"Runbook '{name}' not found. Use search_runbooks to find available runbooks."

    def _list_categories(self) -> str:
        if not self._runbook_dir.exists():
            return _DEMO_CATEGORIES

        categories: dict[str, int] = {}
        for path in self._runbook_dir.rglob("*.md"):
            rel = path.relative_to(self._runbook_dir)
            category = str(rel.parent) if rel.parent != Path(".") else "general"
            categories[category] = categories.get(category, 0) + 1

        if not categories:
            return "No runbooks found."

        lines = ["RUNBOOK CATEGORIES\n"]
        for cat, count in sorted(categories.items()):
            lines.append(f"  {cat}: {count} runbook(s)")
        return "\n".join(lines)


def _extract_title(content: str) -> str:
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _extract_summary(content: str) -> str:
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("# ") and i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            if next_line and not next_line.startswith("#"):
                return next_line[:120]
    return ""


# Demo responses when runbook dir doesn't exist (for testing)
_DEMO_RUNBOOKS_RESPONSE = """RUNBOOKS (demo mode — set RUNBOOK_DIR to load real runbooks)

  [k8s/oomkill.md] OOMKill Remediation
    Steps to diagnose and fix container OOM kills in Kubernetes.
  [k8s/crashloop.md] CrashLoopBackOff Investigation
    How to determine why a pod is crash-looping and fix it.
  [database/failover.md] Database Failover Procedure
    Steps for promoting an RDS replica during a primary failure.
  [aws/alb-5xx.md] ALB 5xx Error Investigation
    Diagnosing elevated error rates on an Application Load Balancer.
"""

_DEMO_RUNBOOK_CONTENT = """# Demo Runbook

This is a demo response. Set the RUNBOOK_DIR environment variable to point at
your organization's runbook directory (Markdown files).

## Format

Runbooks should be Markdown files organized in subdirectories by category:

  runbooks/
  ├── k8s/
  │   ├── oomkill.md
  │   └── crashloop.md
  ├── database/
  │   └── failover.md
  └── aws/
      └── alb-5xx.md
"""

_DEMO_CATEGORIES = """RUNBOOK CATEGORIES (demo mode — set RUNBOOK_DIR)
  k8s: Kubernetes operations runbooks
  database: Database failover and maintenance
  aws: AWS service-specific runbooks
  networking: VPC, DNS, and connectivity issues
  oncall: On-call process and escalation procedures
"""
