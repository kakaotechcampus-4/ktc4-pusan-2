import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from pitch_coach_backend.module.service_feedback.entity import UserSuggestion


@pytest.mark.parametrize(
    "fields",
    [
        {"source": "RATING_POPUP"},
        {"source": "RATING_POPUP", "request_id": uuid.uuid4()},
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
