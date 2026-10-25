"""대본 전달도 확인 API — 항목 받기 · 답 반영 · 건너뛰기 (`docs/ai/architecture.md` 1-3 · 1-7).

답은 /takes/rescore 로 반영하고 리뷰로 넘긴다. 답을 반영하는 동안에는 표식
(`takes.coverage_requested_at`)이 리뷰 시작 · 건너뛰기 · 다른 답을 막는다.
"""

import copy
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.pitch.entity import ScriptSlide
from pitch_coach_backend.module.take.ai_response import CoverageCallError
from pitch_coach_backend.module.take.entity import Take, TakeAIResults, TakeSummary
from pitch_coach_backend.module.take.repository import CONFIRMATION_WAIT, TakeRepository
from pitch_coach_backend.module.take.service import analysis_runner, confirmation
from pitch_coach_backend.module.take.service import coverage as coverage_service
from pitch_coach_backend.module.user.entity import User
from tests.take.conftest import Versions, _make_take
from tests.test_take_ai_validation import coverage

SIMILAR = {
    "item_id": "R1",
    "kind": "number",
    "script_text": "37%",
    "stt_text": "47%",
    "script_sentence_index": 0,
    "stt_sentence_index": 1,
    "stt_raw_span": [10, 13],
}
RUBRIC = {
    "rubric_id": "rubric-1",
    "sentences": [{"index": 0, "text": "지난 분기 매출은 37% 늘었습니다."}],
}
ANSWER = {"slide_number": 1, "item_id": "R1", "answer": "as_script"}


def _scored(take_id: uuid.UUID) -> dict:
    reply = coverage(take_id)
    evaluation = reply["evaluations"][0]
    evaluation["similar_items"] = [SIMILAR]
    evaluation["stt_sentences"] = ["안녕하세요", "지난 분기 매출은 47% 늘었습니다"]
    reply["confirmations"] = [
        {"slide_number": 1, "item_id": "R1", "script_text": "37%", "stt_text": "47%"}
    ]
    return reply


def _rescored(take_id: uuid.UUID) -> dict:
    reply = _scored(take_id)
    reply["evaluations"][0]["applied_answers"] = {"R1": "as_script"}
    reply["confirmations"] = []
    reply["take_scores"]["content_coverage"] = 0.9
    return reply


@pytest.fixture
def take(db_session: Session, pitch_id: uuid.UUID, versions: Versions) -> Take:
    take = _make_take(db_session, pitch_id, versions, 1, status="ANALYZING")
    take.analysis_stage = "AWAITING_CONFIRMATION"
    take.analysis_stage_at = datetime.now(UTC)
    db_session.add(
        ScriptSlide(
            script_version_id=versions[1], slide_number=1, full_content="대본", rubric=RUBRIC
        )
    )
    reply = _scored(take.id)
    db_session.add(
        TakeAIResults(
            take_id=take.id,
            kind="SCRIPT_COVERAGE",
            payload={key: value for key, value in reply.items() if key != "meta"},
            meta=reply["meta"],
        )
    )
    # 서비스가 실패 경로에서 rollback 해도 남게 커밋한다
    db_session.commit()
    return take


@pytest.fixture
def started(monkeypatch, db_session: Session) -> list[uuid.UUID]:
    async def in_current_session(factory, fn, *args):
        return fn(db_session, *args)

    monkeypatch.setattr(confirmation, "_in_session", in_current_session)
    asked: list[uuid.UUID] = []
    monkeypatch.setattr(
        analysis_runner, "start", lambda take_id, *args, **kw: asked.append(take_id)
    )
    return asked


def _rescore(monkeypatch, *results) -> AsyncMock:
    rescore = AsyncMock(side_effect=list(results))
    monkeypatch.setattr(coverage_service, "request_rescore", rescore)
    return rescore


def _get(client: TestClient, take: Take, headers: dict[str, str]):
    return client.get(f"/api/takes/{take.id}/confirmations", headers=headers)


def _send(client: TestClient, take: Take, headers: dict[str, str], answers: list[dict]):
    return client.post(
        f"/api/takes/{take.id}/confirmations", headers=headers, json={"answers": answers}
    )


def _state(db: Session, take: Take) -> tuple[str | None, object]:
    db.refresh(take)
    return take.analysis_stage, take.coverage_requested_at


