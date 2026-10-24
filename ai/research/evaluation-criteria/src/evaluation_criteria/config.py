from pydantic import BaseModel, ConfigDict


class EvaluationCriteriaConfig(BaseModel):
    """analyze_criteria · check_targets 의 기준값 (evaluation-criteria.md 5-3 · 6절)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: 표시 기준은 앞에서부터 이 개수까지 쓴다
    max_display_criteria: int = 10
    #: 숫자 목표는 검사 뒤 앞에서부터 이 개수까지 둔다 (리뷰 입력 한도)
    max_targets: int = 20
    #: 값 범위 0–1 인 비율 지표
    ratio_metrics: frozenset[str] = frozenset(
        {"audience_ratio", "reading_ratio", "content_coverage"}
    )
    #: 값이 0 이상인 개수 지표
    count_metrics: frozenset[str] = frozenset(
        {"pause_count", "long_silence_count", "filler_count", "filler_per_min"}
    )
    #: 값이 0 보다 큰 지표
    positive_metrics: frozenset[str] = frozenset({"cpm"})


DEFAULT = EvaluationCriteriaConfig()
