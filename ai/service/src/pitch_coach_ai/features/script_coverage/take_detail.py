"""Take 상세: 채점된 장들의 장 상세를 모아 Take 요약과 장별 요약을 만든다 (응답 맨 위 `detail`).

기존 `take_scores` 는 바꾸지 않고, 그 옆에 상쇄 없는 전달률 · 상태 · 이유 · 범위를 더한다.
`/takes/analyze` 와 `/takes/rescore` 가 같은 함수로 만든다 (받은 evaluations · failed 로 다시 만든다).

- 채점된 장의 값만 모은다. 간이 값(`estimated`) 장은 Take 값에 넣지 않고 `scope` · `slides` 에만 둔다.
- 값이 없으면 0 이 아니라 null 이고 `state` · `reason_codes` 가 까닭이다.
- `take_detail`: 진입점. 같은 입력이면 같은 출력 (정렬 · 반올림 3자리 고정)
"""

from collections.abc import Collection

from .schemas import (
    ExcludedSlide,
    Scope,
    SlideFailure,
    SlideSummary,
    SpeechMixBrief,
    TakeDetail,
    TakeLegacy,
)
from .shared.rubric import IMPORTANCE_RANK, EvaluationRubric
from .stt_evaluation.config import TAKE_CRITICAL_GAPS_MAX
from .stt_evaluation.detail import (
    accuracy_range,
    fact_counts,
    fact_parts,
    key_point_counts,
    key_point_stats,
    status_shares,
    zero_kind,
)
from .stt_evaluation.schemas import (
    MIX_CATEGORIES,
    CriticalGap,
    Expression,
    FactSummary,
    Fidelity,
    Metric,
    Repetitions,
    SlideEvaluation,
    SpeechMix,
)
from .stt_evaluation.speech_mix import mix_display

EXCLUDED = "SLIDES_EXCLUDED"


def _union(lists: list[list[str]]) -> list[str]:
    """이유 코드를 처음 나온 순서대로 중복 없이 합친다."""
    out: list[str] = []
    for codes in lists:
        out += [c for c in codes if c not in out]
    return out


def _empty_metric(nothing_targeted: bool, excluded: int = 0) -> Metric:
    """채점된 장이 없을 때: 대상이 없으면 `not_applicable`, 있으면 `failed` + `SLIDES_EXCLUDED`."""
    if nothing_targeted:
        return Metric(value=None, state="not_applicable")
    return Metric(value=None, state="failed", reason_codes=[EXCLUDED], excluded=excluded)


def _pooled(metrics: list[Metric], *, excluded_slides: bool, nothing_targeted: bool) -> Metric:
    """장 지표를 `evaluated` 가중으로 모은다. 값이 있는 장이 없으면 그 장들의 이유를 합친 `not_applicable`."""
    if not metrics:
        return _empty_metric(nothing_targeted)
    valued = [m for m in metrics if m.value is not None and m.evaluated]
    if not valued:
        return Metric(
            value=None,
            state="not_applicable",
            reason_codes=_union([m.reason_codes for m in metrics]),
        )
    total = sum(m.evaluated for m in valued)
    reasons = _union([m.reason_codes for m in valued if m.state == "provisional"])
    provisional = bool(reasons) or excluded_slides
    return Metric(
        value=round(sum(m.value * m.evaluated for m in valued) / total, 3),
        state="provisional" if provisional else "ok",
        reason_codes=reasons + ([EXCLUDED] if excluded_slides else []),
        evaluated=total,
    )


def _gaps(evaluations: list[SlideEvaluation]) -> list[CriticalGap]:
    """장의 놓친 중요 내용에 장 번호를 붙여 중요도(critical 먼저) · 장 번호 · 장 안 순서로 정렬하고 자른다."""
    gaps = [
        (
            -IMPORTANCE_RANK[g.importance],
            e.slide_number,
            i,
            g.model_copy(update={"slide_number": e.slide_number}),
        )
        for e in evaluations
        for i, g in enumerate(e.detail.critical_gaps)
    ]
    gaps.sort(key=lambda t: t[:3])
    return [g for *_, g in gaps[:TAKE_CRITICAL_GAPS_MAX]]


