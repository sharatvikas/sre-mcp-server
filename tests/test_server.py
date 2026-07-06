"""Tests for MCP server registration and request routing.

Covers the three MCP surfaces exposed by ``server.py``:
tools (list + dispatch), prompts (list + get), and resources (list + read).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from mcp.types import GetPromptResult, TextContent
from pydantic import AnyUrl

from sre_mcp_server import server


# ── Tool registration ────────────────────────────────────────────────────────


class TestToolRegistration:
    async def test_tool_names_are_unique(self):
        tools = await server.list_tools()
        names = [t.name for t in tools]
        assert len(names) == len(set(names)), f"Duplicate tool names: {names}"

    async def test_every_tool_has_description_and_schema(self):
        for tool in await server.list_tools():
            assert tool.description and len(tool.description) > 10, tool.name
            assert tool.inputSchema.get("type") == "object", tool.name

    async def test_handlers_claim_only_their_own_tools(self):
        """Every listed tool must be claimed by exactly one handler."""
        tools = await server.list_tools()
        for tool in tools:
            claimants = [
                type(h).__name__
                for h in server._ALL_TOOLS
                if await h.handles(tool.name)
            ]
            assert len(claimants) == 1, f"{tool.name} claimed by {claimants}"

    async def test_required_schema_fields_exist_in_properties(self):
        for tool in await server.list_tools():
            schema = tool.inputSchema
            props = schema.get("properties", {})
            for required in schema.get("required", []):
                assert required in props, (
                    f"{tool.name}: required field '{required}' missing from properties"
                )


# ── Tool dispatch ─────────────────────────────────────────────────────────────


class TestToolDispatch:
    async def test_call_tool_routes_to_owning_handler(self, monkeypatch):
        mock_call = AsyncMock(return_value="runbook content here")
        monkeypatch.setattr(server._runbooks, "call", mock_call)

        result = await server.call_tool("search_runbooks", {"query": "oomkill"})

        mock_call.assert_awaited_once_with("search_runbooks", {"query": "oomkill"})
        assert isinstance(result[0], TextContent)
        assert result[0].text == "runbook content here"

    async def test_call_tool_unknown_name_raises(self):
        with pytest.raises(ValueError, match="Unknown tool"):
            await server.call_tool("definitely_not_a_tool", {})


# ── Prompt surface ────────────────────────────────────────────────────────────


class TestPromptSurface:
    async def test_all_prompts_listed(self):
        prompts = await server.list_prompts()
        names = {p.name for p in prompts}
        assert {
            "incident_rca",
            "postmortem_draft",
            "oncall_handoff",
            "incident-triage",
            "slo-review",
            "capacity-check",
            "explain_alert",
        } <= names

    async def test_get_prompt_returns_result(self):
        result = await server.handle_get_prompt(
            "explain_alert", {"alert_name": "HighErrorRate"}
        )
        assert isinstance(result, GetPromptResult)
        assert result.messages

    async def test_get_prompt_with_none_arguments(self):
        with pytest.raises(ValueError, match="required argument"):
            await server.handle_get_prompt("explain_alert", None)


# ── Resource surface ──────────────────────────────────────────────────────────


class TestResourceSurface:
    async def test_all_resources_listed(self):
        resources = await server.list_resources()
        uris = {str(r.uri) for r in resources}
        assert {
            "sre://error-budget/all",
            "sre://capacity/overview",
            "sre://incidents/active",
            "sre://oncall/schedule",
            "sre://alerts/rules",
            "sre://cloudwatch/alarms",
        } <= uris

    async def test_every_resource_has_name_and_description(self):
        for resource in await server.list_resources():
            assert resource.name, str(resource.uri)
            assert resource.description, str(resource.uri)

    @pytest.mark.parametrize(
        ("uri", "attr"),
        [
            ("sre://incidents/active", "read_active_incidents"),
            ("sre://oncall/schedule", "read_oncall_schedule"),
            ("sre://alerts/rules", "read_alert_rules"),
            ("sre://cloudwatch/alarms", "read_cloudwatch_alarms"),
            ("sre://error-budget/all", "read_error_budget"),
            ("sre://capacity/overview", "read_capacity"),
        ],
    )
    async def test_read_resource_routing(self, monkeypatch, uri, attr):
        mock_read = AsyncMock(return_value='{"ok": true}')
        monkeypatch.setattr(server, attr, mock_read)

        result = await server.read_resource(uri)

        mock_read.assert_awaited_once()
        assert result == '{"ok": true}'

    async def test_read_resource_accepts_anyurl(self, monkeypatch):
        """The MCP SDK passes pydantic AnyUrl objects, not plain strings."""
        mock_read = AsyncMock(return_value="{}")
        monkeypatch.setattr(server, "read_active_incidents", mock_read)

        result = await server.read_resource(AnyUrl("sre://incidents/active"))

        mock_read.assert_awaited_once()
        assert result == "{}"

    async def test_read_slo_resource_routes_by_service(self, monkeypatch):
        mock_read = AsyncMock(return_value="SLO STATUS: payments-api")
        monkeypatch.setattr(server, "get_slo_resource", mock_read)

        result = await server.read_resource("sre://slos/payments-api")

        mock_read.assert_awaited_once_with("payments-api")
        assert "payments-api" in result

    async def test_read_resource_unknown_uri_raises(self):
        with pytest.raises(ValueError, match="Unknown resource URI"):
            await server.read_resource("sre://nope/nothing")

    async def test_read_slo_resource_missing_service_raises(self):
        with pytest.raises(ValueError, match="Missing service name"):
            await server.read_resource("sre://slos/")
