"""MCP Prompts for incident response and root cause analysis.

These are structured prompt templates that tell Claude how to use the
SRE tools together for common incident response workflows.

Available prompts:
  incident_rca       — Full root cause analysis workflow for an active incident
  postmortem_draft   — Generate a blameless postmortem from incident data
  oncall_handoff     — Format an on-call shift handoff summary
"""

from __future__ import annotations

from mcp.types import (
    GetPromptResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    TextContent,
)


PROMPT_DEFINITIONS: list[Prompt] = [
    Prompt(
        name="incident_rca",
        description=(
            "Structured root cause analysis workflow for an active incident. "
            "Guides Claude to correlate alerts, check recent deployments, inspect "
            "Kubernetes events, and surface relevant runbooks — then synthesize a "
            "root cause hypothesis with recommended actions."
        ),
        arguments=[
            PromptArgument(
                name="incident_id",
                description="PagerDuty incident ID or incident title",
                required=True,
            ),
            PromptArgument(
                name="service",
                description="Affected service name (maps to Kubernetes namespace or GitHub repo)",
                required=True,
            ),
            PromptArgument(
                name="started_at",
                description="Incident start time (ISO 8601, e.g. 2024-04-11T14:30:00Z)",
                required=True,
            ),
            PromptArgument(
                name="symptoms",
                description="Brief description of observed symptoms (error messages, metrics, user reports)",
                required=False,
            ),
        ],
    ),
    Prompt(
        name="postmortem_draft",
        description=(
            "Generate a blameless postmortem document from incident data. "
            "Pulls the incident timeline from PagerDuty, correlates with deployments, "
            "and drafts contributing factors, impact analysis, and action items."
        ),
        arguments=[
            PromptArgument(
                name="incident_id",
                description="PagerDuty incident ID",
                required=True,
            ),
            PromptArgument(
                name="resolved_at",
                description="When the incident was resolved (ISO 8601)",
                required=False,
            ),
            PromptArgument(
                name="impact",
                description="Customer/revenue impact description",
                required=False,
            ),
        ],
    ),
    Prompt(
        name="oncall_handoff",
        description=(
            "Generate a concise on-call shift handoff summary. "
            "Checks active incidents, current alert status, recent deployments, "
            "and pending action items for the incoming engineer."
        ),
        arguments=[
            PromptArgument(
                name="outgoing_engineer",
                description="Name of the engineer ending their shift",
                required=False,
            ),
            PromptArgument(
                name="incoming_engineer",
                description="Name of the engineer starting their shift",
                required=False,
            ),
        ],
    ),
]


def get_incident_rca_prompt(
    incident_id: str,
    service: str,
    started_at: str,
    symptoms: str = "",
) -> GetPromptResult:
    symptom_section = (
        f"\nReported symptoms:\n{symptoms}\n" if symptoms else ""
    )

    system_message = PromptMessage(
        role="user",
        content=TextContent(
            type="text",
            text=f"""You are an expert SRE performing root cause analysis for an active incident.

Incident: {incident_id}
Service: {service}
Started: {started_at}{symptom_section}

Work through the following RCA workflow using the available tools. Be methodical and
surface concrete evidence for each hypothesis before moving to the next step.

## Step 1 — Alert Context
Use `correlate_alerts` to group active alerts and identify patterns. Look for:
- Alerts firing in the {service} namespace or related services
- Causal dependencies (e.g., database down → service down)
- Storm patterns (many alerts from the same root cause)

## Step 2 — Deployment Correlation
Use `correlate_deployment_with_incident` with:
  incident_start: {started_at}
  service: {service}
  lookback_hours: 2

This surfaces deployments in the 2h window before the incident started.

## Step 3 — Kubernetes Event Inspection
Use `get_kubernetes_events` to check for:
- Pod restarts, OOMKills, evictions in the {service} namespace
- Node pressure events that may affect pod scheduling
- Recent image pulls or configuration changes

## Step 4 — Runbook Search
Use `search_runbooks` with the primary alert name and symptoms to find:
- Established troubleshooting procedures
- Known failure modes and their fixes
- Escalation paths if the runbook doesn't resolve it

## Step 5 — Root Cause Hypothesis
After gathering evidence, use `find_root_cause` with the correlated alert data
to get an AI-synthesized root cause hypothesis and ordered action plan.

## Output Format
Conclude with:
1. **Most likely root cause** (one sentence)
2. **Supporting evidence** (bullet list)
3. **Immediate actions** (ordered list)
4. **Relevant runbook** (if found)
5. **Escalation path** (if not resolved)
""",
        ),
    )

    return GetPromptResult(
        description=f"RCA workflow for incident {incident_id} — service: {service}",
        messages=[system_message],
    )


