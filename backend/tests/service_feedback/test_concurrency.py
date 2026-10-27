import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from pitch_coach_backend.module.service_feedback import service
from pitch_coach_backend.module.service_feedback.entity import (
    ServiceFeedback,
    ServiceFeedbackImpression,
    UserSuggestion,
)
from pitch_coach_backend.module.service_feedback.exception import FeedbackAlreadySubmitted
from pitch_coach_backend.module.user import service as user_service
from pitch_coach_backend.module.user.entity import User
from tests.service_feedback.conftest import make_report


@pytest.fixture
def committed_user(engine):
    with Session(engine, expire_on_commit=False) as db:
        assert db.scalar(text("SHOW transaction_isolation")) == "read committed"
        user = User(email=f"{uuid.uuid4()}@example.com", name="동시 요청")
        db.add(user)
        db.flush()
        make_report(db, user.id)
        db.commit()
        user_id = user.id
    try:
        yield user_id
    finally:
        with Session(engine) as db:
            # 테스트가 만든 수집 데이터만 정리한다. 실제 탈퇴 경로는 이를 삭제하지 않는다.
            db.execute(delete(UserSuggestion).where(UserSuggestion.user_id == user_id))
            db.execute(delete(ServiceFeedback).where(ServiceFeedback.user_id == user_id))
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


def test_concurrent_displays_never_exceed_three(engine, committed_user):
    with Session(engine) as db:
        for _ in range(2):
            service.approve_impression(db, committed_user, uuid.uuid4())
    results = parallel(
        engine, 6, lambda db, _: service.approve_impression(db, committed_user, uuid.uuid4())
    )
    assert sum(row.should_open for row in results) == 1
    with Session(engine) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(ServiceFeedbackImpression)
                .where(ServiceFeedbackImpression.user_id == committed_user)
            )
            == 3
        )


def test_same_display_request_only_creates_one_record(engine, committed_user):
    request_id = uuid.uuid4()
    rows = parallel(
        engine, 6, lambda db, _: service.approve_impression(db, committed_user, request_id)
    )
    assert all(row.should_open and row.display_count == 1 for row in rows)
    with Session(engine) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(ServiceFeedbackImpression)
                .where(ServiceFeedbackImpression.user_id == committed_user)
            )
            == 1
        )


def test_same_feedback_submitted_concurrently_creates_one_rating_and_opinion(
    engine, committed_user
):
    rows = parallel(
        engine,
        4,
        lambda db, _: service.submit_feedback(db, committed_user, rating=4, suggestion="동일 의견"),
    )
    assert sum(created for _, created in rows) == 1
    assert len({row.id for row, _ in rows}) == 1
    with Session(engine) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(UserSuggestion)
                .where(UserSuggestion.user_id == committed_user)
            )
            == 1
        )


def test_different_feedbacks_keep_first_submission(engine, committed_user):
    def submit(db, index):
        try:
            _, created = service.submit_feedback(
                db, committed_user, rating=index + 1, suggestion=None
            )
            return created
        except FeedbackAlreadySubmitted:
            return False

    assert sum(parallel(engine, 5, submit)) == 1


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


@pytest.mark.parametrize("first", ["rating", "display"])
def test_display_and_rating_follow_same_user_lock(engine, committed_user, first):
    ready = Event()

    def second_request():
        with Session(engine, expire_on_commit=False) as other:
            ready.set()
            if first == "rating":
                return service.approve_impression(other, committed_user, uuid.uuid4())
            return service.submit_feedback(other, committed_user, rating=5, suggestion=None)

    with Session(engine, expire_on_commit=False) as db, ThreadPoolExecutor(max_workers=1) as pool:
        assert user_service.lock_for_update(db, committed_user)
        future = pool.submit(second_request)
        assert ready.wait(timeout=5)
        if first == "rating":
            service.submit_feedback(db, committed_user, rating=5, suggestion=None)
            assert future.result(timeout=10).reason == "ALREADY_SUBMITTED"
        else:
            assert service.approve_impression(db, committed_user, uuid.uuid4()).should_open
            assert future.result(timeout=10)[1]
    with Session(engine) as db:
        state = service.get_state(db, committed_user)
        assert state.submitted
        assert state.display_count == (0 if first == "rating" else 1)


def test_mypage_user_deleted_after_auth_returns_401(engine, committed_user):
    from pitch_coach_backend.core.exceptions import UnauthorizedException

    with Session(engine) as db:
        db.execute(delete(User).where(User.id == committed_user))
        db.commit()
    with Session(engine) as db, pytest.raises(UnauthorizedException):
        service.submit_suggestion(db, committed_user, request_id=uuid.uuid4(), content="의견")


def test_deleted_user_cannot_acquire_display_lock(engine, committed_user):
    from pitch_coach_backend.core.exceptions import UnauthorizedException

    with Session(engine) as db:
        db.execute(delete(User).where(User.id == committed_user))
        db.commit()
    with Session(engine) as db, pytest.raises(UnauthorizedException):
        service.approve_impression(db, committed_user, uuid.uuid4())
