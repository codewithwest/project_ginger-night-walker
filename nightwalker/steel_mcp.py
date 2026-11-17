from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

from nightwalker.mcp_ontology import native_tool_name, semantic_tool_view

_STEEL_REF = re.compile(r"^@?e\d+$", re.IGNORECASE)


def normalise_steel_target(target: Any) -> Any:
    """Steel resolves @eN against the snapshot; bare eN is treated as CSS and fails."""
    if not isinstance(target, str):
        return target
    value = target.strip()
    if _STEEL_REF.fullmatch(value):
        return "@" + value.lstrip("@")

    return target


def classify_steel_recovery_error(result: dict[str, Any]) -> str | None:
    """Classify transient Steel reference/cursor failures without guessing a page action."""
    message = " ".join(
        str(item.get("text") or "")
        for item in (result.get("content") or [])
        if isinstance(item, dict)
    ).casefold()
    if any(term in message for term in (
        "no page node", "moving or outside the viewport", "stale element",
        "element is detached", "not attached to the dom",
    )):
        return "stale_element"
    if "cursor" in message and any(term in message for term in (
        "not readable", "invalid cursor", "cursor expired", "unknown cursor",
    )):
        return "invalid_cursor"
    return None


def normalise_steel_tool_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Force accessibility refs into the @eN form Steel's act/find tools require."""
    normalised = dict(arguments)
    if tool_name not in {"steel_act", "steel_find"}:
        return normalised
    if "target" in normalised:
        normalised["target"] = normalise_steel_target(normalised.get("target"))
    if "selector" in normalised:
        normalised["selector"] = normalise_steel_target(normalised.get("selector"))
    actions = normalised.get("actions")
    if isinstance(actions, list):
        cleaned: list[Any] = []
        for item in actions:
            if isinstance(item, dict):
                entry = dict(item)
                if "target" in entry:
                    entry["target"] = normalise_steel_target(entry.get("target"))
                if "selector" in entry:
                    entry["selector"] = normalise_steel_target(entry.get("selector"))
                cleaned.append(entry)
            else:
                cleaned.append(item)
        normalised["actions"] = cleaned

    return normalised