def test_items_carry_kind_and_both_sentences(client, take, auth_headers):
    response = _get(client, take, auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["analysis_stage"] == "AWAITING_CONFIRMATION"
    assert CONFIRMATION_WAIT.total_seconds() * 1000 - 60_000 < body["remaining_ms"] <= 600_000
    assert body["items"] == [
        {
            "slide_number": 1,
            "item_id": "R1",
            "kind": "number",
            "script_text": "37%",
            "stt_text": "47%",
            "script_sentence": "지난 분기 매출은 37% 늘었습니다.",
            "stt_sentence": "지난 분기 매출은 47% 늘었습니다",
        }
    ]


def test_sentences_not_found_are_null(client, db_session, take, auth_headers):
    result = TakeRepository(db_session).get_ai_result(take.id, "SCRIPT_COVERAGE")
    payload = copy.deepcopy(result.payload)
    payload["evaluations"][0]["similar_items"][0]["stt_sentence_index"] = 9
    payload["evaluations"][0]["similar_items"][0]["script_sentence_index"] = 9
    result.payload = payload
    db_session.commit()

    item = _get(client, take, auth_headers).json()["items"][0]

    assert (item["script_sentence"], item["stt_sentence"]) == (None, None)


def test_take_past_confirmation_has_no_items(client, db_session, take, auth_headers):
    take.analysis_stage = "REVIEWING"
    db_session.commit()

    body = _get(client, take, auth_headers).json()

    assert body == {
        "take_id": str(take.id),
        "analysis_stage": "REVIEWING",
        "remaining_ms": None,
        "items": [],
    }


def test_someone_elses_take_is_not_found(client, db_session, take, started):
    stranger = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db_session.add(stranger)
    db_session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(stranger.id)}"}

    assert _get(client, take, headers).status_code == 404
    assert _send(client, take, headers, [ANSWER]).status_code == 404
    assert _send(client, take, headers, []).status_code == 404
    assert _state(db_session, take)[0] == "AWAITING_CONFIRMATION"


def test_answer_is_rescored_and_moves_to_review(
    client, db_session, take, auth_headers, started, monkeypatch
):
    rescore = _rescore(monkeypatch, _rescored(take.id))

    response = _send(client, take, auth_headers, [ANSWER])

    assert response.status_code == 200
    assert response.json() == {"analysis_stage": "REVIEWING"}
    body = rescore.await_args.args[0]
    assert body["answers"] == [ANSWER]
    assert body["rubrics"] == [RUBRIC]
    assert body["evaluations"][0]["similar_items"] == [SIMILAR]
    assert body["failed"] == []
    result = TakeRepository(db_session).get_ai_result(take.id, "SCRIPT_COVERAGE")
    db_session.refresh(result)
    assert result.revision == 1
    assert result.payload["confirmations"] == []
    assert db_session.get(TakeSummary, take.id).speak_accuracy == 90
    assert _state(db_session, take) == ("REVIEWING", None)
    assert started == [take.id]


def test_partial_answer_sends_only_what_was_chosen(
    client, db_session, take, auth_headers, started, monkeypatch
):
    payload = TakeRepository(db_session).get_ai_result(take.id, "SCRIPT_COVERAGE").payload
    payload = copy.deepcopy(payload)
    payload["confirmations"].append(
        {"slide_number": 1, "item_id": "R2", "script_text": "3월", "stt_text": "삼월"}
    )
    TakeRepository(db_session).get_ai_result(take.id, "SCRIPT_COVERAGE").payload = payload
    db_session.commit()
    rescore = _rescore(monkeypatch, _rescored(take.id))

    assert _send(client, take, auth_headers, [ANSWER]).status_code == 200
    assert rescore.await_args.args[0]["answers"] == [ANSWER]


def test_empty_answers_skip_without_rescore(
    client, db_session, take, auth_headers, started, monkeypatch
):
    rescore = _rescore(monkeypatch)

    response = _send(client, take, auth_headers, [])

    assert response.status_code == 200
    assert rescore.await_count == 0
    assert _state(db_session, take) == ("REVIEWING", None)
    assert started == [take.id]


def test_second_answer_is_refused(client, db_session, take, auth_headers, started, monkeypatch):
    rescore = _rescore(monkeypatch, _rescored(take.id))
    assert _send(client, take, auth_headers, [ANSWER]).status_code == 200

    for answers in ([ANSWER], []):
        response = _send(client, take, auth_headers, answers)
        assert response.status_code == 409
        assert response.json()["code"] == "CONFIRMATION_CLOSED"
    assert rescore.await_count == 1


