"""9단계: 점수. 차원 상태를 점수로 바꿔 NOT_EVALUABLE 을 뺀 평균 (0–100)."""

from __future__ import annotations

from .config import ReviewConfig


def compute_score(dimension_states: dict[str, str], cfg: ReviewConfig) -> int | None:
    """평가할 수 있는 차원이 없으면 None"""
    table = cfg.score.state_score
    scores = [table[s] for s in dimension_states.values() if s != "NOT_EVALUABLE"]
    if not scores:
        return None
    return round(sum(scores) / len(scores))
