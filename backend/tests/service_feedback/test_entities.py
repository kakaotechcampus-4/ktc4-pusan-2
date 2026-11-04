import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from pitch_coach_backend.module.service_feedback.entity import (
    ServiceFeedback,
    ServiceFeedbackImpression,
    UserSuggestion,
)


@pytest.mark.parametrize("rating", [0, 6])
def test_db_rejects_invalid_rating(db_session, user_id, rating):
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(ServiceFeedback(user_id=user_id, rating=rating))
        db_session.flush()


def test_db_rejects_duplicate_rating(db_session, user_id):
    db_session.add(ServiceFeedback(user_id=user_id, rating=5))
    db_session.flush()
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(ServiceFeedback(user_id=user_id, rating=4))
        db_session.flush()


@pytest.mark.parametrize("number", [0, 4])
def test_db_rejects_display_number_outside_three_slots(db_session, user_id, number):
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(
            ServiceFeedbackImpression(
                user_id=user_id, display_request_id=uuid.uuid4(), display_number=number
            )
        )
        db_session.flush()


@pytest.mark.parametrize("same_request", [True, False])
def test_db_rejects_duplicate_display_slot_or_request(db_session, user_id, same_request):
    request_id = uuid.uuid4()
    db_session.add(
        ServiceFeedbackImpression(user_id=user_id, display_request_id=request_id, display_number=1)
    )
    db_session.flush()
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(
            ServiceFeedbackImpression(
                user_id=user_id,
                display_request_id=request_id if same_request else uuid.uuid4(),
                display_number=2 if same_request else 1,
            )
        )
        db_session.flush()


@pytest.mark.parametrize(
    "fields",
    [
        {"source": "RATING_POPUP"},
        {"source": "UNKNOWN", "request_id": uuid.uuid4()},
        {"source": "MY_PAGE"},
        {"source": "MY_PAGE", "request_id": uuid.uuid4(), "content": ""},
        {"source": "MY_PAGE", "request_id": uuid.uuid4(), "content": "가" * 2001},
    ],
)
def test_db_rejects_invalid_opinion_source_or_content(db_session, user_id, fields):
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(UserSuggestion(user_id=user_id, **{"content": "의견", **fields}))
        db_session.flush()
