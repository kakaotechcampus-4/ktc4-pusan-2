"""Take 분석의 AI 호출. DB 를 모른다 (pitch/adapter 와 같은 자리)."""

import asyncio
import uuid

import httpx

from pitch_coach_backend.core.ai_http import REQUEST_ID_HEADER, ai_client, retryable_status
from pitch_coach_backend.module.take.ai_response import (
    CoverageCallError,
    CoverageFailureCode,
    validate_coverage_reply,
    validate_review_reply,
)
from pitch_coach_backend.module.take.dto import ReviewRequestDTO, ReviewResponseDTO
from pitch_coach_backend.module.take.exception import InvalidReviewResponse

REVIEW_RETRY_DELAY_SEC = 1.0
REVIEW_TIMEOUT_SEC = 30.0
ANALYZE_TIMEOUT_SEC = 130.0
RESCORE_TIMEOUT_SEC = 10.0


async def request_review_async(
    take_id: uuid.UUID, review_request: ReviewRequestDTO
) -> ReviewResponseDTO:
    """AI 서버에 리뷰를 요청한다. 응답이 계약과 다르거나 다른 Take 의 것이면
    InvalidReviewResponse(502). 저장하다 DB 제약에 걸리기 전에 여기서 거른다."""
    request_id = uuid.uuid4().hex
    body = review_request.model_dump(mode="json")
    async with ai_client(REVIEW_TIMEOUT_SEC) as client:
        for attempt in range(2):
            try:
                # httpx timeout 은 읽기 한 번의 제한이라 조금씩 오는 응답은 끝없이 기다린다.
                # 시도마다 전체 시간을 따로 막는다.
                async with asyncio.timeout(REVIEW_TIMEOUT_SEC):
                    response = await client.post(
                        "/takes/review", json=body, headers={REQUEST_ID_HEADER: request_id}
                    )
            except (httpx.HTTPError, TimeoutError) as exc:
                if attempt:
                    raise InvalidReviewResponse() from exc
            else:
                if response.status_code == 200:
                    try:
                        reply = response.json()
                    except ValueError as exc:
                        raise InvalidReviewResponse() from exc
                    return validate_review_reply(reply, take_id)
                if attempt or not retryable_status(response.status_code):
                    raise InvalidReviewResponse()
            await asyncio.sleep(REVIEW_RETRY_DELAY_SEC)
    raise AssertionError("unreachable")


def _call_failure(error: httpx.HTTPError | TimeoutError) -> CoverageFailureCode:
    # 연결을 맺지 못한 시간 초과는 연결 실패다. 응답을 기다리다 넘긴 것만 시간 초과로 본다
    if isinstance(error, httpx.ConnectTimeout | httpx.PoolTimeout):
        return "AI_UNAVAILABLE"
    if isinstance(error, TimeoutError | httpx.TimeoutException):
        return "AI_TIMEOUT"
    # 본문을 받다 크기 상한(`AiResponseTooLarge`)에 걸렸거나 압축을 풀지 못했다
    if isinstance(error, httpx.DecodingError):
        return "AI_INVALID_RESPONSE"
    return "AI_UNAVAILABLE"


# 나머지 상태 코드(500 · 502 · 그 밖 5xx · 408 · 429 · 계약에 없는 코드)는 AI_ERROR 다
_STATUS_FAILURES: dict[int, CoverageFailureCode] = {
    504: "AI_TIMEOUT",
    503: "AI_UNAVAILABLE",
    422: "AI_REJECTED",
}


async def post_coverage(path: str, body: dict, request_id: str, timeout_sec: float) -> dict:
    """대본 전달도 호출. 실패는 `CoverageCallError` 다 — 다시 불러 볼지(`retryable`)와 실패 이유
    (`failure_code`)를 호출 오류 · 상태 코드로 정한다. 에러 본문은 읽지 않는다."""
    try:
        async with asyncio.timeout(timeout_sec), ai_client(timeout_sec) as client:
            response = await client.post(path, json=body, headers={REQUEST_ID_HEADER: request_id})
    except (httpx.HTTPError, TimeoutError) as e:
        raise CoverageCallError(
            type(e).__name__, retryable=True, failure_code=_call_failure(e)
        ) from e
    if response.status_code != 200:
        raise CoverageCallError(
            f"status={response.status_code}",
            retryable=retryable_status(response.status_code),
            failure_code=_STATUS_FAILURES.get(response.status_code, "AI_ERROR"),
        )
    try:
        reply = response.json()
    except ValueError as exc:
        raise CoverageCallError(
            "invalid coverage JSON", retryable=False, failure_code="AI_INVALID_RESPONSE"
        ) from exc
    return validate_coverage_reply(reply, body["take_id"])


async def request_analyze(body: dict, request_id: str) -> dict:
    return await post_coverage("/takes/analyze", body, request_id, ANALYZE_TIMEOUT_SEC)


async def request_rescore(body: dict, request_id: str) -> dict:
    return await post_coverage("/takes/rescore", body, request_id, RESCORE_TIMEOUT_SEC)
