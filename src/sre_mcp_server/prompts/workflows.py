"""MCP Prompts — guided SRE workflows that inject live context into Claude."""

from __future__ import annotations

from mcp.types import Prompt, PromptArgument, PromptMessage, TextContent


def get_incident_triage_prompt(service: str, alert_name: str) -> list[PromptMessage]:
    """
    Structured incident triage prompt — pre-populated with context instructions.
    Claude will use the MCP tools to fill in the actual data.
    """
    return [
        PromptMessage(
            role="user",
            content=TextContent(
                type="text",
                text=f"""I need you to triage an incident for the service '{service}'.
Alert: {alert_name}

Please follow this structured approach:

1. Use get_active_incidents to check current PagerDuty incidents
2. Use list_firing_alerts to see what's currently alerting in Grafana
3. Use query_metrics to check the service's error rate and latency over the last 1 hour:
   - Error rate: sum(rate(http_requests_total{{status=~"5..",job="{service}"}}[5m])) / sum(rate(http_requests_total{{job="{service}"}}[5m])) * 100
   - P99 latency: histogram_quantile(0.99, rate(http_request_duration_seconds_bucket{{job="{service}"}}[5m]))
4. Use list_recent_events to check for K8s warning events in the service's namespace
5. Use get_oncall_schedule to find who is on-call

Then provide:
- DIAGNOSIS: Most likely root cause ranked by probability
- IMMEDIATE ACTIONS: Specific commands to run right now
- COMMUNICATION: Draft Slack message for #incidents channel
- ESCALATION: Who to page if this doesn't resolve in 15 minutes
""",
            ),
        )
    ]


def get_slo_review_prompt(service: str) -> list[PromptMessage]:
    """Weekly SLO review prompt — pulls live metrics and generates report."""
    return [
        PromptMessage(
            role="user",
            content=TextContent(
                type="text",
                text=f"""Conduct a weekly SLO review for '{service}'.

Use query_metrics to check:
1. 7-day error rate: sum(rate(http_requests_total{{status=~"5..",job="{service}"}}[7d])) / sum(rate(http_requests_total{{job="{service}"}}[7d])) * 100
2. Current burn rate (1h): job:request_error_rate:ratio_rate1h{{job="{service}"}} / 0.001
3. Error budget remaining: job:error_budget_remaining:ratio_rate30d{{job="{service}"}}
4. P99 latency (7d avg): histogram_quantile(0.99, rate(http_request_duration_seconds_bucket{{job="{service}"}}[7d]))

Then produce:
- Current SLO status (pass/fail/at-risk)
- Error budget consumed this month and trajectory
- Whether we're on track to end the month within budget
- Top 2-3 recommendations to protect the error budget
- A Slack-ready weekly digest in 3 sentences or less
""",
            ),
        )
    ]


def get_capacity_check_prompt(namespace: str) -> list[PromptMessage]:
    """Capacity check prompt — reviews current utilization across a namespace."""
    return [
        PromptMessage(
            role="user",
            content=TextContent(
                type="text",
                text=f"""Perform a capacity health check for the '{namespace}' namespace.

1. Use list_pods to show all pods and their states
2. Use query_metrics for:
   - CPU utilization: sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}"}}[5m])) by (pod)
   - Memory utilization: sum(container_memory_working_set_bytes{{namespace="{namespace}"}}) by (pod)
   - CPU requests vs limits: sum(kube_pod_container_resource_requests{{namespace="{namespace}",resource="cpu"}}) by (pod)
3. Use get_cloudwatch_alarms to check any firing AWS alarms

Identify:
- Any pods approaching their memory limits (>80%)
- Any pods with CPU throttling (indicate high cpu limit vs. request ratio)
- Pods with excessive restart counts
- Recommendations for rightsizing (over-provisioned or under-provisioned)
""",
            ),
        )
    ]


AVAILABLE_PROMPTS = [
    Prompt(
        name="incident-triage",
        description="Structured incident triage with auto-populated metrics and alert context",
        arguments=[
            PromptArgument(name="service", description="Service name", required=True),
            PromptArgument(name="alert_name", description="Alert that fired", required=True),
        ],
    ),
    Prompt(
        name="slo-review",
        description="Weekly SLO review with live burn rate and error budget data",
        arguments=[
            PromptArgument(name="service", description="Service name", required=True),
        ],
    ),
    Prompt(
        name="capacity-check",
        description="Namespace capacity health check — CPU, memory, and scaling headroom",
        arguments=[
            PromptArgument(name="namespace", description="Kubernetes namespace", required=True),
        ],
    ),
]
