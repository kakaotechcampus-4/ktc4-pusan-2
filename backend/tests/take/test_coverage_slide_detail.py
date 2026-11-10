"""대본 전달도 장별 상세 — GET /api/takes/{take_id}/script-coverage/slides/{slide_number}."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.take.entity import Take, TakeAIResults
from pitch_coach_backend.module.user.entity import User
from tests.take.conftest import Versions, _make_take
from tests.take.coverage_fixtures import FALLBACK, META, OFFSCRIPT, SLIDE_DETAIL, scored


def _slide(
    client: TestClient,
    take_id: uuid.UUID,
    slide_number: int,
    headers: dict[str, str],
    revision: int | None = None,
):
    query = "" if revision is None else f"?revision={revision}"
    return client.get(
        f"/api/takes/{take_id}/script-coverage/slides/{slide_number}{query}", headers=headers
    )


@pytest.fixture
def take(db_session: Session, pitch_id: uuid.UUID, versions: Versions) -> Take:
    take = _make_take(db_session, pitch_id, versions, 1, status="COMPLETED")
    payload = {key: value for key, value in scored(take.id).items() if key != "meta"}
    db_session.add(
        TakeAIResults(
            take_id=take.id, kind="SCRIPT_COVERAGE", payload=payload, meta=META, revision=1
        )
    )
    db_session.flush()
    return take


def test_scored_slide_carries_only_the_named_fields(
    client: TestClient, take: Take, auth_headers: dict[str, str]
) -> None:
    response = _slide(client, take.id, 3, auth_headers, revision=1)

    assert response.status_code == 200
    assert response.json() == {
        "take_id": str(take.id),
        "revision": 1,
        "slide_number": 3,
        "state": "SCORED",
        "detail": SLIDE_DETAIL,
        "offscript": OFFSCRIPT,
        "failure": None,
        "sentences": [
            {
                "sentence_index": 0,
                "text": "지난 분기 매출은 37% 늘었습니다.",
                "role": "claim",
                "status": "said",
                "expression_mode": "paraphrased",
                "reason": "같은 뜻으로 말했다",
                "evidence": [0],
                "units": [{"unit_id": "S0-U1", "text": "매출 37% 증가", "status": "said"}],
            },
            # 예전 결과에 없는 키도 늘 둔다
            {
                "sentence_index": 1,
                "text": "감사합니다.",
                "role": "closing",
                "status": None,
                "expression_mode": None,
                "reason": None,
                "evidence": [],
                "units": [],
            },
        ],
        "key_points": [
            {
                "key_point_id": "KP1",
                "content": "지난 분기 매출 37% 증가",
                "importance": "critical",
                "status": "covered",
                "reason": "근거 문장에서 말했다",
                "evidence": [0],
            }
        ],
        "critical_facts": [
            {
                "fact_id": "CF1",
                "type": "percentage",
                "value": "37%",
                "stt_value": "37%",
                "status": "matched",
            }
        ],
        "stt_sentences": ["안녕하세요 지난 분기 매출은 삼십칠 퍼센트 늘었는데요"],
    }


@pytest.mark.parametrize(
    "slide_number,state,error_code,fallback",
    [
        (4, "ESTIMATED", "LLM_TIMEOUT", FALLBACK),
        (5, "FAILED", "STT_INCOMPLETE", None),
        # `fallback` 키가 없는 예전 항목
        (6, "FAILED", "RUBRIC_MISSING", None),
    ],
)
def test_slide_that_was_not_scored_carries_its_failure(
    client: TestClient,
    take: Take,
    auth_headers: dict[str, str],
    slide_number: int,
    state: str,
    error_code: str,
    fallback: dict | None,
) -> None:
    body = _slide(client, take.id, slide_number, auth_headers).json()

    assert body["state"] == state
    assert body["failure"]["error"]["code"] == error_code
    assert body["failure"]["fallback"] == fallback
    assert (body["detail"], body["offscript"]) == (None, None)
    assert [
        body[key] for key in ("sentences", "key_points", "critical_facts", "stt_sentences")
    ] == [[]] * 4


def test_changed_revision_is_409_before_looking_for_the_slide(
    client: TestClient, take: Take, auth_headers: dict[str, str]
) -> None:
    for slide_number in (3, 99):
        response = _slide(client, take.id, slide_number, auth_headers, revision=0)
        assert response.status_code == 409
        assert response.json()["code"] == "COVERAGE_REVISION_CHANGED"


def test_slide_missing_from_the_result_is_404(
    client: TestClient, take: Take, auth_headers: dict[str, str]
) -> None:
    response = _slide(client, take.id, 99, auth_headers, revision=1)

    assert response.status_code == 404
    assert response.json()["code"] == "SLIDE_NOT_FOUND"


def test_take_without_a_confirmed_result_is_404(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    take = _make_take(db_session, pitch_id, versions, 1, status="ANALYZING")
    # 병합 후보는 읽지 않는다
    db_session.add(
        TakeAIResults(
            take_id=take.id,
            kind="SCRIPT_COVERAGE_PENDING",
            payload={"evaluations": scored(take.id)["evaluations"]},
            meta=None,
        )
    )
    db_session.flush()

    response = _slide(client, take.id, 3, auth_headers)

    assert response.status_code == 404
    assert response.json()["code"] == "COVERAGE_NOT_FOUND"


def test_missing_or_someone_elses_take_is_404(
    client: TestClient, db_session: Session, take: Take
) -> None:
    stranger = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db_session.add(stranger)
    db_session.flush()
    headers = {"Authorization": f"Bearer {create_access_token(stranger.id)}"}

    for take_id in (take.id, uuid.uuid4()):
        response = _slide(client, take_id, 3, headers)
        assert response.status_code == 404
        assert response.json()["code"] == "TAKE_NOT_FOUND"


@pytest.mark.parametrize("path", ["slides/0", "slides/3?revision=-1"])
def test_out_of_range_numbers_are_rejected(
    client: TestClient, take: Take, auth_headers: dict[str, str], path: str
) -> None:
    response = client.get(f"/api/takes/{take.id}/script-coverage/{path}", headers=auth_headers)

    assert response.status_code == 422
