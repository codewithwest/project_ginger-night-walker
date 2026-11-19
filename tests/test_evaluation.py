from nightwalker.evaluation import aggregate_evaluations, classify_failure, evaluate_mission


def test_false_completion_is_counted_when_criteria_are_not_all_verified():
    result = evaluate_mission({
        "task_id": "search-mission",
        "status": "completed",
        "criteria": [
            {"status": "verified"},
            {"status": "pending"},
        ],
    })

    assert result["mission_completed"] is False
    assert result["false_completion"] is True
    assert result["objective_completion_rate"] == 0.5


def test_search_submission_and_result_verification_can_be_measured_as_separate_actions():
    result = evaluate_mission({
        "task_id": "search-mission",
        "status": "blocked",
        "criteria": [{"status": "failed"}],
        "actions": [
            {"intended_state_change": True, "observed_state_change": True},
            {"intended_state_change": True, "observed_state_change": False},
        ],
        "trace": [
            {"phase": "act", "details": {"reason": "Search submit control was not found"}},
        ],
    })

    assert result["action_effectiveness_rate"] == 0.5
    assert result["failure_class"] == "comprehension"


def test_recovery_rate_and_failure_class_are_aggregated():
    records = [
        {
            "task_id": "recover-1",
            "status": "completed",
            "criteria": [{"status": "verified"}],
            "recoveries": [
                {"attempted": True, "recovered": True},
                {"attempted": True, "recovered": False},
            ],
            "duration_ms": 1200,
        },
        {
            "task_id": "tool-1",
            "status": "failed",
            "criteria": [{"status": "failed"}],
            "trace": [{"phase": "act", "details": {"error": "Steel MCP timeout"}}],
            "duration_ms": 800,
        },
    ]

    summary = aggregate_evaluations(records)

    assert summary["mission_count"] == 2
    assert summary["mission_completion_rate"] == 0.5
    assert summary["mean_recovery_rate"] == 0.5
    assert summary["failure_classes"] == {"tool_execution": 1}
    assert summary["mean_duration_ms"] == 1000


def test_failure_classifier_has_safe_fallback():
    assert classify_failure([]) == "unclassified"



def test_task_record_evaluation_uses_goal_ledger_and_never_infers_action_success():
    from nightwalker.evaluation import evaluate_task_record

    result = evaluate_task_record({
        "task_id": "task-42",
        "status": "completed",
        "goal_state": {
            "criteria": ["Search submitted", "Results inspected"],
            "completed": ["Search submitted"],
        },
        "timing": {"elapsed_ms": 2500},
    }, trace=[
        {"phase": "act", "details": {"reason": "Typed query"}},
    ])

    assert result["false_completion"] is True
    assert result["objective_completion_rate"] == 0.5
    assert result["action_effectiveness_rate"] is None
    assert result["duration_ms"] == 2500
