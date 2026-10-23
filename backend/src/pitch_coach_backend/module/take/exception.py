from pitch_coach_backend.core.exceptions import AppException, ConflictException


class NonExistentTake(AppException):
    status_code = 404
    code = "TAKE_NOT_FOUND"
    message = "존재하지 않는 연습 기록입니다."


class TakeAlreadyEnded(ConflictException):
    # WebSocket 이 끝난 Take 를 거절할 때와 같은 code 다 (realtime/dto.py WsErrorCode)
    code = "TAKE_ENDED"
    message = "이미 끝난 연습입니다."


class DuplicateTranscriptSegment(ConflictException):
    # 실시간 스트림만 받는다. 실패로 알았던 저장이 실제로는 커밋된 경우(커밋 뒤 연결이 끊김)다
    code = "TRANSCRIPT_SEGMENT_EXISTS"
    message = "이미 저장된 전사 구간입니다."


class TakeSessionMismatch(ConflictException):
    # 같은 연습 세션 id 로 처음과 다른 모드 · 버전을 보냈다. 다시 보낸 요청이 아니다
    code = "TAKE_SESSION_MISMATCH"
    message = "이 연습 세션은 다른 설정으로 이미 시작했습니다."


class TakeAlreadyStarted(ConflictException):
    code = "TAKE_STARTED"
    message = "이미 시작한 연습입니다."


class InvalidTakeTime(AppException):
    code = "INVALID_TAKE_TIME"
    message = "끝난 시각이 시작한 시각보다 앞섭니다."
