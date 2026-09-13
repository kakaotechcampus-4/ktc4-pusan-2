"""auth 도메인 예외.

전부 core/exceptions.py 의 베이스를 상속하므로 공통 핸들러가 같은 형태의
에러 응답으로 바꿔준다. HTTPException 을 직접 던지지 않는다.

메시지는 원인을 자세히 알려주지 않는다. 인증 실패의 상세는 공격자에게
"어디까지 맞았는지" 를 알려주는 힌트가 된다.
"""

from pitch_coach_backend.core.exceptions import (
    AppException,
    ConflictException,
    UnauthorizedException,
)


class InvalidAuthorizationRequest(AppException):
    """state 가 없거나 이미 쓰였거나 임시 쿠키와 다르다. CSRF 방어의 1차 관문."""

    message = "유효하지 않은 인증 요청입니다. 다시 로그인해 주세요."


class GoogleTokenExchangeFailed(AppException):
    message = "구글 인증에 실패했습니다. 다시 시도해 주세요."


class GoogleIdentityRejected(UnauthorizedException):
    message = "구글 계정을 확인하지 못했습니다."


class EmailAlreadyRegistered(ConflictException):
    """같은 이메일의 계정이 이미 있는데 소셜 연결이 없다.

    자동으로 붙이지 않는다. Google 의 email_verified 는 외부 주소의 현재 소유권까지
    보장하지 않으므로, 자동 연결은 이메일만 알면 계정을 가져가는 경로가 된다.
    """

    message = "이미 같은 이메일로 가입된 계정이 있습니다."


class InvalidRefreshToken(UnauthorizedException):
    message = "세션이 만료되었습니다. 다시 로그인해 주세요."


class RefreshTokenReused(UnauthorizedException):
    """이미 회전된 토큰이 다시 들어왔다. 유출로 보고 세션 전체를 폐기한다."""

    message = "세션이 만료되었습니다. 다시 로그인해 주세요."


class CsrfValidationFailed(UnauthorizedException):
    message = "잘못된 요청입니다. 다시 로그인해 주세요."
