"""Integration tests for SRE MCP server tools."""

from __future__ import annotations

import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── PagerDuty tool tests ────────────────────────────────────────────────────

class TestPagerDutyTools:
    @pytest.fixture
    def handler(self):
        from sre_mcp_server.tools.pagerduty import PagerDutyToolHandler
        return PagerDutyToolHandler()

    def test_handles_get_active_incidents(self, handler):
        assert handler.handles("get_active_incidents")

    def test_handles_acknowledge_incident(self, handler):
        assert handler.handles("acknowledge_incident")

    def test_does_not_handle_unknown(self, handler):
        assert not handler.handles("unknown_tool")

    def test_tools_have_descriptions(self, handler):
        tools = handler.get_tools()
        for tool in tools:
            assert tool.description, f"Tool {tool.name} has no description"
            assert len(tool.description) > 10

    @pytest.mark.asyncio
    async def test_get_active_incidents_calls_api(self, handler):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={
            "incidents": [
                {
                    "id": "P123",
                    "title": "High error rate on payment-service",
                    "status": "triggered",
                    "urgency": "high",
                    "service": {"summary": "payment-service"},
                    "created_at": "2024-01-15T03:00:00Z",
                    "html_url": "https://company.pagerduty.com/incidents/P123",
                }
            ]
        })

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with patch.dict(os.environ, {"PAGERDUTY_API_KEY": "test-key"}):
                result = await handler.call("get_active_incidents", {"limit": 5})

        assert "payment-service" in result
        assert "P123" in result


# ── Grafana tool tests ───────────────────────────────────────────────────────

class TestGrafanaTools:
    @pytest.fixture
    def handler(self):
        from sre_mcp_server.tools.grafana import GrafanaToolHandler
        return GrafanaToolHandler()

    def test_all_tools_registered(self, handler):
        tool_names = {t.name for t in handler.get_tools()}
        assert "query_metrics" in tool_names
        assert "list_firing_alerts" in tool_names
        assert "get_dashboard_url" in tool_names

    def test_query_metrics_has_required_params(self, handler):
        tools = handler.get_tools()
        query_tool = next(t for t in tools if t.name == "query_metrics")
        schema = query_tool.inputSchema
        assert "query" in schema.get("required", [])

    @pytest.mark.asyncio
    async def test_query_metrics_returns_formatted_table(self, handler):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={
            "results": {
                "A": {
                    "frames": [{
                        "schema": {"fields": [
                            {"name": "Time"},
                            {"name": "Value"},
                        ]},
                        "data": {"values": [
                            [1705286400000],
                            [42.7],
                        ]}
                    }]
                }
            }
        })

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            with patch.dict(os.environ, {
                "GRAFANA_URL": "http://grafana.local",
                "GRAFANA_TOKEN": "test-token",
            }):
                result = await handler.call("query_metrics", {
                    "query": "up",
                    "start": "now-1h",
                    "end": "now",
                })

        assert result  # non-empty result


# ── Runbooks tool tests ──────────────────────────────────────────────────────

class TestRunbookTools:
    @pytest.fixture
    def tmp_runbooks(self, tmp_path):
        runbooks = tmp_path / "runbooks"
        runbooks.mkdir()
        (runbooks / "high-memory.md").write_text(
            "# High Memory Runbook\n\n## Symptoms\nOOM kills on production pods.\n\n## Steps\n1. Check `kubectl top pods`"
        )
        (runbooks / "database-replication.md").write_text(
            "# Database Replication Lag\n\n## Symptoms\nRDS replica is behind primary.\n\n## Steps\n1. Check `show slave status`"
        )
        return runbooks

    @pytest.fixture
    def handler(self, tmp_runbooks, monkeypatch):
        monkeypatch.setenv("RUNBOOKS_DIR", str(tmp_runbooks))
        from sre_mcp_server.tools.runbooks import RunbookToolHandler
        return RunbookToolHandler()

    @pytest.mark.asyncio
    async def test_search_finds_keyword(self, handler):
        result = await handler.call("search_runbooks", {"query": "memory"})
        assert "high-memory" in result.lower() or "memory" in result.lower()

    @pytest.mark.asyncio
    async def test_list_categories_returns_files(self, handler):
        result = await handler.call("list_runbook_categories", {})
        assert "high-memory" in result or "database-replication" in result

    @pytest.mark.asyncio
    async def test_get_runbook_returns_content(self, handler):
        result = await handler.call("get_runbook", {"name": "high-memory"})
        assert "OOM" in result or "memory" in result.lower()


# ── SLO Resource tests ───────────────────────────────────────────────────────

class TestSLOResource:
    @pytest.mark.asyncio
    async def test_slo_resource_formats_output(self):
        from sre_mcp_server.resources.slo import get_slo_status

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [{"metric": {}, "value": [1705286400, "0.42"]}]
            }
        })

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            with patch.dict(os.environ, {
                "GRAFANA_URL": "http://grafana.local",
                "GRAFANA_TOKEN": "test-token",
            }):
                result = await get_slo_status("payment-service")

        assert result  # returned something
        assert isinstance(result, str)
