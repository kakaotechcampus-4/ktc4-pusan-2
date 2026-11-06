"""평가 기준 AI 응답을 읽는다. DB를 모르고 호출 전체를 30초로 제한한다.

실패는 FE 에 502 로 나가고 전역 핸들러가 남기지 않는다. 그래서 상태 · 요청 ID · 오류 종류만 여기서
경고로 남긴다. 오류 본문 · 사용자 입력은 남기지 않는다.
"""

import asyncio
import logging
import uuid
from typing import Annotated, Any, Literal

import httpx
from pydantic import (
    AfterValidator,
    BaseModel,
    Field,
    StringConstraints,
    model_validator,
)

from pitch_coach_backend.core.ai_http import REQUEST_ID_HEADER, ai_client
from pitch_coach_backend.core.validation import reject_nul, reject_unstorable_json
from pitch_coach_backend.module.pitch.exception import StandardInputRejected, StandardParseFailed

logger = logging.getLogger(__name__)

ATTEMPT_TIMEOUT_SEC = 30.0


class _StandardTarget(BaseModel):
    display: str
    metric: str
    operator: Literal["LTE", "GTE", "LT", "GT"]
    value: float = Field(strict=True, allow_inf_nan=False)


class StandardReply(BaseModel):
    # AI의 표시 문장은 자르거나 다듬지 않는다. 조건과 targets의 display 짝이 달라질 수 있다.
    display_criteria: list[
        Annotated[
            str,
            StringConstraints(min_length=1, max_length=50, pattern=r"\S"),
            AfterValidator(reject_nul),
        ]
    ] = Field(max_length=10)
    excluded: list[Annotated[str, AfterValidator(reject_nul)]] | None = None
    values: dict[str, Any]
    targets: list[dict[str, Any]] = Field(max_length=20)
    meta: dict[str, Any]

    @model_validator(mode="after")
    def _analysis_is_storable(self) -> StandardReply:
        for key, value in self.values.items():
            if key == "script_used":
                if value is not None and type(value) is not bool:
                    raise ValueError("script_used는 bool 또는 null이어야 합니다.")
            elif value is not None and not isinstance(value, str):
                raise ValueError("요소 값은 문자열 또는 null이어야 합니다.")
        for target in self.targets:
            checked = _StandardTarget.model_validate(target)
            if checked.display not in self.display_criteria:
                raise ValueError("목표에 해당하는 표시 기준이 없습니다.")
        version = self.meta.get("schema_version")
        if not isinstance(version, str) or version.split(".", 1)[0] != "1":
            raise ValueError("지원하지 않는 평가 기준 응답 버전입니다.")
        if not isinstance(self.meta.get("feature_version"), str):
            raise ValueError("기능 버전이 없습니다.")
        if self.meta.get("model") is not None and not isinstance(self.meta["model"], str):
            raise ValueError("모델 정보가 올바르지 않습니다.")
        payload = self.model_dump(mode="python")
        reject_unstorable_json(payload)
        return self

    @property
    def script_used(self) -> bool | None:
        return self.values.get("script_used")


async def parse_standard_text(text: str) -> StandardReply:
    request_id = uuid.uuid4().hex
    try:
        # httpx의 read timeout은 바이트 사이 간격이므로 요청 전체 상한을 함께 둔다.
        async with (
            asyncio.timeout(ATTEMPT_TIMEOUT_SEC),
            ai_client(httpx.Timeout(ATTEMPT_TIMEOUT_SEC, connect=5.0)) as client,
        ):
            response = await client.post(
                "/evaluation-criteria/parse",
                json={"standard_text": text},
                headers={REQUEST_ID_HEADER: request_id},
            )
            if response.status_code == 422:
                logger.warning("평가 기준 분석 거절 status=422 request_id=%s", request_id)
                raise StandardInputRejected()
            if response.status_code != 200:
                logger.warning(
                    "평가 기준 분석 실패 status=%s request_id=%s",
                    response.status_code,
                    request_id,
                )
                raise StandardParseFailed()
            return StandardReply.model_validate(response.json())
    except (httpx.HTTPError, ValueError, TypeError, TimeoutError) as exc:
        # 오류 본문·사용자 입력은 FE와 로그에 노출하지 않는다. 동기 API라 재시도하지 않는다.
        logger.warning("평가 기준 분석 실패 error=%s request_id=%s", type(exc).__name__, request_id)
        raise StandardParseFailed() from exc
