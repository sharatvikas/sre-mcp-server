# Demo Output — captured, real run

This is the **verbatim** output of `demo/run_demo.py` executed against a live
local stack (Grafana + Prometheus on a `kind-sre-platform` cluster). It was
captured on 2026-07-05. ANSI colour codes from the server's `structlog` logger
were stripped for readability; the `info` log lines are the real server
callbacks firing (`resource_read`, `tool_call`, `prompt_dispatch`).

## Exact command

```bash
cd ~/Documents/GitHub/sre-mcp-server
python3 -m venv .venv && source .venv/bin/activate      # first run only
pip install -e .                                         # first run only
python demo/run_demo.py
```

## What is LIVE vs MOCKED

| Section | Source | Live or mocked |
|---|---|---|
| 1. Registered surface | `server.list_tools/list_resources/list_prompts` | **REAL** (in-process boot) |
| 2. `sre://alerts/rules` | Grafana provisioning API @ `:3000` (admin/admin) | **LIVE** read |
| 3a. `query_metrics` | Grafana datasource proxy → in-cluster Prometheus | **LIVE** read |
| 3b. raw `up` query | Prometheus HTTP API @ `:9090` | **LIVE** read |
| 4. `explain_alert` | `prompts.registry.dispatch_prompt` | **REAL** render |
| 5a. `get_active_incidents` | PagerDutyTools + `httpx.MockTransport` | **MOCKED** (no creds) |
| 5b. `get_cloudwatch_alarms` | AWSTools + patched `boto3` | **MOCKED** (no creds) |

The two mocked handlers run the **real** handler + formatter code from the repo;
only the network/SDK layer is stubbed, using the exact patterns already present
in `tests/test_tools.py` (pytest-httpx for PagerDuty, `patch(...boto3)` for AWS).

---

## Captured output

