"""서비스가 부르는 진입 함수와 오류 모양.

세 진입 함수(대본 평가 기준 · Take 채점 · 확인 반영)가 하위 함수(`script_analysis` · `stt_evaluation`)를
감싸 API 모양(`failed` · `slide_numbers` · Take 값)으로 바꾼다. 하위 함수는 장별 실패를 `stats["failed"]`
({장 번호: 에러 코드})로 돌려주고, 여기서 `failed` 목록(고정 문구)으로 만든다.

- `analyze_script`: `/scripts/rubric` (대본 → 장별 평가 기준)
- `evaluate_take`: `/takes/analyze` (장별 STT → 장별 채점 + Take 값)
- `rescore_evaluation`: `/takes/rescore` (확인 답을 반영해 LLM 없이 다시 계산)

- `evaluate_take` 는 장마다 STT 완전성(`stt_integrity`)을 정한다. 끊겨 비어 있는 장은 채점하지 않고 `STT_INCOMPLETE`,
  평가 기준이 없는 장은 `RUBRIC_MISSING` 이며 대본 글이 있으면 간이 값(`fallback`)을 붙인다.
  응답 맨 위 `detail` 은 `take_detail` 이 만든다 (analyze · rescore 같은 모양).
- 요청 전체를 실패로 돌려줄 때는 `CoverageError` 를 낸다. API 가 `status_code` 와 `{"error": {code, message}}` 로 바꾼다.
- 일부 장만 실패하면 성공한 장은 결과에, 실패한 장은 `failed` 에 담아 200 으로 돌려준다.
  대상 장이 모두 실패하면 요청 전체 실패다. 단 `/takes/analyze` 는 실패 장의 간이 값이 하나라도 있으면
  `evaluations` 가 빈 200 이다 (architecture.md 4절 '장별 API').
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
from .shared.text import normalize_script
from .stt_evaluation.align import positioned_tokens
from .stt_evaluation.confirm import rescore_evaluation as rescore_slide
from .stt_evaluation.core import evaluate_take as evaluate_slides
from .stt_evaluation.detail import slide_detail
from .stt_evaluation.detail import stt_integrity as slide_integrity
from .stt_evaluation.fallback import no_rubric_fallback
from .stt_evaluation.normalize import normalize_stt
from .stt_evaluation.schemas import Fallback, SlideEvaluation, SlideSTT, Take
from .stt_evaluation.scoring import take_scores as slide_take_scores
from .stt_evaluation.speech_mix import build_offscript, build_speech_mix, said_in_other_slides
from .take_detail import take_detail
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
    "STT_INCOMPLETE": 422,
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
    offscript: Any = None  # OffscriptJudgment — 대본 밖 분류(설정 OFFSCRIPT_LLM_ENABLED 일 때만)


def _all_failed(failed: dict[int, str]) -> CoverageError:
    """대상 장이 모두 실패했고 간이 값도 없을 때의 요청 전체 오류.

    LLM 코드가 하나라도 있으면 그중 장 번호가 가장 작은 장의 코드 · 상태 · 고정 문구를 쓴다.
    없으면 모두 `RUBRIC_MISSING` 일 때 422 `RUBRIC_MISSING`, 그 밖에는 장 번호가 가장 작은 장의 코드다.
    `/takes/analyze` 는 `STT_INCOMPLETE` 장이 있으면 이 오류 대신 200 으로 낸다(`evaluate_take`).
    """
    for number in sorted(failed):
        if failed[number] in LLM_ERROR_CODES:
            return llm_error(failed[number])
    if all(c == "RUBRIC_MISSING" for c in failed.values()):
        code = "RUBRIC_MISSING"
        return CoverageError(STATUS_CODES[code], code, ALL_RUBRIC_MISSING_MESSAGE)
    first = min(failed)
    code = failed[first]
    return CoverageError(STATUS_CODES[code], code, _failure_message(first, code))


def _rubric_missing_message(slide_number: int) -> str:
    return f"{slide_number}번 장의 평가 기준이 없습니다."


def _failure_message(slide_number: int, code: str) -> str:
    """실패 문구. `RUBRIC_MISSING` · `STT_INCOMPLETE` 는 장 번호가 앞에 붙고, LLM 코드는 고정 문구다."""
    if code == "RUBRIC_MISSING":
        return _rubric_missing_message(slide_number)
    if code == "STT_INCOMPLETE":
        return f"{slide_number}번 장: 음성 인식이 끊겨 이 장을 채점하지 않았습니다."
    return ERROR_MESSAGES[code]


def _failures(
    failed: dict[int, str], fallbacks: dict[int, Fallback] | None = None
) -> list[SlideFailure]:
    """{장 번호: 에러 코드} → 장 번호 순 `failed` 목록. 문구는 코드마다 고정이고 장 번호만 들어간다. 간이 값을 붙인다."""
    fallbacks = fallbacks or {}
    return [
        SlideFailure(
            slide_number=n,
            error=ErrorBody(code=c, message=_failure_message(n, c)),
            fallback=fallbacks.get(n),
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
    rubrics: dict[int, EvaluationRubric],
    integrity: dict[int, str],
) -> EvaluationResponse:
    evaluations = sorted(evaluations, key=lambda e: e.slide_number)
    return EvaluationResponse(
        take_id=take_id,
        evaluations=evaluations,
        take_scores=take_scores(evaluations, targets),
        confirmations=confirmations(evaluations),
        failed=sorted(failures, key=lambda f: f.slide_number),
        detail=take_detail(evaluations, failures, targets, rubrics, integrity),
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
    대상 장이 없으면 (`slide_numbers=[]`) `rubrics` 와 상관없이 빈 결과 200 이다. `rubrics` 가 비어도 장마다
    STT 완전성을 먼저 정하고(끊겨 빈 장은 `STT_INCOMPLETE`) 나머지를 `RUBRIC_MISSING` 으로 둔다. 대상 장이 모두
    실패하고 간이 값도 `STT_INCOMPLETE` 장도 없으면 `_all_failed` 가 오류를 고른다 (모두 `RUBRIC_MISSING` 이면 422 `RUBRIC_MISSING`).
    STT 완전성(`stt_integrity`)이 `incomplete` 인데 STT 가 빈 장은 채점하지 않고 `failed` (`STT_INCOMPLETE`, 간이 값 없음)이다.
    평가 기준이 없는 장은 `script` 가 있으면 간이 값(`lexical_no_rubric`)을 붙인다. 대상 장이 모두 실패해도 간이 값이
    하나라도 있거나 `STT_INCOMPLETE` 장이 있으면 `evaluations` 가 빈 200 이고(에러 본문은 BE 가 읽지 않아 장마다
    이유를 보일 수 있는 실패는 200 으로 낸다), 둘 다 없으면 `_all_failed` 의 오류다. `llms` 가 None 이면
    503 `LLM_UNAVAILABLE`. `meta.model` 은 LLM 출력을 쓴 채점(STT 가 빈 장이 아닌 것)이 있을 때만 모델 이름이다.
    대본 식별자 `script_name` 은 평가 기준 중 장 번호가 가장 작은 것의 `meta.script_name` 에서 가져온다.
    """
    request = AnalyzeRequest.model_validate(request)
    if llms is None:
        raise llm_error("LLM_UNAVAILABLE")
    by_slide: dict[int, EvaluationRubric] = {r.meta.slide_number: r for r in request.rubrics}
    stt = {s.slide_number: s.stt for s in request.slides}
    info = {s.slide_number: s for s in request.slides}
    targets = sorted(set(request.slide_numbers) if request.slide_numbers is not None else set(stt))
    scripts = {n: (info[n].script or "") for n in targets}
    integrity = {
        n: slide_integrity(info[n].duration_ms, info[n].stt_gap_ms, info[n].stt_unobserved_ms)
        for n in targets
    }
    failed: dict[int, str] = {}
    fallbacks: dict[int, Fallback] = {}
    for n in targets:
        if integrity[n] == "incomplete" and not normalize_stt(stt[n]).text:
            failed[n] = "STT_INCOMPLETE"  # 인식이 끊겨 빈 것을 '빠뜨림'으로 판정하지 않는다
        elif n not in by_slide:
            failed[n] = "RUBRIC_MISSING"
            if scripts[n].strip():
                fallbacks[n] = no_rubric_fallback(scripts[n], stt[n], stt_integrity=integrity[n])
    ready = [n for n in targets if n not in failed and n in by_slide]
    evaluations: list[SlideEvaluation] = []
    if ready:
        take = Take(
            script_name=by_slide[min(by_slide)].meta.script_name,
            take_id=request.take_id,
            slides=[SlideSTT(slide_number=n, stt=stt[n]) for n in ready],
        )
        evaluations, stats = evaluate_slides(
            take,
            by_slide,  # 채점하지 않는 다른 장의 평가 기준도 '다른 장 내용'을 알아보는 데 쓴다
            eval_llm=llms.eval,
            verifier_llm=llms.verifier,
            model=model,
            cache=cache,
            max_workers=MAX_WORKERS,
            integrity=integrity,
            offscript_llm=llms.offscript,
        )
        failed |= stats["failed"]
        fallbacks |= stats.get("fallbacks", {})
    if targets and not evaluations and not fallbacks and "STT_INCOMPLETE" not in failed.values():
        raise _all_failed(failed)
    used_llm = any(e.stt_text for e in evaluations)
    return _scoring_response(
        request.take_id,
        evaluations,
        targets,
        _failures(failed, fallbacks),
        model if used_llm else None,
        by_slide,
        integrity,
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
    _rebuild_details(rescored, by_slide)
    scored = {e.slide_number for e in rescored}
    targets = scored | {f.slide_number for f in request.failed}
    failures: dict[int, SlideFailure] = {}
    for f in request.failed:  # 같은 장이 둘이면 처음 것
        if f.slide_number not in scored:
            failures.setdefault(f.slide_number, f)
    integrity = {e.slide_number: e.stt_integrity or "unknown" for e in rescored}
    return _scoring_response(
        request.take_id,
        rescored,
        targets,
        list(failures.values()),
        None,
        by_slide,
        integrity,
    )


def _rebuild_details(
    evaluations: list[SlideEvaluation], rubrics: dict[int, EvaluationRubric]
) -> None:
    """다시 계산한 채점의 장 상세를 만든다 (제자리). 말의 구성은 처음 채점의 관찰값을 그대로 둔다.

    예전 채점(`detail` · `offscript` 없음)은 `stt_text` 로 대본 밖 구간 · 말의 구성을 만든다 (바로 반복한 말은 세지 않는다).
    """
    mixes = {}
    for e in evaluations:
        rubric = rubrics[e.slide_number]
        e.stt_integrity = e.stt_integrity or "unknown"
        old_mix = e.detail.speech_mix if e.detail is not None else None
        if e.offscript is None or old_mix is None:
            stt_norm = normalize_script(e.stt_text)
            ptokens = positioned_tokens(stt_norm)
            if e.offscript is None:
                e.offscript = build_offscript(
                    rubric,
                    stt_norm,
                    ptokens,
                    e.alignments,
                    {n: r for n, r in rubrics.items() if n != e.slide_number},
                )
            old_mix = old_mix or build_speech_mix(e.offscript, ptokens, None)
        mixes[e.slide_number] = old_mix
    said = said_in_other_slides(
        {e.slide_number: (e.offscript, e.stt_sentences) for e in evaluations}, rubrics
    )
    for e in evaluations:
        e.detail = slide_detail(
            e,
            rubrics[e.slide_number],
            stt_integrity=e.stt_integrity,
            speech_mix=mixes[e.slide_number],
            said={kp: nums for (n, kp), nums in said.items() if n == e.slide_number},
        )


def _input_error(code: str, message: str) -> CoverageError:
    return CoverageError(STATUS_CODES[code], code, message)
