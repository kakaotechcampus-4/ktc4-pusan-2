"""장 상세: 기존 점수(`scores`)와 판정 위에서 계산만 하는 지표 (상쇄 없는 전달률 · 상태 · 이유 · 표현 방식).

기존 점수와 판정은 바꾸지 않는다. 값을 계산할 수 없으면 0 이 아니라 null 이고, `state` · `reason_codes` 가
까닭을 말한다. Take 상세(`take_detail.py`)도 같은 계산(`key_point_stats` · `status_shares` · `fact_parts`)을 쓴다.

- `expression_mode` · `annotate_expression`: said 문장의 표현 방식 (대본 표현에 가까움 · 바꿔 말함 · 구분 어려움)
- `stt_integrity`: 요청의 STT 끊김 값으로 STT 완전성 (`complete` · `incomplete` · `unknown`)
- `key_point_stats` · `status_shares`: 핵심 내용 전달률 · 다르게 말한 몫 · 판정 몫 (가중 평균)
- `fact_counts` · `fact_parts`: 수치 상태별 개수 · 정확도 계산 재료
- `slide_detail`: 장 상세(`SlideDetail`) 한 개
"""

from ..config import SCORE_WEIGHT
from ..shared.rubric import EvaluationRubric
from ..shared.text import normalize_script
from .align import positioned_tokens
from .config import (
    CRITICAL_GAP_IMPORTANCE,
    DELIVERY_SCORE,
    EXPRESSION_PARAPHRASE_MAX,
    EXPRESSION_VERBATIM_MIN,
    FACT_SCORE,
    SENTENCE_FALLBACK_PROVISIONAL,
    STATUS_SCORE,
    STT_GAP_MIN_MS,
    STT_GAP_RATIO,
    STT_UNOBSERVED_MAX_RATIO,
)
from .schemas import (
    CriticalGap,
    Expression,
    FactCheck,
    FactCounts,
    FactSummary,
    Fidelity,
    KeyPointResult,
    Metric,
    SentenceResult,
    SlideDetail,
    SlideEvaluation,
    SlideLegacy,
    SpeechMix,
)

STATUSES = ("covered", "partial", "missing", "contradicted")
IMPORTANCES = ("critical", "high", "normal")


def expression_mode(sentence: SentenceResult) -> str | None:
    """said 문장의 표현 방식. 아닌 문장은 None.

    `lexical_coverage` 가 `EXPRESSION_VERBATIM_MIN` 이상이면 `verbatim_like`, `EXPRESSION_PARAPHRASE_MAX` 미만이면
    `paraphrased`, 사이면 `undetermined`. 바꿔 말해도 감점하지 않는 정보용 값이다.
    """
    if sentence.status != "said":
        return None
    if sentence.lexical_coverage >= EXPRESSION_VERBATIM_MIN:
        return "verbatim_like"
    if sentence.lexical_coverage < EXPRESSION_PARAPHRASE_MAX:
        return "paraphrased"
    return "undetermined"


def annotate_expression(sentences: list[SentenceResult]) -> None:
    """문장마다 `expression_mode` 를 (다시) 채운다. 판정이 바뀐 문장은 None 으로 돌아간다."""
    for s in sentences:
        s.expression_mode = expression_mode(s)


def stt_integrity(duration_ms: int | None, gap_ms: int | None, unobserved_ms: int | None) -> str:
    """STT 완전성: `complete` · `incomplete` · `unknown`.

    하나라도 없으면 `unknown`, 길이가 0 이면 `complete`. 기록 없는 시간 비율이 `STT_UNOBSERVED_MAX_RATIO` 를 넘으면
    `unknown`, ok 가 아닌 시간이 `max(STT_GAP_MIN_MS, STT_GAP_RATIO × 길이)` 이상이면 `incomplete`, 그 밖은 `complete`.
    """
    if duration_ms is None or gap_ms is None or unobserved_ms is None:
        return "unknown"
    if duration_ms == 0:
        return "complete"
    if unobserved_ms / duration_ms > STT_UNOBSERVED_MAX_RATIO:
        return "unknown"
    if gap_ms >= max(STT_GAP_MIN_MS, STT_GAP_RATIO * duration_ms):
        return "incomplete"
    return "complete"


# ── 핵심 내용 ────────────────────────────────────────────────


def key_point_stats(results: list[KeyPointResult]) -> dict:
    """핵심 내용 판정의 가중 합. 장 하나든 Take 전체든 같은 계산이다.

    `weight` 전체 가중치, `delivery` 전달 점수 합(DELIVERY_SCORE × 가중치), `legacy` 기존 점수 합(STATUS_SCORE ×
    가중치), `by_status` 판정별 가중치 합.
    """
    by_status = dict.fromkeys(STATUSES, 0)
    delivery = legacy = 0.0
    for r in results:
        w = SCORE_WEIGHT[r.importance]
        by_status[r.status] += w
        delivery += DELIVERY_SCORE[r.status] * w
        legacy += STATUS_SCORE[r.status] * w
    return {
        "weight": sum(by_status.values()),
        "delivery": delivery,
        "legacy": legacy,
        "by_status": by_status,
    }


