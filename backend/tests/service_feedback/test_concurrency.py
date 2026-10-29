import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from pitch_coach_backend.core.exceptions import UnauthorizedException
from pitch_coach_backend.module.service_feedback import service
from pitch_coach_backend.module.service_feedback.dto import TakeSurveyCreate
from pitch_coach_backend.module.service_feedback.entity import TakeSurvey, UserSuggestion
from pitch_coach_backend.module.service_feedback.exception import TakeSurveyAlreadySubmitted
from pitch_coach_backend.module.user.entity import User
from tests.service_feedback.conftest import make_report


@pytest.fixture
def committed_user(engine):
    with Session(engine, expire_on_commit=False) as db:
        assert db.scalar(text("SHOW transaction_isolation")) == "read committed"
        user = User(email=f"{uuid.uuid4()}@example.com", name="동시 요청")
        db.add(user)
        db.commit()
        user_id = user.id
    try:
        yield user_id
    finally:
        with Session(engine) as db:
            # 테스트가 만든 수집 데이터만 정리한다. 실제 탈퇴 경로는 이를 삭제하지 않는다.
            db.execute(delete(UserSuggestion).where(UserSuggestion.user_id == user_id))
            db.execute(delete(TakeSurvey).where(TakeSurvey.user_id == user_id))
            db.execute(delete(User).where(User.id == user_id))
            db.commit()


def parallel(engine, count, operation):
    barrier = Barrier(count)

    def run(index):
        with Session(engine, expire_on_commit=False) as db:
            barrier.wait(timeout=10)
            return operation(db, index)

    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(run, index) for index in range(count)]
        return [future.result(timeout=15) for future in futures]


def test_mypage_request_retry_is_atomic_across_connections(engine, committed_user):
    request_id = uuid.uuid4()
    rows = parallel(
        engine,
        5,
        lambda db, _: service.submit_suggestion(
            db, committed_user, request_id=request_id, content="여러 번 재시도"
        ),
    )
    assert sum(created for _, created in rows) == 1
    assert len({row.id for row, _ in rows}) == 1


def test_mypage_user_deleted_after_auth_returns_401(engine, committed_user):
    with Session(engine) as db:
        db.execute(delete(User).where(User.id == committed_user))
        db.commit()
    with Session(engine) as db, pytest.raises(UnauthorizedException):
        service.submit_suggestion(db, committed_user, request_id=uuid.uuid4(), content="의견")


@pytest.fixture
def committed_take(engine, committed_user):
    with Session(engine, expire_on_commit=False) as db:
        take = make_report(db, committed_user)
        db.commit()
        return take.id


def test_same_survey_sent_concurrently_creates_one_row(engine, committed_user, committed_take):
    body = TakeSurveyCreate(accuracy_rating=4, usefulness_rating=4, comment="같은 답")
    rows = parallel(
        engine, 5, lambda db, _: service.submit_survey(db, committed_user, committed_take, body)
    )
    assert sum(created for _, created in rows) == 1
    assert len({row.id for row, _ in rows}) == 1


def test_different_surveys_keep_first_one(engine, committed_user, committed_take):
    def submit(db, index):
        body = TakeSurveyCreate(accuracy_rating=index + 1, usefulness_rating=3)
        try:
            return service.submit_survey(db, committed_user, committed_take, body)[1]
        except TakeSurveyAlreadySubmitted:
            return False

    assert sum(parallel(engine, 5, submit)) == 1


def test_withdrawal_keeps_committed_survey(engine, committed_user, committed_take):
    # 사용자 · Take FK 가 둘 다 SET NULL 이라 한 행이 탈퇴 한 번에 두 번 바뀐다.
    # 설문 저장과 탈퇴는 다른 요청(트랜잭션)이므로 그 상태로 확인한다
    body = TakeSurveyCreate(accuracy_rating=2, usefulness_rating=5, comment="보관")
    with Session(engine, expire_on_commit=False) as db:
        survey, _ = service.submit_survey(db, committed_user, committed_take, body)
    try:
        with Session(engine) as db:
            db.execute(delete(User).where(User.id == committed_user))
            db.commit()
        with Session(engine) as db:
            kept = db.get(TakeSurvey, survey.id)
            assert (kept.user_id, kept.take_id, kept.comment) == (None, None, "보관")
    finally:
        with Session(engine) as db:
            db.execute(delete(TakeSurvey).where(TakeSurvey.id == survey.id))
            db.commit()
