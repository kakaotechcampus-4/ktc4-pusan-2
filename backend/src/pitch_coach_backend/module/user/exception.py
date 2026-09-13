from pitch_coach_backend.core.exceptions import NotFoundException


class UserNotFound(NotFoundException):
    message = "사용자를 찾을 수 없습니다."
