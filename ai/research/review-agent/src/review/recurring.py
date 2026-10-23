"""8단계: 반복 문제. 이번 Take 의 PRIORITY 차원을 다음 Take 로 넘긴다.

다음 Take 의 코칭 계획 · 코치는 area(· 장)를 보고 우선순위에 가중을 주고,
다음 리뷰는 dimension 을 보고 그 차원이 나아졌는지(IMPROVED) 판단한다.
"""

from __future__ import annotations

from typing import Any

from .burden import Burden, focus_slide
from .config import ReviewConfig
from .vocab import DIMENSIONS


def build_recurring_issues(
    dimension_states: dict[str, str], burden: Burden, cfg: ReviewConfig
) -> list[dict[str, Any]]:
    """PRIORITY 차원마다 {area, slide_number, dimension}. 부담 큰 순(같으면 이름순).

    - area: 차원의 영역이 하나면 그 영역, 여럿이면(pacing_rhythm) 부담이 가장 큰 영역
      (같으면 차원 표에 적힌 순서)
    - slide_number: 다음 미션과 같은 규칙 (focus_slide). 몰리지 않았으면 None
    - dimension: 명세에 없는 필드. GAZE 처럼 한 영역이 여러 차원으로 나뉘어도 다음 리뷰가 알 수 있게
    """
    dims = [d for d, s in dimension_states.items() if s == "PRIORITY"]
    dims.sort(key=lambda d: (-burden[d]["total"], d))
    issues = []
    for dim in dims:
        areas = DIMENSIONS[dim].areas
        if len(areas) == 1:
            area = areas[0]
        else:
            by_area = burden[dim]["by_area"]
            area = max(areas, key=lambda a: (by_area.get(a, 0.0), -areas.index(a)))
        issues.append(
            {"area": area, "slide_number": focus_slide(dim, burden, cfg), "dimension": dim}
        )
    return issues
