from pitch_coach_backend.core.exceptions import UnauthorizedException, AppException

class InvalidAuthorizationRequest(UnauthorizedException):
     message = "로그인 후 다시 시도해 주세요."

class NonExistentPitch(AppException):
    status_code = 404
    code = "PITCH_NOT_FOUND"
    message = "존재하지 않는 발표자료입니다."
