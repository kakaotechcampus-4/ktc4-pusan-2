from pitch_coach_backend.core.exceptions import NotFoundException


class AdminConsoleDisabled(NotFoundException):
    """관리자 계정이 설정되지 않았다.

    없는 경로와 같은 응답을 준다 — 콘솔이 있다는 것도 알리지 않는다.
    """

    message = "요청한 경로를 찾을 수 없습니다."
