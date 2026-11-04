import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.service_feedback.entity import TakeSurvey
from pitch_coach_backend.module.user.entity import User
from tests.service_feedback.conftest import make_report

PENDING = "/api/take-surveys/pending"
ANSWERS = {
    "accuracy_rating": 4,
    "usefulness_rating": 3,
    "comment": "말 빠르기가 실제보다 느리게 나왔어요",
}
T0 = datetime(2026, 11, 2, 9, tzinfo=UTC)


def path(take_id) -> str:
    return f"/api/takes/{take_id}/survey"


def count(db) -> int:
    return db.scalar(select(func.count()).select_from(TakeSurvey))


def consent(db, user_id, at=T0):
    user = db.get(User, user_id)
    user.test_consent_version, user.test_consent_at = 1, at
    db.flush()


@pytest.mark.parametrize(
    "method,url",
    [("post", path(uuid.uuid4())), ("get", path(uuid.uuid4())), ("get", PENDING)],
)
def test_requires_authentication(client, method, url):
    kwargs = {"json": ANSWERS} if method == "post" else {}
    assert getattr(client, method)(url, **kwargs).status_code == 401


def test_submit_once_and_retry_returns_same_answer(client, auth, report, db_session):
    first = client.post(path(report.id), headers=auth, json=ANSWERS)
    assert first.status_code == 201
    assert first.json()["take_id"] == str(report.id)
    assert first.json()["question_version"] == 1
    replay = client.post(path(report.id), headers=auth, json=ANSWERS)
    assert (replay.status_code, replay.json()) == (200, first.json())
    assert count(db_session) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"accuracy_rating": 5},
        {"usefulness_rating": 1},
        {"comment": "다른 의견"},
        {"comment": None},
        {"question_version": 2},
    ],
)
def test_different_answer_for_same_take_is_409(client, auth, report, db_session, change):
    assert client.post(path(report.id), headers=auth, json=ANSWERS).status_code == 201
    conflict = client.post(path(report.id), headers=auth, json={**ANSWERS, **change})
    assert (conflict.status_code, conflict.json()["code"]) == (
        409,
        "TAKE_SURVEY_ALREADY_SUBMITTED",
    )
    db_session.expire_all()
    assert db_session.scalar(select(TakeSurvey.accuracy_rating)) == 4


@pytest.mark.parametrize("comment", [None, "", "   "])
def test_comment_is_optional(client, auth, report, db_session, comment):
    body = {"accuracy_rating": 2, "usefulness_rating": 5, "comment": comment}
    assert client.post(path(report.id), headers=auth, json=body).status_code == 201
    assert db_session.scalar(select(TakeSurvey.comment)) is None
    # 공백만 보낸 재전송도 남기지 않은 것과 같은 답이다
    del body["comment"]
    assert client.post(path(report.id), headers=auth, json=body).status_code == 200


def test_comment_is_trimmed_before_compare(client, auth, report, db_session):
    body = {**ANSWERS, "comment": "  아쉬운 점  "}
    assert client.post(path(report.id), headers=auth, json=body).status_code == 201
    assert db_session.scalar(select(TakeSurvey.comment)) == "아쉬운 점"
    retry = {**ANSWERS, "comment": "아쉬운 점"}
    assert client.post(path(report.id), headers=auth, json=retry).status_code == 200


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "RUNNING"},
        {"status": "ANALYZING"},
        {"status": "FAILED"},
        {"result": False},
    ],
)
def test_only_reported_take_is_eligible(client, auth, db_session, user_id, fields):
    take = make_report(db_session, user_id, **fields)
    response = client.post(path(take.id), headers=auth, json=ANSWERS)
    assert (response.status_code, response.json()["code"]) == (409, "TAKE_SURVEY_NOT_ELIGIBLE")
    assert count(db_session) == 0


@pytest.mark.parametrize("review_status", ["FAILED", "SKIPPED"])
def test_failed_ai_review_still_has_measurement_report(
    client, auth, db_session, user_id, review_status
):
    take = make_report(db_session, user_id, review_status=review_status)
    assert client.post(path(take.id), headers=auth, json=ANSWERS).status_code == 201


def test_other_users_or_missing_take_is_404(client, auth, db_session):
    other = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db_session.add(other)
    db_session.flush()
    theirs = make_report(db_session, other.id)
    # 404 는 rollback 하므로 준비 데이터를 commit 해 둔다
    db_session.commit()
    for take_id in (theirs.id, uuid.uuid4()):
        assert client.post(path(take_id), headers=auth, json=ANSWERS).status_code == 404
        assert client.get(path(take_id), headers=auth).status_code == 404
    assert count(db_session) == 0


