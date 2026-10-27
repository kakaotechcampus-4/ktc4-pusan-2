from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_uvicorn_app_returns_health_ok() -> None:
    """uvicorn 이 띄우는 모듈 변수 `app` 도 같은 앱이다 (fixture 는 create_app() 을 새로 부른다)."""
    from pitch_coach_ai.main import app

    with TestClient(app) as c:
        res = c.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_no_registered_route_uses_api_prefix(client: TestClient) -> None:
    """BE 와 달리 /api 접두어가 없다. BE 가 http://ai:8000/health 처럼 바로 부른다."""
    app = client.app
    paths = [*app.openapi()["paths"], app.docs_url, app.redoc_url, app.openapi_url]
    assert [p for p in paths if p and (p == "/api" or p.startswith("/api/"))] == []
    assert client.get("/api/health").status_code == 404


def test_only_the_uvicorn_app_stops_the_process_when_warmup_fails() -> None:
    from pitch_coach_ai import main

    assert main.app.state.readiness._on_failure is main._stop_process
    # 테스트가 만드는 앱은 불러오기가 실패해도 테스트 프로세스를 끝내지 않는다
    assert main.create_app().state.readiness._on_failure is None