def status_shares(by_status: dict[str, int | float]) -> dict[str, float] | None:
    """네 판정의 가중 몫 (반올림 3자리, 합이 정확히 1.0). 반올림 차이는 가장 큰 몫에 더한다. 가중치가 0 이면 None."""
    total = sum(by_status.values())
    if not total:
        return None
    shares = {s: round(by_status[s] / total, 3) for s in STATUSES}
    diff = round(1.0 - sum(shares.values()), 3)
    if diff:
        biggest = max(STATUSES, key=lambda s: (shares[s], -STATUSES.index(s)))
        shares[biggest] = round(shares[biggest] + diff, 3)
    return shares


def key_point_counts(results: list[KeyPointResult]) -> dict[str, dict[str, int]]:
    """중요도별 판정 개수. 0 인 칸도 늘 낸다."""
    counts = {i: dict.fromkeys(STATUSES, 0) for i in IMPORTANCES}
    for r in results:
        counts[r.importance][r.status] += 1
    return counts


def zero_kind(delivery_sum: float, *, stt_empty: bool, results: list[KeyPointResult]) -> str | None:
    """전달률이 0 일 때만: STT 가 비면 `slide_skipped`, 모두 missing 이면 `all_missing`, 그 밖 `missing_or_contradicted`."""
    if delivery_sum != 0 or not results:
        return None
    if stt_empty:
        return "slide_skipped"
    if all(r.status == "missing" for r in results):
        return "all_missing"
    return "missing_or_contradicted"


# ── 수치 ────────────────────────────────────────────────────


def fact_counts(fact_checks: list[FactCheck]) -> FactCounts:
    """수치 상태별 개수. sound_alike 는 `pending`, 끝내 확인 못 한 이름(unverified)은 점수가 세는 대로 `missing`."""
    counts = FactCounts()
    for c in fact_checks:
        if c.status == "sound_alike":
            counts.pending += 1
        elif c.status in ("matched", "approximate", "mismatched"):
            setattr(counts, c.status, getattr(counts, c.status) + 1)
        else:
            counts.missing += 1
    return counts


def fact_parts(fact_checks: list[FactCheck]) -> dict:
    """수치 정확도의 재료 (`scoring.slide_scores` 와 같은 가중치 · 점수).

    `weight`·`hit` 은 계산에 든(sound_alike 가 아닌) 수치의 가중치 합 · 점수 합, `pending_weight` 는 대기 수치의 가중치 합,
    `counted`·`pending` 은 수치 개수. unverified 는 점수와 같이 0 점으로 센다.
    """
    counted = [c for c in fact_checks if c.status != "sound_alike"]
    pending = [c for c in fact_checks if c.status == "sound_alike"]
    return {
        "weight": sum(SCORE_WEIGHT[c.importance] for c in counted),
        "hit": sum(SCORE_WEIGHT[c.importance] * FACT_SCORE.get(c.status, 0.0) for c in counted),
        "pending_weight": sum(SCORE_WEIGHT[c.importance] for c in pending),
        "counted": len(counted),
        "pending": len(pending),
    }


def accuracy_range(parts: dict) -> dict[str, float] | None:
    """대기 수치를 모두 맞음(best) · 모두 틀림(worst)으로 넣은 가중 정확도. 대기가 없으면 None."""
    if not parts["pending"]:
        return None
    total = parts["weight"] + parts["pending_weight"]
    return {
        "best": round((parts["hit"] + parts["pending_weight"]) / total, 3),
        "worst": round(parts["hit"] / total, 3),
    }


def _fact_summary(e: SlideEvaluation) -> FactSummary:
    parts = fact_parts(e.critical_facts)
    if not e.critical_facts:
        accuracy = Metric(value=None, state="not_applicable", reason_codes=["NO_FACTS"])
    elif not parts["counted"]:
        accuracy = Metric(
            value=None,
            state="pending",
            reason_codes=["ALL_FACTS_PENDING"],
            excluded=parts["pending"],
        )
    else:
        accuracy = Metric(
            value=e.scores["critical_fact_accuracy"],
            state="provisional" if parts["pending"] else "ok",
            reason_codes=["FACTS_PENDING"] if parts["pending"] else [],
            evaluated=parts["counted"],
            excluded=parts["pending"],
        )
    return FactSummary(
        **fact_counts(e.critical_facts).model_dump(),
        accuracy=accuracy,
        accuracy_range=accuracy_range(parts),
    )


