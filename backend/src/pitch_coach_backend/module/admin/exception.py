from fastapi import status

from pitch_coach_backend.core.exceptions import (
    AppException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
)


class AdminConsoleDisabled(NotFoundException):
    """관리자 계정이 설정되지 않았다.

    없는 경로와 같은 응답을 준다 — 콘솔이 있다는 것도 알리지 않는다.
    """

    message = "요청한 경로를 찾을 수 없습니다."


class AdminChatQueryInvalid(AppException):
    # 진단 기록은 실행 하나나 대화 하나로만 찾는다.
    # 둘 다 없으면 전체를 읽게 되고 둘 다 있으면 모호하다
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "VALIDATION_ERROR"
    message = "run_id 와 conversation_id 중 하나만 보내 주세요."


class AdminActionForbidden(ForbiddenException):
    # 콘솔 스크립트가 붙이는 헤더 없이 상태를 바꾸려 했다 (dependencies.require_admin_action)
    code = "ADMIN_ACTION_FORBIDDEN"
    message = "관리자 콘솔에서 보낸 요청이 아닙니다."


class TakeNotRecoverable(ConflictException):
    # 복구할 상태가 아니다. 무엇이 맞지 않는지는 메시지로 알린다 (recovery.py)
    code = "TAKE_NOT_RECOVERABLE"
    message = "복구할 수 있는 연습이 아닙니다."


class RecoveryRunning(ConflictException):
    # 앞서 누른 복구가 아직 돈다
    code = "RECOVERY_RUNNING"
    message = "이 연습의 복구가 이미 진행 중입니다."


class FinalizeNotStarted(AppException):
    # Take 를 되돌렸지만 finalize 를 띄우지 못했다. 주기 작업이 이어 간다 (recovery.retry_finalize).
    # 10~15분은 coach/finalize.py 의 STALE_FINALIZING(10분)과 SWEEP_INTERVAL_SEC(5분)이다
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "FINALIZE_NOT_STARTED"
    message = (
        "Take 결과를 기다리는 중으로 되돌렸지만 다시 만들기를 바로 시작하지 못했습니다. "
        "주기 작업이 10~15분 뒤 이어 갑니다."
    )
