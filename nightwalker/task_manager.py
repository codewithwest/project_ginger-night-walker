from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Any

from nightwalker.a2a import A2ABus
from nightwalker.agent_allocator import AgentAllocator
from nightwalker.contracts import AgentTask
from nightwalker.decision_contracts import DecisionPlan, DecisionStatus
from nightwalker.goal_state import GoalStateManager
from nightwalker.ollama_client import OllamaUnavailable
from nightwalker.orchestrator import Orchestrator
from nightwalker.research_guard import ResearchLoopGuard
from nightwalker.scratchpad import TaskScratchpad
from nightwalker.task_state_store import TaskStateStore


class TaskManager:
    def __init__(self, orchestrator: Orchestrator) -> None:
        self.orchestrator = orchestrator
        self.tasks: dict[str, dict[str, Any]] = {}
        self.running: dict[str, asyncio.Task[None]] = {}
        self.scratchpad = TaskScratchpad()
        self.state_store = TaskStateStore()
        self.max_rounds = 30
        self.a2a_bus = A2ABus()
        self.agent_allocator = AgentAllocator(self.a2a_bus)
        self.shutting_down = False

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

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
            record.setdefault("follow_up_instructions", [])
            record.setdefault("step_results", [])
            record.setdefault("checkpoint", None)
            record.setdefault("execution_blueprint", None)
            record.setdefault("agent_assignments", [])
            record.setdefault("a2a_messages", [])
            self.a2a_bus.restore(task_id, record.get("a2a_messages", []))
            self.tasks[task_id] = record
            checkpoint = record.get("checkpoint")
            status = record.get("status")
            saved_plan = record.get("plan")
            if not isinstance(saved_plan, dict):
                saved_plan = {}
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
        return {**record, "logs": list(record["logs"])} if record else None

    def list_tasks(self) -> list[dict[str, Any]]:
        snapshots = [
            snapshot for task_id in self.tasks
            if (snapshot := self.snapshot(task_id)) is not None
        ]
        return sorted(
            snapshots, key=lambda item: item.get("created_at", ""), reverse=True
        )

    def submit_natural_language(self, task_id: str, objective: str) -> dict[str, Any]:
        if task_id in self.tasks:
            raise ValueError("task_id already exists")
        now = self._now()
        self.tasks[task_id] = {
            "task_id": task_id,
            "capability": "auto",
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
            "max_rounds": self.max_rounds,
            "scratchpad": self.scratchpad.read(task_id),
            "follow_up_instructions": [],
            "goal_state": None,
            "checkpoint": None,
            "execution_blueprint": None,
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
            initial_plan = DecisionPlan.model_validate(checkpoint["initial_plan"])
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
            if not next_url and not research_complete:
                available = loop_guard.filter_candidates(candidates, visited_urls)
                if available:
                    next_url = available[0]["url"]
                    next_assignment = "Resume: inspect the next available discovered source"
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
                self._log(task_id, "Goal analyst defining success criteria, subtasks, dependencies and tool requirements")
                blueprint = None
                try:
                    blueprint = await self.orchestrator.ollama.create_execution_blueprint(objective, task_id=task_id)
                    record["execution_blueprint"] = blueprint.model_dump(mode="json")
                    criteria = [item.description for item in blueprint.success_criteria]
                    self._log(task_id, f"Execution blueprint created: {len(criteria)} success criteria and {len(blueprint.subtasks)} subtasks")
                    self._persist(task_id)
                except (OllamaUnavailable, AttributeError, ValueError) as exc:
                    self._log(task_id, f"Goal-first planning unavailable; using bounded legacy planning fallback: {exc}", "warning")
                self._log(task_id, "Decider resolving the initial public research entry point")
                try:
                    initial_plan = await self.orchestrator.ollama.decide_task(objective, task_id=task_id)
                except TypeError:
                    initial_plan = await self.orchestrator.ollama.decide_task(objective)
                # Use concise goal-derived search queries, never the entire instruction-heavy brief.
                research_queries = list(dict.fromkeys(
                    " ".join(query.split()).strip()
                    for query in (getattr(blueprint, "research_queries", []) if blueprint else [])
                    if isinstance(query, str) and len(query.strip()) >= 8
                ))[:8]
                explicit_target = (
                    self.orchestrator.ollama._explicit_target_url(objective)
                    if hasattr(self.orchestrator.ollama, "_explicit_target_url") else None
                )
                if not research_queries and not explicit_target:
                    # Deterministic fallback when the planning model is unavailable:
                    # use the task's subject sentence, not its report instructions.
                    subject = re.split(
                        r"\n\s*\n|\bfor each\b|\buse publicly accessible\b|"
                        r"\brecord the urls\b|\bclearly distinguish\b|\bproduce a\b",
                        objective, maxsplit=1, flags=re.IGNORECASE,
                    )[0]
                    subject = re.sub(r"^\s*(?:please\s+)?(?:research|investigate|find|compare|analyze)\s+", "", subject, flags=re.IGNORECASE)
                    subject = " ".join(subject.split()).strip(" .:-")
                    if len(subject) >= 8:
                        research_queries = [subject[:180]]
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
            while next_url and (round_count < self.max_rounds or resume_in_flight):
                if resume_in_flight:
                    resume_in_flight = False
                else:
                    round_count += 1
                record["round"] = round_count
                record["current_step"] = {
                    "index": round_count, "total": self.max_rounds,
                    "capability": "research", "objective": next_assignment,
                    "url": next_url,
                }
                self._log(task_id, f"Round {round_count}/{self.max_rounds}: {next_assignment}")
                attempt = loop_guard.begin(next_url, next_assignment)
                already_visited = loop_guard.is_visited(next_url, visited_urls)
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
                    available = loop_guard.filter_candidates(candidates, visited_urls)
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

                one_page_plan = initial_plan.model_copy(update={
                    "target_url": next_url,
                    "steps": [
                        {"step_id": "open", "capability": "navigate", "objective": "Open this discovered public source."},
                        {"step_id": "capture", "capability": "extract", "objective": next_assignment[:500]},
                    ],
                })
                one_page_plan = type(initial_plan).model_validate(one_page_plan.model_dump(mode="python"))
                effective_objective = self._effective_objective(task_id, objective)
                result = await self.orchestrator.run_browser_workflow(
                    task_id, effective_objective, one_page_plan
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
                        "seed_for_next_round": ["Retry once if useful, otherwise choose a different discovered source"],
                    })
                    candidates.extend(result.get("candidate_urls", []))
                    candidates = loop_guard.filter_candidates(candidates, visited_urls)
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
                        candidates = loop_guard.filter_candidates(candidates, visited_urls)
                        available = loop_guard.filter_candidates(candidates, visited_urls)
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
                    source_id = f"s-{len(sources) + 1:03d}"
                    source_records = result.get("sources", [])
                    source = dict(source_records[0]) if source_records else {
                        "url": final_url, "title": result.get("title", ""),
                        "retrieved_at": self._now(),
                    }
                    source["source_id"] = source_id
                    sources.append(source)
                    round_evidence = []
                    for item in result.get("evidence", []):
                        captured = dict(item)
                        captured["source_id"] = source_id
                        captured["evidence_id"] = result.get("evidence_id") or captured.get("evidence_id")
                        # Full immutable captures stay in EvidenceStore; task polling returns
                        # only a compact excerpt to keep payload and context bounded.
                        captured["captured_text"] = str(captured.get("captured_text", ""))[:1500]
                        round_evidence.append(captured)
                    evidence.extend(round_evidence)
                    if record.get("goal_state") and round_evidence:
                        try:
                            proposal = await self.orchestrator.ollama.evaluate_goal_progress(record["goal_state"], round_evidence, task_id=task_id, round_number=round_count)
                            GoalStateManager.apply_proposal(record["goal_state"], proposal, round_evidence)
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
                    candidates.extend(result.get("candidate_urls", []))
                    # Deduplicate candidates while preserving browser discovery order.
                    candidates = loop_guard.filter_candidates(candidates, visited_urls)
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
                if result.get("status") in {"completed", "blocked"} and (len(latest_text.strip()) < 120 or len(substantive_tokens) < 12):
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
                    "answer": f"Research in progress: inspected {len(sources)} sources across {round_count} rounds.",
                    "findings": list(findings), "sources": list(sources),
                    "evidence": list(evidence), "structured_data": structured_data,
                    "limitations": list(dict.fromkeys(limitations)),
                    "suggested_memory": [], "rounds_completed": round_count,
                    "max_rounds": self.max_rounds, "visited_urls": list(visited_urls),
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
                if record.get("low_information_rounds", 0) >= 3:
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
                if not candidates or round_count >= self.max_rounds:
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
                try:
                    round_plan = await self.orchestrator.ollama.plan_research_round(
                        objective=self._effective_objective(task_id, objective),
                        round_number=round_count,
                        max_rounds=self.max_rounds,
                        scratchpad=self.scratchpad.read(task_id)[-8:],
                        candidates=candidates,
                        visited_urls=visited_urls,
                        task_id=task_id,
                    )
                except (OllamaUnavailable, ValueError, TypeError) as exc:
                    # A model planning failure must not discard successfully captured
                    # evidence. Deterministically continue with a browser-discovered URL.
                    available = loop_guard.filter_candidates(candidates, visited_urls)
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
            if round_count >= self.max_rounds and candidates:
                limitations.append(f"Maximum research budget of {self.max_rounds} rounds reached.")
            if sources:
                self._log(task_id, "Research rounds resolved; synthesising the evidence-backed report")
                record["progress_events"].append({
                    "at": self._now(), "event_type": "synthesis_started",
                    "status": "running", "message": "Research collection is complete; preparing the final synthesis.",
                })
                try:
                    synthesis = await self.orchestrator.ollama.synthesise_research(
                        self._effective_objective(task_id, objective), findings, sources, round_count, self.max_rounds, task_id=task_id
                    )
                    answer = synthesis.answer
                    if synthesis.coverage_summary:
                        answer += "\n\n" + synthesis.coverage_summary
                    limitations.extend(synthesis.gaps)
                except OllamaUnavailable as exc:
                    answer = (
                        f"Captured {len(sources)} sources and {len(findings)} observations. "
                        "Automated synthesis was unavailable; review the evidence-backed findings."
                    )
                    limitations.append(str(exc)[:500])
                remaining_criteria = (record.get("goal_state") or {}).get("remaining", [])
                if remaining_criteria:
                    limitations.append("Success criteria still lacking directly quoted evidence: " + "; ".join(remaining_criteria))
                final_status = "completed" if sources and not limitations and not remaining_criteria else "blocked"
            else:
                answer = "No usable sources could be inspected. Review the limitations and execution log."
                final_status = "blocked"
            record["result"] = {
                "schema_version": "1.0", "answer": answer,
                "findings": findings, "sources": sources, "evidence": evidence,
                "structured_data": structured_data,
                "limitations": list(dict.fromkeys(limitations)),
                "suggested_memory": [], "rounds_completed": round_count,
                "max_rounds": self.max_rounds, "visited_urls": visited_urls,
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
