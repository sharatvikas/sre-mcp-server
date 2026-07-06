"""Shared fixtures for the SRE MCP server test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    """Pin external-service configuration so tests never touch real systems."""
    monkeypatch.setenv("PAGERDUTY_TOKEN", "test-pd-token")
    monkeypatch.setenv("PAGERDUTY_API_KEY", "test-pd-api-key")
    monkeypatch.setenv("GRAFANA_URL", "http://grafana.test")
    monkeypatch.setenv("GRAFANA_TOKEN", "test-grafana-token")
    monkeypatch.setenv("GRAFANA_DATASOURCE_UID", "prometheus")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
