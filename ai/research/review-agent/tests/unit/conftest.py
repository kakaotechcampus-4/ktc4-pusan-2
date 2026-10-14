"""테스트 공용 — 예시 요청을 바탕으로 바꾼 요청을 만드는 도우미.

예시 요청(src/review/examples/review/request.json)의 값으로 계산한 부담:
- script_independence = (0.71 × 13초 + 0.55 × 7초) × 반복 가중 1.2 = 15.696 (3번 장)
- fluency = 0.5 × 12초 = 6.0 (2번 장)
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from review import (
    ReviewConfig,
    ReviewResponse,
    RuleResult,
    apply_rules,
    load_config,
    review_take,
)

EXAMPLES = Path(__file__).parents[2] / "src" / "review" / "examples" / "review"
_REQUEST = json.loads((EXAMPLES / "request.json").read_text(encoding="utf-8"))

#: /takes/analyze 출력 모양 (명세 밖 대본 전달도 문서의 예시). 3번 장만 채점, 핵심 내용은 모두 전달
COVERAGE: dict[str, Any] = {
    "take_id": "take-123",
    "evaluations": [
        {
            "slide_number": 3,
            "scores": {"content_coverage": 1.0, "critical_fact_accuracy": None},
            "key_points": [{"key_point_id": "KP1", "importance": "critical", "status": "covered"}],
            "critical_facts": [{"fact_id": "CF1", "value": "37%", "status": "sound_alike"}],
        }
    ],
    "take_scores": {"content_coverage": 1.0, "critical_fact_accuracy": None, "scored_ratio": 1.0},
    "failed": [],
}


def sample_request() -> dict[str, Any]:
    return copy.deepcopy(_REQUEST)


def sample_coverage() -> dict[str, Any]:
    return copy.deepcopy(COVERAGE)


def segment(
    issue_type: str,
    area: str,
    slide_number: int | None,
    start_ms: int,
    end_ms: int,
    severity: float,
    reliable: bool = True,
) -> dict[str, Any]:
    return {
        "area": area,
        "issue_type": issue_type,
        "slide_number": slide_number,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "mean_severity": severity,
        "reliable": reliable,
    }


def unmeasured(measured_ratio: float = 0.0) -> dict[str, Any]:
    """코치가 측정 비율 미달로 본 영역 (coach-agent.md 5-3)"""
    return {
        "measured_ratio": measured_ratio,
        "unmeasured_reason": "LOW_MEASURED_RATIO",
        "take": None,
        "slides": None,
    }


def target(display: str, metric: str, operator: str, value: float) -> dict[str, Any]:
    return {"display": display, "metric": metric, "operator": operator, "value": value}


def run(
    request: dict[str, Any], config: ReviewConfig | None = None
) -> tuple[ReviewResponse, RuleResult]:
    return review_take(request, config=config), apply_rules(request, config)


@pytest.fixture
def req() -> dict[str, Any]:
    return sample_request()


@pytest.fixture
def cfg() -> ReviewConfig:
    return load_config()
