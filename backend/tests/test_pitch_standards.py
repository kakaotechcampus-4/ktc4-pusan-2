"""평가 기준 등록(POST /add/{id}/standards)·버전별 조회(GET /{id}/evaluations/{version}).

AI 서버는 httpx2.MockTransport 로 대신한다.
"""

import json
import uuid
from collections.abc import Callable
from functools import partial

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.pitch import service
from pitch_coach_backend.module.pitch.entity import Standards

AI_RESPONSE = {
    "display_criteria": ["추임새 5번 이하", "대본 없이 발표하기"],
    "values": {"filler": "5번 이하", "script_used": False},
    "excluded": ["발표 시간", "억양"],
}


@pytest.fixture
def auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


@pytest.fixture
def pitch_id(client: TestClient, auth_headers: dict[str, str]) -> uuid.UUID:
    response = client.post(
        "/api/pitches/add",
        json={"title": "평가 기준 테스트", "time_limit_sec": 600},
        headers=auth_headers,
    )
    assert response.status_code == 200
    return uuid.UUID(response.json()["pitch_id"])


@pytest.fixture
def ai(monkeypatch: pytest.MonkeyPatch) -> Callable[[httpx2.Response], list[httpx2.Request]]:
    """AI 서버가 돌려줄 응답을 정한다. 받은 요청 목록을 돌려준다."""

    def use(response: httpx2.Response) -> list[httpx2.Request]:
        requests: list[httpx2.Request] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            return response

        transport = httpx2.MockTransport(handler)
        monkeypatch.setattr(service.httpx2, "Client", partial(httpx2.Client, transport=transport))
        return requests

    return use


def _post(client: TestClient, pitch_id: uuid.UUID, headers: dict[str, str]):
    return client.post(
        f"/api/pitches/add/{pitch_id}/standards",
        json={"standard_text": "추임새는 5번 이하, 대본 없이. 발표 시간과 억양도 본다"},
        headers=headers,
    )


def _saved(db: Session, pitch_id: uuid.UUID) -> list[tuple[int, int, str]]:
    rows = db.scalars(
        select(Standards)
        .where(Standards.pitch_id == pitch_id)
        .order_by(Standards.version, Standards.position)
    ).all()
    return [(row.version, row.position, row.title) for row in rows]


def test_post_saves_items_in_order_and_returns_them(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    ai: Callable[[httpx2.Response], list[httpx2.Request]],
) -> None:
    requests = ai(httpx2.Response(200, json=AI_RESPONSE))

    response = _post(client, pitch_id, auth_headers)

    assert response.status_code == 200
    # 컨트롤러가 결과를 pitch_id 키 안에 감싸 돌려준다 (FE types/standards.ts 가 이 모양 기준)
    assert response.json()["pitch_id"] == {
        "pitch_id": str(pitch_id),
        "standards": [{"standard": "추임새 5번 이하"}, {"standard": "대본 없이 발표하기"}],
        "except_standard": "발표 시간, 억양",
    }
    assert _saved(db_session, pitch_id) == [
        (1, 1, "추임새 5번 이하"),
        (1, 2, "대본 없이 발표하기"),
    ]
    assert len(requests) == 1
    assert requests[0].url.path == "/evaluation-criteria/parse"
    assert json.loads(requests[0].content) == {
        "standard_text": "추임새는 5번 이하, 대본 없이. 발표 시간과 억양도 본다"
    }


def test_post_again_makes_next_version(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    ai: Callable[[httpx2.Response], list[httpx2.Request]],
) -> None:
    ai(httpx2.Response(200, json=AI_RESPONSE))
    assert _post(client, pitch_id, auth_headers).status_code == 200
    ai(httpx2.Response(200, json={"display_criteria": ["적절한 음량"], "excluded": []}))

    response = _post(client, pitch_id, auth_headers)

    assert response.status_code == 200
    assert response.json()["pitch_id"]["except_standard"] is None
    assert _saved(db_session, pitch_id)[-1] == (2, 1, "적절한 음량")


def test_get_evaluation_returns_saved_version_in_order(
    client: TestClient,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    ai: Callable[[httpx2.Response], list[httpx2.Request]],
) -> None:
    ai(httpx2.Response(200, json=AI_RESPONSE))
    assert _post(client, pitch_id, auth_headers).status_code == 200

    response = client.get(f"/api/pitches/{pitch_id}/evaluations/1", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["evaluations"] == [
        {"order": 1, "standard": "추임새 5번 이하"},
        {"order": 2, "standard": "대본 없이 발표하기"},
    ]


@pytest.mark.parametrize(
    "ai_response",
    [
        httpx2.Response(500, json={"detail": "LLM 실패"}),
        httpx2.Response(200, text="not json"),
        httpx2.Response(200, json={"values": {}}),
        httpx2.Response(200, json={"display_criteria": "문자열 하나"}),
    ],
    ids=["ai-500", "not-json", "no-display-criteria", "wrong-type"],
)
def test_ai_failure_is_502_and_saves_nothing(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    ai: Callable[[httpx2.Response], list[httpx2.Request]],
    ai_response: httpx2.Response,
) -> None:
    ai(ai_response)

    response = _post(client, pitch_id, auth_headers)

    assert response.status_code == 502
    assert response.json()["code"] == "STANDARD_PARSE_FAILED"
    assert _saved(db_session, pitch_id) == []


def test_ai_unreachable_is_502(
    client: TestClient,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    transport = httpx2.MockTransport(refuse)
    monkeypatch.setattr(service.httpx2, "Client", partial(httpx2.Client, transport=transport))

    response = _post(client, pitch_id, auth_headers)

    assert response.status_code == 502
    assert response.json()["code"] == "STANDARD_PARSE_FAILED"