def test_answer_after_the_deadline_is_refused(
    client, db_session, take, auth_headers, started, monkeypatch
):
    take.analysis_stage_at = datetime.now(UTC) - CONFIRMATION_WAIT - timedelta(seconds=1)
    db_session.commit()
    rescore = _rescore(monkeypatch)

    response = _send(client, take, auth_headers, [ANSWER])

    assert response.status_code == 409
    assert response.json()["code"] == "CONFIRMATION_CLOSED"
    assert rescore.await_count == 0
    assert _state(db_session, take) == ("AWAITING_CONFIRMATION", None)


@pytest.mark.parametrize(
    "answers",
    [
        [{**ANSWER, "item_id": "R9"}],
        [{**ANSWER, "slide_number": 2}],
        [ANSWER, {**ANSWER, "answer": "as_stt"}],
    ],
    ids=["unknown-item", "other-slide", "twice"],
)
def test_answers_not_matching_the_items_are_rejected(
    client, db_session, take, auth_headers, started, monkeypatch, answers
):
    rescore = _rescore(monkeypatch)

    response = _send(client, take, auth_headers, answers)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert rescore.await_count == 0
    # 표식을 잡지 않은 것으로 되돌려 바로 다시 보낼 수 있다
    assert _state(db_session, take) == ("AWAITING_CONFIRMATION", None)


def test_unknown_answer_value_is_rejected(client, take, auth_headers, started):
    response = _send(client, take, auth_headers, [{**ANSWER, "answer": "unsure"}])

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_rescore_failure_keeps_waiting_so_the_answer_can_be_sent_again(
    client, db_session, take, auth_headers, started, monkeypatch
):
    failure = CoverageCallError("status=503", retryable=True)
    rescore = _rescore(monkeypatch, failure, failure)

    response = _send(client, take, auth_headers, [ANSWER])

    assert response.status_code == 502
    assert response.json()["code"] == "COVERAGE_RESCORE_FAILED"
    assert rescore.await_count == 2
    assert rescore.await_args_list[0].args[1] == rescore.await_args_list[1].args[1]
    assert _state(db_session, take) == ("AWAITING_CONFIRMATION", None)
    assert TakeRepository(db_session).get_ai_result(take.id, "SCRIPT_COVERAGE").revision == 0
    assert started == []

    _rescore(monkeypatch, _rescored(take.id))
    assert _send(client, take, auth_headers, [ANSWER]).status_code == 200


def test_invalid_rescore_reply_is_not_retried(
    client, db_session, take, auth_headers, started, monkeypatch
):
    rescore = _rescore(monkeypatch, CoverageCallError("invalid coverage response", False))

    assert _send(client, take, auth_headers, [ANSWER]).status_code == 502
    assert rescore.await_count == 1
    assert _state(db_session, take) == ("AWAITING_CONFIRMATION", None)


def test_answer_being_applied_holds_off_review_and_skip(
    client, db_session, take, auth_headers, started
):
    repository = TakeRepository(db_session)
    requested_at = repository.claim_confirmation(take.id)
    assert requested_at is not None
    db_session.commit()

    assert not repository.start_review(take.id, uuid.uuid4())
    assert _send(client, take, auth_headers, []).status_code == 409
    assert _send(client, take, auth_headers, [ANSWER]).status_code == 409
    assert _state(db_session, take) == ("AWAITING_CONFIRMATION", requested_at)

    # BE 가 다시 뜬 뒤에는 앞 프로세스가 잡은 표식과 관계없이 리뷰를 이어 간다
    assert repository.start_review(take.id, uuid.uuid4(), takeover=True)


def test_late_rescore_after_review_started_changes_nothing(
    client, db_session, take, auth_headers, started, monkeypatch
):
    async def review_starts_meanwhile(body, request_id):
        # 표식이 끊긴 것으로 보일 만큼 오래 걸려 그사이 리뷰가 시작됐다
        take.coverage_requested_at = datetime.now(UTC) - timedelta(minutes=5)
        db_session.commit()
        assert TakeRepository(db_session).start_review(take.id, uuid.uuid4())
        db_session.commit()
        return _rescored(take.id)

    monkeypatch.setattr(coverage_service, "request_rescore", review_starts_meanwhile)

    response = _send(client, take, auth_headers, [ANSWER])

    assert response.status_code == 409
    result = TakeRepository(db_session).get_ai_result(take.id, "SCRIPT_COVERAGE")
    db_session.refresh(result)
    assert (result.revision, len(result.payload["confirmations"])) == (0, 1)
    assert _state(db_session, take)[0] == "REVIEWING"
    assert started == []
