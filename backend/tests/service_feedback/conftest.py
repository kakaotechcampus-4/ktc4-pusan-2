import uuid

import pytest
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
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
