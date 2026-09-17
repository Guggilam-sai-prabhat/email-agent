from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_app_starts():
    assert app.title == "mail-agent"


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