```text
SRE MCP SERVER — LOCAL DEMO
LIVE   : Grafana (:3000, admin/admin) + Prometheus (:9090) on kind-sre-platform
MOCKED : PagerDuty + CloudWatch (no credentials in this environment)

══════════════════════════════════════════════════════════════════════════════
1. REGISTERED MCP SURFACE  (booted in-process from sre_mcp_server.server)
══════════════════════════════════════════════════════════════════════════════

──────────────────────────────────────────────────────────────────────────────
TOOLS — 37 registered
──────────────────────────────────────────────────────────────────────────────
  • acknowledge_incident
  • analyze_capacity
  • compare_deployment_envs
  • correlate_alerts
  • create_incident
  • describe_asg
  • describe_deployment
  • describe_pod
  • find_root_cause
  • generate_shift_handoff
  • get_active_incidents
  • get_alert_storm_summary
  • get_cloudwatch_alarms
  • get_current_oncall
  • get_dashboard_url
  • get_ec2_instance_status
  • get_escalation_policy
  • get_incident_details
  • get_incident_timeline
  • get_node_status
  • get_oncall_schedule
  • get_pod_logs
  • get_resource_headroom
  • get_rollout_history
  • get_rollout_status
  • get_runbook
  • list_alertmanager_alerts
  • list_firing_alerts
  • list_pods
  • list_recent_deployments
  • list_recent_events
  • list_runbook_categories
  • project_growth
  • query_metrics
  • rollback_deployment
  • search_runbooks
  • silence_alert

──────────────────────────────────────────────────────────────────────────────
RESOURCES — 9 registered
──────────────────────────────────────────────────────────────────────────────
  • sre://error-budget/all       Error Budget Status — All Services
  • sre://capacity/overview      Cluster Capacity Overview
  • sre://incidents/active       Active Incidents — PagerDuty
  • sre://oncall/schedule        On-Call Schedule — Current Rotation
  • sre://alerts/rules           Alert Rules — Grafana
  • sre://cloudwatch/alarms      CloudWatch Alarms — Recent Activity
  • sre://slos/payments-api      SLO Status: payments-api
  • sre://slos/checkout-api      SLO Status: checkout-api
  • sre://slos/auth-api          SLO Status: auth-api

──────────────────────────────────────────────────────────────────────────────
PROMPTS — 7 registered
──────────────────────────────────────────────────────────────────────────────
  • incident_rca         (incident_id*, service*, started_at*, symptoms)
  • postmortem_draft     (incident_id*, resolved_at, impact)
  • oncall_handoff       (outgoing_engineer, incoming_engineer)
  • incident-triage      (service*, alert_name*)
  • slo-review           (service*)
  • capacity-check       (namespace*)
  • explain_alert        (alert_name*, labels, audience)

  SUMMARY: 37 tools · 9 resources · 7 prompts

══════════════════════════════════════════════════════════════════════════════
2. LIVE READ — Grafana alert-rule catalog  [REAL, admin/admin @ :3000]
══════════════════════════════════════════════════════════════════════════════
Resource URI : sre://alerts/rules
Grafana      : http://localhost:3000  (auth: HTTP basic admin/admin)
Handler      : sre_mcp_server.resources.alert_rules.read_alert_rules()

2026-07-05 20:59:02 [info     ] resource_read                  uri=sre://alerts/rules
2026-07-05 20:59:02 [info     ] alert_rules_resource_read      count=0
{
  "timestamp": 1783306742,
  "summary": {
    "total_rules": 0,
    "paused": 0,
    "rule_groups": {}
  },
  "rules": []
}

  → Live Grafana provisioning API answered: 0 Grafana-managed alert rule(s).
    (kube-prometheus ships its alerts as Prometheus rules, so this
     Grafana-managed catalog is legitimately empty — the 200 OK proves
     the authenticated live read worked.)

══════════════════════════════════════════════════════════════════════════════
3a. LIVE READ — metrics via Grafana `query_metrics` tool  [REAL]
══════════════════════════════════════════════════════════════════════════════
Tool    : query_metrics   (Grafana proxies PromQL → in-cluster Prometheus)
PromQL  : sum by (job) (up)

2026-07-05 20:59:02 [info     ] tool_call                      args=['query', 'time_range'] tool=query_metrics
Query: sum by (job) (up)
Range: last 5m
Results: 13 series

  metric{job="apiserver"}: 1 (latest)
  metric{job="coredns"}: 2 (latest)
  metric{job="kube-controller-manager"}: 0 (latest)
  metric{job="kube-etcd"}: 0 (latest)
  metric{job="kube-prometheus-grafana"}: 1 (latest)
  metric{job="kube-prometheus-kube-prome-alertmanager"}: 2 (latest)
  metric{job="kube-prometheus-kube-prome-operator"}: 1 (latest)
  metric{job="kube-prometheus-kube-prome-prometheus"}: 2 (latest)
  metric{job="kube-proxy"}: 0 (latest)
  metric{job="kube-scheduler"}: 0 (latest)
  ... and 3 more series

══════════════════════════════════════════════════════════════════════════════
3b. LIVE READ — raw Prometheus JSON  [REAL, direct @ :9090]
══════════════════════════════════════════════════════════════════════════════
Prometheus : http://localhost:9090/api/v1/query?query=up   (no auth)
Purpose    : show the real JSON the Grafana datasource proxies to.

HTTP 200 · status=success · 18 series returned
First 3 series (trimmed):

{
  "status": "success",
  "data": {
    "resultType": "vector",
    "result": [
      {
        "metric": {
          "__name__": "up",
          "container": "config-reloader",
          "endpoint": "reloader-web",
          "instance": "10.244.0.9:8080",
          "job": "kube-prometheus-kube-prome-alertmanager",
          "namespace": "monitoring",
          "pod": "alertmanager-kube-prometheus-kube-prome-alertmanager-0",
          "service": "kube-prometheus-kube-prome-alertmanager"
        },
        "value": [
          1783306742.266,
          "1"
        ]
      },
      {
        "metric": {
          "__name__": "up",
          "container": "alertmanager",
          "endpoint": "http-web",
          "instance": "10.244.0.9:9093",
          "job": "kube-prometheus-kube-prome-alertmanager",
          "namespace": "monitoring",
          "pod": "alertmanager-kube-prometheus-kube-prome-alertmanager-0",
          "service": "kube-prometheus-kube-prome-alertmanager"
        },
        "value": [
          1783306742.266,
          "1"
        ]
      },
      {
        "metric": {
          "__name__": "up",
          "container": "prometheus",
          "endpoint": "http-web",
          "instance": "10.244.0.10:9090",
          "job": "kube-prometheus-kube-prome-prometheus",
          "namespace": "monitoring",
          "pod": "prometheus-kube-prometheus-kube-prome-prometheus-0",
          "service": "kube-prometheus-kube-prome-prometheus"
        },
        "value": [
          1783306742.266,
          "1"
        ]
      }
    ]
  }
}

══════════════════════════════════════════════════════════════════════════════
4. RENDER PROMPT — `explain_alert`  [REAL prompt registry]
══════════════════════════════════════════════════════════════════════════════
dispatch_prompt('explain_alert', {"alert_name": "KubePodCrashLooping", "labels": "namespace=monitoring, severity=warning", "audience": "engineer"})

2026-07-05 20:59:02 [info     ] prompt_get                     args=['alert_name', 'labels', 'audience'] prompt=explain_alert
2026-07-05 20:59:02 [info     ] prompt_dispatch                prompt=explain_alert
description: Plain-language explanation of alert 'KubePodCrashLooping'

[user]
Explain the alert 'KubePodCrashLooping'.
Alert labels: namespace=monitoring, severity=warning

Gather context before explaining:

1. **Rule definition**: Read the `sre://alerts/rules` resource and find the rule
   matching 'KubePodCrashLooping'. Note its expression, threshold, `for` duration, and
   annotations.

2. **Current state**: Use `list_firing_alerts` to check whether it is firing right
   now, and `query_metrics` to plot the underlying metric over the last hour.

3. **Runbook**: Use `search_runbooks` with 'KubePodCrashLooping' to find an established
   response procedure.

