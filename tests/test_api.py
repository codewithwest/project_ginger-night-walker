from fastapi.testclient import TestClient
from nightwalker.api import app


def test_health():
    # Lifespan starts Chromium, so API integration tests run in the Playwright container.
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
