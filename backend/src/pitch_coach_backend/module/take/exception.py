from pitch_coach_backend.core.exceptions import AppException


class NonExistentTake(AppException):
    status_code = 404
    code = "TAKE_NOT_FOUND"
    message = "존재하지 않는 연습 기록입니다."
