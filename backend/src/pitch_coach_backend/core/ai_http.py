"""AI 서버를 부르는 곳이 함께 쓰는 부품. 재시도 루프는 로그 모양이 달라 각 호출부에 둔다."""

import ssl
from functools import cache
from typing import Any

import httpx

from pitch_coach_backend.core.ai_response_limit import AI_RESPONSE_HOOKS
from pitch_coach_backend.core.config import settings

REQUEST_ID_HEADER = "X-Request-ID"

# 이 major 버전의 응답만 읽는다. major 가 다르면 필드 이름 · 뜻이 바뀐 것이다 (architecture.md 5절)
SCHEMA_MAJOR = "1"

# 4xx 인데 다시 부르면 나을 수 있는 것: 408 Request Timeout, 429 Too Many Requests
_RETRYABLE_CLIENT_STATUSES = frozenset({408, 429})

# 오류 본문에는 사용자 원문이 돌아올 수 있다. 계약에 있는 코드만 로그에 남긴다
_AI_ERROR_CODES = frozenset(
    {
        "VALIDATION_ERROR",
        "NOT_FOUND",
        "METHOD_NOT_ALLOWED",
        "LLM_TIMEOUT",
        "LLM_ERROR",
        "LLM_INVALID_OUTPUT",
        "LLM_UNAVAILABLE",
        "NOT_READY",
        "INTERNAL_ERROR",
        "REPLAY_REQUIRED",
    }
)


def retryable_status(status: int) -> bool:
    return status >= 500 or status in _RETRYABLE_CLIENT_STATUSES


def ai_error_code(response: httpx.Response) -> str:
    """AI 공통 에러 본문의 code. 본문 전체는 남기지 않는다."""
    try:
        body = response.json()
    except ValueError:
        return "UNKNOWN"
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and code in _AI_ERROR_CODES else "UNKNOWN"


@cache
def _ssl_context() -> ssl.SSLContext:
    """httpx 는 클라이언트를 만들 때마다 CA 번들을 읽어 SSL 컨텍스트를 만든다 (실측 약 9ms).

    그 일이 STT 와 같은 이벤트 루프에서 일어나므로 한 번만 만들어 재사용한다.
    인증서 검증은 그대로 켜 둔다 (AI_BASE_URL 을 https 로 바꿔도 안전하게).
    """
    return httpx.create_ssl_context()


def ai_client(
    timeout: httpx.Timeout | float,
    *,
    base_url: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """AI 호출용 클라이언트. 호출마다 새로 연다 (compose 내부망이라 연결 비용이 작다).

    `base_url` 을 주지 않으면 `AI_BASE_URL` 을 쓴다. 시험이 `transport` 를 끼운다.
    """
    options: dict[str, Any] = {} if transport is None else {"transport": transport}
    return httpx.AsyncClient(
        base_url=(base_url or settings.ai_base_url).rstrip("/"),
        timeout=timeout,
        verify=_ssl_context(),
        event_hooks=AI_RESPONSE_HOOKS,
        **options,
    )
