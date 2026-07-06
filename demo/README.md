# Local demo — SRE MCP server

A one-command, runnable demo that boots the SRE MCP server **in-process** and
proves its full surface works against a real local stack. It performs genuine
**live reads** against Grafana and Prometheus, and demonstrates the
credential-gated PagerDuty / CloudWatch handlers through the repo's own mock
patterns.

See [`OUTPUT.md`](./OUTPUT.md) for a captured real run and an explanation of
exactly what is live vs mocked.

## What it does

1. **Registered surface** — enumerates every tool, resource, and prompt from
   the live in-process registry (37 tools · 9 resources · 7 prompts).
2. **LIVE Grafana read** — `sre://alerts/rules` resource against Grafana at
   `http://localhost:3000` (`admin/admin`).
3. **LIVE metrics read** — the `query_metrics` tool (Grafana → Prometheus
   datasource proxy) plus a direct raw JSON read of Prometheus at `:9090`.
4. **Prompt render** — renders `explain_alert` through the prompt registry.
5. **MOCKED integrations** — runs the real PagerDuty and CloudWatch handlers
   with their network / SDK layer stubbed (no credentials required).

Nothing mutates Grafana, Prometheus, or the Kubernetes cluster — every external
call is a read or a mock.

## Prerequisites

- Grafana reachable at `http://localhost:3000` (`admin/admin`).
- Prometheus reachable at `http://localhost:9090`.
  (Both are provided by the kube-prometheus stack on the `kind-sre-platform`
  cluster; port-forward them if they are only exposed inside the cluster.)
- Python 3.11+.

## Run it

```bash
cd ~/Documents/GitHub/sre-mcp-server

# one-time setup
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# run the demo
python demo/run_demo.py
```

## Configuration

The demo sets sensible defaults but respects these environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `GRAFANA_URL` | `http://admin:admin@localhost:3000` | Grafana base URL (basic auth in URL) |
| `GRAFANA_TOKEN` | *(empty)* | Bearer token; empty → basic-auth-via-URL path |
| `GRAFANA_DATASOURCE_UID` | `prometheus` | Datasource UID the `query_metrics` tool proxies through |
| `PROMETHEUS_URL` | `http://localhost:9090` | Direct raw Prometheus read (section 3b) |
| `SLO_SERVICES` | `payments-api,checkout-api,auth-api` | Services exposed as `sre://slos/<name>` resources |

PagerDuty and CloudWatch need no configuration in the demo — they are mocked.
