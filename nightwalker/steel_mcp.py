from __future__ import annotations

import asyncio
import json
import os
from typing import Any


class SteelMCPClient:
    """Lifecycle-managed stdio client for Steel's official self-hosted MCP server."""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._lock = asyncio.Lock()
        self._tools: list[dict[str, Any]] = []
        self._stderr_task: asyncio.Task | None = None
        self._sessions: dict[str, str] = {}

    @property
    def connected(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [dict(tool) for tool in self._tools]

    async def _read_response(self, request_id: int) -> dict[str, Any]:
        if self._process is None or self._process.stdout is None:
            raise RuntimeError("Steel MCP process is not running")
        while True:
            line = await asyncio.wait_for(self._process.stdout.readline(), timeout=60)
            if not line:
                raise RuntimeError("Steel MCP server closed its output stream")
            message = json.loads(line)
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(f"Steel MCP protocol error: {message['error']}")
                return message.get("result") or {}

    async def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("Steel MCP is not connected")
        async with self._lock:
            self._request_id += 1
            request_id = self._request_id
            message = {"jsonrpc": "2.0", "id": request_id, "method": method}
            if params is not None:
                message["params"] = params
            self._process.stdin.write((json.dumps(message) + "\n").encode())
            await self._process.stdin.drain()
            return await self._read_response(request_id)

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("Steel MCP is not connected")
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._process.stdin.write((json.dumps(message) + "\n").encode())
        await self._process.stdin.drain()

    async def _drain_stderr(self) -> None:
        if self._process is None or self._process.stderr is None:
            return
        while line := await self._process.stderr.readline():
            # Keep server diagnostics out of the MCP protocol stream.
            import logging
            logging.getLogger(__name__).debug("steel_mcp: %s", line.decode(errors="replace").rstrip())

    async def start(self) -> None:
        if self.connected:
            return
        if os.getenv("STEEL_MCP_ENABLED", "false").strip().lower() != "true":
            return
        command = os.getenv("STEEL_MCP_COMMAND", "node").strip() or "node"
        server_path = os.getenv("STEEL_MCP_SERVER_PATH", "/app/steel-mcp-server/dist/stdio.js")
        env = os.environ.copy()
        env["STEEL_LOCAL"] = "true"
        env["STEEL_BASE_URL"] = os.getenv(
            "STEEL_MCP_BASE_URL",
            os.getenv("STEEL_API_BASE_URL", "http://steel-browser:3000/v1"),
        ).rstrip("/")
        env["STEEL_PROFILE"] = os.getenv("STEEL_MCP_PROFILE", "browse")
        self._process = await asyncio.create_subprocess_exec(
            command, server_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        try:
            await self._request("initialize", {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "ginger-night-walker", "version": "0.4.0"},
            })
            await self._notify("notifications/initialized")
            response = await self._request("tools/list")
            self._tools = [
                {
                    "name": item["name"],
                    "description": item.get("description", ""),
                    "input_schema": item.get("inputSchema", {}),
                }
                for item in response.get("tools", [])
            ]
        except Exception:
            await self.close()
            raise

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.connected:
            raise RuntimeError("Steel MCP is not connected")
        if name not in {tool["name"] for tool in self._tools}:
            raise ValueError(f"Steel MCP tool is not registered: {name}")
        result = await self._request("tools/call", {"name": name, "arguments": arguments})
        content = [
            {"type": item.get("type"), "text": item.get("text", "")[:30000]}
            for item in result.get("content", [])
            if item.get("type") == "text"
        ]
        return {
            "is_error": bool(result.get("isError")),
            "content": content,
            "structured_content": result.get("structuredContent"),
        }

    async def session_for_task(self, task_id: str) -> str:
        """Create one Steel MCP browser session per mission and reuse it across steps."""
        existing = self._sessions.get(task_id)
        if existing:
            return existing
        created = await self.call_tool("steel_session_create", {})
        if created["is_error"]:
            raise RuntimeError(str(created["content"])[:1000])
        session_id = str((created.get("structured_content") or {}).get("session_id") or "")
        if not session_id:
            raise RuntimeError("Steel MCP session_create did not return a session_id")
        self._sessions[task_id] = session_id
        return session_id

    async def execute_mission_step(
        self, task_id: str, url: str, objective: str, capability: str,
        propose_tool_call,
    ) -> dict[str, Any]:
        """Execute one authorised model-authored mission step using registered Steel tools."""
        session_id = await self.session_for_task(task_id)
        if capability == "navigate":
            result = await self.call_tool("steel_navigate", {
                "session_id": session_id, "url": url, "include_snapshot": True,
            })
            selected_tool = "steel_navigate"
        else:
            snapshot = await self.call_tool("steel_snapshot", {
                "session_id": session_id, "interactive_only": False,
            })
            if snapshot["is_error"]:
                raise RuntimeError(str(snapshot["content"])[:1000])
            allowed_by_capability = {
                "interact": {"steel_find", "steel_act", "steel_wait_for"},
                "extract": {"steel_snapshot", "steel_scrape"},
                "recover": {"steel_navigate", "steel_wait_for"},
            }
            allowed = allowed_by_capability.get(capability)
            if not allowed:
                raise PermissionError(f"Unsupported Steel MCP mission capability: {capability}")
            proposal = await propose_tool_call(
                task_id=task_id, objective=objective, capability=capability,
                observation={"url": url, "snapshot": snapshot, "session_id": session_id},
                tools=[tool for tool in self._tools if tool["name"] in allowed],
                session_id=session_id,
            )
            selected_tool = str(proposal.get("tool_name") or "")
            if selected_tool not in allowed:
                raise PermissionError("Model selected a Steel tool outside the capability allowlist")
            arguments = dict(proposal.get("arguments") or {})
            selected_schema = next(
                (tool.get("input_schema") or {} for tool in self._tools
                 if tool.get("name") == selected_tool),
                {},
            )
            schema_properties = selected_schema.get("properties") or {}
            # Steel MCP tools do not all accept a session_id argument. Keep the
            # session task-scoped here, and pass it only when the selected tool's
            # published input schema explicitly declares it.
            if "session_id" in schema_properties:
                arguments["session_id"] = session_id
            else:
                arguments.pop("session_id", None)
            result = await self.call_tool(selected_tool, arguments)
        if result["is_error"]:
            raise RuntimeError(str(result["content"])[:1500])
        after = await self.call_tool("steel_snapshot", {
            "session_id": session_id, "interactive_only": False,
        })
        if after["is_error"]:
            raise RuntimeError(str(after["content"])[:1000])
        return {
            "status": "completed", "session_id": session_id,
            "tool_name": selected_tool, "tool_result": result,
            "snapshot": after,
        }

    async def release_task_session(self, task_id: str) -> None:
        session_id = self._sessions.pop(task_id, None)
        if session_id and self.connected:
            try:
                await self.call_tool("steel_session_release", {"session_id": session_id})
            except Exception:
                pass

    async def smoke_test(self, url: str) -> dict[str, Any]:
        """Run a minimal create/navigate/snapshot/release flow through Steel MCP."""
        created = await self.call_tool("steel_session_create", {})
        if created["is_error"]:
            raise RuntimeError(str(created["content"])[:1000])
        structured = created.get("structured_content") or {}
        session_id = structured.get("session_id")
        if not session_id:
            raise RuntimeError("Steel MCP session_create did not return a session_id")
        try:
            navigated = await self.call_tool(
                "steel_navigate",
                {"session_id": session_id, "url": url, "include_snapshot": True},
            )
            if navigated["is_error"]:
                raise RuntimeError(str(navigated["content"])[:1000])
            snapshot = await self.call_tool(
                "steel_snapshot", {"session_id": session_id, "interactive_only": False}
            )
            if snapshot["is_error"]:
                raise RuntimeError(str(snapshot["content"])[:1000])
            return {
                "status": "completed",
                "session_id": session_id,
                "navigate": navigated,
                "snapshot": snapshot,
            }
        finally:
            await self.call_tool("steel_session_release", {"session_id": session_id})

    async def close(self) -> None:
        process, self._process = self._process, None
        self._tools = []
        self._sessions.clear()
        if process is not None:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            self._stderr_task = None
