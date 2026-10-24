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


class TakeInProgress(ConflictException):
    # 창을 다시 연 FE 가 Take 를 실패로 끝내려 했는데 다른 창이 아직 발표 중이다
    # (실시간 연결이 붙어 있고 오디오가 들어온다)
    code = "TAKE_IN_PROGRESS"
    message = "다른 창에서 연습 중입니다."


class TakeSessionMismatch(ConflictException):
    # 같은 연습 세션 id 로 처음과 다른 모드 · 버전을 보냈다. 다시 보낸 요청이 아니다
    code = "TAKE_SESSION_MISMATCH"
    message = "이 연습 세션은 다른 설정으로 이미 시작했습니다."


class TakeAlreadyStarted(ConflictException):
    code = "TAKE_STARTED"
    message = "이미 시작한 연습입니다."


class TakeNotStarted(ConflictException):
    # 실시간 연결이 한 번도 붙지 않은(`READY`) Take 를 끝내려 했다. FE 는 첫 `ready` 를 받은 뒤에
    # 발표를 시작하므로 이 Take 에는 남은 기록이 없다
    code = "TAKE_NOT_STARTED"
    message = "시작하지 않은 연습입니다."


class InvalidTakeTime(AppException):
    code = "INVALID_TAKE_TIME"
    message = "끝난 시각이 시작한 시각보다 앞섭니다."


class TakeNotReviewable(ConflictException):
    # 리뷰는 Take 결과 · 대본 전달도 채점이 끝난 Take 만 부른다. SCORING 은 채점 진행 중이므로
    # 허용하지 않는다. AWAITING_CONFIRMATION 에서 부르면 확인 건너뛰기로 본다
    code = "TAKE_NOT_REVIEWABLE"
    message = "아직 리뷰할 수 없는 연습입니다."


class InvalidReviewResponse(AppException):
    # AI 리뷰 응답이 계약(review-agent.md 5-3)과 다르거나 다른 Take 의 응답이다. 저장하지 않고
    # Take 를 FAILED 로 끝낸다
    status_code = 502
    code = "INVALID_REVIEW_RESPONSE"
    message = "리뷰 결과가 올바르지 않아 저장하지 못했습니다."
