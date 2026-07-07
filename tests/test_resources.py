"""Tests for MCP resource providers with mocked external clients."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx
from botocore.exceptions import ClientError

from sre_mcp_server.resources import alert_rules, cloudwatch, incidents, oncall


# ── Active incidents (PagerDuty) ──────────────────────────────────────────────


class TestIncidentsResource:
    def test_metadata(self):
        resource = incidents.get_incidents_resource()
        assert str(resource.uri) == "sre://incidents/active"
        assert resource.mimeType == "application/json"

    async def test_read_returns_structured_incidents(self, httpx_mock):
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
                        "created_at": "2026-07-05T10:00:00Z",
                        "html_url": "https://pd.example.com/incidents/P123",
                    },
                    {
                        "id": "P456",
                        "title": "Disk filling on db-01",
                        "status": "acknowledged",
                        "urgency": "low",
                        "service": {"summary": "postgres"},
                        "assignments": [],
                        "created_at": "2026-07-05T09:00:00Z",
                        "html_url": "https://pd.example.com/incidents/P456",
                    },
                ]
            },
        )

        data = json.loads(await incidents.read_active_incidents())

        assert data["summary"] == {
            "total": 2,
            "triggered": 1,
            "acknowledged": 1,
            "high_urgency": 1,
        }
        first = data["incidents"][0]
        assert first["id"] == "P123"
        assert first["service"] == "payments-api"
        assert first["assignees"] == ["Alice"]

    async def test_read_sends_auth_header(self, httpx_mock):
        httpx_mock.add_response(
            url=re.compile(r"https://api\.pagerduty\.com/incidents.*"),
            json={"incidents": []},
        )

        await incidents.read_active_incidents()

        request = httpx_mock.get_request()
        assert request.headers["Authorization"] == "Token token=test-pd-token"

    async def test_http_error_returns_error_payload(self, httpx_mock):
        httpx_mock.add_response(
            url=re.compile(r"https://api\.pagerduty\.com/incidents.*"),
            status_code=503,
        )

        data = json.loads(await incidents.read_active_incidents())

        assert "503" in data["error"]
        assert "timestamp" in data

    async def test_transport_error_returns_error_payload(self, httpx_mock):
        httpx_mock.add_exception(httpx.ConnectError("connection refused"))

        data = json.loads(await incidents.read_active_incidents())

        assert "Could not reach PagerDuty" in data["error"]


# ── On-call schedule (PagerDuty) ──────────────────────────────────────────────


class TestOncallResource:
    def test_metadata(self):
        resource = oncall.get_oncall_resource()
        assert str(resource.uri) == "sre://oncall/schedule"

    async def test_read_groups_by_policy_and_sorts_levels(self, httpx_mock):
        httpx_mock.add_response(
            url=re.compile(r"https://api\.pagerduty\.com/oncalls.*"),
            json={
                "oncalls": [
                    {
                        "escalation_policy": {"summary": "Platform"},
                        "escalation_level": 2,
                        "user": {"summary": "Bob"},
                        "schedule": {"summary": "Platform Secondary"},
                        "start": "2026-07-05T00:00:00Z",
                        "end": "2026-07-12T00:00:00Z",
                    },
                    {
                        "escalation_policy": {"summary": "Platform"},
                        "escalation_level": 1,
                        "user": {"summary": "Alice"},
                        "schedule": {"summary": "Platform Primary"},
                        "start": "2026-07-05T00:00:00Z",
                        "end": "2026-07-12T00:00:00Z",
                    },
                    {
                        "escalation_policy": {"summary": "Database"},
                        "escalation_level": 1,
                        "user": {"summary": "Carol"},
                        "schedule": None,
                        "start": None,
                        "end": None,
                    },
                ]
            },
        )

        data = json.loads(await oncall.read_oncall_schedule())

        assert data["summary"]["escalation_policies"] == 2
        platform = data["policies"]["Platform"]
        assert [e["engineer"] for e in platform] == ["Alice", "Bob"]
        assert data["policies"]["Database"][0]["engineer"] == "Carol"

    async def test_http_error_returns_error_payload(self, httpx_mock):
        httpx_mock.add_response(
            url=re.compile(r"https://api\.pagerduty\.com/oncalls.*"),
            status_code=401,
        )

        data = json.loads(await oncall.read_oncall_schedule())

        assert "401" in data["error"]


# ── Alert rules (Grafana) ─────────────────────────────────────────────────────


class TestAlertRulesResource:
    def test_metadata(self):
        resource = alert_rules.get_alert_rules_resource()
        assert str(resource.uri) == "sre://alerts/rules"

    async def test_read_summarizes_rules(self, httpx_mock):
        httpx_mock.add_response(
            url="http://grafana.test/api/v1/provisioning/alert-rules",
            json=[
                {
                    "uid": "abc",
                    "title": "HighErrorRate",
                    "ruleGroup": "slo-alerts",
                    "folderUID": "sre",
                    "isPaused": False,
                    "for": "5m",
                    "labels": {"severity": "critical"},
                    "annotations": {"summary": "Error rate above 1%"},
                },
                {
                    "uid": "def",
                    "title": "OldNoisyAlert",
                    "ruleGroup": "legacy",
                    "folderUID": "sre",
                    "isPaused": True,
                    "for": "10m",
                    "labels": None,
                    "annotations": None,
                },
            ],
        )

        data = json.loads(await alert_rules.read_alert_rules())

        assert data["summary"]["total_rules"] == 2
        assert data["summary"]["paused"] == 1
        assert data["summary"]["rule_groups"] == {"slo-alerts": 1, "legacy": 1}
        rule = data["rules"][0]
        assert rule["title"] == "HighErrorRate"
        assert rule["labels"] == {"severity": "critical"}

    async def test_read_sends_bearer_token(self, httpx_mock):
        httpx_mock.add_response(
            url="http://grafana.test/api/v1/provisioning/alert-rules", json=[]
        )

        await alert_rules.read_alert_rules()

        request = httpx_mock.get_request()
        assert request.headers["Authorization"] == "Bearer test-grafana-token"

    async def test_http_error_returns_error_payload(self, httpx_mock):
        httpx_mock.add_response(
            url="http://grafana.test/api/v1/provisioning/alert-rules",
            status_code=500,
        )

        data = json.loads(await alert_rules.read_alert_rules())

        assert "500" in data["error"]


# ── CloudWatch alarms (boto3) ─────────────────────────────────────────────────


def _alarm(name: str, state: str, updated: datetime) -> dict:
    return {
        "AlarmName": name,
        "StateValue": state,
        "StateReason": f"{name} threshold crossed",
        "Namespace": "AWS/RDS",
        "MetricName": "CPUUtilization",
        "Threshold": 90.0,
        "ComparisonOperator": "GreaterThanThreshold",
        "StateUpdatedTimestamp": updated,
    }


class TestCloudWatchResource:
    def test_metadata(self):
        resource = cloudwatch.get_cloudwatch_resource()
        assert str(resource.uri) == "sre://cloudwatch/alarms"

    async def test_read_returns_firing_and_recent(self):
        now = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)
        old = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
        mock_cw = MagicMock()
        mock_cw.describe_alarms.side_effect = [
            {"MetricAlarms": [_alarm("rds-cpu-high", "ALARM", now)]},
            {
                "MetricAlarms": [
                    _alarm("old-ok-alarm", "OK", old),
                    _alarm("rds-cpu-high", "ALARM", now),
                ]
            },
        ]
        mock_session = MagicMock()
        mock_session.client.return_value = mock_cw

        with patch.object(cloudwatch.boto3, "Session", return_value=mock_session):
            data = json.loads(await cloudwatch.read_cloudwatch_alarms())

        assert data["region"] == "us-east-1"
        assert data["summary"] == {"in_alarm": 1, "total_alarms": 2}
        assert data["firing"][0]["name"] == "rds-cpu-high"
        assert data["firing"][0]["metric"] == "AWS/RDS/CPUUtilization"
        # Most recently updated alarm sorts first.
        assert data["recently_updated"][0]["name"] == "rds-cpu-high"

    async def test_aws_error_returns_error_payload(self):
        mock_cw = MagicMock()
        mock_cw.describe_alarms.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "nope"}},
            "DescribeAlarms",
        )
        mock_session = MagicMock()
        mock_session.client.return_value = mock_cw

        with patch.object(cloudwatch.boto3, "Session", return_value=mock_session):
            data = json.loads(await cloudwatch.read_cloudwatch_alarms())

        assert "CloudWatch query failed" in data["error"]
        assert "AccessDenied" in data["error"]
