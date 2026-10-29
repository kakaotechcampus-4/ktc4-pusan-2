"""마이페이지 의견은 여러 번 보낼 수 있다. 같은 요청 ID 의 재전송은 한 건으로 친다."""

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pitch_coach_backend.core.exceptions import UnauthorizedException
from pitch_coach_backend.module.service_feedback import repository
from pitch_coach_backend.module.service_feedback.entity import UserSuggestion
from pitch_coach_backend.module.service_feedback.enums import SuggestionCategory
from pitch_coach_backend.module.service_feedback.exception import SuggestionRequestConflict
from pitch_coach_backend.module.user import service as user_service


def submit_suggestion(
    db: Session,
    user_id: uuid.UUID,
    *,
    request_id: uuid.UUID,
    content: str,
    category: SuggestionCategory = SuggestionCategory.OTHER,
) -> tuple[UserSuggestion, bool]:
    try:
        suggestion = repository.insert_suggestion(db, user_id, request_id, content, category)
        created = suggestion is not None
        if suggestion is None:
            suggestion = repository.suggestion_by_request(db, user_id, request_id)
            if suggestion is None or (suggestion.content, suggestion.category) != (
                content,
                category.value,
            ):
                raise SuggestionRequestConflict()
        db.commit()
        return suggestion, created
    except IntegrityError as exc:
        db.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "fk_user_suggestions_user" and not user_service.exists(db, user_id):
            raise UnauthorizedException() from None
        raise
    except Exception:
        db.rollback()
        raise
