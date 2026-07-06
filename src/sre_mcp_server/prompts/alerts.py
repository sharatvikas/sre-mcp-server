"""MCP Prompt: alert explanation workflow.

Turns a raw alert (name + labels + annotations) into a plain-language
explanation with severity assessment and next steps, grounded in the
server's live resources and tools.
"""

from __future__ import annotations

from mcp.types import (
    GetPromptResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    TextContent,
)

EXPLAIN_ALERT_PROMPT = Prompt(
    name="explain_alert",
    description=(
        "Explain an alert in plain language: what it measures, why it likely "
        "fired, how urgent it is, and what to do next. Cross-references the "
        "alert rule catalog, live metrics, and runbooks."
    ),
    arguments=[
        PromptArgument(
            name="alert_name",
            description="Name of the alert that fired (e.g. HighErrorRate)",
            required=True,
        ),
        PromptArgument(
            name="labels",
            description="Alert labels as key=value pairs (e.g. service=payments-api, severity=critical)",
            required=False,
        ),
        PromptArgument(
            name="audience",
            description="Who the explanation is for: 'engineer' (default) or 'stakeholder'",
            required=False,
        ),
    ],
)


def get_explain_alert_prompt(
    alert_name: str,
    labels: str = "",
    audience: str = "engineer",
) -> GetPromptResult:
    """Build the explain-alert prompt for the given alert."""
    labels_section = f"\nAlert labels: {labels}" if labels else ""
    if audience == "stakeholder":
        tone = (
            "The audience is a non-technical stakeholder. Avoid jargon, focus on "
            "customer impact and expected resolution time, and keep it under 150 words."
        )
    else:
        tone = (
            "The audience is an on-call engineer. Be precise and technical, and "
            "include exact queries or commands where useful."
        )

    message = PromptMessage(
        role="user",
        content=TextContent(
            type="text",
            text=f"""Explain the alert '{alert_name}'.{labels_section}

Gather context before explaining:

1. **Rule definition**: Read the `sre://alerts/rules` resource and find the rule
   matching '{alert_name}'. Note its expression, threshold, `for` duration, and
   annotations.

2. **Current state**: Use `list_firing_alerts` to check whether it is firing right
   now, and `query_metrics` to plot the underlying metric over the last hour.

3. **Runbook**: Use `search_runbooks` with '{alert_name}' to find an established
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

{tone}
""",
        ),
    )

    return GetPromptResult(
        description=f"Plain-language explanation of alert '{alert_name}'",
        messages=[message],
    )
