# SRE MCP Server

> A production-grade **Model Context Protocol (MCP) server** that gives AI assistants (Claude, Cursor, etc.) direct, secure access to your SRE toolchain — PagerDuty, Grafana, Kubernetes, AWS CloudWatch, and Runbooks — turning any LLM into an on-call co-pilot.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![MCP SDK](https://img.shields.io/badge/MCP-1.x-green.svg)](https://modelcontextprotocol.io)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](Dockerfile)

---

## Why This Exists

On-call SREs spend the first 5–10 minutes of every incident doing the same mechanical work: correlating alerts, pulling metrics, checking recent deployments, skimming runbooks. This MCP server offloads that to an AI assistant so humans can focus on the actual fix.

This is not a chatbot wrapper. It's a proper MCP server that exposes **Tools**, **Resources**, and **Prompts** following the [Model Context Protocol spec](https://modelcontextprotocol.io/introduction), making it compatible with any MCP-capable client.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    MCP Client                           │
│         (Claude Desktop / Cursor / custom app)          │
└───────────────────────┬─────────────────────────────────┘
                        │ MCP Protocol (stdio / SSE)
┌───────────────────────▼─────────────────────────────────┐
│                  SRE MCP Server                         │
│  ┌────────────┐  ┌────────────┐  ┌────────────────────┐ │
│  │   Tools    │  │ Resources  │  │      Prompts       │ │
│  │ (actions)  │  │  (reads)   │  │  (guided workflows)│ │
│  └─────┬──────┘  └─────┬──────┘  └────────────────────┘ │
│        │               │                                 │
│  ┌─────▼───────────────▼──────────────────────────────┐  │
│  │              Adapter Layer                         │  │
│  │  PagerDuty │ Grafana │ kubectl │ CloudWatch │ JIRA │  │
│  └────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

---

## Features

### MCP Tools (Actions the AI can take)
| Tool | Description |
|------|-------------|
| `get_active_incidents` | Fetch open PagerDuty incidents with severity + assignee |
| `get_service_metrics` | Query Grafana datasource by service name + time range |
| `describe_k8s_pod` | Get pod status, events, and recent logs |
| `list_recent_deployments` | Pull last N deployments from ArgoCD/Helm |
| `run_runbook_step` | Execute a pre-approved runbook action (restart pod, scale deployment) |
| `get_cloudwatch_alarms` | List firing CloudWatch alarms for an AWS account |
| `create_postmortem_draft` | Scaffold a blameless postmortem from incident timeline |
| `search_runbooks` | Semantic search over runbook library |

### MCP Resources (Data the AI can read)
- `sre://runbooks/{service}` — Service-specific runbooks
- `sre://oncall/schedule` — Current on-call rotation
- `sre://slos/{service}` — SLO burn rate and error budget status
- `sre://topology/{service}` — Service dependency graph

### MCP Prompts (Guided workflows)
- `incident-triage` — Structured incident analysis prompt with auto-populated context
- `slo-review` — Weekly SLO review with recommended actions
- `capacity-planning` — Capacity forecast prompt with current utilization

---

## Quick Start

### Prerequisites
- Python 3.11+
- Docker (for containerized deployment)
- API keys for your tools (PagerDuty, Grafana, AWS)

### Installation

```bash
git clone https://github.com/sharatvikas/sre-mcp-server.git
cd sre-mcp-server
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Fill in your credentials
```

### Configure Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "sre": {
      "command": "python",
      "args": ["-m", "sre_mcp_server"],
      "env": {
        "PAGERDUTY_TOKEN": "your-token",
        "GRAFANA_URL": "https://grafana.your-org.com",
        "GRAFANA_TOKEN": "your-token",
        "KUBECONFIG": "/path/to/kubeconfig",
        "AWS_PROFILE": "your-profile"
      }
    }
  }
}
```

### Deploy as SSE Server (for team use)

```bash
docker compose up -d
# Exposes MCP over HTTP+SSE at :8080 for multi-client use
```

---

## Project Structure

```
sre-mcp-server/
├── src/
│   └── sre_mcp_server/
│       ├── __init__.py
│       ├── server.py           # MCP server entrypoint
│       ├── tools/
│       │   ├── pagerduty.py    # PagerDuty tool handlers
│       │   ├── grafana.py      # Grafana/Prometheus tools
│       │   ├── kubernetes.py   # kubectl-based tools
│       │   ├── aws.py          # CloudWatch + AWS tools
│       │   └── runbooks.py     # Runbook execution engine
│       ├── resources/
│       │   ├── runbooks.py     # Runbook resource providers
│       │   └── topology.py     # Service dependency resources
│       ├── prompts/
│       │   └── workflows.py    # Guided SRE workflow prompts
│       └── adapters/
│           ├── pagerduty_client.py
│           ├── grafana_client.py
│           └── k8s_client.py
├── tests/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── .env.example
```

---

## Security Model

- All tool calls that mutate state (restart pod, scale deployment) require an `allowed_actions` allowlist in config
- Secrets are never passed through the LLM — only injected server-side
- Supports OAuth2 / API token auth per adapter
- Audit log of every tool invocation written to structured JSON

---

## Roadmap

- [x] PagerDuty incident tools
- [x] Grafana metrics tools
- [x] Kubernetes pod/deployment tools
- [ ] AWS CloudWatch alarms
- [ ] Runbook semantic search (embeddings)
- [ ] Slack notification tool
- [ ] JIRA incident ticket creation
- [ ] OpsGenie adapter
- [ ] Multi-tenant SSE server with RBAC
- [ ] MCP tool versioning / deprecation

---

## Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md). Run tests with `pytest tests/ -v`.

---

## License

MIT — see [LICENSE](LICENSE).

---

*Built by a Staff SRE tired of doing the same thing manually at 2am.*
