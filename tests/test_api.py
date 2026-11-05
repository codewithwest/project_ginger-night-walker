from fastapi.testclient import TestClient

from nightwalker.api import app, browser


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
