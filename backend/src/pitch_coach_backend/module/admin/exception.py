from fastapi import status

from pitch_coach_backend.core.exceptions import AppException, NotFoundException


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
