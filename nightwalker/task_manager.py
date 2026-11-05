from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from nightwalker.contracts import AgentTask
from nightwalker.ollama_client import OllamaUnavailable
from nightwalker.orchestrator import Orchestrator


class TaskManager:
    def __init__(self, orchestrator: Orchestrator) -> None:
        self.orchestrator = orchestrator
        self.tasks: dict[str, dict[str, Any]] = {}
        self.running: dict[str, asyncio.Task[None]] = {}

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _log(self, task_id: str, message: str, level: str = "info") -> None:
        record = self.tasks[task_id]
        record["logs"].append({"at": self._now(), "level": level, "message": message})
        record["updated_at"] = self._now()

    def snapshot(self, task_id: str) -> dict[str, Any] | None:
        record = self.tasks.get(task_id)
        return {**record, "logs": list(record["logs"])} if record else None

    def list_tasks(self) -> list[dict[str, Any]]:
        return [self.snapshot(task_id) for task_id in reversed(self.tasks)]

    def submit(self, task: AgentTask) -> dict[str, Any]:
        if task.task_id in self.tasks:
            raise ValueError("task_id already exists")
        now = self._now()
        self.tasks[task.task_id] = {
            "task_id": task.task_id, "capability": task.capability.value,
            "objective": task.objective, "status": "queued", "created_at": now,
            "updated_at": now, "result": None, "error": None, "logs": [],
        }
        self._log(task.task_id, "Task accepted by Night Walker control plane")
        self.running[task.task_id] = asyncio.create_task(self._execute(task))
        return self.snapshot(task.task_id) or {}

    async def _execute(self, task: AgentTask) -> None:
        task_id = task.task_id
        self.tasks[task_id]["status"] = "running"
        self._log(task_id, f"Starting {task.capability.value} agent")
        try:
            self._log(task_id, "Compiling capability-scoped task context")
            self._log(task_id, "Sending bounded inference request to remote Ollama")
            result = await self.orchestrator.run_agent_task(task)
            self.tasks[task_id]["result"] = result.model_dump(mode="json")
            self.tasks[task_id]["status"] = result.status
            self._log(task_id, f"Agent returned status: {result.status}")
        except asyncio.CancelledError:
            self.tasks[task_id]["status"] = "stopped"
            self._log(task_id, "Task stopped by operator", "warning")
            raise
        except (OllamaUnavailable, RuntimeError, ValueError) as exc:
            self.tasks[task_id]["status"] = "failed"
            self.tasks[task_id]["error"] = str(exc)[:1000]
            self._log(task_id, f"Task failed: {exc}", "error")
        except Exception as exc:
            self.tasks[task_id]["status"] = "failed"
            self.tasks[task_id]["error"] = "Unexpected internal error"
            self._log(task_id, f"Unexpected internal error: {type(exc).__name__}", "error")
        finally:
            self.tasks[task_id]["updated_at"] = self._now()
            self.running.pop(task_id, None)

    async def stop(self, task_id: str) -> dict[str, Any] | None:
        if task_id not in self.tasks:
            return None
        task = self.running.get(task_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return self.snapshot(task_id)
