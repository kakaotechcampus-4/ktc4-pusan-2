"""서비스가 부르는 진입 함수와 오류 모양.

세 진입 함수(대본 평가 기준 · Take 채점 · 확인 반영)가 하위 함수(`script_analysis` · `stt_evaluation`)를
감싸 API 모양(`failed` · `slide_numbers` · Take 값)으로 바꾼다. 하위 함수는 장별 실패를 `stats["failed"]`
({장 번호: 에러 코드})로 돌려주고, 여기서 `failed` 목록(고정 문구)으로 만든다.

- `analyze_script`: `/scripts/rubric` (대본 → 장별 평가 기준)
- `evaluate_take`: `/takes/analyze` (장별 STT → 장별 채점 + Take 값)
- `rescore_evaluation`: `/takes/rescore` (확인 답을 반영해 LLM 없이 다시 계산)

- 요청 전체를 실패로 돌려줄 때는 `CoverageError` 를 낸다. API 가 `status_code` 와 `{"error": {code, message}}` 로 바꾼다.
- 일부 장만 실패하면 성공한 장은 결과에, 실패한 장은 `failed` 에 담아 200 으로 돌려준다.
  대상 장이 모두 실패하면 요청 전체 실패다 (architecture.md 4절 '장별 API').
- LLM 과 캐시는 인자로 받는다. 코어는 파일 · 환경변수 · 시계를 쓰지 않는다.
"""

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from .config import CRITERIA_VERSION, MAX_WORKERS, RUBRIC_MAX_WORKERS
from .schemas import (
    AnalyzeRequest,
    Confirmation,
    ErrorBody,
    EvaluationResponse,
    RescoreRequest,
    ResponseMeta,
    RubricRequest,
    RubricResponse,
    ScoringMeta,
    SlideFailure,
    TakeScores,
)
from .script_analysis.core import analyze_script as analyze_slides
from .script_analysis.schemas import SlideScript
from .shared.llm_step import ERROR_MESSAGES, LLM_ERROR_CODES, LLMCache
from .shared.rubric import EvaluationRubric
from .stt_evaluation.confirm import rescore_evaluation as rescore_slide
from .stt_evaluation.core import evaluate_take as evaluate_slides
from .stt_evaluation.schemas import SlideEvaluation, SlideSTT, Take
from .stt_evaluation.scoring import take_scores as slide_take_scores
from .version import FEATURE_VERSION, SCHEMA_VERSION

# 에러 코드 → HTTP 상태 (기능 문서 7절). LLM 코드의 문구는 `ERROR_MESSAGES`
STATUS_CODES = {
    "LLM_TIMEOUT": 504,
    "LLM_ERROR": 502,
    "LLM_INVALID_OUTPUT": 502,
    "LLM_UNAVAILABLE": 503,
    "RUBRIC_MISSING": 422,
    "RUBRIC_MISMATCH": 422,
    "STT_TEXT_MISSING": 422,
}
ALL_RUBRIC_MISSING_MESSAGE = "채점할 장의 평가 기준이 없습니다."


