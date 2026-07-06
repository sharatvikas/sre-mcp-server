"""Tests for the prompt registry and individual prompt builders."""

from __future__ import annotations

import pytest
from mcp.types import GetPromptResult

from sre_mcp_server.prompts.registry import ALL_PROMPTS, dispatch_prompt


def _prompt_text(result: GetPromptResult) -> str:
    return "\n".join(m.content.text for m in result.messages)


SAMPLE_ARGS = {
    "incident_rca": {
        "incident_id": "P1A2B3C",
        "service": "payments-api",
        "started_at": "2026-07-05T10:00:00Z",
        "symptoms": "elevated 5xx",
    },
    "postmortem_draft": {"incident_id": "P1A2B3C"},
    "oncall_handoff": {},
    "incident-triage": {"service": "payments-api", "alert_name": "HighErrorRate"},
    "slo-review": {"service": "checkout-api"},
    "capacity-check": {"namespace": "production"},
    "explain_alert": {"alert_name": "KubePodCrashLooping"},
}


class TestRegistry:
    def test_prompt_names_are_unique(self):
        names = [p.name for p in ALL_PROMPTS]
        assert len(names) == len(set(names))

    def test_every_prompt_has_description(self):
        for prompt in ALL_PROMPTS:
            assert prompt.description and len(prompt.description) > 20, prompt.name

    def test_every_registered_prompt_has_sample_args(self):
        """Keep SAMPLE_ARGS in sync so every prompt is exercised below."""
        assert {p.name for p in ALL_PROMPTS} == set(SAMPLE_ARGS)

    @pytest.mark.parametrize("name", sorted(SAMPLE_ARGS))
    def test_dispatch_returns_nonempty_messages(self, name):
        result = dispatch_prompt(name, SAMPLE_ARGS[name])
        assert isinstance(result, GetPromptResult)
        assert result.messages
        assert len(_prompt_text(result)) > 100

    def test_dispatch_unknown_prompt_raises(self):
        with pytest.raises(ValueError, match="Unknown prompt"):
            dispatch_prompt("no_such_prompt", {})

    @pytest.mark.parametrize(
        ("name", "missing"),
        [
            ("incident_rca", "incident_id"),
            ("incident-triage", "alert_name"),
            ("slo-review", "service"),
            ("capacity-check", "namespace"),
            ("explain_alert", "alert_name"),
        ],
    )
    def test_missing_required_argument_raises(self, name, missing):
        args = dict(SAMPLE_ARGS[name])
        args.pop(missing, None)
        with pytest.raises(ValueError, match=missing):
            dispatch_prompt(name, args)

    def test_empty_required_argument_raises(self):
        with pytest.raises(ValueError, match="alert_name"):
            dispatch_prompt("explain_alert", {"alert_name": "   "})


class TestArgumentInterpolation:
    def test_incident_rca_embeds_arguments(self):
        result = dispatch_prompt("incident_rca", SAMPLE_ARGS["incident_rca"])
        text = _prompt_text(result)
        assert "P1A2B3C" in text
        assert "payments-api" in text
        assert "2026-07-05T10:00:00Z" in text
        assert "elevated 5xx" in text

    def test_triage_prompt_embeds_service_and_alert(self):
        result = dispatch_prompt("incident-triage", SAMPLE_ARGS["incident-triage"])
        text = _prompt_text(result)
        assert "payments-api" in text
        assert "HighErrorRate" in text

    def test_explain_alert_references_rules_resource(self):
        result = dispatch_prompt("explain_alert", SAMPLE_ARGS["explain_alert"])
        text = _prompt_text(result)
        assert "KubePodCrashLooping" in text
        assert "sre://alerts/rules" in text

    def test_explain_alert_stakeholder_audience_changes_tone(self):
        engineer = _prompt_text(
            dispatch_prompt("explain_alert", {"alert_name": "X", "audience": "engineer"})
        )
        stakeholder = _prompt_text(
            dispatch_prompt(
                "explain_alert", {"alert_name": "X", "audience": "stakeholder"}
            )
        )
        assert engineer != stakeholder
        assert "non-technical" in stakeholder

    def test_optional_labels_are_included_when_given(self):
        text = _prompt_text(
            dispatch_prompt(
                "explain_alert",
                {"alert_name": "X", "labels": "service=payments-api"},
            )
        )
        assert "service=payments-api" in text
