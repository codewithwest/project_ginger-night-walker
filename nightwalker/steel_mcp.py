from __future__ import annotations

import os
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class SteelMCPClient:
    """Lifecycle-managed client for Steel's official self-hosted MCP server."""

    def __init__(self) -> None:
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._tools: list[dict[str, Any]] = []

    @property
    def connected(self) -> bool:
        return self._session is not None

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [dict(tool) for tool in self._tools]

    async def start(self) -> None:
        if self._session is not None:
            return
        if os.getenv("STEEL_MCP_ENABLED", "false").strip().lower() != "true":
            return
        command = os.getenv("STEEL_MCP_COMMAND", "node").strip() or "node"
        server_path = os.getenv(
            "STEEL_MCP_SERVER_PATH", "/app/steel-mcp-server/dist/stdio.js"
        )
        env = os.environ.copy()
        env["STEEL_LOCAL"] = "true"
        env["STEEL_BASE_URL"] = os.getenv(
            "STEEL_MCP_BASE_URL",
            os.getenv("STEEL_API_BASE_URL", "http://steel-browser:3000/v1"),
        ).rstrip("/")
        env["STEEL_PROFILE"] = os.getenv("STEEL_MCP_PROFILE", "browse")
        self._stack = AsyncExitStack()
        try:
            read, write = await self._stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=command,
                        args=[server_path],
                        env=env,
                    )
                )
            )
            self._session = await self._stack.enter_async_context(ClientSession(read, write))
            await self._session.initialize()
            response = await self._session.list_tools()
            self._tools = [
                {
                    "name": item.name,
                    "description": item.description or "",
                    "input_schema": item.inputSchema,
                }
                for item in response.tools
            ]
        except Exception:
            await self._stack.aclose()
            self._stack = None
            self._session = None
            self._tools = []
            raise

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._session is None:
            raise RuntimeError("Steel MCP is not connected")
        if name not in {tool["name"] for tool in self._tools}:
            raise ValueError(f"Steel MCP tool is not registered: {name}")
        result = await self._session.call_tool(name, arguments=arguments)
        content: list[dict[str, Any]] = []
        for item in result.content:
            if getattr(item, "type", None) == "text":
                content.append({"type": "text", "text": item.text})
            elif getattr(item, "type", None) == "image":
                content.append({"type": "image", "mimeType": item.mimeType})
        return {
            "is_error": bool(result.isError),
            "content": content,
            "structured_content": getattr(result, "structuredContent", None),
        }

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None
        self._tools = []
