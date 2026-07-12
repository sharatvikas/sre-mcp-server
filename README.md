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

Read-only SRE state exposed as addressable resources. An MCP client can attach
these at conversation start for ambient context — no tool round-trips needed.

| URI | Content |
|-----|---------|
| `sre://incidents/active` | Open PagerDuty incidents (JSON): urgency, service, assignee, age |
| `sre://oncall/schedule` | Current on-call rotation per escalation policy, sorted by level |
| `sre://alerts/rules` | Grafana-managed alert rule catalog: titles, groups, labels, paused state |
| `sre://cloudwatch/alarms` | CloudWatch alarms in ALARM state + most recently updated alarms |
| `sre://error-budget/all` | SLO burn rates and error budget status across all services |
| `sre://capacity/overview` | Cluster capacity, utilization, headroom, and rightsizing recommendations |
| `sre://slos/{service}` | Per-service SLO burn rate report (services from `SLO_SERVICES` env) |

Every resource returns valid JSON even when the upstream system is down — failures
are reported in an `error` field instead of crashing the read, so the model always
gets a parseable answer.

### MCP Prompts (Guided workflows)

Reusable prompt templates that orchestrate the tools and resources above into
repeatable SRE workflows:

| Prompt | Arguments | What it does |
|--------|-----------|--------------|
| `incident_rca` | `incident_id`, `service`, `started_at`, `symptoms?` | Five-step root cause analysis: correlate alerts → deployment diff → K8s events → runbooks → hypothesis |
| `incident-triage` | `service`, `alert_name` | Fast triage: live incidents, firing alerts, error-rate/latency PromQL, K8s events, on-call — then diagnosis + comms draft |
| `postmortem_draft` | `incident_id`, `resolved_at?`, `impact?` | Blameless postmortem scaffolded from the real incident timeline and deployment history |
| `explain_alert` | `alert_name`, `labels?`, `audience?` | Plain-language alert explanation grounded in the `sre://alerts/rules` catalog, live metrics, and runbooks; engineer or stakeholder tone |
| `oncall_handoff` | `outgoing_engineer?`, `incoming_engineer?` | Shift handoff summary: active incidents, alert noise, recent deploys, error budgets |
| `slo-review` | `service` | Weekly SLO review with burn-rate math and budget trajectory |
| `capacity-check` | `namespace` | Namespace capacity health check with rightsizing recommendations |

Required arguments are validated server-side — a missing argument fails fast with a
clear error instead of rendering a half-filled template.

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

## Try it locally

One command boots the MCP server in-process and exercises its full surface — it enumerates every tool, resource, and prompt, performs **live reads** against a local Grafana/Prometheus stack, renders a prompt, and runs the PagerDuty/CloudWatch handlers with their network layer mocked (no credentials needed):

```bash
python demo/run_demo.py
```

This proves the server's tools, resources, and prompts all register and respond end-to-end. See [`demo/OUTPUT.md`](demo/OUTPUT.md) for a captured real run (with a line-by-line breakdown of what is live vs mocked) and [`demo/README.md`](demo/README.md) for prerequisites and configuration.

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
│       │   ├── incidents.py    # sre://incidents/active (PagerDuty)
│       │   ├── oncall.py       # sre://oncall/schedule (PagerDuty)
│       │   ├── alert_rules.py  # sre://alerts/rules (Grafana)
│       │   ├── cloudwatch.py   # sre://cloudwatch/alarms (AWS)
│       │   ├── error_budget.py # sre://error-budget/all (Prometheus)
│       │   ├── capacity.py     # sre://capacity/overview (Prometheus)
│       │   └── slo.py          # sre://slos/{service} (Grafana)
│       └── prompts/
│           ├── registry.py     # Unified prompt catalog + dispatch + validation
│           ├── incident_rca.py # RCA, postmortem, and handoff prompts
│           ├── workflows.py    # Triage, SLO review, capacity check prompts
│           └── alerts.py       # explain_alert prompt
├── tests/
│   ├── conftest.py             # Env pinning so tests never touch real systems
│   ├── test_server.py          # Registration + routing for tools/resources/prompts
│   ├── test_tools.py           # Tool handlers with mocked PagerDuty/Grafana/AWS
│   ├── test_resources.py       # Resource providers incl. upstream-failure paths
│   └── test_prompts.py         # Prompt registry, interpolation, arg validation
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

## Testing

The test suite mocks every external client (PagerDuty, Grafana, AlertManager,
CloudWatch via boto3, filesystem runbooks) — no credentials or network access
required. HTTP traffic is intercepted with `pytest-httpx`; AWS calls are stubbed
at the `boto3.Session` boundary.

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

Coverage focus:

- **Registration invariants** — every tool has a unique name, a description, and a
  valid input schema; every listed tool is claimed by exactly one handler; every
  `required` schema field actually exists in `properties`.
- **Routing** — `call_tool` dispatches to the owning handler, `read_resource`
  routes each `sre://` URI (including pydantic `AnyUrl` inputs from the MCP SDK),
  and unknown names raise cleanly.
- **Handler logic** — response formatting, auth headers, empty-result paths.
- **Failure modes** — HTTP 4xx/5xx, transport errors, and AWS `ClientError` all
  degrade to structured error payloads instead of exceptions.
- **Prompts** — argument interpolation, required-argument validation, and
  audience-specific rendering.

---

## Roadmap

- [x] PagerDuty incident tools
- [x] Grafana metrics tools
- [x] Kubernetes pod/deployment tools
- [x] AWS CloudWatch alarms
- [x] MCP Resources: incidents, on-call, alert rules, CloudWatch, SLO/error budget
- [x] MCP Prompts: RCA, triage, postmortem, alert explanation, handoff, SLO review
- [x] Test suite with mocked external clients
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