class CoverageError(Exception):
    """요청 전체를 실패로 돌려줄 때 (API 가 status_code 와 {"error": {code, message}} 로 바꾼다)."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message


def llm_error(code: str) -> CoverageError:
    """LLM 에러 코드 → 요청 전체 오류 (상태 · 고정 문구)."""
    return CoverageError(STATUS_CODES[code], code, ERROR_MESSAGES[code])


@dataclass(frozen=True)
class ScriptLLMs:
    """`/scripts/rubric` 의 LLM 묶음. 출력 스키마마다 하나다 (기능 문서 6-5)."""

    semantic: Any  # SlideSemanticAnalysis
    final: Any  # FinalReview
    unit: Any  # SlideUnits


@dataclass(frozen=True)
class TakeLLMs:
    """`/takes/analyze` 의 LLM 묶음 (의미 평가 · 교차 검증)."""

    eval: Any  # SemanticEvaluation
    verifier: Any  # VerifierResult


def _all_failed(failed: dict[int, str]) -> CoverageError:
    """대상 장이 모두 실패했을 때의 요청 전체 오류.

    LLM 코드가 하나라도 있으면 그중 장 번호가 가장 작은 장의 코드 · 상태 · 고정 문구를 쓴다.
    모두 `RUBRIC_MISSING` 일 때만 422 `RUBRIC_MISSING` 이다.
    """
    for number in sorted(failed):
        if failed[number] in LLM_ERROR_CODES:
            return llm_error(failed[number])
    code = "RUBRIC_MISSING"
    return CoverageError(STATUS_CODES[code], code, ALL_RUBRIC_MISSING_MESSAGE)


def _rubric_missing_message(slide_number: int) -> str:
    return f"{slide_number}번 장의 평가 기준이 없습니다."


def _failures(failed: dict[int, str]) -> list[SlideFailure]:
    """{장 번호: 에러 코드} → 장 번호 순 `failed` 목록. 문구는 코드마다 고정이고 장 번호만 들어간다."""
    return [
        SlideFailure(
            slide_number=n,
            error=ErrorBody(
                code=c,
                message=_rubric_missing_message(n) if c == "RUBRIC_MISSING" else ERROR_MESSAGES[c],
            ),
        )
        for n, c in sorted(failed.items())
    ]


def analyze_script(
    request: RubricRequest | dict,
    *,
    llms: ScriptLLMs | None,
    model: str,
    cache: LLMCache | None = None,
) -> RubricResponse:
    """`/scripts/rubric`: 대본의 장별 평가 기준을 만든다.

    `slide_numbers` 가 있으면 그 장만 만들고, 나머지 장은 키워드 · 이름 판정의 맥락으로만 쓴다.
    `slide_numbers` 가 빈 목록이면 대상이 없으므로 빈 결과를 돌려준다. `llms` 가 None 이면
    (LLM 설정 없음) 요청 전체가 503 `LLM_UNAVAILABLE` 이다.
    `meta.model` 은 LLM 출력(캐시 포함)을 쓴 결과가 있을 때만 모델 이름이고, 읽을 내용이 있는 장이
    하나도 없으면 (빈 대본 장뿐이거나 대상이 없음) None 이다 (architecture.md 5절).
    """
    request = RubricRequest.model_validate(request)
    if llms is None:
        raise llm_error("LLM_UNAVAILABLE")
    slides = [SlideScript(slide_number=s.slide_number, script=s.script) for s in request.slides]
    rubrics, stats = analyze_slides(
        request.script_version_id,
        slides,
        semantic_llm=llms.semantic,
        final_llm=llms.final,
        unit_llm=llms.unit,
        model=model,
        cache=cache,
        max_workers=RUBRIC_MAX_WORKERS,
        targets=request.slide_numbers,
    )
    if stats["slides"] and not rubrics:
        raise _all_failed(stats["failed"])
    used_llm = any(r.normalized_script for r in rubrics)
    return RubricResponse(
        rubrics=sorted(rubrics, key=lambda r: r.meta.slide_number),
        failed=_failures(stats["failed"]),
        meta=ResponseMeta(
            schema_version=SCHEMA_VERSION,
            feature_version=FEATURE_VERSION,
            model=model if used_llm else None,
        ),
    )


# ── 채점 ─────────────────────────────────────────────────────


def take_scores(evaluations: list[SlideEvaluation], targets: Collection[int]) -> TakeScores:
    """Take 값: 채점된 장들의 점수로 낸 가중 평균 · 합(기능 문서 6-4)에 `scored_ratio` 를 더한다.

    `scored_ratio` = 채점된 대상 장 수 ÷ 대상 장 수 (대상이 없으면 0.0). 평가 기준이 없거나 실패한 장도
    대상 장에 들어가므로, 일부만 채점되면 1 보다 작다.
    """
    scored = {e.slide_number for e in evaluations} & set(targets)
    ratio = round(len(scored) / len(set(targets)), 3) if targets else 0.0
    return TakeScores(**slide_take_scores(evaluations), scored_ratio=ratio)


def confirmations(evaluations: list[SlideEvaluation]) -> list[Confirmation]:
    """아직 답을 받지 않은 비슷한 말 전체: 장 번호 순, 장 안에서는 `similar_items` 순."""
    return [
        Confirmation(
            slide_number=e.slide_number,
            item_id=item.item_id,
            script_text=item.script_text,
            stt_text=item.stt_text,
        )
        for e in sorted(evaluations, key=lambda e: e.slide_number)
        for item in e.similar_items
        if item.item_id not in e.applied_answers
    ]


def _scoring_response(
    take_id: str,
    evaluations: list[SlideEvaluation],
    targets: Collection[int],
    failures: list[SlideFailure],
    model: str | None,
) -> EvaluationResponse:
    evaluations = sorted(evaluations, key=lambda e: e.slide_number)
    return EvaluationResponse(
        take_id=take_id,
        evaluations=evaluations,
        take_scores=take_scores(evaluations, targets),
        confirmations=confirmations(evaluations),
        failed=sorted(failures, key=lambda f: f.slide_number),
        meta=ScoringMeta(
            schema_version=SCHEMA_VERSION,
            feature_version=FEATURE_VERSION,
            criteria_versions={"script_coverage": CRITERIA_VERSION},
            model=model,
        ),
    )


def evaluate_take(
    request: AnalyzeRequest | dict,
    *,
    llms: TakeLLMs | None,
    model: str,
    cache: LLMCache | None = None,
) -> EvaluationResponse:
    """`/takes/analyze`: Take 의 장별 STT 를 평가 기준과 비교해 채점한다.

    대상 장은 `slide_numbers`(없으면 `slides` 전부)다. 평가 기준이 없는 장은 채점하지 않고 `failed`
    (`RUBRIC_MISSING`)에 담되 `scored_ratio` 의 대상 장에는 들어가며, 나머지 장은 계속한다.
    대상 장이 없으면 (`slide_numbers=[]`) `rubrics` 와 상관없이 빈 결과 200 이다. 대상이 있는데 `rubrics` 가
    비었거나 대상 장이 모두 평가 기준이 없으면 422 `RUBRIC_MISSING` 이다. `llms` 가 None 이면
    503 `LLM_UNAVAILABLE`. `meta.model` 은 LLM 출력을 쓴 채점(STT 가 빈 장이 아닌 것)이 있을 때만 모델 이름이다.
    대본 식별자 `script_name` 은 평가 기준 중 장 번호가 가장 작은 것의 `meta.script_name` 에서 가져온다.
    """
    request = AnalyzeRequest.model_validate(request)
    if llms is None:
        raise llm_error("LLM_UNAVAILABLE")
    by_slide: dict[int, EvaluationRubric] = {r.meta.slide_number: r for r in request.rubrics}
    stt = {s.slide_number: s.stt for s in request.slides}
    targets = sorted(set(request.slide_numbers) if request.slide_numbers is not None else set(stt))
    if not request.rubrics and targets:
        raise _input_error("RUBRIC_MISSING", ALL_RUBRIC_MISSING_MESSAGE)
    failed = {n: "RUBRIC_MISSING" for n in targets if n not in by_slide}
    ready = [n for n in targets if n in by_slide]
    evaluations: list[SlideEvaluation] = []
    if ready:
        take = Take(
            script_name=by_slide[min(by_slide)].meta.script_name,
            take_id=request.take_id,
            slides=[SlideSTT(slide_number=n, stt=stt[n]) for n in ready],
        )
        evaluations, stats = evaluate_slides(
            take,
            {n: by_slide[n] for n in ready},
            eval_llm=llms.eval,
            verifier_llm=llms.verifier,
            model=model,
            cache=cache,
            max_workers=MAX_WORKERS,
        )
        failed |= stats["failed"]
    if targets and not evaluations:
        raise _all_failed(failed)
    used_llm = any(e.stt_text for e in evaluations)
    return _scoring_response(
        request.take_id, evaluations, targets, _failures(failed), model if used_llm else None
    )


def rescore_evaluation(request: RescoreRequest | dict) -> EvaluationResponse:
    """`/takes/rescore`: 저장해 둔 채점에 확인 답을 반영해 다시 계산한다 (LLM 없음).

    이미 반영한 답(`applied_answers`)은 다시 적용하지 않고 새 항목의 답만 더한다 (기능 문서 6-3). 답이 없으면
    Take 값만 다시 계산한다. 채점한 장의 평가 기준이 없거나(`RUBRIC_MISSING`), `rubric_id` 가 다르거나
    (`RUBRIC_MISMATCH`), 채점에 `stt_text` 필드가 없으면(`STT_TEXT_MISSING`) 장 번호 순으로 검사해 첫 문제에서
    요청 전체를 422 로 거절한다. 빈 문자열 `stt_text`(발표에서 넘어간 장)는 정상이다.
    대상 장은 `evaluations` 의 장과 요청 `failed` 의 장이고, `failed` 에서는 채점이 있는 장을 빼고 같은 장이 둘이면 처음 것만 둔다.
    `meta.model` 은 늘 None 이다.
    """
    request = RescoreRequest.model_validate(request)
    by_slide = {r.meta.slide_number: r for r in request.rubrics}
    evaluations = sorted(request.evaluations, key=lambda e: e.slide_number)
    for e in evaluations:
        n = e.slide_number
        rubric = by_slide.get(n)
        if rubric is None:
            raise _input_error("RUBRIC_MISSING", _rubric_missing_message(n))
        if rubric.meta.rubric_id != e.rubric_id:
            raise _input_error(
                "RUBRIC_MISMATCH",
                f"{n}번 장의 채점과 평가 기준이 맞지 않습니다. 다시 채점해야 합니다.",
            )
        if "stt_text" not in e.model_fields_set:
            raise _input_error(
                "STT_TEXT_MISSING",
                f"{n}번 장의 채점에 STT 텍스트가 없습니다. 다시 채점해야 합니다.",
            )
    answers: dict[int, dict[str, str]] = {}
    for a in request.answers:  # 같은 장 · 같은 항목의 답이 둘이면 처음 것
        answers.setdefault(a.slide_number, {}).setdefault(a.item_id, a.answer)
    rescored = [
        rescore_slide(e, answers.get(e.slide_number, {}), by_slide[e.slide_number])
        for e in evaluations
    ]
    scored = {e.slide_number for e in rescored}
    targets = scored | {f.slide_number for f in request.failed}
    failures: dict[int, SlideFailure] = {}
    for f in request.failed:  # 같은 장이 둘이면 처음 것
        if f.slide_number not in scored:
            failures.setdefault(f.slide_number, f)
    return _scoring_response(request.take_id, rescored, targets, list(failures.values()), None)


def _input_error(code: str, message: str) -> CoverageError:
    return CoverageError(STATUS_CODES[code], code, message)
