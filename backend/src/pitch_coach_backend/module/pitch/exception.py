from pitch_coach_backend.core.exceptions import AppException, UnauthorizedException


class InvalidAuthorizationRequest(UnauthorizedException):
     message = "로그인 후 다시 시도해 주세요."

class NonExistentPitch(AppException):
    status_code = 404
    code = "PITCH_NOT_FOUND"
    message = "존재하지 않는 발표자료입니다."

class NonExistentTake(AppException):
     status_code = 404
     code = "TAKE_NOT_FOUND"
     message = "존재하지 않는 테이크입니다."

class NotExistPresentationVersion(AppException):
    status_code = 404
    code = "PRESENTATION_VERSION_NOT_FOUND"
    message = "존재하지 않는 발표자료 버전입니다."
