from pitch_coach_backend.core.exceptions import ConflictException


class SuggestionRequestConflict(ConflictException):
    code = "SUGGESTION_REQUEST_CONFLICT"
    message = "같은 요청 ID로 다른 의견을 보낼 수 없습니다."


class TakeSurveyNotEligible(ConflictException):
    code = "TAKE_SURVEY_NOT_ELIGIBLE"
    message = "리포트가 나온 연습에만 설문을 남길 수 있습니다."


class TakeSurveyAlreadySubmitted(ConflictException):
    code = "TAKE_SURVEY_ALREADY_SUBMITTED"
    message = "이 연습의 설문은 이미 제출했습니다."
