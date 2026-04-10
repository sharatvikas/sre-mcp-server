"""AWS MCP tools — CloudWatch alarms, EC2, and cost queries."""

from __future__ import annotations

import os
import boto3
from mcp.types import Tool

from sre_mcp_server.tools.base import BaseToolHandler


def _session() -> boto3.Session:
    profile = os.environ.get("AWS_PROFILE")
    region = os.environ.get("AWS_REGION", "us-east-1")
    return boto3.Session(profile_name=profile, region_name=region)


class AWSTools(BaseToolHandler):
    TOOL_NAMES = {"get_cloudwatch_alarms", "get_ec2_instance_status", "describe_asg"}

    async def get_tools(self) -> list[Tool]:
        return [
            Tool(
                name="get_cloudwatch_alarms",
                description="List CloudWatch alarms in ALARM state for an AWS account/region.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "region": {
                            "type": "string",
                            "description": "AWS region. Default: us-east-1",
                            "default": "us-east-1",
                        },
                        "state": {
                            "type": "string",
                            "enum": ["ALARM", "OK", "INSUFFICIENT_DATA"],
                            "default": "ALARM",
                        },
                        "alarm_name_prefix": {
                            "type": "string",
                            "description": "Filter alarms by name prefix",
                        },
                    },
                },
            ),
            Tool(
                name="get_ec2_instance_status",
                description="Get EC2 instance status checks and state.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "instance_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of instance IDs. Omit to get all with issues.",
                        },
                        "region": {"type": "string", "default": "us-east-1"},
                    },
                },
            ),
            Tool(
                name="describe_asg",
                description="Describe an Auto Scaling Group — instance count, health, and recent activity.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Auto Scaling Group name",
                        },
                        "region": {"type": "string", "default": "us-east-1"},
                    },
                    "required": ["name"],
                },
            ),
        ]

    async def call(self, name: str, args: dict) -> str:
        match name:
            case "get_cloudwatch_alarms":
                return _get_cloudwatch_alarms(**args)
            case "get_ec2_instance_status":
                return _get_ec2_instance_status(**args)
            case "describe_asg":
                return _describe_asg(**args)
            case _:
                raise ValueError(f"Unknown AWS tool: {name}")


def _get_cloudwatch_alarms(
    region: str = "us-east-1",
    state: str = "ALARM",
    alarm_name_prefix: str | None = None,
) -> str:
    cw = _session().client("cloudwatch", region_name=region)
    kwargs: dict = {"StateValue": state, "MaxRecords": 50}
    if alarm_name_prefix:
        kwargs["AlarmNamePrefix"] = alarm_name_prefix

    resp = cw.describe_alarms(**kwargs)
    alarms = resp.get("MetricAlarms", [])

    if not alarms:
        return f"No alarms in state {state} in {region}."

    lines = [f"CLOUDWATCH ALARMS ({state}) in {region} — {len(alarms)} found\n{'═' * 60}"]
    for alarm in alarms:
        lines.append(
            f"\n{alarm['AlarmName']}\n"
            f"  State:     {alarm['StateValue']}\n"
            f"  Reason:    {alarm.get('StateReason', 'N/A')[:100]}\n"
            f"  Metric:    {alarm.get('Namespace', '')}/{alarm.get('MetricName', '')}\n"
            f"  Updated:   {alarm.get('StateUpdatedTimestamp', 'N/A')}"
        )
    return "\n".join(lines)


def _get_ec2_instance_status(
    instance_ids: list | None = None, region: str = "us-east-1"
) -> str:
    ec2 = _session().client("ec2", region_name=region)
    kwargs: dict = {}
    if instance_ids:
        kwargs["InstanceIds"] = instance_ids
    else:
        kwargs["Filters"] = [{"Name": "instance-status.status", "Values": ["impaired"]}]

    resp = ec2.describe_instance_status(**kwargs)
    statuses = resp.get("InstanceStatuses", [])

    if not statuses:
        return "All queried EC2 instances are healthy."

    lines = [f"EC2 INSTANCE STATUS ({len(statuses)} instances)\n{'─' * 60}"]
    for s in statuses:
        iid = s["InstanceId"]
        state = s["InstanceState"]["Name"]
        sys_check = s["SystemStatus"]["Status"]
        inst_check = s["InstanceStatus"]["Status"]
        lines.append(
            f"\n{iid}\n"
            f"  State:          {state}\n"
            f"  System check:   {sys_check}\n"
            f"  Instance check: {inst_check}"
        )
        for event in s.get("Events", []):
            lines.append(f"  Event: {event['Code']} — {event['Description']}")
    return "\n".join(lines)


def _describe_asg(name: str, region: str = "us-east-1") -> str:
    asg_client = _session().client("autoscaling", region_name=region)
    resp = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[name])
    groups = resp.get("AutoScalingGroups", [])

    if not groups:
        return f"No ASG found named '{name}' in {region}"

    g = groups[0]
    healthy = sum(1 for i in g["Instances"] if i["HealthStatus"] == "Healthy")
    unhealthy = len(g["Instances"]) - healthy

    lines = [
        f"ASG: {name}\n{'═' * 50}",
        f"Desired:   {g['DesiredCapacity']}",
        f"Min/Max:   {g['MinSize']} / {g['MaxSize']}",
        f"Healthy:   {healthy} / {len(g['Instances'])}",
        f"Unhealthy: {unhealthy}",
        f"AZs:       {', '.join(g.get('AvailabilityZones', []))}",
    ]

    # Recent activities
    acts = asg_client.describe_scaling_activities(
        AutoScalingGroupName=name, MaxRecords=5
    )
    recent = acts.get("Activities", [])
    if recent:
        lines.append("\nRECENT ACTIVITY:")
        for act in recent:
            lines.append(
                f"  [{act['StartTime'].strftime('%Y-%m-%d %H:%M')}] "
                f"{act['StatusCode']}: {act['Description'][:80]}"
            )

    return "\n".join(lines)
