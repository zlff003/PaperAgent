from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_list_papers_empty() -> None:
    client = TestClient(app)
    response = client.get("/api/papers")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