Then produce:

## What this alert means
<One paragraph: what the metric measures and what condition trips the alert>

## Why it likely fired
<Most probable causes ranked, based on the current metric shape>

## How urgent is it
<Severity assessment: page-worthy now, business-hours, or informational — and why>

## What to do next
<Ordered, concrete next steps; reference the runbook if one exists>

The audience is an on-call engineer. Be precise and technical, and include exact queries or commands where useful.


══════════════════════════════════════════════════════════════════════════════
5a. MOCKED — PagerDuty `get_active_incidents`  [NO CREDS → MOCK]
══════════════════════════════════════════════════════════════════════════════
No PAGERDUTY_TOKEN in this environment. We exercise the REAL
PagerDutyTools handler + formatter, but swap its httpx client for an
httpx.MockTransport (the same HTTP-layer mocking the test suite uses).

ACTIVE INCIDENTS (1 total)
══════════════════════════════════════════════════

[HIGH] High error rate on payments-api
  ID:       PABC123
  Service:  payments-api
  Status:   triggered
  Assignee: Alice Chen
  Age:      16h 44m
  URL:      https://acme.pagerduty.com/incidents/PABC123

══════════════════════════════════════════════════════════════════════════════
5b. MOCKED — CloudWatch `get_cloudwatch_alarms`  [NO CREDS → MOCK]
══════════════════════════════════════════════════════════════════════════════
No AWS credentials here. We exercise the REAL AWSTools handler +
formatter with boto3 patched to a MagicMock (identical to the pattern
in tests/test_tools.py::TestAWSTools).

CLOUDWATCH ALARMS (ALARM) in us-east-1 — 2 found
════════════════════════════════════════════════════════════

payments-rds-cpu-high
  State:     ALARM
  Reason:    Threshold Crossed: CPUUtilization > 85% for 3 datapoints
  Metric:    AWS/RDS/CPUUtilization
  Updated:   2026-07-05T10:22:00Z

checkout-alb-5xx
  State:     ALARM
  Reason:    Threshold Crossed: HTTPCode_Target_5XX_Count > 50
  Metric:    AWS/ApplicationELB/HTTPCode_Target_5XX_Count
  Updated:   2026-07-05T10:18:00Z

══════════════════════════════════════════════════════════════════════════════
DEMO COMPLETE
══════════════════════════════════════════════════════════════════════════════
LIVE reads   : Grafana alert-rule catalog, Grafana query_metrics, raw Prometheus.
MOCKED calls : PagerDuty incidents, CloudWatch alarms (repo mock patterns).
```

---

## What the live output proves

- The server's **real registry** matches the documented surface exactly:
  **37 tools · 9 resources · 7 prompts** — enumerated straight from the
  in-process `list_tools()` / `list_resources()` / `list_prompts()` callbacks,
  not a hardcoded list.
- **Grafana authentication and read path work end-to-end**: the
  `sre://alerts/rules` resource handler reached Grafana `12.4.3` at `:3000`,
  authenticated with `admin/admin`, and returned a valid `200` JSON payload
  (0 Grafana-managed rules — correct for a kube-prometheus install, where alert
  rules live in Prometheus).
- **Prometheus is really being queried**: `query_metrics` returned 13 live
  `up` series via the Grafana datasource proxy, and the direct `:9090` read
  returned 18 real `up` series with genuine cluster pod/instance labels
  (`monitoring` namespace, `10.244.0.x` pod IPs). These are live values from
  the running `kind-sre-platform` cluster.
- **Prompts render for real** through the registry's argument validation and
  dispatch.
- **PagerDuty and CloudWatch handlers execute their real formatting logic**
  even without credentials, via the repo's own mocking patterns.

## Honest gaps / caveats

- **Grafana auth uses HTTP basic (`admin:admin` embedded in `GRAFANA_URL`),
  not a bearer token.** The handlers send `Authorization: Bearer <token>`; with
  an empty token this local Grafana accepts the basic-auth credentials carried
  in the URL. In production you would set `GRAFANA_TOKEN` to a service-account
  token instead. No repo source was modified to make this work.
- The `sre://alerts/rules` catalog is **empty (0 rules)** because this cluster's
  alerts are Prometheus `PrometheusRule` objects, not Grafana-managed rules.
  The read is genuinely live (200 OK, authenticated); it is simply an empty set.
- `query_metrics` output labels show `metric{...}` because the formatter reads
  `__name__`, which a `sum by (job)` result does not carry — this is the tool's
  existing formatting behaviour, not a demo artifact. The raw `:9090` section
  (3b) shows the fully-labelled underlying series.
- **PagerDuty and CloudWatch are mocked, not live** — there are no credentials
  in this environment. Those sections are clearly labelled `[NO CREDS → MOCK]`.
- Kubernetes tools are registered and the cluster is reachable
  (`kubectl` context `kind-sre-platform`), but the demo does not call a K8s tool
  to honour the read-only / no-mutation constraint and keep the run
  deterministic. They can be exercised the same way as `query_metrics`.
