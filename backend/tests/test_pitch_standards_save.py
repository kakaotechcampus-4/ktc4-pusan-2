"""고친 평가 기준 저장(POST /{id}/standards) — 다시 분석해 새 버전으로 저장한다.

`docs/ai/features/evaluation-criteria.md` 12절. AI 서버는 httpx.MockTransport 로 대신한다.
"""

import json
import uuid
from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.take import coach_service
from pitch_coach_backend.module.user.entity import User
from tests import test_pitch_standards as base
from tests.test_standard_analysis_storage import make_take, saved

pitch_id = base.pitch_id
auth_headers = base.auth_headers
ai = base.ai

EDITED = ["추임새 3번 이하", "청중 보기"]
REANALYZED = {
    **base.AI_RESPONSE,
    "display_criteria": ["추임새 3번 이하", "청중을 자주 보기"],
    "excluded": ["청중 반응"],
}


def _save(client: TestClient, pitch_id: uuid.UUID, headers: dict[str, str], standards: list):
    return client.post(
        f"/api/pitches/{pitch_id}/standards",
        json={"standards": [{"standard": standard} for standard in standards]},
        headers=headers,
    )


def test_edited_lines_are_reanalyzed_and_saved_as_a_new_version(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    ai: Callable[[httpx.Response], list[httpx.Request]],
) -> None:
    ai(httpx.Response(200, json=base.AI_RESPONSE))
    assert base._post(client, pitch_id, auth_headers).status_code == 200
    requests = ai(httpx.Response(200, json=REANALYZED))

    response = _save(client, pitch_id, auth_headers, [" 추임새 3번 이하 ", "청중 보기"])

    assert response.status_code == 200
    # 다시 분석한 문장으로 저장한다. targets[].display 와 짝이 맞아야 한다 (계약 12-2)
    assert response.json() == {
        "pitch_id": str(pitch_id),
        "version": 2,
        "standards": [{"standard": "추임새 3번 이하"}, {"standard": "청중을 자주 보기"}],
        "except_standard": "청중 반응",
    }
    assert json.loads(requests[0].content) == {"standard_text": "\n".join(EDITED)}
    assert base._saved(db_session, pitch_id)[-2:] == [
        (2, 1, "추임새 3번 이하"),
        (2, 2, "청중을 자주 보기"),
    ]
    analysis = saved(db_session, pitch_id)[-1]
    assert (analysis.version, analysis.excluded) == (2, ["청중 반응"])


def test_take_keeps_the_version_it_started_with(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    ai: Callable[[httpx.Response], list[httpx.Request]],
) -> None:
    ai(httpx.Response(200, json=base.AI_RESPONSE))
    assert base._post(client, pitch_id, auth_headers).status_code == 200
    take = make_take(db_session, pitch_id, 1)
    assert coach_service.start_take(db_session, take.id)
    ai(httpx.Response(200, json=REANALYZED))

    assert _save(client, pitch_id, auth_headers, EDITED).json()["version"] == 2

    db_session.refresh(take)
    assert take.standard_version == 1
    assert [row for row in base._saved(db_session, pitch_id) if row[0] == 1] == [
        (1, 1, "추임새 5번 이하"),
        (1, 2, "대본 없이 발표하기"),
    ]


def test_ai_failure_keeps_the_saved_version(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    ai: Callable[[httpx.Response], list[httpx.Request]],
) -> None:
    ai(httpx.Response(200, json=base.AI_RESPONSE))
    assert base._post(client, pitch_id, auth_headers).status_code == 200
    ai(httpx.Response(500, json={"code": "LLM_ERROR"}))

    response = _save(client, pitch_id, auth_headers, EDITED)

    assert response.status_code == 502
    assert response.json()["code"] == "STANDARD_PARSE_FAILED"
    assert [row.version for row in saved(db_session, pitch_id)] == [1]
    assert {row[0] for row in base._saved(db_session, pitch_id)} == {1}


@pytest.mark.parametrize(
    "standards",
    [
        [],
        [f"기준 {n}" for n in range(11)],
        ["가" * 51],
        ["   "],
        ["추임새\n청중"],
        ["추임새\x00"],
    ],
    ids=["empty", "eleven", "51-chars", "blank", "newline", "nul"],
)
def test_lines_outside_the_limits_are_rejected_before_ai(
    client: TestClient,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    ai: Callable[[httpx.Response], list[httpx.Request]],
    standards: list[str],
) -> None:
    requests = ai(httpx.Response(200, json=REANALYZED))

    response = _save(client, pitch_id, auth_headers, standards)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert requests == []


def test_ten_lines_of_fifty_chars_are_accepted(
    client: TestClient,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    ai: Callable[[httpx.Response], list[httpx.Request]],
) -> None:
    requests = ai(httpx.Response(200, json=REANALYZED))

    response = _save(client, pitch_id, auth_headers, ["가" * 50] * 10)

    assert response.status_code == 200
    assert len(requests) == 1


def test_someone_elses_pitch_is_not_found(
    client: TestClient,
    db_session: Session,
    pitch_id: uuid.UUID,
    ai: Callable[[httpx.Response], list[httpx.Request]],
) -> None:
    stranger = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db_session.add(stranger)
    db_session.flush()
    requests = ai(httpx.Response(200, json=REANALYZED))
    headers = {"Authorization": f"Bearer {create_access_token(stranger.id)}"}

    response = _save(client, pitch_id, headers, EDITED)

    assert response.status_code == 404
    assert requests == []
