"""인증 요청·응답 모델.

Refresh 토큰은 어떤 DTO 에도 없다. 쿠키로만 오가고 응답 바디에 넣지 않는다
(자바스크립트가 읽을 수 있으면 HttpOnly 로 둔 의미가 없다).
"""

from pydantic import BaseModel, Field


class TokenResponse(BaseModel):
    """POST /api/auth/refresh 의 응답. 프론트는 access_token 을 메모리에만 둔다."""

    access_token: str
    token_type: str = "Bearer"
    # 프론트가 만료 직전에 미리 갱신할 수 있도록 알려준다 (초).
    expires_in: int


class LogoutResponse(BaseModel):
    detail: str = Field(default="로그아웃되었습니다.")