def _facts(
    evaluations: list[SlideEvaluation], *, excluded_slides: bool, nothing_targeted: bool
) -> FactSummary:
    checks = [c for e in evaluations for c in e.critical_facts]
    parts = fact_parts(checks)
    counts = fact_counts(checks)
    if not evaluations:
        accuracy = _empty_metric(nothing_targeted)
    elif not checks:
        accuracy = Metric(value=None, state="not_applicable", reason_codes=["NO_FACTS"])
    elif not parts["counted"]:
        accuracy = Metric(
            value=None,
            state="pending",
            reason_codes=["ALL_FACTS_PENDING"],
            excluded=parts["pending"],
        )
    else:
        pairs = [
            (e.scores["critical_fact_accuracy"], e.scores["_weights"]["facts"])
            for e in evaluations
            if e.scores["critical_fact_accuracy"] is not None
        ]
        total = sum(w for _, w in pairs)
        reasons = (["FACTS_PENDING"] if parts["pending"] else []) + (
            [EXCLUDED] if excluded_slides else []
        )
        accuracy = Metric(
            value=round(sum(v * w for v, w in pairs) / total, 3),
            state="provisional" if reasons else "ok",
            reason_codes=reasons,
            evaluated=parts["counted"],
            excluded=parts["pending"],
        )
    return FactSummary(
        **counts.model_dump(), accuracy=accuracy, accuracy_range=accuracy_range(parts)
    )


def _speech_mix(
    evaluations: list[SlideEvaluation], *, excluded_slides: bool, nothing_targeted: bool
) -> SpeechMix:
    """범주별 형태소 수를 합쳐 화면 목록을 만든다. 형태소가 0 이면 `not_applicable` (`NO_SPEECH`)."""
    mixes = [e.detail.speech_mix for e in evaluations]
    categories = {c: sum(m.categories.get(c, 0) for m in mixes) for c in MIX_CATEGORIES}
    tokens = sum(m.tokens for m in mixes)
    repetitions = Repetitions(count=sum(m.repetitions.count for m in mixes))
    if not evaluations:
        empty = _empty_metric(nothing_targeted)
        return SpeechMix(
            state=empty.state,
            reason_codes=empty.reason_codes,
            tokens=0,
            categories=categories,
            display=[],
            repetitions=repetitions,
        )
    if not tokens:
        return SpeechMix(
            state="not_applicable",
            reason_codes=["NO_SPEECH"],
            tokens=0,
            categories=categories,
            display=[],
            repetitions=repetitions,
        )
    spoken = [m for m in mixes if m.tokens]
    reasons = _union([m.reason_codes for m in spoken])
    provisional = excluded_slides or any(m.state == "provisional" for m in spoken)
    return SpeechMix(
        state="provisional" if provisional else "ok",
        reason_codes=reasons + ([EXCLUDED] if excluded_slides else []),
        tokens=tokens,
        categories=categories,
        display=mix_display(categories),
        repetitions=repetitions,
    )


def _scored_summary(e: SlideEvaluation) -> SlideSummary:
    d = e.detail
    return SlideSummary(
        slide_number=e.slide_number,
        state="scored",
        reason_codes=list(d.reason_codes),
        stt_integrity=d.stt_integrity,
        rubric_quality=d.rubric_quality,
        delivery=d.delivery,
        zero_kind=d.zero_kind,
        contradiction=d.contradiction,
        status_shares=d.status_shares,
        critical_gap_count=len(d.critical_gaps),
        delivery_band=None,
        speech_mix=SpeechMixBrief(
            state=d.speech_mix.state,
            reason_codes=list(d.speech_mix.reason_codes),
            display=list(d.speech_mix.display),
        ),
    )


def _failed_summary(
    f: SlideFailure, rubric: EvaluationRubric | None, integrity: dict[int, str]
) -> SlideSummary:
    fallback = f.fallback
    return SlideSummary(
        slide_number=f.slide_number,
        state="estimated" if fallback else "failed",
        reason_codes=_union([[f.error.code], fallback.reason_codes if fallback else []]),
        stt_integrity=fallback.stt_integrity
        if fallback
        else integrity.get(f.slide_number, "unknown"),
        rubric_quality=rubric.meta.rubric_quality if rubric else None,
        delivery=None,
        zero_kind=None,
        contradiction=None,
        status_shares=None,
        critical_gap_count=0,
        delivery_band=fallback.delivery_band if fallback else None,
        speech_mix=None,
    )


