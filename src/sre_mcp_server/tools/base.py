"""Base class for MCP tool handlers."""

from abc import ABC, abstractmethod
from mcp.types import Tool


class BaseToolHandler(ABC):
    """Base class that all tool handlers must implement."""

    TOOL_NAMES: set[str] = set()

    async def handles(self, tool_name: str) -> bool:
        return tool_name in self.TOOL_NAMES

    @abstractmethod
    async def get_tools(self) -> list[Tool]: ...

    @abstractmethod
    async def call(self, name: str, args: dict) -> str: ...
