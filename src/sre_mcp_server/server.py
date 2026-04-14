"""Main MCP server entrypoint for SRE operations."""

import asyncio
import logging
import os

import structlog
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    GetPromptResult,
    Prompt,
    Resource,
    TextContent,
    Tool,
)

from sre_mcp_server.tools.pagerduty import PagerDutyTools
from sre_mcp_server.tools.grafana import GrafanaTools
from sre_mcp_server.tools.kubernetes import KubernetesTools
from sre_mcp_server.tools.aws import AWSTools
from sre_mcp_server.tools.runbooks import RunbookTools
from sre_mcp_server.tools.oncall import OnCallToolHandler
from sre_mcp_server.tools.capacity import CapacityToolHandler
from sre_mcp_server.tools.correlation import AlertCorrelationTools
from sre_mcp_server.tools.incidents import IncidentToolHandler
from sre_mcp_server.tools.deployments import DeploymentToolHandler
from sre_mcp_server.prompts.incident_rca import PROMPT_DEFINITIONS, get_prompt
from sre_mcp_server.resources.error_budget import get_error_budget_resource, read_error_budget
from sre_mcp_server.resources.capacity import get_capacity_resource, read_capacity
from sre_mcp_server.resources.slo import get_slo_resource, list_slo_resources

log = structlog.get_logger()

app = Server("sre-mcp-server")

# Initialize tool handlers
_pd = PagerDutyTools()
_grafana = GrafanaTools()
_k8s = KubernetesTools()
_aws = AWSTools()
_runbooks = RunbookTools()
_oncall = OnCallToolHandler()
_capacity = CapacityToolHandler()
_correlation = AlertCorrelationTools()
_incidents = IncidentToolHandler()
_deployments = DeploymentToolHandler()

_ALL_TOOLS = [_pd, _grafana, _k8s, _aws, _runbooks, _oncall, _capacity, _correlation, _incidents, _deployments]


@app.list_tools()
async def list_tools() -> list[Tool]:
    tools = []
    for handler in _ALL_TOOLS:
        tools.extend(await handler.get_tools())
    return tools


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    log.info("tool_call", tool=name, args=list(arguments.keys()))
    for handler in _ALL_TOOLS:
        if await handler.handles(name):
            result = await handler.call(name, arguments)
            return [TextContent(type="text", text=result)]
    raise ValueError(f"Unknown tool: {name}")


@app.list_prompts()
async def list_prompts() -> list[Prompt]:
    """Expose structured incident response prompts to MCP clients."""
    return PROMPT_DEFINITIONS


@app.get_prompt()
async def handle_get_prompt(name: str, arguments: dict | None) -> GetPromptResult:
    """Return the prompt messages for the given prompt name."""
    log.info("prompt_get", prompt=name, args=list((arguments or {}).keys()))
    return get_prompt(name, arguments or {})


_SLO_SERVICES = [
    s.strip()
    for s in os.environ.get("SLO_SERVICES", "payments-api,checkout-api,auth-api").split(",")
    if s.strip()
]


@app.list_resources()
async def list_resources() -> list[Resource]:
    """Expose MCP resources that Claude can read for ambient context."""
    return [
        await get_error_budget_resource(),
        await get_capacity_resource(),
        *list_slo_resources(_SLO_SERVICES),
    ]


@app.read_resource()
async def read_resource(uri: str) -> str:
    """Return the content of the requested MCP resource."""
    log.info("resource_read", uri=uri)
    if uri.startswith("sre://slos/"):
        service = uri.removeprefix("sre://slos/")
        if not service:
            raise ValueError("Missing service name in SLO resource URI")
        return await get_slo_resource(service)
    match uri:
        case "sre://error-budget/all":
            return await read_error_budget()
        case "sre://capacity/overview":
            return await read_capacity()
        case _:
            raise ValueError(f"Unknown resource URI: {uri}")


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ]
    )
    asyncio.run(_run())


async def _run() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())
