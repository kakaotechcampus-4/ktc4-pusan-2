"""대역 모듈이 함께 쓰는 작은 도우미. 지금 코치 평가기의 `ramp` 와 같은 식이다."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

HIGHER = "HIGHER_IS_WORSE"
LOWER = "LOWER_IS_WORSE"
TICK_MS = 1_000


class In(BaseModel):
    """입력 모델 공통. 모르는 필드는 무시한다."""

    model_config = ConfigDict(extra="ignore")


class Section(BaseModel):
    """기준값 모델 공통. 모르는 이름은 막는다."""

    model_config = ConfigDict(extra="forbid")


def parse[M: BaseModel](model: type[M], inputs: M | dict[str, Any]) -> M:
    return inputs if isinstance(inputs, model) else model.model_validate(inputs)


def ramp(value: float, start: float, bad: float) -> float:
    """start 에서 0.5, bad 에서 1.0 이 되도록 선형으로 올린다. bad < start 면 작을수록 나쁘다."""
    if bad == start:
        return 1.0
    frac = (value - start) / (bad - start)
    return 0.5 + 0.5 * min(1.0, max(0.0, frac))


def ratio(num: float, den: float) -> float | None:
    """분모가 0 이면 None. 소수 넷째 자리까지."""
    return round(num / den, 4) if den > 0 else None


def clean(values: dict[str, float]) -> dict[str, float]:
    """0 인 값은 뺀다 — 집계 조각을 짧게 둔다."""
    return {k: v for k, v in values.items() if v}
