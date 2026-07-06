"""Tests for MCP tool handlers with mocked external clients."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from sre_mcp_server.tools.aws import AWSTools
from sre_mcp_server.tools.grafana import GrafanaTools
from sre_mcp_server.tools.pagerduty import PagerDutyTools
from sre_mcp_server.tools.runbooks import RunbookTools


# ── PagerDuty ─────────────────────────────────────────────────────────────────


class TestPagerDutyTools:
    @pytest.fixture
    def handler(self):
        return PagerDutyTools()

    async def test_handles_own_tools_only(self, handler):
        assert await handler.handles("get_active_incidents")
        assert await handler.handles("acknowledge_incident")
        assert not await handler.handles("query_metrics")

    async def test_get_active_incidents_formats_output(self, handler, httpx_mock):
        created = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        httpx_mock.add_response(
            url=re.compile(r"https://api\.pagerduty\.com/incidents.*"),
            json={
                "incidents": [
                    {
                        "id": "P123",
                        "title": "High error rate on payments-api",
                        "status": "triggered",
                        "urgency": "high",
                        "service": {"summary": "payments-api"},
                        "assignments": [{"assignee": {"summary": "Alice"}}],
                        "created_at": created,
                        "html_url": "https://pd.example.com/incidents/P123",
                    }
                ]
            },
        )

        result = await handler.call("get_active_incidents", {"limit": 5})

        assert "P123" in result
        assert "payments-api" in result
        assert "Alice" in result

    async def test_get_active_incidents_empty(self, handler, httpx_mock):
        httpx_mock.add_response(
            url=re.compile(r"https://api\.pagerduty\.com/incidents.*"),
            json={"incidents": []},
        )

        result = await handler.call("get_active_incidents", {})

        assert "No active incidents" in result

    async def test_acknowledge_incident_sends_from_header(self, handler, httpx_mock):
        httpx_mock.add_response(
            method="PUT",
            url="https://api.pagerduty.com/incidents/P123",
            json={"incident": {"id": "P123", "status": "acknowledged"}},
        )

        result = await handler.call(
            "acknowledge_incident",
            {"incident_id": "P123", "from_email": "sre@example.com"},
        )

        request = httpx_mock.get_request()
        assert request.headers["From"] == "sre@example.com"
        assert "acknowledged" in result

    async def test_unknown_tool_raises(self, handler):
        with pytest.raises(ValueError, match="Unknown PagerDuty tool"):
            await handler.call("not_a_tool", {})


# ── Grafana ───────────────────────────────────────────────────────────────────


class TestGrafanaTools:
    @pytest.fixture
    def handler(self):
        return GrafanaTools()

    async def test_registered_tools(self, handler):
        names = {t.name for t in await handler.get_tools()}
        assert names == {"query_metrics", "get_dashboard_url", "list_firing_alerts"}

    async def test_query_metrics_requires_query_param(self, handler):
        tool = next(
            t for t in await handler.get_tools() if t.name == "query_metrics"
        )
        assert "query" in tool.inputSchema["required"]

    async def test_query_metrics_formats_series(self, handler, httpx_mock):
        httpx_mock.add_response(
            url=re.compile(r"http://grafana\.test/api/datasources/proxy/.*"),
            json={
                "status": "success",
                "data": {
                    "result": [
                        {
                            "metric": {"__name__": "up", "job": "payments-api"},
                            "values": [[1751700000, "1"]],
                        }
                    ]
                },
            },
        )

        result = await handler.call("query_metrics", {"query": "up"})

        assert "up" in result
        assert "payments-api" in result

    async def test_query_metrics_no_data(self, handler, httpx_mock):
        httpx_mock.add_response(
            url=re.compile(r"http://grafana\.test/api/datasources/proxy/.*"),
            json={"status": "success", "data": {"result": []}},
        )

        result = await handler.call("query_metrics", {"query": "missing_metric"})

        assert "No data returned" in result

    async def test_list_firing_alerts_filters_by_state(self, handler, httpx_mock):
        httpx_mock.add_response(
            url="http://grafana.test/api/alertmanager/grafana/api/v2/alerts",
            json=[
                {
                    "status": {"state": "firing"},
                    "labels": {"alertname": "HighErrorRate", "severity": "critical"},
                    "annotations": {"summary": "Error rate above 1%"},
                },
                {
                    "status": {"state": "normal"},
                    "labels": {"alertname": "QuietAlert", "severity": "info"},
                    "annotations": {},
                },
            ],
        )

        result = await handler.call("list_firing_alerts", {"state": "firing"})

        assert "HighErrorRate" in result
        assert "QuietAlert" not in result


# ── AWS ───────────────────────────────────────────────────────────────────────


class TestAWSTools:
    @pytest.fixture
    def handler(self):
        return AWSTools()

    async def test_get_cloudwatch_alarms_formats_output(self, handler):
        mock_cw = MagicMock()
        mock_cw.describe_alarms.return_value = {
            "MetricAlarms": [
                {
                    "AlarmName": "rds-cpu-high",
                    "StateValue": "ALARM",
                    "StateReason": "Threshold crossed",
                    "Namespace": "AWS/RDS",
                    "MetricName": "CPUUtilization",
                    "StateUpdatedTimestamp": "2026-07-05T12:00:00Z",
                }
            ]
        }
        mock_session = MagicMock()
        mock_session.client.return_value = mock_cw

        with patch("sre_mcp_server.tools.aws.boto3") as mock_boto3:
            mock_boto3.Session.return_value = mock_session
            result = await handler.call("get_cloudwatch_alarms", {"state": "ALARM"})

        mock_cw.describe_alarms.assert_called_once()
        assert "rds-cpu-high" in result
        assert "AWS/RDS/CPUUtilization" in result

    async def test_get_cloudwatch_alarms_empty(self, handler):
        mock_cw = MagicMock()
        mock_cw.describe_alarms.return_value = {"MetricAlarms": []}
        mock_session = MagicMock()
        mock_session.client.return_value = mock_cw

        with patch("sre_mcp_server.tools.aws.boto3") as mock_boto3:
            mock_boto3.Session.return_value = mock_session
            result = await handler.call("get_cloudwatch_alarms", {})

        assert "No alarms" in result


# ── Runbooks ──────────────────────────────────────────────────────────────────


class TestRunbookTools:
    @pytest.fixture
    def runbook_dir(self, tmp_path):
        k8s = tmp_path / "k8s"
        k8s.mkdir()
        (k8s / "oomkill.md").write_text(
            "# OOMKill Remediation\nSteps to diagnose container OOM kills.\n"
            "\n## Steps\n1. kubectl top pods\n"
        )
        (tmp_path / "database-failover.md").write_text(
            "# Database Failover\nPromote the RDS replica during primary failure.\n"
        )
        return tmp_path

    @pytest.fixture
    def handler(self, runbook_dir, monkeypatch):
        monkeypatch.setenv("RUNBOOK_DIR", str(runbook_dir))
        return RunbookTools()

    async def test_search_ranks_matches(self, handler):
        result = await handler.call("search_runbooks", {"query": "OOM kills"})
        assert "OOMKill Remediation" in result

    async def test_search_no_match(self, handler):
        result = await handler.call("search_runbooks", {"query": "zzzznope"})
        assert "No runbooks found" in result

    async def test_get_runbook_by_stem(self, handler):
        result = await handler.call("get_runbook", {"name": "oomkill"})
        assert "kubectl top pods" in result

    async def test_get_runbook_missing(self, handler):
        result = await handler.call("get_runbook", {"name": "does-not-exist"})
        assert "not found" in result

    async def test_list_categories_counts(self, handler):
        result = await handler.call("list_runbook_categories", {})
        assert "k8s: 1" in result
        assert "general: 1" in result

    async def test_demo_mode_when_dir_missing(self, monkeypatch):
        monkeypatch.setenv("RUNBOOK_DIR", "/nonexistent/runbooks")
        handler = RunbookTools()
        result = await handler.call("search_runbooks", {"query": "anything"})
        assert "demo mode" in result
