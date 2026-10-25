"""Take 결과(`TAKE_RESULT`)에서 지표 하나를 꺼내는 규칙.

take_summaries 칸(`coach/replies.py` 의 `summary_measures`)과 리포트가 같이 쓴다. 두 곳이 따로
꺼내면 같은 Take 의 리포트와 Take 목록 · 비교 값이 어긋난다.
"""

import math
from collections.abc import Mapping
from typing import Any

from pitch_coach_backend.core.rounding import round_half_up


def take_metric(areas: Mapping[str, Any], area: str, name: str) -> float | None:
    """`areas.<area>.take.<name>`. 영역 값이 없거나(측정 비율 미달) 지표가 null(분모 0)이면 None."""
    values = (areas.get(area) or {}).get("take") or {}
    value = values.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def take_count(areas: Mapping[str, Any], area: str, name: str) -> int | None:
    """개수 지표. .5 는 올린다 (architecture.md 1-1 '반올림')."""
    value = take_metric(areas, area, name)
    return None if value is None else round_half_up(value)
