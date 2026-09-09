from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_every_path_lives_under_api_prefix() -> None:
    """Caddy 는 /api/* 만 백엔드로 보낸다. 접두어 없는 경로는 프론트로 가서 조용히 실패한다."""
    from pitch_coach_backend.main import API_PREFIX, app

    paths = [
        *app.openapi()["paths"],
        app.docs_url,
        app.redoc_url,
        app.openapi_url,
        app.swagger_ui_oauth2_redirect_url,
    ]
    outside = [p for p in paths if p and not p.startswith(API_PREFIX)]
    assert outside == [], f"/api 밖에 있는 경로: {outside}"
