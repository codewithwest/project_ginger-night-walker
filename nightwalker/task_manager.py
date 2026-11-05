from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from nightwalker.contracts import AgentTask
from nightwalker.decision_contracts import DecisionStatus
from nightwalker.ollama_client import OllamaUnavailable
from nightwalker.orchestrator import Orchestrator


class TaskManager:
    def __init__(self, orchestrator: Orchestrator) -> None:
        self.orchestrator = orchestrator
        self.tasks: dict[str, dict[str, Any]] = {}
        self.running: dict[str, asyncio.Task[None]] = {}

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def _log(self, task_id: str, message: str, level: str = "info") -> None:
        record = self.tasks[task_id]
        record["logs"].append({"at": self._now(), "level": level, "message": message})
        record["updated_at"] = self._now()

    def snapshot(self, task_id: str) -> dict[str, Any] | None:
        record = self.tasks.get(task_id)
        return {**record, "logs": list(record["logs"])} if record else None

    def list_tasks(self) -> list[dict[str, Any]]:
        return [
            snapshot for task_id in reversed(self.tasks)
            if (snapshot := self.snapshot(task_id)) is not None
        ]

    def submit_natural_language(self, task_id: str, objective: str) -> dict[str, Any]:
        if task_id in self.tasks:
            raise ValueError("task_id already exists")
        now = self._now()
        self.tasks[task_id] = {
            "task_id": task_id,
            "capability": "auto",
            "objective": objective,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "result": None,
            "error": None,
            "plan": None,
            "current_step": None,
            "step_results": [],
            "logs": [],
        }
        self._log(task_id, "Natural-language task accepted; starting Decider")
        self.running[task_id] = asyncio.create_task(
            self._execute_decided(task_id, objective)
        )
        return self.snapshot(task_id) or {}

    async def _execute_decided(self, task_id: str, objective: str) -> None:
        record = self.tasks[task_id]
        record["status"] = "running"
        try:
            self._log(task_id, "Decider analysing objective and identifying the likely site")
            plan = await self.orchestrator.ollama.decide_task(objective)
            record["plan"] = plan.model_dump(mode="json")
            record["site_name"] = plan.site_name
            record["target_url"] = str(plan.target_url) if plan.target_url else None
            if plan.status != DecisionStatus.READY:
                record["status"] = plan.status.value
                self._log(task_id, plan.clarification_question or plan.summary, "warning")
                return

            self._log(
                task_id,
                f"Decider selected {len(plan.steps)} steps; starting isolated Playwright session",
            )
            for index, step in enumerate(plan.steps, start=1):
                record["current_step"] = {
                    "index": index,
                    "total": len(plan.steps),
                    "capability": step.capability.value,
                    "objective": step.objective,
                }
                self._log(
                    task_id,
                    f"Step {index}/{len(plan.steps)}: {step.capability.value} — {step.objective}",
                )

            result = await self.orchestrator.run_browser_workflow(task_id, objective, plan)
            record["step_results"] = [result]
            record["current_step"] = None
            record["result"] = result
            record["status"] = result.get("status", "failed")
            if result.get("evidence_id"):
                self._log(
                    task_id,
                    f"Browser run saved evidence {result['evidence_id']}",
                )
            if result.get("issues"):
                for issue in result["issues"]:
                    self._log(task_id, issue, "warning")
            self._log(task_id, f"Playwright workflow finished with status {record['status']}")
        except asyncio.CancelledError:
            record["status"] = "stopped"
            self._log(task_id, "Task stopped by operator", "warning")
            raise
        except (OllamaUnavailable, RuntimeError, ValueError, OSError, TypeError, KeyError) as exc:
            record["status"] = "failed"
            record["error"] = str(exc)[:1000]
            self._log(task_id, f"Task failed: {exc}", "error")
        except Exception as exc:  # noqa: BLE001 - keep task state observable
            record["status"] = "failed"
            record["error"] = "Unexpected internal error"
            self._log(task_id, f"Unexpected internal error: {type(exc).__name__}", "error")
        finally:
            record["current_step"] = None
            record["updated_at"] = self._now()
            self.running.pop(task_id, None)

    async def _execute(self, task: AgentTask) -> None:
        task_id = task.task_id
        self.tasks[task_id]["status"] = "running"
        self._log(task_id, f"Starting {task.capability.value} agent")
        try:
            result = await self.orchestrator.run_agent_task(task)
            self.tasks[task_id]["result"] = result.model_dump(mode="json")
            self.tasks[task_id]["status"] = result.status
            self._log(task_id, f"Agent returned status: {result.status}")
        except asyncio.CancelledError:
            self.tasks[task_id]["status"] = "stopped"
            self._log(task_id, "Task stopped by operator", "warning")
            raise
        except (OllamaUnavailable, RuntimeError, ValueError, OSError, TypeError, KeyError) as exc:
            self.tasks[task_id]["status"] = "failed"
            self.tasks[task_id]["error"] = str(exc)[:1000]
            self._log(task_id, f"Task failed: {exc}", "error")
        except Exception as exc:  # noqa: BLE001 - keep task state observable on unexpected failures
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
