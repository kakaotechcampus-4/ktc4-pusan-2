import uuid
from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.service_feedback.entity import FirstReleaseFeedback, UserSuggestion
from pitch_coach_backend.module.take.entity import TakeAIResults
from tests.test_take_history import _pitch, _take


@pytest.fixture
def auth(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def make_report(db: Session, user_id: uuid.UUID, *, status="COMPLETED", result=True, **fields):
    pitch = _pitch(db, user_id)
    take = _take(db, pitch, 1, status=status, **fields)
    if result:
        db.add(TakeAIResults(take_id=take.id, kind="TAKE_RESULT", payload={}))
        db.flush()
    return take


@pytest.fixture
def report(db_session: Session, user_id: uuid.UUID):
    return make_report(db_session, user_id)


def make_first_release(
    db: Session,
    user_id: uuid.UUID,
    rating: int,
    suggestion: str | None = None,
    *,
    category: str = "OTHER",
    created_at: datetime | None = None,
) -> FirstReleaseFeedback:
    """1차 배포 팝업이 남긴 별점과 의견. 지금은 API 로 받지 않아 직접 넣는다."""
    feedback = FirstReleaseFeedback(user_id=user_id, rating=rating)
    if created_at is not None:
        feedback.created_at = created_at
    db.add(feedback)
    db.flush()
    if suggestion is not None:
        db.add(
            UserSuggestion(
                user_id=user_id,
                content=suggestion,
                source="RATING_POPUP",
                category=category,
                service_feedback_id=feedback.id,
                created_at=feedback.created_at,
            )
        )
    db.commit()
    return feedback