def get_postmortem_draft_prompt(
    incident_id: str,
    resolved_at: str = "",
    impact: str = "",
) -> GetPromptResult:
    impact_section = f"\nCustomer impact: {impact}" if impact else ""
    resolved_section = f"\nResolved at: {resolved_at}" if resolved_at else ""

    message = PromptMessage(
        role="user",
        content=TextContent(
            type="text",
            text=f"""Generate a blameless postmortem document for incident {incident_id}.{resolved_section}{impact_section}

Use the available tools to gather the following data before drafting:

1. **Incident timeline**: Use `get_incident` or PagerDuty tools to get the full alert history,
   who was paged, and key timestamps (detected, acknowledged, mitigated, resolved).

2. **Deployment context**: Use `list_recent_deployments` over the 24h window around the incident
   to identify what changed before the incident.

3. **Alert correlation**: Use `correlate_alerts` to understand which services were affected
   and in what order (helps establish the blast radius).

Then draft the postmortem in this structure:

---
# Postmortem: {incident_id}

**Date**: <incident date>
**Duration**: <time to resolution>
**Severity**: <P1/P2/P3>
**Status**: Resolved

## Summary
<2-3 sentences: what happened, why, how it was fixed>

## Impact
<Quantified customer/revenue impact, error rates, affected regions>

## Timeline
| Time (UTC) | Event |
|------------|-------|
| HH:MM | Alert fired: ... |
| HH:MM | On-call paged |
| HH:MM | Incident acknowledged |
| HH:MM | Root cause identified: ... |
| HH:MM | Mitigation applied: ... |
| HH:MM | Incident resolved |

## Root Cause
<Technical explanation of what failed and why>

## Contributing Factors
- <Factor 1>
- <Factor 2>

## What Went Well
- <Detection was fast because ...>
- <Rollback was straightforward because ...>

## What Went Poorly
- <Alert had wrong threshold ...>
- <No runbook for this failure mode ...>

## Action Items
| Action | Owner | Due Date | Priority |
|--------|-------|----------|----------|
| Add runbook for X | SRE team | +1 week | HIGH |
| Fix alert threshold | Platform | +2 weeks | MEDIUM |

## Lessons Learned
<Key takeaways that apply beyond this incident>
---

Use a blameless tone throughout. Focus on systemic improvements, not individual errors.
""",
        ),
    )

    return GetPromptResult(
        description=f"Blameless postmortem draft for incident {incident_id}",
        messages=[message],
    )


def get_oncall_handoff_prompt(
    outgoing_engineer: str = "",
    incoming_engineer: str = "",
) -> GetPromptResult:
    outgoing = f" from {outgoing_engineer}" if outgoing_engineer else ""
    incoming = f" to {incoming_engineer}" if incoming_engineer else ""

    message = PromptMessage(
        role="user",
        content=TextContent(
            type="text",
            text=f"""Generate an on-call shift handoff summary{outgoing}{incoming}.

Use the available tools to compile this handoff:

1. **Active incidents**: Use `get_current_on_call` and PagerDuty tools to list any
   open incidents or escalations that need to be handed off.

2. **Alert health**: Use `get_alert_storm_summary` to identify any noisy or
   persistently firing alerts the incoming engineer should be aware of.

3. **Recent deployments**: Use `list_recent_deployments` with hours_back=24 to
   surface any deployments that went out during the shift that may need monitoring.

4. **Error budgets**: Use the `sre://error-budget/all` resource to check if any
   services are in burn-rate warning or critical state going into the handoff.

5. **Pending runbooks**: Note any ongoing investigations or open action items.

Format the handoff as:

---
## On-Call Handoff{outgoing}{incoming}
**Time**: <current UTC time>

### 🔴 Active Incidents
<List any open incidents with status and next steps>
None. ✓

### ⚠️ Watch List (Elevated Risk)
<Services with high error budget burn or recent deployments needing monitoring>

### 📦 Recent Deployments (Last 24h)
<Key deployments that went out this shift>

### 🔔 Alert Noise
<Any persistently firing alerts that are known/expected>

### 📋 Action Items for Incoming Engineer
<Specific tasks or follow-ups to hand off>

### 📊 Error Budget Status
<Services in warning/critical state>

### 💬 Notes
<Anything else the incoming engineer should know>
---

Keep the summary concise and actionable. The goal is a 5-minute read.
""",
        ),
    )

    return GetPromptResult(
        description="On-call shift handoff summary",
        messages=[message],
    )


def get_prompt(name: str, arguments: dict) -> GetPromptResult:
    """Dispatch to the appropriate prompt builder."""
    match name:
        case "incident_rca":
            return get_incident_rca_prompt(
                incident_id=arguments.get("incident_id", ""),
                service=arguments.get("service", ""),
                started_at=arguments.get("started_at", ""),
                symptoms=arguments.get("symptoms", ""),
            )
        case "postmortem_draft":
            return get_postmortem_draft_prompt(
                incident_id=arguments.get("incident_id", ""),
                resolved_at=arguments.get("resolved_at", ""),
                impact=arguments.get("impact", ""),
            )
        case "oncall_handoff":
            return get_oncall_handoff_prompt(
                outgoing_engineer=arguments.get("outgoing_engineer", ""),
                incoming_engineer=arguments.get("incoming_engineer", ""),
            )
        case _:
            raise ValueError(f"Unknown prompt: {name}")
