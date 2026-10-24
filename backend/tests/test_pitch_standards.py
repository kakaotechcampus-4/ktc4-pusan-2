"""평가 기준 등록(POST /add/{id}/standards)·버전별 조회(GET /{id}/evaluations/{version}).

AI 서버는 httpx.MockTransport 로 대신한다.
"""

import json
import uuid
from collections.abc import Callable
from functools import partial

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.pitch import standard_parser
from pitch_coach_backend.module.pitch.entity import Standards

AI_RESPONSE = {
    "display_criteria": ["추임새 5번 이하", "대본 없이 발표하기"],
    "values": {"filler": "5번 이하", "script_used": False},
    "excluded": ["발표 시간", "억양"],
    "targets": [],
    "meta": {"schema_version": "1.0", "feature_version": "evaluation_criteria-1.0", "model": None},
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
def ai(monkeypatch: pytest.MonkeyPatch) -> Callable[[httpx.Response], list[httpx.Request]]:
    """AI 서버가 돌려줄 응답을 정한다. 받은 요청 목록을 돌려준다."""

    def use(response: httpx.Response) -> list[httpx.Request]:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return response

        transport = httpx.MockTransport(handler)
        monkeypatch.setattr(
            standard_parser.httpx, "AsyncClient", partial(httpx.AsyncClient, transport=transport)
        )
        return requests

    return use


def _post(
    client: TestClient,
    pitch_id: uuid.UUID,
    headers: dict[str, str],
    standard_text: str = "추임새는 5번 이하, 대본 없이. 발표 시간과 억양도 본다",
):
    return client.post(
        f"/api/pitches/add/{pitch_id}/standards",
        content=json.dumps({"standard_text": standard_text}).encode(),
        headers={**headers, "Content-Type": "application/json"},
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
    ai: Callable[[httpx.Response], list[httpx.Request]],
) -> None:
    requests = ai(httpx.Response(200, json=AI_RESPONSE))

    response = _post(client, pitch_id, auth_headers)

    assert response.status_code == 200
    assert response.json() == {
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
    ai: Callable[[httpx.Response], list[httpx.Request]],
) -> None:
    ai(httpx.Response(200, json=AI_RESPONSE))
    assert _post(client, pitch_id, auth_headers).status_code == 200
    ai(
        httpx.Response(
            200, json={**AI_RESPONSE, "display_criteria": ["적절한 음량"], "excluded": []}
        )
    )

    response = _post(client, pitch_id, auth_headers)

    assert response.status_code == 200
    assert response.json()["except_standard"] is None
    assert _saved(db_session, pitch_id)[-1] == (2, 1, "적절한 음량")


def test_get_evaluation_returns_saved_version_in_order(
    client: TestClient,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    ai: Callable[[httpx.Response], list[httpx.Request]],
) -> None:
    ai(httpx.Response(200, json=AI_RESPONSE))
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
        httpx.Response(500, json={"detail": "LLM 실패"}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"values": {}}),
        httpx.Response(200, json={"display_criteria": "문자열 하나"}),
    ],
    ids=["ai-500", "not-json", "no-display-criteria", "wrong-type"],
)
def test_ai_failure_is_502_and_saves_nothing(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    ai: Callable[[httpx.Response], list[httpx.Request]],
    ai_response: httpx.Response,
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
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(refuse)
    monkeypatch.setattr(
        standard_parser.httpx, "AsyncClient", partial(httpx.AsyncClient, transport=transport)
    )

    response = _post(client, pitch_id, auth_headers)

    assert response.status_code == 502
    assert response.json()["code"] == "STANDARD_PARSE_FAILED"


@pytest.mark.parametrize(
    "text",
    ["", " \t\n", "\u2003\u3000", "가" * 2001, "기준\x00내용"],
    ids=["empty", "whitespace", "unicode-whitespace", "too-long", "nul"],
)
def test_invalid_input_is_422_without_calling_ai_or_saving(
    client,
    db_session,
    auth_headers,
    pitch_id,
    ai,
    text,
) -> None:
    requests = ai(httpx.Response(200, json=AI_RESPONSE))

    response = _post(client, pitch_id, auth_headers, text)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert requests == []
    assert _saved(db_session, pitch_id) == []


@pytest.mark.parametrize(
    "text",
    ["가", "가" * 2000, " \t" + "가" * 2000 + "\n "],
    ids=["one-character", "two-thousand", "trim-before-limit"],
)
def test_input_limits_apply_after_trimming(client, auth_headers, pitch_id, ai, text) -> None:
    requests = ai(httpx.Response(200, json=AI_RESPONSE))

    response = _post(client, pitch_id, auth_headers, text)

    assert response.status_code == 200
    assert len(requests) == 1
    assert json.loads(requests[0].content)["standard_text"] == text.strip()


@pytest.mark.parametrize(
    "reply",
    [
        {"display_criteria": ["가" * 51]},
        {"display_criteria": [f"기준 {n}" for n in range(11)]},
        {"display_criteria": ["정상 기준", "뒤 기준\x00"]},
        {"display_criteria": [""]},
        {"display_criteria": ["   "]},
        {"display_criteria": ["정상 기준"], "excluded": ["제외\x00"]},
        {"display_criteria": ["정상 기준"], "excluded": "문자열 목록 아님"},
    ],
    ids=[
        "title-too-long",
        "too-many",
        "nul",
        "empty-title",
        "blank-title",
        "excluded-nul",
        "excluded-wrong-type",
    ],
)
def test_invalid_ai_response_is_502_and_preserves_the_previous_version(
    client,
    db_session,
    auth_headers,
    pitch_id,
    ai,
    reply,
) -> None:
    ai(httpx.Response(200, json=AI_RESPONSE))
    assert _post(client, pitch_id, auth_headers).status_code == 200
    saved = _saved(db_session, pitch_id)
    requests = ai(httpx.Response(200, json={**AI_RESPONSE, **reply}))

    response = _post(client, pitch_id, auth_headers)

    assert response.status_code == 502
    assert response.json()["code"] == "STANDARD_PARSE_FAILED"
    assert len(requests) == 1
    assert _saved(db_session, pitch_id) == saved


def test_ten_fifty_character_titles_are_saved_without_truncation(
    client,
    db_session,
    auth_headers,
    pitch_id,
    ai,
) -> None:
    titles = [str(n) + "가" * 49 for n in range(10)]
    ai(httpx.Response(200, json={**AI_RESPONSE, "display_criteria": titles, "excluded": []}))

    response = _post(client, pitch_id, auth_headers)

    assert response.status_code == 200
    assert [item["standard"] for item in response.json()["standards"]] == titles
    assert [title for _, _, title in _saved(db_session, pitch_id)] == titles


@pytest.mark.parametrize("excluded", [None, []])
def test_no_display_criteria_is_a_valid_response(
    client,
    auth_headers,
    pitch_id,
    ai,
    excluded,
) -> None:
    ai(httpx.Response(200, json={**AI_RESPONSE, "display_criteria": [], "excluded": excluded}))

    response = _post(client, pitch_id, auth_headers)

    assert response.status_code == 200
    assert response.json()["standards"] == []
    assert response.json()["except_standard"] is None


@pytest.mark.parametrize("status,expected", [(422, 422), (500, 502), (503, 502), (504, 502)])
def test_ai_input_rejection_is_distinct_from_an_ai_outage(
    client,
    db_session,
    auth_headers,
    pitch_id,
    ai,
    status,
    expected,
) -> None:
    requests = ai(httpx.Response(status, json={"error": {"code": "fake", "message": "fake"}}))

    response = _post(client, pitch_id, auth_headers)

    assert response.status_code == expected
    assert response.json()["code"] == (
        "STANDARD_INPUT_REJECTED" if status == 422 else "STANDARD_PARSE_FAILED"
    )
    assert len(requests) == 1
    assert _saved(db_session, pitch_id) == []
