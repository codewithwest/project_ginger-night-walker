from __future__ import annotations

import asyncio
import os
import re
from datetime import UTC, datetime
from typing import Any

from nightwalker.a2a import A2ABus
from nightwalker.agent_allocator import AgentAllocator
from nightwalker.contracts import AgentTask
from nightwalker.decision_contracts import DecisionPlan, DecisionStatus, normalise_decision_plan
from nightwalker.goal_state import GoalStateManager
from nightwalker.ollama_client import OllamaUnavailable
from nightwalker.plan_execution import complete_step, fail_step, initialise_plan, ready_steps, start_step
from nightwalker.orchestrator import Orchestrator
from nightwalker.research_guard import ResearchLoopGuard, canonical_url_key
from nightwalker.research_relevance import (
    focused_research_queries,
    rank_research_candidates,
    source_relevance,
)
from nightwalker.scratchpad import TaskScratchpad
from nightwalker.task_checklist import TaskChecklist
from nightwalker.task_state_store import TaskStateStore
from nightwalker.workflows import WorkflowType, build_workflow_blueprint, classify_workflow, target_url_from_objective


class TaskManager:
    def __init__(self, orchestrator: Orchestrator) -> None:
        self.orchestrator = orchestrator
        self.tasks: dict[str, dict[str, Any]] = {}
        self.running: dict[str, asyncio.Task[None]] = {}
        self.scratchpad = TaskScratchpad()
        self.checklist = TaskChecklist()
        self.state_store = TaskStateStore()
        self.max_rounds = 30
        self.a2a_bus = A2ABus()
        self.agent_allocator = AgentAllocator(self.a2a_bus)
        self.shutting_down = False

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _max_audit_pages() -> int:
        try:
            return max(1, min(1000, int(os.getenv("AUDIT_MAX_PAGES", "200"))))
        except ValueError:
            return 200

    def _log(self, task_id: str, message: str, level: str = "info") -> None:
        record = self.tasks[task_id]
        event = {"at": self._now(), "level": level, "message": message}
        record["logs"].append(event)
        record["progress_events"].append({**event, "event_type": "progress", "status": record["status"]})
        record["updated_at"] = self._now()
        self._persist(task_id)

    def _note(self, task_id: str, entry: dict[str, Any]) -> None:
        note = self.scratchpad.append(task_id, entry)
        self.tasks[task_id]["scratchpad"].append(note)
        self.tasks[task_id]["updated_at"] = self._now()
        self._persist(task_id)

    def _persist(self, task_id: str) -> None:
        record = self.tasks.get(task_id)
        if record is None:
            return
        try:
            try:
                self.checklist.write(record)
            except (OSError, TypeError, ValueError) as checklist_exc:
                record["checklist_error"] = str(checklist_exc)[:500]
            self.state_store.save(record)
        except (OSError, TypeError, ValueError) as exc:
            if not record.get("persistence_error"):
                message = f"Durable task-state write failed: {exc}"
                record["persistence_error"] = message[:500]
                record.setdefault("progress_events", []).append({
                    "at": self._now(), "event_type": "persistence_warning",
                    "status": record.get("status", "unknown"), "message": message[:500],
                })

    def _save_checkpoint(
        self, task_id: str, initial_plan: DecisionPlan, *, sources: list[dict[str, Any]],
        findings: list[dict[str, Any]], evidence: list[dict[str, Any]],
        visited_urls: list[str], candidates: list[dict[str, str]],
        structured_data: dict[str, list[Any]], limitations: list[str],
        loop_guard: ResearchLoopGuard, round_count: int, next_url: str,
        next_assignment: str, research_complete: bool = False,
        round_in_progress: bool = False,
    ) -> None:
        # Revalidate the complete object before serialisation. This also repairs
        # legacy/in-memory plans restored from older snapshots where URL and step
        # fields may still be plain strings/dicts instead of typed Pydantic values.
        initial_plan = normalise_decision_plan(initial_plan)
        self.tasks[task_id]["checkpoint"] = {
            "version": 1, "initial_plan": initial_plan.model_dump(mode="json"),
            "sources": sources, "findings": findings, "evidence": evidence,
            "visited_urls": visited_urls, "candidates": candidates,
            "structured_data": structured_data, "limitations": limitations,
            "loop_guard": loop_guard.snapshot(), "round_count": round_count,
            "next_url": next_url, "next_assignment": next_assignment,
            "research_complete": research_complete,
            "round_in_progress": round_in_progress,
        }
        self._persist(task_id)

    async def shutdown(self) -> None:
        """Pause active tasks for restart instead of treating process shutdown as user cancel."""
        self.shutting_down = True
        active = [task for task in self.running.values() if not task.done()]
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        for task_id, record in self.tasks.items():
            if record.get("status") == "running":
                record["status"] = "queued"
                record["current_step"] = None
                self._log(task_id, "Service is shutting down; task queued for checkpoint recovery", "warning")
            elif record.get("status") == "queued":
                self._persist(task_id)

    async def restore_and_resume(self) -> int:
        """Restore saved tasks and resume work interrupted by process/container restart."""
        self.shutting_down = False
        restored = 0
        for record in self.state_store.load_all():
            task_id = record["task_id"]
            if task_id in self.tasks:
                continue
            record.setdefault("logs", [])
            record.setdefault("progress_events", [])
            record.setdefault("scratchpad", self.scratchpad.read(task_id))
            record.setdefault("checklist_markdown", self.checklist.read(task_id) or "")
            record.setdefault("follow_up_instructions", [])
            record.setdefault("step_results", [])
            record.setdefault("checkpoint", None)
            record.setdefault("execution_blueprint", None)
            record.setdefault("execution_plan", None)
            record.setdefault("workflow_type", classify_workflow(str(record.get("objective") or "")).value)
            if record.get("execution_plan") is None and isinstance(record.get("execution_blueprint"), dict):
                record["execution_plan"] = initialise_plan(record["execution_blueprint"])
            record.setdefault("agent_assignments", [])
            record.setdefault("a2a_messages", [])
            self.a2a_bus.restore(task_id, record.get("a2a_messages", []))
            self.tasks[task_id] = record
            checkpoint = record.get("checkpoint")
            status = record.get("status")
            saved_plan = record.get("plan")
            if not isinstance(saved_plan, dict):
                saved_plan = {}
            if record.get("execution_plan") is None and saved_plan.get("steps"):
                migrated_subtasks = []
                previous_step_id = None
                for index, step in enumerate(saved_plan.get("steps") or [], 1):
                    if not isinstance(step, dict):
                        continue
                    step_id = str(step.get("step_id") or f"legacy-step-{index}")
                    capability = step.get("capability") or "extract"
                    migrated_subtasks.append({
                        "subtask_id": step_id,
                        "objective": str(step.get("objective") or step_id),
                        "capability": str(getattr(capability, "value", capability)),
                        "depends_on": [previous_step_id] if previous_step_id else [],
                        "success_criteria": [],
                        "expected_output": "Evidence-backed output for this step",
                    })
                    previous_step_id = step_id
                if migrated_subtasks:
                    record["execution_blueprint"] = record.get("execution_blueprint") or {
                        "version": 1, "subtasks": migrated_subtasks, "success_criteria": [],
                    }
                    record["execution_plan"] = initialise_plan(record["execution_blueprint"])
            # Older Decider versions sometimes stored the website-only prompt
            # in summary rather than clarification_question. Check both fields so
            # those persisted tasks are automatically re-planned after an upgrade.
            saved_question = " ".join((
                str(saved_plan.get("clarification_question") or ""),
                str(saved_plan.get("summary") or ""),
            )).casefold()
            legacy_site_prompt = (
                status == DecisionStatus.NEEDS_CLARIFICATION.value
                and any(term in saved_question for term in (
                    "website", "web site", "exact website", "url", "domain",
                    "which site", "what site",
                ))
            )
            if status in {"queued", "running"} or legacy_site_prompt:
                if legacy_site_prompt:
                    record["plan"] = None
                    record["error"] = None
                    self._log(task_id, "Retrying an old website-only clarification with automatic search discovery", "warning")
                if checkpoint:
                    try:
                        DecisionPlan.model_validate(checkpoint["initial_plan"])
                        ResearchLoopGuard.from_snapshot(checkpoint.get("loop_guard"))
                        int(checkpoint.get("round_count", 0))
                    except (AttributeError, KeyError, TypeError, ValueError) as exc:
                        record["status"] = "failed"
                        record["error"] = f"Saved research checkpoint is invalid: {exc}"[:500]
                        self._log(task_id, record["error"], "error")
                        continue
                record["status"] = "queued"
                record["current_step"] = None
                self._log(task_id, "Service restarted; restoring task checkpoint and resuming", "warning")
                record["progress_events"].append({
                    "at": self._now(), "event_type": "task_resumed",
                    "status": "queued",
                    "message": "Recovered durable task state after service restart.",
                })
                self.running[task_id] = asyncio.create_task(
                    self._execute_decided(
                        task_id,
                        str(record.get("execution_objective") or record.get("objective") or ""),
                        resume=True,
                    )
                )
                restored += 1
            else:
                # Re-evaluate completed/blocked historical tasks against the current
                # deterministic verifier after an upgrade; do not re-browse their sources.
                saved_result = record.get("result") or {}
                if status == "blocked" and isinstance(saved_result, dict) and record.get("goal_state"):
                    GoalStateManager.apply_verified_plan_results(
                        record["goal_state"], record.get("execution_plan"),
                        saved_result.get("evidence") or [],
                        record.get("plan_outputs"),
                        saved_result.get("structured_data") or {},
                    )
                    saved_result["goal_state"] = record["goal_state"]
                    remaining = record["goal_state"].get("remaining", [])
                    if not remaining and saved_result.get("sources"):
                        saved_result["limitations"] = [
                            item for item in saved_result.get("limitations", [])
                            if not str(item).startswith("Success criteria still lacking directly quoted evidence:")
                        ]
                        record["status"] = "completed"
                        record["error"] = None
                        self._log(task_id, "Historical task re-verified from its captured evidence and completed mission plan.")
                self._persist(task_id)
        return restored

    def send_a2a_message(self, task_id: str, message) -> dict[str, Any] | None:
        record = self.tasks.get(task_id)
        if record is None:
            return None
        assignments = record.get("agent_assignments", [])
        allowed_agents = {item.get("agent_id") for item in assignments}
        allowed_agents.add("night-walker-orchestrator")
        if message.parent_task_id != task_id:
            raise ValueError("A2A message parent task does not match the route")
        if message.sender_agent_id not in allowed_agents or message.recipient_agent_id not in allowed_agents:
            raise ValueError("A2A sender and recipient must be allocated agents for this task")
        by_agent = {item.get("agent_id"): item for item in assignments}
        known_subtasks = {item.get("subtask_id") for item in assignments}
        if message.subtask_id not in known_subtasks:
            raise ValueError("A2A message references an unknown subtask")
        expected_capability = (
            by_agent[message.sender_agent_id].get("capability")
            if message.recipient_agent_id == "night-walker-orchestrator"
            else by_agent[message.recipient_agent_id].get("capability")
        )
        if expected_capability and message.capability.value != expected_capability:
            raise ValueError("A2A message capability does not match the allocated recipient")
        self.a2a_bus.send(message)
        record["a2a_messages"] = self.a2a_bus.history(task_id)
        self._log(task_id, f"A2A {message.message_type.value}: {message.sender_agent_id} → {message.recipient_agent_id} ({message.subtask_id})")
        self._persist(task_id)
        return {"task_id": task_id, "messages": record["a2a_messages"]}

    def snapshot(self, task_id: str) -> dict[str, Any] | None:
        record = self.tasks.get(task_id)
        if record is None:
            return None
        snapshot = {**record, "logs": list(record.get("logs", []))}
        plan = record.get("execution_plan") or {}
        steps = plan.get("steps") or []
        snapshot["plan_progress"] = {
            "status": plan.get("status", "pending"),
            "completed": sum(1 for step in steps if step.get("status") == "completed"),
            "total": len(steps),
        } if steps else None
        return snapshot

    def list_tasks(self) -> list[dict[str, Any]]:
        """Return lightweight mission cards; full task state is fetched only when opened."""
        fields = (
            "task_id", "objective", "capability", "status", "created_at",
            "updated_at", "site_name", "current_step", "round", "max_rounds",
            "plan_progress",
        )
        summaries = []
        for record in self.tasks.values():
            summary = {key: record.get(key) for key in fields if key != "plan_progress"}
            plan = record.get("execution_plan") or {}
            steps = plan.get("steps") or []
            summary["plan_progress"] = {
                "status": plan.get("status", "pending"),
                "completed": sum(1 for step in steps if step.get("status") == "completed"),
                "total": len(steps),
            } if steps else None
            summaries.append(summary)
        return sorted(
            summaries, key=lambda item: item.get("created_at", ""), reverse=True
        )

    def submit_natural_language(self, task_id: str, objective: str) -> dict[str, Any]:
        if task_id in self.tasks:
            raise ValueError("task_id already exists")
        now = self._now()
        workflow_type = classify_workflow(objective)
        task_page_budget = self._max_audit_pages() if workflow_type in {WorkflowType.SEO, WorkflowType.QA, WorkflowType.SEO_QA} else self.max_rounds
        self.tasks[task_id] = {
            "task_id": task_id,
            "capability": "auto",
            "workflow_type": workflow_type.value,
            "objective": objective,
            "execution_objective": objective,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "result": None,
            "error": None,
            "plan": None,
            "current_step": None,
            "step_results": [],
            "logs": [],
            "progress_events": [],
            "round": 0,
            "max_rounds": task_page_budget,
            "scratchpad": self.scratchpad.read(task_id),
            "checklist_markdown": "",
            "follow_up_instructions": [],
            "goal_state": None,
            "checkpoint": None,
            "execution_blueprint": None,
            "execution_plan": None,
            "agent_assignments": [],
            "a2a_messages": [],
        }
        self._persist(task_id)
        self._log(task_id, "Natural-language task accepted; starting Decider")
        self.running[task_id] = asyncio.create_task(
            self._execute_decided(task_id, objective)
        )
        return self.snapshot(task_id) or {}

    async def _execute_decided(
        self, task_id: str, objective: str, resume: bool = False
    ) -> None:
        record = self.tasks[task_id]
        record["status"] = "running"
        if not resume or not record.get("execution_objective"):
            record["execution_objective"] = objective
        checkpoint = record.get("checkpoint") if resume else None
        if checkpoint:
            initial_plan = normalise_decision_plan(checkpoint["initial_plan"])
            sources = list(checkpoint.get("sources", []))
            findings = list(checkpoint.get("findings", []))
            evidence = list(checkpoint.get("evidence", []))
            visited_urls = list(checkpoint.get("visited_urls", []))
            candidates = list(checkpoint.get("candidates", []))
            structured_data = dict(checkpoint.get("structured_data", {}))
            limitations = list(checkpoint.get("limitations", []))
            loop_guard = ResearchLoopGuard.from_snapshot(checkpoint.get("loop_guard"))
            round_count = int(checkpoint.get("round_count", 0))
            next_url = str(checkpoint.get("next_url") or "")
            next_assignment = str(checkpoint.get("next_assignment") or "Resume research")
            research_complete = bool(checkpoint.get("research_complete", False))
            resume_in_flight = bool(checkpoint.get("round_in_progress", False))
            record["low_information_rounds"] = int(record.get("low_information_rounds", 0))
            effective_objective = self._effective_objective(task_id, objective)
            if next_url and not rank_research_candidates(
                effective_objective, [{"url": next_url, "label": next_assignment}]
            ):
                self._log(task_id, "Checkpoint recovery skipped the previously selected off-topic URL", "warning")
                next_url = ""
                resume_in_flight = False
            if not next_url and not research_complete:
                available = loop_guard.filter_candidates(
                    rank_research_candidates(effective_objective, candidates), visited_urls
                )
                if not available:
                    # Older checkpoints may contain only unrelated discovery links.
                    # Re-seed the remaining rounds from the original focused query set.
                    from urllib.parse import quote

                    queries = focused_research_queries(
                        effective_objective, record.get("research_queries", [])
                    )
                    candidates.extend({
                        "url": "https://www.bing.com/search?q=" + quote(query),
                        "label": "Recovery research query: " + query[:180],
                    } for query in queries)
                    available = loop_guard.filter_candidates(
                        rank_research_candidates(effective_objective, candidates), visited_urls
                    )
                    if available:
                        self._log(task_id, "Checkpoint recovery discarded unrelated links and re-seeded focused research queries", "warning")
                if available:
                    next_url = available[0]["url"]
                    next_assignment = "Resume: inspect the next relevant discovered source"
                else:
                    research_complete = True
            self._log(task_id, f"Restored research checkpoint at round {round_count}; {len(sources)} sources already captured")
        else:
            sources: list[dict[str, Any]] = []
            findings: list[dict[str, Any]] = []
            evidence: list[dict[str, Any]] = []
            visited_urls: list[str] = []
            candidates: list[dict[str, str]] = []
            loop_guard = ResearchLoopGuard(max_attempts_per_url=2)
            structured_data: dict[str, list[Any]] = {}
            limitations: list[str] = []
            next_url = ""
            next_assignment = ""
            round_count = 0
            research_complete = False
            resume_in_flight = False
            record["low_information_rounds"] = 0
        try:
            if not checkpoint:
                workflow = WorkflowType(record.get("workflow_type") or classify_workflow(objective).value)
                if workflow == WorkflowType.UNSUPPORTED:
                    message = "This Night Walker beta currently supports SEO audits and website/application QA. General research and other task types are deferred."
                    record["status"] = "blocked"
                    record["result"] = {
                        "schema_version": "1.0", "workflow_type": workflow.value,
                        "answer": message, "findings": [], "sources": [], "evidence": [],
                        "structured_data": {}, "limitations": [message], "suggested_memory": [],
                    }
                    record["error"] = None
                    self._log(task_id, "Task is outside the initial SEO/QA product scope; no general-research fallback was started.", "warning")
                    self._persist(task_id)
                    return

                explicit_target = target_url_from_objective(objective)
                if not explicit_target:
                    question = "Which website URL should I audit or test?"
                    record["status"] = "needs_clarification"
                    record["plan"] = {
                        "status": "needs_clarification",
                        "summary": "An SEO or QA workflow requires a target website.",
                        "clarification_question": question,
                        "steps": [],
                    }
                    self._log(task_id, question, "warning")
                    self._persist(task_id)
                    return

                self._log(task_id, "Preparing dedicated " + workflow.value.upper() + " workflow")
                blueprint = build_workflow_blueprint(objective, workflow)
                record["execution_blueprint"] = blueprint.model_dump(mode="json")
                record["execution_plan"] = initialise_plan(record["execution_blueprint"])
                record["workflow_summary"] = blueprint.interpretation
                criteria = [item.description for item in blueprint.success_criteria]
                record["research_queries"] = []
                from urllib.parse import urlsplit
                first_subtask = blueprint.subtasks[0]
                initial_plan = DecisionPlan.model_validate({
                    "status": "ready",
                    "summary": blueprint.interpretation,
                    "site_name": urlsplit(explicit_target).hostname,
                    "target_url": explicit_target,
                    "steps": [{
                        "step_id": first_subtask.subtask_id,
                        "capability": first_subtask.capability.value,
                        "objective": first_subtask.objective,
                    }],
                })
                self._log(task_id, workflow.value.upper() + " workflow planned: " + str(len(blueprint.success_criteria)) + " acceptance criteria and " + str(len(blueprint.subtasks)) + " workflow steps")
                self._persist(task_id)
                research_queries = []
                if initial_plan.status == DecisionStatus.READY and research_queries and not explicit_target:
                    from urllib.parse import quote
                    initial_plan = DecisionPlan.model_validate({
                        **initial_plan.model_dump(mode="python"),
                        "target_url": "https://www.bing.com/search?q=" + quote(research_queries[0]),
                        "site_name": "Web search",
                        "summary": "Search using concise, objective-derived queries, then inspect independent sources.",
                    })
                    candidates.extend({
                        "url": "https://www.bing.com/search?q=" + quote(query),
                        "label": "Targeted research query: " + query[:180],
                    } for query in research_queries[1:])
                    self._log(task_id, f"Research planner generated {len(research_queries)} focused search queries; avoiding the full instruction prompt as a search term")
                initial_plan = DecisionPlan.model_validate(initial_plan.__dict__)
                record["research_queries"] = research_queries
                record["plan"] = initial_plan.model_dump(mode="json")
                if blueprint is None:
                    proposed_steps = [step.objective for step in initial_plan.steps]
                    try:
                        criteria = await self.orchestrator.ollama.initialise_goal_criteria(objective, proposed_steps, task_id=task_id)
                    except (OllamaUnavailable, AttributeError) as exc:
                        criteria = proposed_steps or [initial_plan.summary or objective]
                        self._log(task_id, f"Goal criteria model unavailable; using planner steps: {exc}", "warning")
                record["goal_state"] = GoalStateManager.initialise(objective, criteria)
                if record.get("execution_plan") is None:
                    fallback_subtasks = []
                    previous_id = None
                    for index, step in enumerate(initial_plan.steps, 1):
                        step_id = str(getattr(step, "step_id", None) or f"step-{index}")
                        step_capability = getattr(step, "capability", None) or "extract"
                        step_capability = getattr(step_capability, "value", step_capability)
                        fallback_subtasks.append({
                            "subtask_id": step_id,
                            "objective": str(getattr(step, "objective", None) or step_id),
                            "capability": str(step_capability),
                            "depends_on": [previous_id] if previous_id else [],
                            "success_criteria": [],
                            "expected_output": "Evidence-backed output for this step",
                        })
                        previous_id = step_id
                    if fallback_subtasks:
                        record["execution_plan"] = initialise_plan({"version": 1, "subtasks": fallback_subtasks})
                self._log(task_id, f"Goal State Manager established {len(criteria)} success criteria")
                if initial_plan.status == DecisionStatus.READY and blueprint is not None:
                    record["agent_assignments"] = self.agent_allocator.assign(task_id, blueprint)
                    record["a2a_messages"] = self.a2a_bus.history(task_id)
                    self._log(task_id, f"Allocated {len(record['agent_assignments'])} capability agents and dispatched A2A assignments")
                record["site_name"] = initial_plan.site_name
                record["target_url"] = str(initial_plan.target_url) if initial_plan.target_url else None
                if initial_plan.status != DecisionStatus.READY:
                    record["status"] = initial_plan.status.value
                    question = initial_plan.clarification_question or initial_plan.summary
                    self._log(task_id, question, "warning")
                    record["progress_events"].append({
                        "at": self._now(), "event_type": "clarification",
                        "status": record["status"], "message": question,
                    })
                    self._note(task_id, {
                        "round": 0, "assignment": "Resolve research objective",
                        "status": "needs_clarification", "findings": [],
                        "remaining_work": [question], "seed_for_next_round": [objective],
                    })
                    return
                self._note(task_id, {
                    "round": 0, "assignment": "Resolve research objective and starting URL",
                    "status": "completed", "target_url": record["target_url"],
                    "findings": [initial_plan.summary],
                    "seed_for_next_round": ["Inspect starting page and discover relevant sources"],
                })
                next_url = str(initial_plan.target_url)
                next_assignment = "Inspect the starting page and discover relevant sources"
                self._save_checkpoint(
                    task_id, initial_plan, sources=sources, findings=findings,
                    evidence=evidence, visited_urls=visited_urls, candidates=candidates,
                    structured_data=structured_data, limitations=limitations,
                    loop_guard=loop_guard, round_count=round_count,
                    next_url=next_url, next_assignment=next_assignment,
                )
            while next_url and (round_count < int(record.get("max_rounds", self.max_rounds)) or resume_in_flight):
                if resume_in_flight:
                    resume_in_flight = False
                else:
                    round_count += 1
                record["round"] = round_count
                execution_plan = record.get("execution_plan")
                active_plan_step = None
                resume_plan_source = False
                if isinstance(execution_plan, dict):
                    available_plan_steps = ready_steps(execution_plan)
                    if str(record.get("workflow_type") or "") in {"seo", "qa", "seo_qa"}:
                        # Final synthesis is a task-level acceptance step, not a page-level
                        # action; leave it pending until the crawl is exhausted.
                        available_plan_steps = [
                            step for step in available_plan_steps
                            if not str(step.get("step_id", "")).endswith(("seo-synthesise", "qa-synthesise"))
                        ]
                    if available_plan_steps:
                        active_plan_step = start_step(execution_plan, available_plan_steps[0]["step_id"])
                        record["active_plan_step_id"] = active_plan_step["step_id"]
                        plan_step_urls = record.setdefault("plan_step_urls", {})
                        saved_step_url = plan_step_urls.get(active_plan_step["step_id"])
                        long_running_crawl_step = str(active_plan_step["step_id"]).endswith(
                            ("seo-crawl", "qa-crawl", "audit-crawl")
                        )
                        if saved_step_url and not long_running_crawl_step:
                            next_url = saved_step_url
                            resume_plan_source = active_plan_step.get("status") == "in_progress"
                        next_assignment = active_plan_step["objective"]
                        self._log(task_id, f"Plan step started: {active_plan_step['step_id']} — {active_plan_step['objective']}")
                record["current_step"] = {
                    "index": round_count, "total": int(record.get("max_rounds", self.max_rounds)),
                    "capability": "research", "objective": next_assignment,
                    "url": next_url,
                    "plan_step_id": active_plan_step.get("step_id") if active_plan_step else None,
                }
                self._persist(task_id)
                self._log(task_id, f"Round {round_count}/{record.get('max_rounds', self.max_rounds)}: {next_assignment}")
                attempt = loop_guard.begin(next_url, next_assignment)
                already_visited = loop_guard.is_visited(next_url, visited_urls) and not resume_plan_source
                self._save_checkpoint(
                    task_id, initial_plan, sources=sources, findings=findings,
                    evidence=evidence, visited_urls=visited_urls, candidates=candidates,
                    structured_data=structured_data, limitations=limitations,
                    loop_guard=loop_guard, round_count=round_count,
                    next_url=next_url, next_assignment=next_assignment,
                    round_in_progress=True,
                )
                if attempt["should_skip"] or already_visited:
                    reason = (
                        f"Already inspected canonical source; skipped duplicate: {next_url}"
                        if already_visited else
                        f"Repeated source/action detected; skipped after {attempt['attempt'] - 1} attempts: {next_url}"
                    )
                    limitations.append(reason)
                    self._log(task_id, reason, "warning")
                    self._note(task_id, {
                        "round": round_count, "assignment": next_assignment,
                        "status": "skipped_repeated_action", "sources": [{"url": next_url}],
                        "remaining_work": [reason], "loop_guard": loop_guard.snapshot(),
                        "seed_for_next_round": ["Choose a different undiscovered source"],
                    })
                    if str(record.get("workflow_type") or "") in {"seo", "qa", "seo_qa"}:
                        available = loop_guard.filter_candidates(
                            candidates, visited_urls,
                            limit=int(record.get("max_rounds", self.max_rounds)),
                        )
                    else:
                        available = loop_guard.filter_candidates(
                            rank_research_candidates(self._effective_objective(task_id, objective), candidates),
                            visited_urls,
                        )
                    if not available:
                        next_url = ""
                        research_complete = True
                        self._save_checkpoint(
                            task_id, initial_plan, sources=sources, findings=findings,
                            evidence=evidence, visited_urls=visited_urls, candidates=candidates,
                            structured_data=structured_data, limitations=limitations,
                            loop_guard=loop_guard, round_count=round_count,
                            next_url="", next_assignment=next_assignment, research_complete=True,
                        )
                        break
                    next_url = available[0]["url"]
                    next_assignment = "Recovery: inspect a different source after repeat detection"
                    self._save_checkpoint(
                        task_id, initial_plan, sources=sources, findings=findings,
                        evidence=evidence, visited_urls=visited_urls, candidates=candidates,
                        structured_data=structured_data, limitations=limitations,
                        loop_guard=loop_guard, round_count=round_count,
                        next_url=next_url, next_assignment=next_assignment,
                    )
                    continue

                if not isinstance(execution_plan, dict) or active_plan_step is None:
                    limitations.append("Browser execution was not started because no dependency-ready mission plan step authorises it.")
                    self._log(task_id, "Execution blocked: no active authorised mission plan step")
                    self._persist(task_id)
                    break
                one_page_plan = normalise_decision_plan({
                    "status": initial_plan.status,
                    "summary": initial_plan.summary,
                    "site_name": initial_plan.site_name,
                    "target_url": next_url,
                    "clarification_question": initial_plan.clarification_question,
                    "steps": [{
                        "step_id": active_plan_step["step_id"],
                        "capability": active_plan_step["capability"],
                        "objective": active_plan_step["objective"][:500],
                    }],
                })
                effective_objective = self._effective_objective(task_id, objective)
                result = await self.orchestrator.run_browser_workflow(
                    task_id,
                    effective_objective,
                    one_page_plan,
                    authorized_step=active_plan_step,
                )
                round_evidence = []
                if result.get("status") not in {"completed", "blocked"}:
                    issue_text = "; ".join(result.get("issues", [])) or "Source inspection failed"
                    limitations.append(f"{next_url}: {issue_text}")
                    loop_guard.record_outcome(next_url, next_assignment, result.get("status", "failed"), issue_text)
                    self._note(task_id, {
                        "round": round_count, "assignment": next_assignment,
                        "status": result.get("status", "failed"),
                        "sources": [{"url": next_url}],
                        "remaining_work": [issue_text],
                        "loop_guard": loop_guard.snapshot(),
                        "seed_for_next_round": ["Restore browser connectivity before resuming research"],
                    })
                    infrastructure_error = any(
                        marker in issue_text.casefold()
                        for marker in (
                            "econnrefused", "connect_over_cdp", "browser runtime is not started",
                            "steel session", "websocket error",
                        )
                    )
                    if infrastructure_error:
                        blocker = (
                            "Browser infrastructure is unavailable; research paused to avoid "
                            "repeating the same connection failure across rounds. " + issue_text[:500]
                        )
                        limitations.append(blocker)
                        self._log(task_id, blocker, "error")
                        record["progress_events"].append({
                            "at": self._now(), "event_type": "infrastructure_blocked",
                            "status": "blocked", "round": round_count, "message": blocker,
                        })
                        next_url = ""
                        research_complete = True
                        self._save_checkpoint(
                            task_id, initial_plan, sources=sources, findings=findings,
                            evidence=evidence, visited_urls=visited_urls, candidates=candidates,
                            structured_data=structured_data, limitations=limitations,
                            loop_guard=loop_guard, round_count=round_count,
                            next_url="", next_assignment="Restore browser connectivity", research_complete=True,
                        )
                        break
                    candidates.extend(result.get("candidate_urls", []))
                    candidates = loop_guard.filter_candidates(rank_research_candidates(effective_objective, candidates), visited_urls)
                    next_url = ""
                else:
                    final_url = result.get("final_url") or next_url
                    loop_guard.record_outcome(next_url, next_assignment, "completed")
                    visited_urls.append(final_url)
                    if not loop_guard.is_visited(next_url, visited_urls):
                        visited_urls.append(next_url)
                    # Search engines are discovery surfaces, never research evidence.
                    from urllib.parse import urlsplit
                    search_host = (urlsplit(final_url).hostname or "").lower()
                    is_search_page = (
                        search_host in {"bing.com", "google.com", "duckduckgo.com"}
                        or search_host.endswith((".bing.com", ".google.com", ".duckduckgo.com"))
                    )
                    if is_search_page:
                        candidates.extend(result.get("candidate_urls", []))
                        candidates = loop_guard.filter_candidates(rank_research_candidates(self._effective_objective(task_id, objective), candidates), visited_urls)
                        available = loop_guard.filter_candidates(rank_research_candidates(self._effective_objective(task_id, objective), candidates), visited_urls)
                        next_url = available[0]["url"] if available else ""
                        next_assignment = (
                            "Inspect an actual result destination discovered from search: "
                            + available[0].get("label", "relevant source")
                            if available else "No relevant destination discovered from search"
                        )
                        self._note(task_id, {
                            "round": round_count, "assignment": "Search-result discovery only",
                            "status": "completed", "actions": [final_url],
                            "findings": [], "sources": [],
                            "discovered_source_count": len(candidates),
                            "seed_for_next_round": ["Inspect destination pages; do not cite this search page as evidence"],
                        })
                        self._log(task_id, f"Search results inspected for discovery only; {len(candidates)} candidate destinations available.")
                        self._save_checkpoint(
                            task_id, initial_plan, sources=sources, findings=findings,
                            evidence=evidence, visited_urls=visited_urls, candidates=candidates,
                            structured_data=structured_data, limitations=limitations,
                            loop_guard=loop_guard, round_count=round_count,
                            next_url=next_url, next_assignment=next_assignment,
                            research_complete=not bool(next_url),
                        )
                        continue
                    source_records = result.get("sources", [])
                    source = dict(source_records[0]) if source_records else {
                        "url": final_url, "title": result.get("title", ""),
                        "retrieved_at": self._now(),
                    }
                    round_evidence = []
                    for item in result.get("evidence", []):
                        captured = dict(item)
                        captured["evidence_id"] = result.get("evidence_id") or captured.get("evidence_id")
                        # Full immutable captures stay in EvidenceStore; task polling returns
                        # only a compact excerpt to keep payload and context bounded.
                        captured["captured_text"] = str(captured.get("captured_text", ""))[:1500]
                        round_evidence.append(captured)

                    workflow_value = str(record.get("workflow_type") or "")
                    if workflow_value in {"seo", "qa", "seo_qa"}:
                        from urllib.parse import urlsplit
                        target_parts = urlsplit(str(record.get("target_url") or ""))
                        source_parts = urlsplit(str(source.get("url") or final_url))
                        saved_origin = str(record.get("audit_origin") or "")
                        if saved_origin:
                            origin_parts = urlsplit(saved_origin)
                            same_origin = (
                                source_parts.scheme, source_parts.hostname, source_parts.port
                            ) == (
                                origin_parts.scheme, origin_parts.hostname, origin_parts.port
                            )
                        else:
                            target_host = (target_parts.hostname or "").removeprefix("www.").casefold()
                            source_host = (source_parts.hostname or "").removeprefix("www.").casefold()
                            target_port = target_parts.port or (443 if target_parts.scheme == "https" else 80)
                            source_port = source_parts.port or (443 if source_parts.scheme == "https" else 80)
                            same_origin = bool(target_host and target_host == source_host and target_port == source_port)
                            if same_origin:
                                record["audit_origin"] = f"{source_parts.scheme}://{source_parts.netloc}"
                        accepted = same_origin
                        relevance_reason = (
                            "Accepted page within the established website audit origin."
                            if accepted else "Rejected page outside the requested website origin."
                        )
                    else:
                        accepted, relevance_reason = source_relevance(
                            effective_objective,
                            str(source.get("url") or final_url),
                            str(source.get("title") or result.get("title") or ""),
                            " ".join(str(item.get("captured_text") or "") for item in round_evidence),
                        )
                    if not accepted:
                        loop_guard.record_outcome(next_url, next_assignment, "irrelevant", relevance_reason)
                        self._note(task_id, {
                            "round": round_count, "assignment": next_assignment,
                            "status": "rejected_irrelevant_source", "actions": [final_url],
                            "findings": [], "sources": [],
                            "remaining_work": [relevance_reason],
                            "seed_for_next_round": ["Discard this page and inspect a category-relevant furniture retailer source"],
                        })
                        self._log(task_id, f"Rejected irrelevant source before evidence admission: {final_url}", "warning")
                        candidates.extend(result.get("candidate_urls", []))
                        candidates = loop_guard.filter_candidates(rank_research_candidates(self._effective_objective(task_id, objective), candidates), visited_urls)
                        available = loop_guard.filter_candidates(rank_research_candidates(self._effective_objective(task_id, objective), candidates), visited_urls)
                        next_url = available[0]["url"] if available else ""
                        next_assignment = "Find a relevant furniture retailer source"
                        self._save_checkpoint(
                            task_id, initial_plan, sources=sources, findings=findings,
                            evidence=evidence, visited_urls=visited_urls, candidates=candidates,
                            structured_data=structured_data, limitations=limitations,
                            loop_guard=loop_guard, round_count=round_count,
                            next_url=next_url, next_assignment=next_assignment,
                            research_complete=not bool(next_url),
                        )
                        continue

                    source_id = f"s-{len(sources) + 1:03d}"
                    source["source_id"] = source_id
                    sources.append(source)
                    for captured in round_evidence:
                        captured["source_id"] = source_id
                    evidence.extend(round_evidence)
                    execution_plan = record.get("execution_plan")
                    evidence_refs = [item.get("evidence_id") for item in round_evidence if item.get("evidence_id")]
                    products = result.get("products") or (result.get("structured_data") or {}).get("products") or []
                    if isinstance(execution_plan, dict) and evidence_refs:
                        # Advance all sequential steps that this single captured page
                        # can actually satisfy. The trusted runtime performs extraction
                        # and numeric comparison; models cannot self-tick the checklist.
                        def _price_value(product):
                            value = product.get("price_value")
                            if isinstance(value, (int, float)):
                                return float(value)
                            match = re.search(r"[\d,]+(?:\.\d{1,2})?", str(product.get("price") or ""))
                            return float(match.group(0).replace(",", "")) if match else None

                        while True:
                            ready = ready_steps(execution_plan)
                            if not ready:
                                break
                            step = ready[0]
                            if step.get("status") != "in_progress":
                                start_step(execution_plan, step["step_id"])
                            capability = str(step.get("capability") or "").casefold()
                            objective_text = str(step.get("objective") or "").casefold()
                            verified_result = None
                            detail_lookup = capability == "extract" and any(
                                term in objective_text
                                for term in ("retrieve details", "open product", "product page", "visit product")
                            )
                            plan_step_urls = record.setdefault("plan_step_urls", {})
                            if detail_lookup:
                                selected_product = (record.get("plan_outputs") or {}).get("cheapest_product") or {}
                                product_url = selected_product.get("url")
                                if product_url:
                                    plan_step_urls[step["step_id"]] = product_url
                            else:
                                plan_step_urls.setdefault(step["step_id"], result.get("final_url") or next_url)
                            if capability == "navigate" and (
                                int(step.get("index") or 0) == 1
                                or str(step.get("step_id", "")).endswith(("seo-discover", "qa-discover"))
                                or any(term in objective_text for term in ("homepage", "home page", "starting page"))
                            ):
                                verified_result = f"Captured {source.get('title') or final_url}"
                            elif capability == "extract" and detail_lookup:
                                selected_product = (record.get("plan_outputs") or {}).get("cheapest_product") or {}
                                expected_name = str(selected_product.get("name") or "").strip().casefold()
                                expected_price = _price_value(selected_product) if selected_product else None
                                expected_url = str(selected_product.get("url") or "")
                                current_url = str(result.get("final_url") or "")
                                matching_product = next((
                                    item for item in products
                                    if expected_name
                                    and str(item.get("name") or "").strip().casefold() == expected_name
                                    and expected_price is not None
                                    and _price_value(item) == expected_price
                                ), None)
                                if expected_url and canonical_url_key(current_url) == canonical_url_key(expected_url) and matching_product:
                                    verified_result = f"Opened the selected product page and verified {matching_product.get('name')} at {matching_product.get('price')}"
                            elif capability == "extract" and products:
                                if any(term in objective_text for term in ("cheapest", "lowest", "minimum", "compare", "identify")):
                                    priced = [(amount, product) for product in products if (amount := _price_value(product)) is not None]
                                    if priced:
                                        amount, cheapest_product = min(priced, key=lambda item: item[0])
                                        record.setdefault("plan_outputs", {})["cheapest_product"] = cheapest_product
                                        verified_result = f"Lowest extracted homepage price: {cheapest_product.get('name')} — {cheapest_product.get('price')} (R {amount:,.2f}); compared {len(priced)} priced products"
                                else:
                                    verified_result = f"Extracted {len(products)} product records with displayed prices"
                            elif capability == "extract" and str(step.get("step_id", "")).endswith("seo-technical"):
                                discovery = (result.get("structured_data") or {}).get("seo_discovery") or {}
                                robots = discovery.get("robots_txt") or {}
                                sitemap = discovery.get("sitemap") or {}
                                robots_outcome_recorded = robots.get("status") is not None or bool(robots.get("error"))
                                sitemap_outcome_recorded = sitemap.get("status") is not None or bool(sitemap.get("error"))
                                if robots_outcome_recorded and sitemap_outcome_recorded and result.get("evidence_id"):
                                    verified_result = f"Recorded robots.txt HTTP {robots.get('status')} and sitemap discovery outcome"
                            elif capability == "extract" and str(step.get("step_id", "")).endswith("seo-page"):
                                seo_checks = (result.get("structured_data") or {}).get("seo_checks") or {}
                                if seo_checks and result.get("evidence_id"):
                                    verified_result = "Captured page-level title, description, canonical, headings, image and link signals"
                            elif capability == "extract" and str(step.get("step_id", "")).endswith("qa-availability"):
                                if result.get("http_status") is not None and result.get("http_status", 0) < 400 and result.get("evidence_id"):
                                    verified_result = f"Target page returned HTTP {result.get('http_status')}; visible-content issues are tracked separately"
                            elif capability == "extract" and str(step.get("step_id", "")).endswith("qa-content"):
                                qa_checks = (result.get("structured_data") or {}).get("qa_checks") or {}
                                if qa_checks and result.get("evidence_id"):
                                    verified_result = "Captured visible DOM structure, controls, forms, links and duplicate-ID checks"
                            elif capability == "interact" and str(step.get("step_id", "")).endswith(("seo-links", "qa-navigation")):
                                link_checks = (result.get("structured_data") or {}).get("internal_link_checks") or {}
                                navigation = (result.get("structured_data") or {}).get("qa_navigation_journeys") or {}
                                if result.get("evidence_id") and isinstance(link_checks, dict) and "checked_count" in link_checks:
                                    verified_result = (
                                        f"Captured {link_checks.get('checked_count', 0)} same-origin link destination check(s) "
                                        f"and {navigation.get('checked_count', 0)} browser navigation journey(s)"
                                    )
                            if verified_result is None:
                                workflow_value = str(record.get("workflow_type") or "")
                                if workflow_value in {"seo", "qa", "seo_qa"} and str(step.get("step_id", "")).endswith(
                                    ("seo-crawl", "qa-crawl", "audit-crawl", "seo-synthesise", "qa-synthesise")
                                ):
                                    # Long-running crawl and final synthesis are completed only after
                                    # the deterministic page queue has been exhausted.
                                    break
                                if workflow_value in {"seo", "qa", "seo_qa"} and str(step.get("step_id", "")).startswith(("seo-", "qa-", "combined-")):
                                    fail_step(
                                        execution_plan, step["step_id"],
                                        "This step's acceptance test was not met by the available read-only browser evidence; it is not being retried as if verified.",
                                        blocked=True,
                                    )
                                    self._log(task_id, f"Plan step blocked without evidence-backed acceptance: {step['step_id']}", "warning")
                                break
                            complete_step(execution_plan, step["step_id"], evidence_refs=evidence_refs, result=verified_result)
                            self._log(task_id, f"Plan step verified: {step['step_id']}")
                        record["active_plan_step_id"] = None
                        self._persist(task_id)
                    if record.get("goal_state") and round_evidence:
                        try:
                            proposal = await self.orchestrator.ollama.evaluate_goal_progress(
                                record["goal_state"], round_evidence, task_id=task_id,
                                round_number=round_count,
                                checklist_markdown=self.checklist.write(record),
                            )
                            GoalStateManager.apply_proposal(record["goal_state"], proposal, round_evidence)
                            GoalStateManager.apply_verified_plan_results(
                                record["goal_state"], record.get("execution_plan"), evidence,
                                record.get("plan_outputs"),
                                {**structured_data, "products": products or structured_data.get("products", [])},
                            )
                            GoalStateManager.apply_verified_workflow_results(
                                record["goal_state"], record.get("execution_blueprint"),
                                record.get("execution_plan"), evidence,
                            )
                            self._log(task_id, f"Goal progress: {len(record['goal_state']['completed'])}/{len(record['goal_state']['criteria'])} criteria have verified evidence.")
                        except (OllamaUnavailable, AttributeError) as exc:
                            self._log(task_id, f"Goal progress evaluation unavailable: {exc}", "warning")
                    for item in result.get("findings", []):
                        finding = dict(item)
                        finding["finding_id"] = f"f-{len(findings) + 1:03d}"
                        finding["source_ids"] = [source_id]
                        finding["evidence_ids"] = [result["evidence_id"]] if result.get("evidence_id") else []
                        findings.append(finding)
                    for key, values in (result.get("structured_data") or {}).items():
                        records = values if isinstance(values, list) else [values]
                        for value in records:
                            if isinstance(value, dict):
                                value = {
                                    **value,
                                    "source_id": source_id,
                                    "evidence_ids": [result["evidence_id"]] if result.get("evidence_id") else [],
                                }
                            structured_data.setdefault(key, []).append(value)
                    new_discoveries = result.get("candidate_urls", [])
                    record.setdefault("discovered_urls", [])
                    record["discovered_urls"] = list(dict.fromkeys([
                        *record["discovered_urls"],
                        *(str(item.get("url") or "") for item in new_discoveries if isinstance(item, dict)),
                    ]))
                    candidates.extend(new_discoveries)
                    # Audit crawls use deterministic same-origin breadth-first discovery.
                    # General research retains its relevance-ranked candidate selection.
                    workflow_value = str(record.get("workflow_type") or "")
                    if workflow_value in {"seo", "qa", "seo_qa"}:
                        candidates = loop_guard.filter_candidates(
                            candidates, visited_urls,
                            limit=int(record.get("max_rounds", self.max_rounds)),
                        )
                    else:
                        candidates = loop_guard.filter_candidates(
                            rank_research_candidates(self._effective_objective(task_id, objective), candidates),
                            visited_urls,
                        )
                    round_findings = [
                        item["claim"][:300] for item in findings[-12:]
                        if source_id in item.get("source_ids", [])
                    ]
                    self._note(task_id, {
                        "round": round_count, "assignment": next_assignment,
                        "status": result.get("status", "completed"),
                        "actions": [final_url], "findings": round_findings,
                        "sources": [source], "evidence_ids": [result["evidence_id"]] if result.get("evidence_id") else [],
                        "discovered_source_count": len(candidates),
                        "loop_guard": loop_guard.snapshot(),
                        "seed_for_next_round": [
                            "Objective: " + objective[:500],
                            "Visited: " + final_url,
                            "Captured: " + "; ".join(round_findings[:5]),
                        ],
                    })
                    record["step_results"].append({
                        "round": round_count, "status": result.get("status"),
                        "source": source, "evidence_id": result.get("evidence_id"),
                    })
                    self._log(
                        task_id,
                        f"Round {round_count} captured {source.get('title') or final_url}; "
                        f"{len(candidates)} undiscovered links available.",
                    )
                    record["progress_events"].append({
                        "at": self._now(), "event_type": "round_completed",
                        "status": "running", "round": round_count,
                        "message": f"Inspected {source.get('title') or final_url}",
                    })
                    if round_findings:
                        record["progress_events"].append({
                            "at": self._now(), "event_type": "finding",
                            "status": "running", "round": round_count,
                            "message": round_findings[0][:300],
                        })
                    if result.get("issues"):
                        limitations.extend(str(issue) for issue in result["issues"])

                # Stop early when consecutive pages contain only navigation chrome,
                # duplicated snippets or otherwise non-substantive content.
                latest_text = " ".join(
                    str(item.get("captured_text") or "") for item in round_evidence
                )
                substantive_tokens = set(re.findall(r"[a-z0-9]{3,}", latest_text.casefold()))
                if workflow_value not in {"seo", "qa", "seo_qa"} and result.get("status") in {"completed", "blocked"} and (len(latest_text.strip()) < 120 or len(substantive_tokens) < 12):
                    record["low_information_rounds"] = int(record.get("low_information_rounds", 0)) + 1
                    limitations.append(
                        f"Round {round_count} yielded insufficient substantive page text; "
                        "it cannot verify success criteria."
                    )
                    self._log(task_id, f"Low-information source streak: {record['low_information_rounds']}/3", "warning")
                elif result.get("status") in {"completed", "blocked"}:
                    record["low_information_rounds"] = 0

                # Persist a useful partial report after every resolved round. If cancelled,
                # the operator still receives the evidence collected so far.
                record["result"] = {
                    "schema_version": "1.0",
                    "workflow_type": record.get("workflow_type", "unsupported"),
                    "answer": f"Workflow in progress: inspected {len(sources)} pages across {round_count} rounds.",
                    "findings": list(findings), "sources": list(sources),
                    "evidence": list(evidence), "structured_data": structured_data,
                    "limitations": list(dict.fromkeys(limitations)),
                    "suggested_memory": [], "rounds_completed": round_count,
                    "max_rounds": int(record.get("max_rounds", self.max_rounds)), "visited_urls": list(visited_urls),
                    "goal_state": record.get("goal_state"),
                    "loop_guard": loop_guard.snapshot(),
                }

                self._save_checkpoint(
                    task_id, initial_plan, sources=sources, findings=findings,
                    evidence=evidence, visited_urls=visited_urls, candidates=candidates,
                    structured_data=structured_data, limitations=limitations,
                    loop_guard=loop_guard, round_count=round_count,
                    next_url="", next_assignment=next_assignment,
                )
                if workflow_value not in {"seo", "qa", "seo_qa"} and record.get("low_information_rounds", 0) >= 3:
                    limitations.append(
                        "Research stopped after three consecutive low-information sources. "
                        "No further search-navigation pages will be counted as progress."
                    )
                    next_url = ""
                    research_complete = True
                    self._save_checkpoint(
                        task_id, initial_plan, sources=sources, findings=findings,
                        evidence=evidence, visited_urls=visited_urls, candidates=candidates,
                        structured_data=structured_data, limitations=limitations,
                        loop_guard=loop_guard, round_count=round_count,
                        next_url="", next_assignment=next_assignment, research_complete=True,
                    )
                    break
                if not candidates or round_count >= int(record.get("max_rounds", self.max_rounds)):
                    next_url = ""
                    research_complete = True
                    self._save_checkpoint(
                        task_id, initial_plan, sources=sources, findings=findings,
                        evidence=evidence, visited_urls=visited_urls, candidates=candidates,
                        structured_data=structured_data, limitations=limitations,
                        loop_guard=loop_guard, round_count=round_count,
                        next_url="", next_assignment=next_assignment, research_complete=True,
                    )
                    break
                if workflow_value in {"seo", "qa", "seo_qa"}:
                    available = loop_guard.filter_candidates(
                        candidates, visited_urls,
                        limit=int(record.get("max_rounds", self.max_rounds)),
                    )
                    if not available:
                        next_url = ""
                        research_complete = True
                        self._save_checkpoint(
                            task_id, initial_plan, sources=sources, findings=findings,
                            evidence=evidence, visited_urls=visited_urls, candidates=candidates,
                            structured_data=structured_data, limitations=limitations,
                            loop_guard=loop_guard, round_count=round_count,
                            next_url="", next_assignment="Same-origin crawl exhausted",
                            research_complete=True,
                        )
                        break
                    next_url = available[0]["url"]
                    next_assignment = "Deterministic same-origin crawl: inspect discovered URL"
                    self._save_checkpoint(
                        task_id, initial_plan, sources=sources, findings=findings,
                        evidence=evidence, visited_urls=visited_urls, candidates=candidates,
                        structured_data=structured_data, limitations=limitations,
                        loop_guard=loop_guard, round_count=round_count,
                        next_url=next_url, next_assignment=next_assignment,
                    )
                    self._log(task_id, f"Audit crawl queued next same-origin URL: {next_url}")
                    continue

                try:
                    round_plan = await self.orchestrator.ollama.plan_research_round(
                        objective=self._effective_objective(task_id, objective),
                        round_number=round_count,
                        max_rounds=self.max_rounds,
                        scratchpad=self.scratchpad.read(task_id)[-8:],
                        checklist_markdown=self.checklist.write(record),
                        candidates=candidates,
                        visited_urls=visited_urls,
                        task_id=task_id,
                    )
                except (OllamaUnavailable, ValueError, TypeError) as exc:
                    # A model planning failure must not discard successfully captured
                    # evidence. Deterministically continue with a browser-discovered URL.
                    available = loop_guard.filter_candidates(rank_research_candidates(self._effective_objective(task_id, objective), candidates), visited_urls)
                    if not available:
                        limitations.append(
                            "Research planner failed and no discovered source remained."
                        )
                        next_url = ""
                        research_complete = True
                        self._save_checkpoint(
                            task_id, initial_plan, sources=sources, findings=findings,
                            evidence=evidence, visited_urls=visited_urls, candidates=candidates,
                            structured_data=structured_data, limitations=limitations,
                            loop_guard=loop_guard, round_count=round_count,
                            next_url="", next_assignment=next_assignment, research_complete=True,
                        )
                        break
                    round_plan = None
                    next_url = available[0]["url"]
                    next_assignment = (
                        "Fallback source inspection after planner output failure"
                    )
                    warning = (
                        "Research planner output was invalid; continuing with the next "
                        "browser-discovered source. " + str(exc)[:240]
                    )
                    self._log(task_id, warning, "warning")
                    record["progress_events"].append({
                        "at": self._now(), "event_type": "planner_fallback",
                        "status": "running", "round": round_count,
                        "message": warning,
                    })
                    self._save_checkpoint(
                        task_id, initial_plan, sources=sources, findings=findings,
                        evidence=evidence, visited_urls=visited_urls, candidates=candidates,
                        structured_data=structured_data, limitations=limitations,
                        loop_guard=loop_guard, round_count=round_count,
                        next_url=next_url, next_assignment=next_assignment,
                    )
                if round_plan is None:
                    continue
                if round_plan.done:
                    self._log(task_id, "Research planner determined that the available evidence is sufficient")
                    next_url = ""
                    research_complete = True
                    self._save_checkpoint(
                        task_id, initial_plan, sources=sources, findings=findings,
                        evidence=evidence, visited_urls=visited_urls, candidates=candidates,
                        structured_data=structured_data, limitations=limitations,
                        loop_guard=loop_guard, round_count=round_count,
                        next_url="", next_assignment=next_assignment, research_complete=True,
                    )
                    break
                next_url = str(round_plan.next_url)
                next_assignment = round_plan.assignment
                self._save_checkpoint(
                    task_id, initial_plan, sources=sources, findings=findings,
                    evidence=evidence, visited_urls=visited_urls, candidates=candidates,
                    structured_data=structured_data, limitations=limitations,
                    loop_guard=loop_guard, round_count=round_count,
                    next_url=next_url, next_assignment=next_assignment,
                )
                self._log(task_id, f"Next source selected: {next_url}")

            record["round"] = round_count
            workflow_value = str(record.get("workflow_type") or "")
            if workflow_value in {"seo", "qa", "seo_qa"}:
                from nightwalker.audit_analysis import analyse_duplicate_pages, crawl_coverage, prioritise_findings
                crawl_pages = structured_data.get("crawl_page", [])
                duplicate_analysis = analyse_duplicate_pages(crawl_pages)
                known_urls = list(record.get("discovered_urls") or [])
                known_urls.extend(str(item.get("url") or "") for item in crawl_pages if isinstance(item, dict))
                coverage = crawl_coverage(known_urls, visited_urls)
                discovery_records = structured_data.get("seo_discovery", [])
                sitemap_by_url = {}
                for discovery in discovery_records:
                    if not isinstance(discovery, dict):
                        continue
                    sitemap = discovery.get("sitemap") or {}
                    sitemap_url = str(sitemap.get("url") or "")
                    if sitemap_url:
                        sitemap_by_url[sitemap_url] = sitemap
                sitemap_total = max(
                    (int(item.get("url_count") or 0) for item in sitemap_by_url.values()),
                    default=0,
                )
                sitemap_disallowed = max(
                    (int(item.get("disallowed_url_count") or 0) for item in sitemap_by_url.values()),
                    default=0,
                )
                sitemap_sample_urls = list(dict.fromkeys(
                    url for item in sitemap_by_url.values()
                    for url in item.get("sample_urls", []) if url
                ))
                sitemap_sample_visited = {
                    canonical_url_key(url) for url in sitemap_sample_urls
                } & {canonical_url_key(url) for url in visited_urls}
                sitemap_urls_beyond_sample_cap = max(0, sitemap_total - len(sitemap_sample_urls))
                robots_policy_blocked = any(
                    (discovery.get("robots_txt") or {}).get("crawl_policy")
                    in {"unknown", "temporarily_unavailable_conservatively_blocked"}
                    for discovery in discovery_records if isinstance(discovery, dict)
                )
                coverage.update({
                    "sitemap_url_count": sitemap_total,
                    "sitemap_disallowed_by_robots": sitemap_disallowed,
                    "sitemap_urls_sampled": len(sitemap_sample_urls),
                    "sitemap_sample_urls_visited": len(sitemap_sample_visited),
                    "sitemap_sample_coverage_percent": round(
                        len(sitemap_sample_visited) / len(sitemap_sample_urls) * 100, 1
                    ) if sitemap_sample_urls else None,
                    "sitemap_urls_beyond_sample_cap": sitemap_urls_beyond_sample_cap,
                    "robots_policy_blocked": robots_policy_blocked,
                })
                crawl_complete = (
                    coverage["known_discovered_urls_remaining"] == 0
                    and sitemap_urls_beyond_sample_cap == 0
                    and not robots_policy_blocked
                )
                coverage["crawl_complete"] = crawl_complete
                structured_data["crawl_coverage"] = coverage
                structured_data["duplicate_analysis"] = duplicate_analysis
                link_records = structured_data.get("internal_link_checks", [])
                flattened_checks = []
                for record_item in link_records:
                    if isinstance(record_item, dict):
                        flattened_checks.extend(record_item.get("checks", []))
                structured_data["internal_link_audit"] = {
                    "pages_with_link_checks": len(link_records),
                    "checked_destination_count": len(flattened_checks),
                    "broken_destination_count": sum(
                        item.get("ok") is False and not item.get("off_origin_redirect")
                        for item in flattened_checks
                    ),
                    "off_origin_redirect_count": sum(
                        bool(item.get("off_origin_redirect")) for item in flattened_checks
                    ),
                    "unknown_destination_count": sum(item.get("ok") is None for item in flattened_checks),
                    "checks": flattened_checks[:2000],
                }
                for group_type, groups in (
                    ("duplicate_canonical", duplicate_analysis["duplicate_canonical_groups"]),
                    ("duplicate_title", duplicate_analysis["duplicate_title_groups"]),
                    ("duplicate_meta_description", duplicate_analysis["duplicate_meta_description_groups"]),
                    ("duplicate_content", duplicate_analysis["duplicate_content_groups"]),
                ):
                    for group in groups:
                        page_map = {str(item.get("url")): item for item in crawl_pages if isinstance(item, dict)}
                        matched = [page_map[url] for url in group["urls"] if url in page_map]
                        findings.append({
                            "finding_id": f"f-{len(findings) + 1:03d}",
                            "claim": f"{group_type.replace('_', ' ').title()} detected across {group['count']} inspected pages.",
                            "type": group_type, "severity": "medium",
                            "affected_urls": group["urls"],
                            "source_ids": list(dict.fromkeys(item.get("source_id") for item in matched if item.get("source_id"))),
                            "evidence_ids": list(dict.fromkeys(eid for item in matched for eid in item.get("evidence_ids", []))),
                        })
                findings = prioritise_findings(findings)
                # Complete report synthesis steps only after deterministic aggregation exists.
                execution_plan = record.get("execution_plan")
                all_evidence_refs = list(dict.fromkeys(
                    str(item.get("evidence_id")) for item in evidence if item.get("evidence_id")
                ))
                if isinstance(execution_plan, dict) and all_evidence_refs:
                    for step in execution_plan.get("steps", []):
                        step_id = str(step.get("step_id", ""))
                        is_crawl_step = step_id.endswith(("seo-crawl", "qa-crawl", "audit-crawl"))
                        is_synthesis_step = step_id.endswith(("seo-synthesise", "qa-synthesise"))
                        if (
                            (is_crawl_step and crawl_complete or is_synthesis_step)
                            and step.get("status") not in {"completed", "blocked", "failed", "skipped"}
                        ):
                            start_step(execution_plan, step_id)
                            complete_step(
                                execution_plan, step_id,
                                evidence_refs=all_evidence_refs,
                                result=(
                                    "Crawl coverage acceptance satisfied; all known discovered and sampled sitemap URLs were visited or explicitly excluded."
                                    if is_crawl_step else
                                    "Aggregated crawl coverage, duplicate analysis, verified link checks and prioritised evidence-backed findings."
                                ),
                            )
                    if record.get("goal_state"):
                        GoalStateManager.apply_verified_workflow_results(
                            record["goal_state"], record.get("execution_blueprint"),
                            execution_plan, evidence,
                        )
                priority_counts = {
                    priority: sum(item.get("priority") == priority for item in findings)
                    for priority in ("P0", "P1", "P2", "P3", "P4")
                }
                navigation_records = structured_data.get("qa_navigation_journeys", [])
                navigation_checks = [
                    check for page_record in navigation_records if isinstance(page_record, dict)
                    for check in page_record.get("checks", [])
                ]
                interaction_records = structured_data.get("qa_interaction_journeys", [])
                interaction_checks = [
                    check for page_record in interaction_records if isinstance(page_record, dict)
                    for check in page_record.get("checks", [])
                ]
                responsive_viewports = [
                    viewport
                    for page_record in structured_data.get("qa_extended", [])
                    if isinstance(page_record, dict)
                    for viewport in page_record.get("responsive_viewports", [])
                ]
                responsive_overflows = sum(
                    bool(viewport.get("horizontal_overflow")) for viewport in responsive_viewports
                )
                focus_sequences = [
                    page_record.get("keyboard_focus_sequence", [])
                    for page_record in structured_data.get("qa_extended", [])
                    if isinstance(page_record, dict)
                ]
                focus_steps_sampled = sum(
                    1 for sequence in focus_sequences for item in sequence if item
                )
                accessibility_finding_count = sum(
                    item.get("type") in {"qa_accessibility_issue", "qa_accessibility_warning"}
                    for item in findings
                )
                runtime_events = structured_data.get("qa_runtime_events", [])
                structured_data["audit_report_summary"] = {
                    "pages_inspected": len(crawl_pages),
                    "known_discovered_url_count": coverage["discovered_url_count"],
                    "crawl_coverage_percent": coverage["coverage_percent"],
                    "crawl_complete": coverage["crawl_complete"],
                    "robots_policy_blocked": coverage["robots_policy_blocked"],
                    "sitemap_url_count": coverage["sitemap_url_count"],
                    "sitemap_disallowed_by_robots": coverage["sitemap_disallowed_by_robots"],
                    "sitemap_sample_urls_visited": coverage["sitemap_sample_urls_visited"],
                    "sitemap_sample_coverage_percent": coverage["sitemap_sample_coverage_percent"],
                    "sitemap_urls_beyond_sample_cap": coverage["sitemap_urls_beyond_sample_cap"],
                    "duplicate_canonical_groups": len(duplicate_analysis["duplicate_canonical_groups"]),
                    "duplicate_title_groups": len(duplicate_analysis["duplicate_title_groups"]),
                    "duplicate_meta_description_groups": len(duplicate_analysis["duplicate_meta_description_groups"]),
                    "duplicate_content_groups": len(duplicate_analysis["duplicate_content_groups"]),
                    "internal_links_checked": len(flattened_checks),
                    "internal_link_pages_truncated": sum(
                        bool(item.get("truncated")) for item in link_records if isinstance(item, dict)
                    ),
                    "broken_internal_links": sum(
                        item.get("ok") is False and not item.get("off_origin_redirect")
                        for item in flattened_checks
                    ),
                    "off_origin_internal_redirects": sum(
                        bool(item.get("off_origin_redirect")) for item in flattened_checks
                    ),
                    "unknown_internal_link_results": sum(item.get("ok") is None for item in flattened_checks),
                    "qa_navigation_destinations_checked": len(navigation_checks),
                    "qa_navigation_failures": sum(item.get("ok") is False for item in navigation_checks),
                    "qa_interaction_controls_checked": len(interaction_checks),
                    "qa_interaction_controls_passed": sum(item.get("ok") is True for item in interaction_checks),
                    "qa_interaction_controls_failed": sum(item.get("ok") is False for item in interaction_checks),
                    "responsive_viewports_checked": len(responsive_viewports),
                    "responsive_viewport_overflows": responsive_overflows,
                    "keyboard_focus_steps_sampled": focus_steps_sampled,
                    "accessibility_findings": accessibility_finding_count,
                    "browser_console_errors": sum(item.get("kind") == "console_error" for item in runtime_events if isinstance(item, dict)),
                    "browser_page_errors": sum(item.get("kind") == "page_error" for item in runtime_events if isinstance(item, dict)),
                    "failed_network_requests": sum(item.get("kind") == "request_failed" for item in runtime_events if isinstance(item, dict)),
                    "finding_priority_counts": priority_counts,
                }
                limitations.append(
                    f"Crawl inspected {len(crawl_pages)} pages and visited {coverage['visited_url_count']} distinct URLs "
                    f"from {coverage['discovered_url_count']} known discovered URLs "
                    f"({coverage['coverage_percent'] if coverage['coverage_percent'] is not None else 'not measurable'}% coverage). "
                    "This is bounded discovery coverage, not proof that every URL on the website was found."
                )
                if coverage["known_discovered_urls_remaining"]:
                    limitations.append(
                        f"{coverage['known_discovered_urls_remaining']} discovered same-origin URL(s) remained unvisited when the crawl ended."
                    )
                if sitemap_urls_beyond_sample_cap:
                    limitations.append(
                        f"{sitemap_urls_beyond_sample_cap} allowed sitemap URL(s) were beyond the configured 1,000-URL sitemap sample cap."
                    )
                if robots_policy_blocked:
                    limitations.append(
                        "robots.txt could not be read reliably; further crawling was conservatively paused and crawl coverage remains unverified."
                    )
            if round_count >= int(record.get("max_rounds", self.max_rounds)) and candidates:
                limitations.append(f"Maximum page/crawl budget of {record.get('max_rounds', self.max_rounds)} reached.")
            if sources:
                self._log(task_id, "Research rounds resolved; synthesising the evidence-backed report")
                record["progress_events"].append({
                    "at": self._now(), "event_type": "synthesis_started",
                    "status": "running", "message": "Research collection is complete; preparing the final synthesis.",
                })
                try:
                    synthesis = await self.orchestrator.ollama.synthesise_research(
                        self._effective_objective(task_id, objective), findings, sources, round_count,
                        int(record.get("max_rounds", self.max_rounds)), task_id=task_id,
                        checklist_markdown=self.checklist.write(record),
                    )
                    answer = synthesis.answer
                    if synthesis.coverage_summary:
                        answer += "\n\n" + synthesis.coverage_summary
                    limitations.extend(synthesis.gaps)
                    if workflow_value in {"seo", "qa", "seo_qa"}:
                        summary = structured_data.get("audit_report_summary", {})
                        answer += (
                            "\n\nDeterministic audit summary: "
                            f"{summary.get('pages_inspected', 0)} page(s) inspected; "
                            f"{summary.get('crawl_coverage_percent') if summary.get('crawl_coverage_percent') is not None else 'unknown'}% "
                            "of known discovered URLs visited; "
                            f"crawl completion={'complete' if summary.get('crawl_complete') else 'incomplete'}; "
                            f"{summary.get('broken_internal_links', 0)} broken internal destination(s) confirmed; "
                            f"{summary.get('off_origin_internal_redirects', 0)} off-origin internal redirect(s); "
                            f"{summary.get('duplicate_title_groups', 0)} duplicate-title group(s); "
                            f"{summary.get('duplicate_meta_description_groups', 0)} duplicate-description group(s); "
                            f"{summary.get('duplicate_content_groups', 0)} duplicate-content group(s)."
                        )
                        if workflow_value in {"seo", "seo_qa"}:
                            answer += (
                                f" Sitemap sample coverage: {summary.get('sitemap_sample_coverage_percent') if summary.get('sitemap_sample_coverage_percent') is not None else 'not available'}%; "
                                f"{summary.get('sitemap_disallowed_by_robots', 0)} sitemap URL(s) excluded by robots policy."
                            )
                        if workflow_value in {"qa", "seo_qa"}:
                            answer += (
                                f" QA sampled {summary.get('qa_navigation_destinations_checked', 0)} navigation destination(s) "
                                f"across {summary.get('responsive_viewports_checked', 0)} responsive viewport(s), "
                                f"with {summary.get('responsive_viewport_overflows', 0)} overflow condition(s) "
                                f"and {summary.get('accessibility_findings', 0)} accessibility finding(s); "
                                f"{summary.get('keyboard_focus_steps_sampled', 0)} keyboard focus step(s) sampled; "
                                f"{summary.get('qa_interaction_controls_passed', 0)} reversible local interaction(s) passed; "
                                f"{summary.get('off_origin_internal_redirects', 0)} off-origin internal redirect(s), "
                                f"{summary.get('browser_console_errors', 0)} console error(s), "
                                f"{summary.get('browser_page_errors', 0)} page exception(s) and "
                                f"{summary.get('failed_network_requests', 0)} failed request(s) captured."
                            )
                except OllamaUnavailable as exc:
                    answer = (
                        f"Captured {len(sources)} sources and {len(findings)} observations. "
                        "Automated synthesis was unavailable; review the evidence-backed findings."
                    )
                    limitations.append(str(exc)[:500])
                remaining_criteria = (record.get("goal_state") or {}).get("remaining", [])
                if remaining_criteria:
                    limitations.append("Success criteria still lacking directly quoted evidence: " + "; ".join(remaining_criteria))
                # Caveats and non-blocking synthesis gaps remain visible in the report.
                # Task status reflects whether the user's verified success criteria were met.
                final_status = "completed" if sources and not remaining_criteria else "blocked"
            else:
                answer = "No usable sources could be inspected. Review the limitations and execution log."
                final_status = "blocked"
            record["result"] = {
                "schema_version": "1.0", "workflow_type": record.get("workflow_type", "unsupported"),
                "answer": answer,
                "findings": findings, "sources": sources, "evidence": evidence,
                "structured_data": structured_data,
                "limitations": list(dict.fromkeys(limitations)),
                "suggested_memory": [], "rounds_completed": round_count,
                "max_rounds": int(record.get("max_rounds", self.max_rounds)), "visited_urls": visited_urls,
                "goal_state": record.get("goal_state"),
                "loop_guard": loop_guard.snapshot(),
            }
            record["status"] = final_status
            record["current_step"] = None
            self._save_checkpoint(
                task_id, initial_plan, sources=sources, findings=findings,
                evidence=evidence, visited_urls=visited_urls, candidates=candidates,
                structured_data=structured_data, limitations=limitations,
                loop_guard=loop_guard, round_count=round_count,
                next_url="", next_assignment="Final synthesis", research_complete=True,
            )
            self._note(task_id, {
                "round": round_count, "assignment": "Final evidence-constrained synthesis",
                "status": final_status, "findings": [answer[:1200]],
                "sources": sources[:30], "evidence_ids": [
                    item.get("evidence_id") for item in evidence if item.get("evidence_id")
                ],
                "remaining_work": list(dict.fromkeys(limitations)),
                "seed_for_next_round": [],
            })
            record["progress_events"].append({
                "at": self._now(), "event_type": "milestone",
                "status": final_status,
                "message": f"Research finished after {round_count} rounds and {len(sources)} sources.",
            })
            self._log(task_id, f"Research finished: {len(sources)} sources, {len(findings)} findings.")
        except asyncio.CancelledError:
            if self.shutting_down:
                record["status"] = "queued"
                self._log(task_id, "Service shutdown interrupted execution; task will resume from its saved checkpoint", "warning")
                record["progress_events"].append({
                    "at": self._now(), "event_type": "service_shutdown",
                    "status": "queued",
                    "message": "Task paused for automatic restart recovery.",
                })
                self._note(task_id, {
                    "round": record.get("round", 0), "assignment": "Service shutdown",
                    "status": "queued",
                    "remaining_work": ["Resume from the last durable checkpoint after startup"],
                    "seed_for_next_round": [],
                })
            else:
                record["status"] = "stopped"
                self._log(task_id, "Task cancelled by operator", "warning")
                record["progress_events"].append({
                    "at": self._now(), "event_type": "cancelled", "status": "stopped",
                    "message": "Task cancelled; partial research and evidence have been preserved.",
                })
                self._note(task_id, {
                    "round": record.get("round", 0), "assignment": "Cancellation",
                    "status": "stopped", "remaining_work": ["Task cancelled before completion"],
                    "seed_for_next_round": [],
                })
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
            try:
                await self.orchestrator.browser.close_task_session(task_id)
            except (AttributeError, RuntimeError, OSError) as browser_close_error:
                self._log(task_id, f"Browser session cleanup warning: {browser_close_error}", "warning")
            record["current_step"] = None
            record["updated_at"] = self._now()
            self._persist(task_id)
            self.running.pop(task_id, None)

    def add_follow_up(self, task_id: str, message: str) -> dict[str, Any] | None:
        """Attach a user instruction to an active research task for the next round."""
        record = self.tasks.get(task_id)
        if record is None:
            return None
        if record.get("status") not in {"queued", "running"}:
            raise ValueError("Follow-ups can only be added while a task is active")
        instruction = message.strip()
        if not instruction:
            raise ValueError("A follow-up instruction is required")
        if len(instruction) > 2000:
            raise ValueError("Follow-up instruction must be 2000 characters or fewer")
        record.setdefault("follow_up_instructions", []).append(instruction)
        self._note(task_id, {
            "round": record.get("round", 0), "assignment": "User follow-up instruction",
            "status": "accepted", "operator_input": instruction,
            "seed_for_next_round": [instruction],
        })
        self._log(task_id, "User added a follow-up instruction for subsequent research rounds")
        record["progress_events"].append({
            "at": self._now(), "event_type": "user_follow_up",
            "status": record["status"],
            "message": "Additional user instruction received; it will guide subsequent research rounds.",
        })
        return self.snapshot(task_id)

    def _effective_objective(self, task_id: str, base_objective: str) -> str:
        additions = self.tasks[task_id].get("follow_up_instructions", [])
        if not additions:
            return base_objective
        return base_objective + "\n\nAdditional user instructions:\n" + "\n".join(
            f"- {item}" for item in additions[-10:]
        )
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

    def clarify(self, task_id: str, answer: str) -> dict[str, Any] | None:
        """Resume a task after the operator answers a Decider clarification."""
        record = self.tasks.get(task_id)
        if record is None:
            return None
        if record.get("status") != DecisionStatus.NEEDS_CLARIFICATION.value:
            raise ValueError("This task is not waiting for clarification")
        if not answer.strip():
            raise ValueError("A clarification answer is required")
        if task_id in self.running and not self.running[task_id].done():
            raise ValueError("This task is already running")

        original_objective = record["objective"]
        clarified_objective = (
            f"{original_objective}\n\nOperator clarification: {answer.strip()}"
        )
        record["clarification"] = answer.strip()
        record["execution_objective"] = clarified_objective
        self._note(task_id, {
            "round": record.get("round", 0), "assignment": "Incorporate operator clarification",
            "status": "resolved", "operator_input": answer.strip(),
            "seed_for_next_round": [original_objective, answer.strip()],
        })
        record["status"] = "queued"
        record["plan"] = None
        record["error"] = None
        self._log(task_id, "Operator supplied clarification; restarting Decider")
        self.running[task_id] = asyncio.create_task(
            self._execute_decided(task_id, clarified_objective)
        )
        return self.snapshot(task_id)

    async def _retry_after_backoff(
        self, task_id: str, objective: str, delay_seconds: int,
    ) -> None:
        await asyncio.sleep(delay_seconds)
        record = self.tasks.get(task_id)
        if record is None or record.get("status") != "queued" or self.shutting_down:
            return
        await self._execute_decided(task_id, objective, resume=True)

    def retry(self, task_id: str) -> dict[str, Any] | None:
        """Retry a failed natural-language task from its latest durable checkpoint."""
        record = self.tasks.get(task_id)
        if record is None:
            return None
        if record.get("status") != "failed":
            raise ValueError("Only failed tasks can be retried")
        active = self.running.get(task_id)
        if active and not active.done():
            raise ValueError("This task is already running")

        objective = str(
            record.get("execution_objective") or record.get("objective") or ""
        ).strip()
        if not objective:
            raise ValueError("Task has no saved objective to retry")

        checkpoint = record.get("checkpoint")
        if checkpoint and checkpoint.get("round_in_progress"):
            self._log(
                task_id,
                "Retry requested; resuming the interrupted research round from its saved checkpoint",
                "warning",
            )
        else:
            self._log(
                task_id,
                "Retry requested; resuming from the latest durable research checkpoint",
                "warning",
            )
        record["retry_count"] = int(record.get("retry_count", 0)) + 1
        delay_seconds = min(30, 2 ** min(record["retry_count"], 5))
        record["error"] = None
        record["status"] = "queued"
        record["current_step"] = None
        record["progress_events"].append({
            "at": self._now(),
            "event_type": "task_retry",
            "status": "queued",
            "message": (
                f"Manual retry #{record['retry_count']} accepted. "
                f"Resuming in {delay_seconds} seconds with existing evidence and checkpoints preserved."
            ),
        })
        self._persist(task_id)
        self.running[task_id] = asyncio.create_task(
            self._retry_after_backoff(task_id, objective, delay_seconds)
        )
        return self.snapshot(task_id)

    async def stop(self, task_id: str) -> dict[str, Any] | None:
        if task_id not in self.tasks:
            return None
        record = self.tasks[task_id]
        if record.get("status") in {"completed", "failed", "blocked", "stopped", "planned"}:
            return self.snapshot(task_id)
        task = self.running.get(task_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if record.get("status") != "stopped":
            record["status"] = "stopped"
            record["current_step"] = None
            self._log(task_id, "Task cancelled before execution or while awaiting clarification", "warning")
            record["progress_events"].append({
                "at": self._now(), "event_type": "cancelled", "status": "stopped",
                "message": "Task cancelled by operator",
            })
            self._note(task_id, {
                "round": record.get("round", 0), "assignment": "Cancellation",
                "status": "stopped", "remaining_work": ["Task cancelled before completion"],
                "seed_for_next_round": [],
            })
        return self.snapshot(task_id)
