from pitch_coach_backend.core.exceptions import AppException, ConflictException


class NonExistentTake(AppException):
    status_code = 404
    code = "TAKE_NOT_FOUND"
    message = "존재하지 않는 연습 기록입니다."


class TakeAlreadyEnded(ConflictException):
    # WebSocket 이 끝난 Take 를 거절할 때와 같은 code 다 (realtime/dto.py WsErrorCode)
    code = "TAKE_ENDED"
    message = "이미 끝난 연습입니다."


class TakeAlreadyStarted(ConflictException):
    code = "TAKE_STARTED"
    message = "이미 시작한 연습입니다."


class InvalidTakeTime(AppException):
    code = "INVALID_TAKE_TIME"
    message = "끝난 시각이 시작한 시각보다 앞섭니다."
