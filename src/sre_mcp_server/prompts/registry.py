"""Unified prompt registry for the SRE MCP server.

Merges every prompt module into a single catalog with one dispatch
function, so ``server.py`` only needs a single import. Also enforces
required-argument validation declared on each ``Prompt`` definition, so a
missing argument fails fast with a clear error instead of producing a
half-filled template.
"""

from __future__ import annotations

import structlog
from mcp.types import GetPromptResult, Prompt, PromptMessage

from sre_mcp_server.prompts import incident_rca, workflows
from sre_mcp_server.prompts.alerts import EXPLAIN_ALERT_PROMPT, get_explain_alert_prompt

log = structlog.get_logger()

ALL_PROMPTS: list[Prompt] = [
    *incident_rca.PROMPT_DEFINITIONS,
    *workflows.AVAILABLE_PROMPTS,
    EXPLAIN_ALERT_PROMPT,
]

_PROMPTS_BY_NAME: dict[str, Prompt] = {p.name: p for p in ALL_PROMPTS}

if len(_PROMPTS_BY_NAME) != len(ALL_PROMPTS):
    raise RuntimeError("Duplicate prompt names registered in prompt registry")


def _validate_arguments(prompt: Prompt, arguments: dict) -> None:
    """Raise ValueError when a required prompt argument is missing/empty."""
    missing = [
        arg.name
        for arg in prompt.arguments or []
        if arg.required and not str(arguments.get(arg.name, "") or "").strip()
    ]
    if missing:
        raise ValueError(
            f"Prompt '{prompt.name}' is missing required argument(s): {', '.join(missing)}"
        )


def _wrap(description: str, messages: list[PromptMessage]) -> GetPromptResult:
    return GetPromptResult(description=description, messages=messages)


def dispatch_prompt(name: str, arguments: dict) -> GetPromptResult:
    """Return the rendered prompt for ``name`` or raise ValueError."""
    prompt = _PROMPTS_BY_NAME.get(name)
    if prompt is None:
        raise ValueError(f"Unknown prompt: {name}")

    _validate_arguments(prompt, arguments)
    log.info("prompt_dispatch", prompt=name)

    match name:
        # Incident response workflows (incident_rca module)
        case "incident_rca" | "postmortem_draft" | "oncall_handoff":
            return incident_rca.get_prompt(name, arguments)

        # Guided SRE workflows (workflows module)
        case "incident-triage":
            return _wrap(
                f"Incident triage for {arguments['service']}",
                workflows.get_incident_triage_prompt(
                    service=arguments["service"],
                    alert_name=arguments["alert_name"],
                ),
            )
        case "slo-review":
            return _wrap(
                f"Weekly SLO review for {arguments['service']}",
                workflows.get_slo_review_prompt(service=arguments["service"]),
            )
        case "capacity-check":
            return _wrap(
                f"Capacity health check for {arguments['namespace']}",
                workflows.get_capacity_check_prompt(namespace=arguments["namespace"]),
            )

        # Alert explanation (alerts module)
        case "explain_alert":
            return get_explain_alert_prompt(
                alert_name=arguments["alert_name"],
                labels=arguments.get("labels", ""),
                audience=arguments.get("audience", "engineer"),
            )

        case _:  # pragma: no cover — registry and dispatch out of sync
            raise ValueError(f"Prompt '{name}' is registered but has no dispatcher")
