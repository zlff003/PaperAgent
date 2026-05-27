from fastapi.testclient import TestClient

from app.main import app


def test_qa_without_papers_returns_fallback() -> None:
    client = TestClient(app)
    response = client.post("/api/qa/ask", json={"question": "What is in my library?"})
    assert response.status_code == 200
    data = response.json()
    assert "answer" in data
    assert "cited_papers" in data
    assert "conversation_id" in data


def test_qa_history_empty() -> None:
    client = TestClient(app)
    response = client.get("/api/qa/history")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
