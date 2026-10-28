"""자연어 평가 기준 → 측정 가능 요소 · 숫자 목표 추출 파이프라인: LLM → 검사."""

import logging
from typing import Any, get_args

from .config import DEFAULT, EvaluationCriteriaConfig
from .prompts import SYSTEM_PROMPT
from .schemas import (
    CriteriaMeta,
    EvaluationCriteriaAnalysis,
    EvaluationCriteriaRequest,
    EvaluationCriteriaResponse,
    Metric,
    Operator,
    Target,
)
from .version import FEATURE_VERSION, SCHEMA_VERSION

log = logging.getLogger(__name__)

_METRICS = frozenset(get_args(Metric))
_OPERATORS = frozenset(get_args(Operator))


def analyze_criteria(request: EvaluationCriteriaRequest, llm: Any) -> EvaluationCriteriaResponse:
    """요청 하나를 처리해 응답 모델을 돌려줍니다.

    llm은 EvaluationCriteriaAnalysis 출력 스키마가 걸린 객체(.invoke만 씀)입니다.
    LLM 예외는 잡지 않고 올립니다. meta.model은 서비스 라우터가 채웁니다.
    """
    out: EvaluationCriteriaAnalysis = llm.invoke(
        [("system", SYSTEM_PROMPT), ("user", request.standard_text)]
    )
    display_criteria = out.display_criteria[: DEFAULT.max_display_criteria]
    return EvaluationCriteriaResponse(
        display_criteria=display_criteria,
        values=out.values,
        targets=check_targets(out.targets, display_criteria, DEFAULT),
        excluded=out.excluded,
        meta=CriteriaMeta(
            schema_version=SCHEMA_VERSION, feature_version=FEATURE_VERSION, model=None
        ),
    )


def check_targets(
    targets: list[Target],
    display_criteria: list[str],
    config: EvaluationCriteriaConfig = DEFAULT,
) -> list[Target]:
    """숫자 목표를 검사해 맞지 않는 목표를 뺍니다 (LLM 없음, 문서 6절 2번)."""
    displays = set(display_criteria)
    seen: set[tuple[str, str, str]] = set()
    kept: list[Target] = []
    for target in targets:
        if target.metric not in _METRICS or target.operator not in _OPERATORS:
            continue
        if not _in_range(target.metric, target.value, config):
            continue
        if target.display not in displays:
            continue
        key = (target.display, target.metric, target.operator)
        if key in seen:
            continue
        seen.add(key)
        kept.append(target)

    if len(kept) > config.max_targets:
        log.info(
            "targets %d개 중 %d개를 뺐다 (상한 %d)",
            len(kept),
            len(kept) - config.max_targets,
            config.max_targets,
        )
        kept = kept[: config.max_targets]
    return kept


def _in_range(metric: str, value: float, config: EvaluationCriteriaConfig) -> bool:
    if metric in config.ratio_metrics:
        return 0 <= value <= 1
    if metric in config.count_metrics:
        return value >= 0
    if metric in config.positive_metrics:
        return value > 0
    return True
