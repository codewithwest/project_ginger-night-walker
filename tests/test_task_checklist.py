from __future__ import annotations

from nightwalker.task_checklist import TaskChecklist


def test_checklist_tracks_verified_criteria_and_evidence(tmp_path):
    checklist = TaskChecklist(str(tmp_path))
    task = {
        "task_id": "task-a",
        "objective": "Compare three retailers and their pricing",
        "status": "running",
        "round": 2,
        "max_rounds": 30,
        "goal_state": {
            "original_goal": "Compare three retailers and their pricing",
            "criteria": ["Identify three retailers", "Compare current pricing"],
            "completed": ["Identify three retailers"],
            "remaining": ["Compare current pricing"],
            "evidence_by_criterion": {
                "Identify three retailers": {
                    "evidence_id": "ev-001",
                    "quote": "Retailer A, Retailer B and Retailer C",
                }
            },
        },
        "execution_blueprint": {
            "subtasks": [
                {
                    "subtask_id": "discover",
                    "objective": "Identify relevant retailers",
                    "capability": "navigate",
                    "depends_on": [],
                    "success_criteria": ["criterion-1"],
                }
            ]
        },
        "agent_assignments": [{"subtask_id": "discover", "status": "assigned"}],
        "scratchpad": [{
            "round": 2,
            "assignment": "Inspect retailer pricing",
            "status": "completed",
            "findings": ["Captured a current listed price"],
            "evidence_ids": ["ev-002"],
            "remaining_work": ["Check the other retailers"],
        }],
        "result": {
            "sources": [{"source_id": "s-001"}],
            "findings": [{"finding_id": "f-001"}],
            "limitations": [],
        },
    }

    markdown = checklist.write(task)

    assert "1. [x] Identify three retailers — evidence: ev-001" in markdown
    assert "2. [ ] Compare current pricing" in markdown
    assert "Research rounds:** 2/30" in markdown
    assert "Check the other retailers" in markdown
    assert task["checklist_markdown"] == markdown
    assert checklist.read("task-a") == markdown
    assert checklist.read("task-b") is None


def test_checklist_never_marks_assigned_subtask_as_complete(tmp_path):
    checklist = TaskChecklist(str(tmp_path))
    markdown = checklist.render({
        "task_id": "task-b",
        "objective": "Research a subject",
        "goal_state": {"criteria": ["Find primary sources"], "completed": []},
        "execution_blueprint": {
            "subtasks": [{
                "subtask_id": "source",
                "objective": "Find primary sources",
                "capability": "navigate",
                "depends_on": [],
                "success_criteria": ["criterion-1"],
            }]
        },
        "agent_assignments": [{"subtask_id": "source", "status": "assigned"}],
    })

    assert "1. [ ] Find primary sources" in markdown
    assert "**source** (assigned)" in markdown


def test_checklist_shows_decider_plan_separately_from_acceptance_workflow(tmp_path):
    checklist = TaskChecklist(str(tmp_path))
    markdown = checklist.render({
        "task_id": "task-decider",
        "objective": "Verify a product search on https://example.com",
        "goal_state": {"original_goal": "Verify a product search on https://example.com"},
        "planner_provenance": {"decider": "ollama.decide_task"},
        "decider_plan": {
            "summary": "Open the requested page and verify the search result",
            "target_url": "https://example.com",
            "steps": [{
                "step_id": "open-target",
                "capability": "navigate",
                "objective": "Open the exact URL supplied by the user",
            }],
        },
        "execution_blueprint": {"subtasks": [{
            "subtask_id": "qa-content",
            "objective": "Run the requested search and verify its result",
            "capability": "extract",
            "depends_on": [],
            "success_criteria": ["product-result"],
        }]},
    })

    assert "## Decider routing plan" in markdown
    assert "ollama.decide_task" in markdown
    assert "open-target" in markdown
    assert "## Evidence acceptance workflow" in markdown
    assert "verification gates, not the Decider's routing plan" in markdown
