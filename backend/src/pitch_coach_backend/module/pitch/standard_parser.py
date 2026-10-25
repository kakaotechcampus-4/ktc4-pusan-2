"""평가 기준 AI 응답을 읽는다. DB를 모르고 호출 전체를 30초로 제한한다."""

import asyncio
import uuid

import httpx

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.module.pitch.dto import StandardAIResponseDTO
from pitch_coach_backend.module.pitch.exception import StandardInputRejected, StandardParseFailed

ATTEMPT_TIMEOUT_SEC = 30.0


async def parse_standard_text(text: str) -> StandardAIResponseDTO:
    request_id = uuid.uuid4().hex
    try:
        # httpx의 read timeout은 바이트 사이 간격이므로 요청 전체 상한을 함께 둔다.
        async with (
            asyncio.timeout(ATTEMPT_TIMEOUT_SEC),
            httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client,
        ):
            response = await client.post(
                f"{settings.ai_base_url}/evaluation-criteria/parse",
                json={"standard_text": text},
                headers={"X-Request-ID": request_id},
            )
            if response.status_code == 422:
                raise StandardInputRejected()
            if response.status_code != 200:
                raise StandardParseFailed()
            return StandardAIResponseDTO.model_validate(response.json())
    except (httpx.HTTPError, ValueError, TypeError, TimeoutError) as exc:
        # 오류 본문·사용자 입력은 FE와 로그에 노출하지 않는다. 동기 API라 재시도하지 않는다.
        raise StandardParseFailed() from exc
