from pitch_coach_backend.core.exceptions import ConflictException


class SuggestionRequestConflict(ConflictException):
    code = "SUGGESTION_REQUEST_CONFLICT"
    message = "같은 요청 ID로 다른 의견을 보낼 수 없습니다."