class SteelMCPClient:
    """Lifecycle-managed stdio client for Steel's official self-hosted MCP server."""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._lock = asyncio.Lock()
        self._tools: list[dict[str, Any]] = []
        self._stderr_task: asyncio.Task | None = None
        self._sessions: dict[str, str] = {}
        self.reclaim_sessions = None
        self.release_remote_session = None

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
        is_error = bool(result.get("isError"))
        message = " ".join(str(item.get("text") or "") for item in content).casefold()
        if is_error and (
            ("hard timeout" in message and "released" in message)
            or ("session" in message and "has been released by steel" in message)
        ):
            # Steel has already destroyed this browser. Never leave its ID cached
            # against the mission: retry/resume must allocate a genuinely new one.
            expired_id = str(arguments.get("session_id") or "")
            for task_id, cached_id in list(self._sessions.items()):
                if expired_id and cached_id == expired_id:
                    self._sessions.pop(task_id, None)
        return {
            "is_error": is_error,
            "content": content,
            "structured_content": result.get("structuredContent"),
        }

    async def _create_session(self, task_id: str) -> str:
        # guest=True asks Steel for an isolated browser without leftover profiles/cart state.
        created = await self.call_tool("steel_session_create", {"guest": True})
        if created["is_error"]:
            raise RuntimeError(str(created["content"])[:1000])
        session_id = str((created.get("structured_content") or {}).get("session_id") or "")
        if not session_id:
            raise RuntimeError("Steel MCP session_create did not return a session_id")
        self._sessions[task_id] = session_id
        return session_id

    async def session_for_task(self, task_id: str, *, fresh: bool = False) -> str:
        """Create one Steel MCP browser session per mission and reuse it across steps.

        New missions and navigate steps pass fresh=True so a prior run's idle
        browser is never reused.
        """
        if fresh:
            await self.release_task_session(task_id)
            if self.reclaim_sessions is not None:
                await self.reclaim_sessions()
        existing = self._sessions.get(task_id)
        if existing and not fresh:
            return existing
        try:
            return await self._create_session(task_id)
        except RuntimeError as exc:
            slots_exhausted = "session slots" in str(exc).casefold()
            if not slots_exhausted or self.reclaim_sessions is None:
                raise
            await self.reclaim_sessions()
            return await self._create_session(task_id)

    async def execute_mission_step(
        self, task_id: str, url: str, objective: str, capability: str,
        propose_tool_call, session_callback=None, *, mission_objective: str | None = None,
    ) -> dict[str, Any]:
        """Execute exactly one authorised, Decider-authored mission step."""
        session_id = await self.session_for_task(task_id)
        if session_callback is not None:
            session_callback(session_id)

        executed_calls: list[dict[str, Any]] = []
        if capability == "navigate":
            result = await self.call_tool("steel_navigate", {
                "session_id": session_id, "url": url, "include_snapshot": True,
            })
            if result.get("is_error"):
                raise RuntimeError(str(result.get("content") or [])[:1500])
            selected_tool = "steel_navigate"
        else:
            snapshot = await self.call_tool("steel_snapshot", {
                "session_id": session_id, "interactive_only": False,
            })
            if snapshot["is_error"]:
                raise RuntimeError(str(snapshot["content"])[:1000])
            available_tools = semantic_tool_view(self._tools, capability)
            if not available_tools:
                raise PermissionError(f"No ontology-defined Steel functions are available for capability: {capability}")
            previous_failure: dict[str, Any] | None = None
            result = {"is_error": True, "content": [{"text": "No tool proposal was executed"}]}
            selected_tool = ""
            step_confirmed = False
            # The action budget is a safety bound, not a workflow definition. The
            # executor may adapt to any site/application state within this budget.
            try:
                max_proposals = max(4, min(32, int(os.getenv("STEEL_MCP_MAX_PROPOSALS", "16"))))
            except ValueError:
                max_proposals = 16
            # An interact-capability step is not complete after observation alone.
            # Element resolution/waiting may precede an action, but at least one
            # ontology-marked page mutation must execute before the step can pass.
            if capability != "interact":
                max_proposals = 2
            interaction_executed = False
            consecutive_read_only_calls = 0
            recovery_mode: str | None = None
            recovery_attempts = 0
            # Reserve a small, bounded recovery allowance so transient browser
            # references do not consume the entire objective-execution budget.
            recovery_reserve = min(3, max(1, max_proposals // 8)) if capability == "interact" else 0
            for attempt in range(max_proposals + recovery_reserve):
                observation: dict[str, Any] = {
                    "url": url, "snapshot": snapshot, "session_id": session_id,
                    "previous_failure": previous_failure,
                    "mission_objective": str(mission_objective or objective)[:2000],
                    "step_objective": objective[:1000],
                    "executed_calls": executed_calls[-12:],
                    "guidance": (
                        "Functions already executed in this step: "
                        + ", ".join(call["semantic_function"] for call in executed_calls)
                        + ". Use their outcomes and the latest page state to choose the next useful action. "
                        "Do not repeat an action that failed or did not change the relevant state."
                    ) if executed_calls else "",
                }
                # Repeated read-only discovery must not consume the whole
                # interaction budget. After two successful preparation calls,
                # require the model to choose from the ontology's state-changing
                # functions; it still selects the target and action from the live
                # observation and the Decider-authored objective.
                proposal_tools = available_tools
                if recovery_mode in {"stale_element", "invalid_cursor"}:
                    # A stale reference must never be sent straight back to an
                    # interaction call. Re-observe/resolve first; the model still
                    # chooses the appropriate live target and subsequent action.
                    proposal_tools = [
                        item for item in available_tools
                        if item.get("name") in {"inspect_page", "find_element", "wait_for_condition"}
                    ]
                    observation["guidance"] = (
                        "RECOVERY REQUIRED (" + recovery_mode + "): discard every prior element "
                        "reference/cursor. Use a fresh page observation and, if needed, resolve "
                        "the control again from the current page. Do not repeat the failed action "
                        "or reuse its target. Once the new state is grounded, continue the same "
                        "step objective. This is a recovery instruction, not a new task workflow."
                    )
                    recovery_attempts += 1
                    if recovery_attempts > recovery_reserve:
                        raise RuntimeError(
                            "Browser reference recovery could not re-establish a usable page state; "
                            "the step remains incomplete."
                        )
                if capability == "interact" and consecutive_read_only_calls >= 2 and recovery_mode is None:
                    proposal_tools = [
                        item for item in available_tools if item.get("mutates_page")
                    ]
                    if not proposal_tools:
                        raise RuntimeError(
                            "Interaction step cannot proceed: no state-changing browser "
                            "function is available in the authorised capability."
                        )
                    observation["guidance"] = (
                        str(observation.get("guidance") or "")
                        + " Repeated read-only preparation has not advanced the step. "
                        "Choose a state-changing function now, using only a grounded "
                        "element and an action authorised by the objective."
                    )[:700]
                proposal = await propose_tool_call(
                    task_id=task_id, objective=objective, capability=capability,
                    observation=observation, tools=proposal_tools, session_id=session_id,
                )
                semantic_name = str(proposal.get("tool_name") or "")
                if capability == "interact" and semantic_name == "__complete__":
                    if not interaction_executed:
                        raise RuntimeError(
                            "Interaction step cannot be completed: the model reported the objective "
                            "finished without performing a page interaction."
                        )
                    step_confirmed = True
                    break
                selected = next((item for item in available_tools if item.get("name") == semantic_name), None)
                native_name = native_tool_name(semantic_name, available_tools)
                if not native_name or selected is None:
                    raise PermissionError("Model selected a function outside the capability ontology")
                arguments = normalise_steel_tool_arguments(
                    native_name, dict(proposal.get("arguments") or {}),
                )
                selected_schema = selected.get("input_schema") or {}
                if "session_id" in (selected_schema.get("properties") or {}):
                    arguments["session_id"] = session_id
                else:
                    arguments.pop("session_id", None)
                result = await self.call_tool(native_name, arguments)
                selected_tool = native_name
                result_excerpt = " ".join(
                    str(item.get("text") or "") for item in (result.get("content") or [])
                    if isinstance(item, dict)
                )[:700]
                executed_calls.append({
                    "tool_name": native_name,
                    "semantic_function": semantic_name,
                    "status": "failed" if result.get("is_error") else "completed",
                    "reason": str(proposal.get("reason") or "")[:240],
                    "result_excerpt": result_excerpt,
                })
                if result.get("is_error"):
                    recovery_kind = classify_steel_recovery_error(result)
                    failed_arguments = dict(arguments)
                    if recovery_kind == "stale_element":
                        # Do not feed the invalid element reference back as a
                        # candidate for the next action.
                        failed_arguments.pop("target", None)
                        failed_arguments.pop("selector", None)
                        failed_arguments.pop("actions", None)
                        recovery_mode = recovery_kind
                    elif recovery_kind == "invalid_cursor":
                        failed_arguments.pop("cursor", None)
                        recovery_mode = recovery_kind
                    previous_failure = {
                        "tool_name": native_name, "arguments": failed_arguments,
                        "error": result.get("content") or [], "recovery_attempt": attempt + 1,
                        "recovery_kind": recovery_kind,
                    }
                    refreshed = await self.call_tool("steel_snapshot", {
                        "session_id": session_id, "interactive_only": False,
                    })
                    if refreshed.get("is_error"):
                        # The next model proposal may select inspect_page with a
                        # clean argument object; do not reuse a failed cursor.
                        previous_failure["snapshot_error"] = refreshed.get("content") or []
                        break
                    snapshot = refreshed
                    if recovery_mode is None:
                        recovery_mode = "general_failure"
                    continue

                if selected.get("mutates_page"):
                    interaction_executed = True
                    consecutive_read_only_calls = 0
                elif capability == "interact":
                    consecutive_read_only_calls += 1
                if capability != "interact":
                    break

                # Keep executing the same Decider-authored objective after the
                # first mutation. A click may only open a category or reveal a
                # variant; it does not prove the whole step is complete.
                # The model can explicitly finish only when the latest snapshot
                # supports the step objective; the independent verifier remains
                # the final completion gate.
                refreshed = await self.call_tool("steel_snapshot", {
                    "session_id": session_id, "interactive_only": False,
                })
                if refreshed.get("is_error"):
                    raise RuntimeError(str(refreshed.get("content") or [])[:1000])
                snapshot = refreshed
                previous_failure = None
                if recovery_mode is not None:
                    recovery_mode = None
                    recovery_attempts = 0

            if result.get("is_error"):
                raise RuntimeError(str(result.get("content") or [])[:1500])
            if capability == "interact" and not interaction_executed:
                attempted = ", ".join(call["tool_name"] for call in executed_calls) or "no tool"
                raise RuntimeError(
                    "Interaction step was not executed: the agent only inspected or resolved "
                    f"page elements ({attempted}) and did not perform a page interaction."
                )
            if capability == "interact" and not step_confirmed:
                raise RuntimeError(
                    "Adaptive execution budget exhausted before the agent confirmed the complete "
                    "step objective. The last browser state is retained for diagnosis; the step "
                    "must not be treated as completed."
                )

        after = await self.call_tool("steel_snapshot", {
            "session_id": session_id, "interactive_only": False,
        })
        if after["is_error"]:
            raise RuntimeError(str(after["content"])[:1000])
        return {
            "status": "completed", "session_id": session_id,
            "tool_name": selected_tool, "tool_calls": executed_calls, "tool_result": result, "snapshot": after,
        }

    async def release_task_session(self, task_id: str) -> None:
        session_id = self._sessions.pop(task_id, None)
        if not session_id:
            return
        if self.connected:
            try:
                await self.call_tool("steel_session_release", {"session_id": session_id})
            except Exception:
                pass
        # MCP release can leave an idle slot; free it through Steel's REST API too.
        if self.release_remote_session is not None:
            try:
                await self.release_remote_session(session_id)
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
        for task_id in list(self._sessions):
            await self.release_task_session(task_id)
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
