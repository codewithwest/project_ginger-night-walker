from fastapi.testclient import TestClient

from nightwalker.api import app, browser, task_manager


def test_health(monkeypatch):
    async def no_op_start():
        return None

    async def no_op_close():
        return None

    # The health route does not depend on Chromium; keep this test browser-independent.
    monkeypatch.setattr(browser, "start", no_op_start)
    monkeypatch.setattr(browser, "close", no_op_close)

    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"



def test_natural_language_task_submission(monkeypatch):
    async def no_op_start():
        return None

    async def no_op_close():
        return None

    submitted = {}

    def capture_submit(task_id, objective):
        submitted["task_id"] = task_id
        submitted["objective"] = objective
        return {"task_id": task_id, "objective": objective, "status": "queued"}

    monkeypatch.setattr(browser, "start", no_op_start)
    monkeypatch.setattr(browser, "close", no_op_close)
    monkeypatch.setattr(task_manager, "submit_natural_language", capture_submit)

    with TestClient(app) as client:
        response = client.post("/tasks", json={"objective": "Find Acme's product catalogue"})

    assert response.status_code == 202
    assert submitted["objective"] == "Find Acme's product catalogue"
    assert submitted["task_id"].startswith("nw-")
