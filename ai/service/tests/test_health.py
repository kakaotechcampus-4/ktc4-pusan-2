from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_paths_have_no_api_prefix(client: TestClient) -> None:
    """BE 와 달리 /api 접두어가 없다. BE 가 http://ai:8000/health 처럼 바로 부른다."""
    assert client.get("/api/health").status_code == 404
