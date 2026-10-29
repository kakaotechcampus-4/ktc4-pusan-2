from pitch_coach_backend.core.exceptions import ConflictException


class FeedbackNotEligible(ConflictException):
    code = "SERVICE_FEEDBACK_NOT_ELIGIBLE"
    message = "완료된 발표 리포트가 있어야 평점을 보낼 수 있습니다."


class FeedbackAlreadySubmitted(ConflictException):
    code = "SERVICE_FEEDBACK_ALREADY_SUBMITTED"
    message = "이미 서비스 평점을 제출했습니다."


class SuggestionRequestConflict(ConflictException):
    code = "SUGGESTION_REQUEST_CONFLICT"
    message = "같은 요청 ID로 다른 의견을 보낼 수 없습니다."
