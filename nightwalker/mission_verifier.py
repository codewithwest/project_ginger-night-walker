"""Verifier-owned JSON memory for independently checking worker evidence claims.

The worker never receives this store or its contents. The task coordinator hands
the verifier a bounded claim and immutable evidence references after execution.
"""
from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nightwalker.ollama_client import OllamaUnavailable


class MissionVerifier:
    """Independent, read-only-to-browser verifier with exclusive memory ownership."""

    def __init__(self, directory: str | None = None) -> None:
        root = Path(directory or os.getenv("EVIDENCE_DIR", "./evidence"))
        self.directory = root / "verification-memory"

    def _path(self, task_id: str) -> Path:
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", task_id)[:120]
        return self.directory / f"{safe_id}.json"

    async def verify_step(
        self,
        *,
        task_id: str,
        ollama: Any,
        step_id: str,
        objective: str,
        worker_status: str,
        worker_claim: str | None,
        evidence_refs: list[str],
        evidence_records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Verify the worker's claim against immutable evidence IDs and content.

        This verifier has no browser tools and cannot change the Decider plan.
        Objective-specific action checks are performed before this hand-off;
        this independent stage validates the evidence chain and rejects claims
        whose cited records are missing or empty.
        """
        path = self._path(task_id)
        try:
            memory = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
                "schema_version": 1, "task_id": task_id, "events": [],
            }
        except (OSError, ValueError):
            memory = {"schema_version": 1, "task_id": task_id, "events": []}
        if not isinstance(memory, dict):
            memory = {"schema_version": 1, "task_id": task_id, "events": []}
        prior_events = memory.get("events", [])
        if not isinstance(prior_events, list):
            prior_events = []

        evidence_by_id = {
            str(item.get("evidence_id")): item
            for item in evidence_records if item.get("evidence_id")
        }
        refs = list(dict.fromkeys(str(ref) for ref in evidence_refs if ref))
        missing = [ref for ref in refs if ref not in evidence_by_id]
        empty = [
            ref for ref in refs
            if not str(evidence_by_id.get(ref, {}).get("captured_text") or "").strip()
        ]
        if worker_status in {"failed", "error", "blocked"}:
            status = "rejected"
            reason = "The worker did not report a successful execution outcome."
        elif not worker_claim or not worker_claim.strip():
            status = "rejected"
            reason = "The worker supplied no verifiable completion claim."
        elif not refs:
            status = "needs_more_evidence"
            reason = "No immutable evidence references were supplied."
        elif missing:
            status = "needs_more_evidence"
            reason = "One or more cited evidence records are missing from the verifier's evidence packet."
        elif empty:
            status = "needs_more_evidence"
            reason = "One or more cited evidence records contain no captured content."
        else:
            try:
                model_review = await ollama.review_mission_evidence(
                    task_id=task_id,
                    step_id=step_id,
                    objective=objective,
                    worker_claim=worker_claim or "",
                    evidence_records=evidence_records,
                    verifier_memory=prior_events,
                )
            except (OllamaUnavailable, AttributeError, RuntimeError, ValueError):
                model_review = {
                    "status": "needs_more_evidence",
                    "reason": "The independent verifier model was unavailable; completion is not authorised.",
                }
            status = str(model_review.get("status") or "needs_more_evidence")
            reason = str(model_review.get("reason") or "The independent verifier returned no reason.")
            # The model may reject or request more evidence, but it cannot bypass
            # the deterministic evidence-reference and non-empty-content gates.
            if status == "verified" and (missing or empty or not refs):
                status = "needs_more_evidence"
                reason = "The model review passed, but the deterministic evidence integrity gate failed."

        decision = {
            "step_id": step_id,
            "objective": objective[:500],
            "worker_status": worker_status[:40],
            "worker_claim": (worker_claim or "")[:1000],
            "verification_status": status,
            "evidence_refs": refs,
            "missing_evidence_refs": missing,
            "empty_evidence_refs": empty,
            "reason": reason,
            "verified_at": datetime.now(UTC).isoformat(),
            "verifier": "ginger-independent-verifier",
            "model_review": model_review if worker_status not in {"failed", "error", "blocked"} and worker_claim and refs and not missing and not empty else None,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        memory.setdefault("events", []).append(decision)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        temporary.replace(path)
        return {
            "status": status,
            "reason": reason,
            "evidence_refs": refs,
            "verified_at": decision["verified_at"],
        }
