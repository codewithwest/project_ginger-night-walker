from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

from nightwalker.cart_progress import (
    cart_follow_up_actions,
    cart_intent,
    minicart_count,
    on_product_detail_page,
    product_out_of_stock,
)
from nightwalker.nimble_classifier import cart_mission_allows_alternate
from nightwalker.workflows import objective_requests_search

_SEARCH_TEXTBOX = re.compile(
    r"""(?im)^[ \t]*-[ \t]+(?P<role>textbox|searchbox)\s+"(?P<name>[^"]+)"\s+@(?P<ref>e\d+)(?P<rest>[^\n]*)"""
)
_FIELD_VALUE = re.compile(r'\[value="([^"]*)"\]')
_STEEL_REF = re.compile(r"^@?e\d+$", re.IGNORECASE)


def normalise_steel_target(target: Any) -> Any:
    """Steel resolves @eN against the snapshot; bare eN is treated as CSS and fails."""
    if not isinstance(target, str):
        return target
    value = target.strip()
    if _STEEL_REF.fullmatch(value):
        return "@" + value.lstrip("@")

    return target


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


def search_follow_up_actions(snapshot_text: str, query: str) -> list[dict[str, str]]:
    """Type the requested query into an open search box, then submit it with Enter.

    A click that only opens the search form leaves the textbox empty. That is not
    a completed search. Email and off-screen fields are ignored.
    """
    field: tuple[str, str] | None = None
    for match in _SEARCH_TEXTBOX.finditer(snapshot_text or ""):
        name = match.group("name").casefold()
        rest = match.group("rest") or ""
        if "off-screen" in rest.casefold() or "email" in name:
            continue
        if not any(token in name for token in ("search", "find", "query")):
            continue
        value_match = _FIELD_VALUE.search(rest)
        field = (match.group("ref"), value_match.group(1) if value_match else "")
        break
    if field is None:
        return []
    ref, current = field
    actions: list[dict[str, str]] = []
    if current.strip().casefold() != query.strip().casefold():
        actions.append({"action": "type", "target": "@" + ref, "value": query})
    actions.append({"action": "press", "target": "@" + ref, "value": "Enter"})
    return actions


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
        return {
            "is_error": bool(result.get("isError")),
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
        """Execute one authorised model-authored mission step using registered Steel tools."""
        # A navigate step starts the journey: always take a brand-new guest session.
        session_id = await self.session_for_task(task_id, fresh=(capability == "navigate"))
        if session_callback is not None:
            session_callback(session_id)
        product_scope = mission_objective or objective
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
            available_tools = [tool for tool in self._tools if tool["name"] in allowed]
            previous_failure: dict[str, Any] | None = None
            result = {"is_error": True, "content": [{"text": "No tool proposal was executed"}]}
            selected_tool = ""
            # A failed selector/DOM operation gets one fresh, model-directed
            # recovery proposal against the latest page state. Never replay the
            # same arguments blindly and never change the authorised objective.
            for attempt in range(2):
                snapshot_text = "\n".join(
                    str(item.get("text") or "")
                    for item in (snapshot.get("content") or [])
                    if isinstance(item, dict)
                )
                observation: dict[str, Any] = {
                    "url": url, "snapshot": snapshot, "session_id": session_id,
                    "previous_failure": previous_failure,
                }
                if (
                    on_product_detail_page(snapshot_text)
                    and product_out_of_stock(snapshot_text)
                    and cart_mission_allows_alternate(product_scope)
                ):
                    observation["guidance"] = (
                        "Current product is out of stock and the objective did not name an "
                        "exact product. Leave this PDP and open a different in-stock listing "
                        "to continue the cart journey."
                    )
                elif cart_intent(objective) in {"select", "add"} and not on_product_detail_page(snapshot_text):
                    observation["guidance"] = (
                        "Not on a product detail page yet. Search or open a catalogue product "
                        "with an Add to Cart / Quantity control before verifying stock."
                    )
                proposal = await propose_tool_call(
                    task_id=task_id, objective=objective, capability=capability,
                    observation=observation,
                    tools=available_tools, session_id=session_id,
                )
                selected_tool = str(proposal.get("tool_name") or "")
                if selected_tool not in allowed:
                    raise PermissionError("Model selected a Steel tool outside the capability allowlist")
                arguments = normalise_steel_tool_arguments(
                    selected_tool, dict(proposal.get("arguments") or {}),
                )
                selected_schema = next(
                    (tool.get("input_schema") or {} for tool in available_tools
                     if tool.get("name") == selected_tool),
                    {},
                )
                schema_properties = selected_schema.get("properties") or {}
                # Session ownership remains here; pass the ID only if the
                # selected tool's published schema explicitly declares it.
                if "session_id" in schema_properties:
                    arguments["session_id"] = session_id
                else:
                    arguments.pop("session_id", None)
                result = await self.call_tool(selected_tool, arguments)
                if not result["is_error"]:
                    break
                previous_failure = {
                    "tool_name": selected_tool,
                    "arguments": arguments,
                    "error": result.get("content") or [],
                    "recovery_attempt": attempt + 1,
                }
                if attempt == 0:
                    refreshed = await self.call_tool("steel_snapshot", {
                        "session_id": session_id, "interactive_only": False,
                    })
                    if refreshed["is_error"]:
                        break
                    snapshot = refreshed
            # A bare eN / stale model click must not skip the deterministic cart
            # follow-up, which re-reads live @eN refs from the current snapshot.
            model_failed = bool(result.get("is_error"))
            if model_failed and not (
                capability == "interact" and cart_intent(objective) in {
                    "remove", "confirm_empty", "verify_line", "add",
                }
            ):
                raise RuntimeError(str(result["content"])[:1500])
        search_submission = None
        query = objective_requests_search(objective) if capability == "interact" else None
        if query:
            # The model is allowed one action. Opening the search form is not the
            # search: type the exact query and press Enter before this step ends.
            search_submission = await self._submit_open_search(session_id, query)
            result.setdefault("content", []).extend(search_submission.pop("notes", []))
        cart_progress = None
        if capability in {"interact", "extract"} and cart_intent(objective):
            # Opening the minicart is only the first click. Removal, and reading
            # the cart line, continue in this same step. An add waits until the
            # cart count actually changes before the step is judged.
            cart_progress = await self._advance_cart(session_id, objective)
            if cart_progress:
                result.setdefault("content", []).extend(cart_progress.pop("notes", []))
        if capability != "navigate" and result.get("is_error"):
            cart_actions = list((cart_progress or {}).get("actions") or [])
            if not cart_actions:
                raise RuntimeError(str(result["content"])[:1500])
        after = await self.call_tool("steel_snapshot", {
            "session_id": session_id, "interactive_only": False,
        })
        if after["is_error"]:
            raise RuntimeError(str(after["content"])[:1000])
        return {
            "status": "completed", "session_id": session_id,
            "tool_name": selected_tool, "tool_result": result,
            "snapshot": after, "search_submission": search_submission,
            "cart_progress": cart_progress,
        }

    async def _submit_open_search(self, session_id: str, query: str) -> dict[str, Any]:
        """Type and submit when a search textbox is already open. Leave other pages untouched."""
        observed = await self.call_tool("steel_snapshot", {
            "session_id": session_id, "interactive_only": False,
        })
        text = "\n".join(
            str(item.get("text") or "") for item in observed.get("content") or [] if isinstance(item, dict)
        )
        actions = search_follow_up_actions(text, query)
        if not actions:
            return {
                "query": query, "performed": False, "status": "search_control_not_found",
                "actions": [], "notes": [],
            }
        completed: list[str] = []
        notes: list[dict[str, Any]] = []
        for action in actions:
            outcome = await self.call_tool(
                "steel_act",
                normalise_steel_tool_arguments(
                    "steel_act", {"session_id": session_id, **action},
                ),
            )
            notes.extend(outcome.get("content") or [])
            if outcome.get("is_error"):
                return {
                    "query": query, "performed": False, "status": "search_type_failed",
                    "actions": completed, "notes": notes,
                    "error": str(outcome.get("content") or "")[:500],
                }
            completed.append(action["action"])
        return {
            "query": query, "performed": True, "status": "submitted",
            "actions": completed, "notes": notes,
        }

    async def _page_text(self, session_id: str) -> tuple[str, list[dict[str, Any]]]:
        observed = await self.call_tool("steel_snapshot", {
            "session_id": session_id, "interactive_only": False,
        })
        notes = list(observed.get("content") or [])
        text = "\n".join(
            str(item.get("text") or "") for item in notes if isinstance(item, dict)
        )
        return text, notes

    async def _advance_cart(self, session_id: str, objective: str) -> dict[str, Any]:
        """Finish the cart action the single model click left incomplete."""
        intent = cart_intent(objective)
        text, notes = await self._page_text(session_id)
        completed: list[str] = []
        if (
            intent in {"select", "add"}
            and on_product_detail_page(text)
            and product_out_of_stock(text)
        ):
            return {
                "intent": intent, "actions": completed, "notes": notes,
                "out_of_stock": True,
            }
        if intent == "add" and (minicart_count(text) or 0) < 1:
            waited = await self._wait_for_text(session_id, "1 item", 12000)
            if waited:
                notes.extend(waited.get("content") or [])
                completed.append("wait")
            text, fresh_notes = await self._page_text(session_id)
            notes.extend(fresh_notes)
            if product_out_of_stock(text):
                return {
                    "intent": intent, "actions": completed, "notes": notes,
                    "out_of_stock": True,
                }
            return {"intent": intent, "actions": completed, "notes": notes}

        for _ in range(3):
            actions = cart_follow_up_actions(text, objective)
            if not actions:
                break
            failed = False
            for action in actions:
                outcome = await self.call_tool(
                    "steel_act",
                    normalise_steel_tool_arguments(
                        "steel_act", {"session_id": session_id, **action},
                    ),
                )
                notes.extend(outcome.get("content") or [])
                if outcome.get("is_error"):
                    failed = True
                    break
                completed.append(action["action"])
            if failed:
                break
            if intent == "remove" and actions[0].get("target", "").startswith("@e"):
                # The remove control was clicked. Wait until the cart reports empty.
                if "Remove" in text:
                    waited = await self._wait_for_text(session_id, "Cart is empty", 12000)
                    if waited:
                        notes.extend(waited.get("content") or [])
                        completed.append("wait")
            text, fresh = await self._page_text(session_id)
            notes.extend(fresh)
            if intent == "remove" and (minicart_count(text) == 0 or "cart is empty" in text.casefold()):
                break
            if intent != "remove":
                break
        return {"intent": intent, "actions": completed, "notes": notes}

    async def _wait_for_text(self, session_id: str, text: str, timeout_ms: int) -> dict[str, Any] | None:
        if "steel_wait_for" not in {tool["name"] for tool in self._tools}:
            return None
        try:
            return await self.call_tool("steel_wait_for", {
                "session_id": session_id, "text": text, "timeout_ms": timeout_ms,
            })
        except (RuntimeError, OSError, ValueError):
            return None

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