@pytest.mark.parametrize(
    "change",
    [
        {"accuracy_rating": 0},
        {"accuracy_rating": 6},
        {"accuracy_rating": "3"},
        {"usefulness_rating": 3.5},
        {"usefulness_rating": True},
        {"usefulness_rating": None},
        {"comment": "가" * 2001},
        {"comment": "bad\x00text"},
        {"comment": "\ud800"},
        {"question_version": 0},
        {"question_version": "1"},
        {"user_id": str(uuid.uuid4())},
    ],
)
def test_invalid_answer_is_422(client, auth, report, change):
    response = client.post(
        path(report.id),
        headers={**auth, "Content-Type": "application/json"},
        content=json.dumps({**ANSWERS, **change}),
    )
    assert response.status_code == 422


def test_missing_rating_is_422(client, auth, report):
    body = {key: value for key, value in ANSWERS.items() if key != "usefulness_rating"}
    assert client.post(path(report.id), headers=auth, json=body).status_code == 422


def test_get_shows_submission_and_is_not_cached(client, auth, report):
    before = client.get(path(report.id), headers=auth)
    assert before.headers["cache-control"] == "no-store"
    assert before.json() == {"submitted": False, "survey": None}
    saved = client.post(path(report.id), headers=auth, json=ANSWERS).json()
    after = client.get(path(report.id), headers=auth).json()
    assert after == {"submitted": True, "survey": saved}


def test_pending_is_empty_until_consent(client, auth, db_session, user_id):
    make_report(db_session, user_id, completed_at=T0 + timedelta(hours=1))
    response = client.get(PENDING, headers=auth)
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"items": []}


def test_pending_lists_reports_after_consent_without_survey(client, auth, db_session, user_id):
    consent(db_session, user_id)
    before = make_report(db_session, user_id, completed_at=T0 - timedelta(minutes=1))
    old = make_report(db_session, user_id, completed_at=T0 + timedelta(hours=1))
    new = make_report(db_session, user_id, completed_at=T0 + timedelta(hours=2))
    done = make_report(db_session, user_id, completed_at=T0 + timedelta(hours=3))
    make_report(db_session, user_id, completed_at=T0 + timedelta(hours=4), result=False)
    make_report(db_session, user_id, status="ANALYZING")
    other = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db_session.add(other)
    db_session.flush()
    consent(db_session, other.id)
    make_report(db_session, other.id, completed_at=T0 + timedelta(hours=1))
    assert client.post(path(done.id), headers=auth, json=ANSWERS).status_code == 201
    # 동의 전에 끝난 Take 도 리포트에서 직접 설문을 남길 수 있다. 목록에만 넣지 않는다
    assert client.post(path(before.id), headers=auth, json=ANSWERS).status_code == 201

    items = client.get(PENDING, headers=auth).json()["items"]

    assert [item["take_id"] for item in items] == [str(new.id), str(old.id)]
    assert items[0] == {
        "take_id": str(new.id),
        "pitch_id": str(new.pitch_id),
        "pitch_title": "발표",
        "take_number": 1,
        "completed_at": items[0]["completed_at"],
    }
    assert datetime.fromisoformat(items[0]["completed_at"]) == T0 + timedelta(hours=2)


def test_survey_is_kept_after_take_deletion_and_withdrawal(
    client, auth, report, db_session, user_id
):
    assert client.post(path(report.id), headers=auth, json=ANSWERS).status_code == 201
    db_session.delete(report)
    db_session.flush()
    db_session.expire_all()
    survey = db_session.scalar(select(TakeSurvey))
    assert (survey.take_id, survey.user_id) == (None, user_id)
    assert client.delete("/api/users/me", headers=auth).status_code == 204
    db_session.expire_all()
    survey = db_session.scalar(select(TakeSurvey))
    assert (survey.user_id, survey.accuracy_rating, survey.comment) == (
        None,
        4,
        ANSWERS["comment"],
    )


def test_two_users_surveys_do_not_mix(client, auth, report, db_session):
    other = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db_session.add(other)
    db_session.flush()
    theirs = make_report(db_session, other.id)
    other_auth = {"Authorization": f"Bearer {create_access_token(other.id)}"}
    assert client.post(path(report.id), headers=auth, json=ANSWERS).status_code == 201
    assert client.post(path(theirs.id), headers=other_auth, json=ANSWERS).status_code == 201
    assert count(db_session) == 2


@pytest.mark.parametrize(
    "fields",
    [
        {"accuracy_rating": 0},
        {"usefulness_rating": 6},
        {"comment": ""},
        {"comment": "가" * 2001},
        {"question_version": 0},
    ],
)
def test_db_rejects_invalid_survey(db_session, user_id, report, fields):
    values = {"accuracy_rating": 3, "usefulness_rating": 3, **fields}
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(TakeSurvey(user_id=user_id, take_id=report.id, **values))
        db_session.flush()


def test_db_rejects_second_survey_for_same_take(db_session, user_id, report):
    db_session.add(
        TakeSurvey(user_id=user_id, take_id=report.id, accuracy_rating=3, usefulness_rating=3)
    )
    db_session.flush()
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(
            TakeSurvey(user_id=user_id, take_id=report.id, accuracy_rating=4, usefulness_rating=4)
        )
        db_session.flush()
