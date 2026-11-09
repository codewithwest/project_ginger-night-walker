from nightwalker.plan_execution import complete_step, initialise_plan, start_step
from nightwalker.task_checklist import TaskChecklist


def test_markdown_checklist_ticks_only_verified_plan_steps(tmp_path):
    blueprint = {
        "version": 1,
        "success_criteria": [],
        "subtasks": [
            {"subtask_id": "open", "objective": "Open homepage", "capability": "navigate", "depends_on": [], "success_criteria": [], "expected_output": "Page evidence"},
        ],
    }
    plan = initialise_plan(blueprint)
    task = {
        "task_id": "nw-plan-md",
        "objective": "Find the cheapest homepage product",
        "status": "running",
        "execution_blueprint": blueprint,
        "execution_plan": plan,
        "goal_state": {"original_goal": "Find the cheapest homepage product", "criteria": [], "completed": []},
        "agent_assignments": [],
        "scratchpad": [],
        "result": {},
        "round": 1,
        "max_rounds": 1,
    }
    checklist = TaskChecklist(str(tmp_path))
    before = checklist.render(task)
    assert "- [ ] **open** (pending)" in before

    start_step(plan, "open")
    complete_step(plan, "open", evidence_refs=["ev-home"], result="Homepage captured")
    after = checklist.write(task)
    assert "- [x] **open** (completed)" in after
    assert "evidence: ev-home" in after
    assert "Homepage captured" in after
