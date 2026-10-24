"""`POST /sample/judge` — 규약 확인용 대역. 판정 모듈이 서비스 규약으로 붙는지 본다.

- 부르는 곳: AI 담당의 로컬 확인뿐이다. BE 는 부르지 않는다 (`docs/ai/ai-service.md` 4-2)
- 등록: `AI_ENABLE_SAMPLE=true` 일 때만 `main.create_app` 이 붙인다. 기본(false)이면
  경로가 없어 공통 404 `NOT_FOUND` 다
- 입력: `{t_ms, since_ms, records}`. 모르는 필드는 무시하고, 형식이 틀리면 공통 422
  `VALIDATION_ERROR` 다(`common/errors.py`)
- 출력: 200 `{results: [JudgmentResult], meta}`. 판정 응답이라 `meta` 에 `criteria_versions` 를
  싣고, LLM 을 쓰지 않아 `model` 은 null 이다 (`docs/ai/architecture.md` 5절).
  예시는 `features/sample/examples/`
- 라우터는 요청 검사 → 코어 호출 → `meta` 붙이기만 한다. 코어가 동기라 `def` 로 쓴다
  (스레드 풀에서 돈다). 실제 판정 모듈도 같은 모양으로 붙인다
- 첫 실제 기능 엔드포인트가 붙으면 이 파일 · `features/sample/` · 그 테스트
  (`tests/test_sample.py` · `tests/unit/sample/` · conftest 의 `sample_client`) ·
  `main.py` 의 등록 · `AI_ENABLE_SAMPLE`(settings · `.env.example` · README 표)을 지운다
"""

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from pitch_coach_ai.common.meta import JudgeMeta, build_judge_meta, criteria_version
from pitch_coach_ai.features.sample import core
from pitch_coach_ai.features.sample.config import DEFAULT
from pitch_coach_ai.features.sample.schemas import JudgmentResult, SampleInput
from pitch_coach_ai.features.sample.version import FEATURE_VERSION

router = APIRouter(prefix="/sample", tags=["sample"])


class JudgeRequest(SampleInput):
    #: 판정 시각 (Take 기준 ms)
    t_ms: int = Field(ge=0)


class JudgeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[JudgmentResult]
    meta: JudgeMeta


@router.post("/judge")
def judge(req: JudgeRequest) -> JudgeResponse:
    results = core.judge(req, req.t_ms)
    meta = build_judge_meta(
        FEATURE_VERSION,
        {core.EVALUATOR: criteria_version(FEATURE_VERSION, DEFAULT)},
    )
    return JudgeResponse(results=results, meta=meta)