def take_detail(
    evaluations: list[SlideEvaluation],
    failures: list[SlideFailure],
    targets: Collection[int],
    rubrics: dict[int, EvaluationRubric],
    integrity: dict[int, str],
) -> TakeDetail:
    """채점된 장의 장 상세를 모아 Take 상세를 만든다.

    - 전달 · 다르게 말함 · 판정 몫 · 기존 점수 원값은 핵심 내용 가중치로, 수치 정확도는 기존 Take 값과 같은 계산
      (장의 수치 가중치)으로, 대본 재현은 대본 형태소 수 · 정확도는 STT 형태소 수 가중으로 모은다
    - 채점된 장이 없으면 값 지표는 `failed` + `SLIDES_EXCLUDED` (대상 장이 아예 없으면 `not_applicable`),
      채점된 장이 있는데 빠진 장도 있으면 `provisional` + `SLIDES_EXCLUDED`
    - 모으는 장 중 그 지표가 `provisional` 인 장의 이유를 합쳐 Take 지표도 `provisional` 이다
    """
    evs = sorted(evaluations, key=lambda e: e.slide_number)
    fails = sorted(failures, key=lambda f: f.slide_number)
    scored = {e.slide_number for e in evs}
    target_set = set(targets) | scored
    missing = sorted(target_set - scored)
    nothing = not target_set
    excluded_slides = bool(missing)

    results = [r for e in evs for r in e.key_points]
    stats = key_point_stats(results)
    excluded_kp = sum(len(rubrics[n].key_points) for n in missing if n in rubrics)
    unknown_excluded = any(n not in rubrics for n in missing)

    if not evs:
        delivery = _empty_metric(nothing, excluded_kp)
        contradiction = delivery.model_copy(deep=True)
    elif not results:
        if excluded_kp or unknown_excluded:
            delivery = Metric(
                value=None, state="failed", reason_codes=[EXCLUDED], excluded=excluded_kp
            )
        else:
            delivery = Metric(value=None, state="not_applicable", reason_codes=["NO_KEY_POINTS"])
        contradiction = delivery.model_copy(deep=True)
    else:
        total = stats["weight"]
        with_value = [e.detail for e in evs if e.detail.delivery.value is not None]
        reasons = _union(
            [d.delivery.reason_codes for d in with_value if d.delivery.state == "provisional"]
        )
        flagged = reasons + ([EXCLUDED] if excluded_slides else [])
        state = "provisional" if flagged else "ok"
        delivery = Metric(
            value=round(stats["delivery"] / total, 3),
            state=state,
            reason_codes=flagged,
            evaluated=len(results),
            excluded=excluded_kp,
        )
        contradiction = Metric(
            value=round(stats["by_status"]["contradicted"] / total, 3),
            state=state,
            reason_codes=list(flagged),
            evaluated=len(results),
            excluded=excluded_kp,
        )
    with_kp = [e for e in evs if e.key_points]
    stt_empty = bool(with_kp) and all(e.detail.zero_kind == "slide_skipped" for e in with_kp)
    raw = round(stats["legacy"] / stats["weight"], 3) + 0.0 if results else None

    counts = key_point_counts(results)
    modes = [e.detail.expression for e in evs]
    expression = Expression(
        verbatim_like=sum(x.verbatim_like for x in modes),
        paraphrased=sum(x.paraphrased for x in modes),
        undetermined=sum(x.undetermined for x in modes),
    )
    fidelity = Fidelity(
        recall=_pooled(
            [e.detail.fidelity.recall for e in evs],
            excluded_slides=excluded_slides,
            nothing_targeted=nothing,
        ),
        precision=_pooled(
            [e.detail.fidelity.precision for e in evs],
            excluded_slides=excluded_slides,
            nothing_targeted=nothing,
        ),
    )
    summaries = [_scored_summary(e) for e in evs] + [
        _failed_summary(f, rubrics.get(f.slide_number), integrity)
        for f in fails
        if f.slide_number not in scored
    ]
    summaries.sort(key=lambda s: s.slide_number)
    return TakeDetail(
        delivery=delivery,
        zero_kind=(
            zero_kind(
                stats["delivery"],
                stt_empty=stt_empty,
                results=results,
            )
            if delivery.value is not None
            else None
        ),
        contradiction=contradiction,
        status_shares=status_shares(stats["by_status"]) if results else None,
        key_points=counts,
        critical_gaps=_gaps(evs),
        legacy=TakeLegacy(content_coverage_raw=raw),
        facts=_facts(evs, excluded_slides=excluded_slides, nothing_targeted=nothing),
        expression=expression,
        fidelity=fidelity,
        speech_mix=_speech_mix(evs, excluded_slides=excluded_slides, nothing_targeted=nothing),
        scope=Scope(
            target_slides=len(target_set),
            scored_slides=len(evs),
            estimated_slides=sum(1 for f in fails if f.fallback and f.slide_number not in scored),
            provisional_slides=sum(1 for e in evs if e.detail.state == "provisional"),
            excluded=[
                ExcludedSlide(slide_number=f.slide_number, reason_code=f.error.code)
                for f in fails
                if f.slide_number not in scored
            ],
        ),
        slides=summaries,
    )
