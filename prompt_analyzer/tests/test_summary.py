from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_summary_endpoint():
    user_id = "user123"
    resp = client.get(f"/summary/{user_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert "user_id" in data and data["user_id"] == user_id
    assert "summary" in data