# ── 장 상세 ─────────────────────────────────────────────────


def _fidelity(e: SlideEvaluation) -> Fidelity:
    script_tokens = e.fidelity.get("script_tokens", 0)
    if not script_tokens:
        empty = Metric(value=None, state="not_applicable")
        return Fidelity(recall=empty, precision=empty.model_copy())
    stt_tokens = len(positioned_tokens(normalize_script(e.stt_text))) if e.stt_text else 0
    recall = Metric(value=e.fidelity["recall"], state="ok", evaluated=script_tokens)
    if stt_tokens:
        precision = Metric(value=e.fidelity["precision"], state="ok", evaluated=stt_tokens)
    else:
        precision = Metric(value=None, state="not_applicable", reason_codes=["NO_SPEECH"])
    return Fidelity(recall=recall, precision=precision)


def slide_detail(
    e: SlideEvaluation,
    rubric: EvaluationRubric,
    *,
    stt_integrity: str,
    speech_mix: SpeechMix,
    said: dict[str, list[int]] | None = None,
) -> SlideDetail:
    """채점된 장 하나의 상세. 기존 점수 · 판정은 읽기만 한다.

    - 전달률 = 핵심 내용 판정 점수(covered 1 · partial 0.5 · missing 0 · contradicted 0)의 중요도 가중 평균.
      핵심 내용이 없으면 전달 · 다르게 말함은 null (`not_applicable` · `NO_KEY_POINTS`)
    - 장 상태: STT 가 있는데 불완전(`STT_INCOMPLETE`) · 문장 단위 복구 기준(`SENTENCE_FALLBACK`) · STT 가 비었는데 확인
      못 함(`STT_UNVERIFIED`)이면 `provisional`. 값이 있는 전달 · 다르게 말함이 이 상태 · 이유를 따른다
    - `said`: {핵심 내용 id: [그 내용을 말한 다른 장 번호]} — 놓친 중요 내용에 붙일 뿐 점수로 인정하지 않는다
    """
    said = said or {}
    results = e.key_points
    stats = key_point_stats(results)
    stt_empty = not e.stt_text
    reasons = []
    if not stt_empty and stt_integrity == "incomplete":
        reasons.append("STT_INCOMPLETE")
    if rubric.meta.rubric_quality == "sentence_fallback" and SENTENCE_FALLBACK_PROVISIONAL:
        reasons.append("SENTENCE_FALLBACK")
    if stt_empty and stt_integrity == "unknown":
        reasons.append("STT_UNVERIFIED")
    state = "provisional" if reasons else "ok"

    if results:
        total = stats["weight"]
        delivery = Metric(
            value=round(stats["delivery"] / total, 3),
            state=state,
            reason_codes=list(reasons),
            evaluated=len(results),
        )
        contradiction = Metric(
            value=round(stats["by_status"]["contradicted"] / total, 3),
            state=state,
            reason_codes=list(reasons),
            evaluated=len(results),
        )
        raw = round(stats["legacy"] / total, 3) + 0.0
        if raw < 0:
            legacy_zero = "clamped_negative"
        elif raw == 0 and delivery.value > 0:
            legacy_zero = "cancelled_to_zero"
        else:
            legacy_zero = None
    else:
        delivery = Metric(value=None, state="not_applicable", reason_codes=["NO_KEY_POINTS"])
        contradiction = delivery.model_copy(deep=True)
        raw, legacy_zero = None, None

    gaps = [
        CriticalGap(
            key_point_id=r.key_point_id,
            content=r.content,
            importance=r.importance,
            status=r.status,
            said_in_slides=sorted(said.get(r.key_point_id, [])),
        )
        for r in results
        if r.importance in CRITICAL_GAP_IMPORTANCE and r.status in ("missing", "contradicted")
    ]
    modes = [s.expression_mode for s in e.sentences if s.status == "said"]
    expression = Expression(
        verbatim_like=modes.count("verbatim_like"),
        paraphrased=modes.count("paraphrased"),
        undetermined=modes.count("undetermined"),
    )
    return SlideDetail(
        state=state,
        reason_codes=list(reasons),
        stt_integrity=stt_integrity,
        rubric_quality=rubric.meta.rubric_quality,
        delivery=delivery,
        zero_kind=zero_kind(stats["delivery"], stt_empty=stt_empty, results=results),
        contradiction=contradiction,
        status_shares=status_shares(stats["by_status"]),
        key_points=key_point_counts(results),
        critical_gaps=gaps,
        legacy=SlideLegacy(content_coverage_raw=raw, zero_kind=legacy_zero),
        facts=_fact_summary(e),
        expression=expression,
        fidelity=_fidelity(e),
        speech_mix=speech_mix,
    )
