"""기본 문장 (LLM 없이). 1차의 문장이고, 2차에서 LLM 이 실패했을 때도 이 문장을 쓴다.

문장 안 숫자는 장 번호뿐이고, 지표 값은 evidence 에 입력 그대로 싣는다 (숫자 검사에 걸리지 않게).
"""

from __future__ import annotations

from typing import Any

from .burden import Burden
from .config import ReviewConfig
from .metrics import find_metric
from .recurring import build_recurring_issues
from .vocab import DIMENSIONS

#: 미션 설명용 지표 이름 (명세에 표시 이름이 없어 정한 것. FE 표시 이름이 정해지면 맞춘다)
METRIC_LABELS = {
    "audience_ratio": "청중 응시 비율",
    "script_ratio": "대본 응시 비율",
    "voice_diff_db": "평소 대비 음량(dB)",
    "filler_per_min": "분당 군더더기",
    "filler_count": "군더더기 횟수",
    "content_coverage": "핵심 내용 전달률",
}
OPERATOR_WORDS = {"LT": "미만", "LTE": "이하", "GT": "초과", "GTE": "이상", "EQ": ""}

#: '좋았던 점' 과 '다듬을 점' 양쪽에 들어가도 어색하지 않은 이름
DIMENSION_LABELS = {
    "audience_gaze": "청중 응시",
    "script_independence": "대본 의존도",
    "pacing_rhythm": "말 속도와 시간 배분",
    "vocal_delivery": "목소리 크기",
    "fluency": "말의 매끄러움",
    "content_delivery": "핵심 내용 전달",
}
STRENGTH_TEXT = {
    "audience_gaze": "청중을 고르게 바라봤습니다",
    "script_independence": "대본에 기대지 않고 발표했습니다",
    "pacing_rhythm": "말 속도와 시간 배분이 안정적이었습니다",
    "vocal_delivery": "목소리 크기가 안정적이었습니다",
    "fluency": "군더더기 없이 매끄럽게 말했습니다",
    "content_delivery": "핵심 내용을 빠짐없이 전달했습니다",
}
IMPROVED_TEXT = "{label} 부분이 지난번보다 나아졌습니다"
IMPROVEMENT_TEXT = {
    "audience_gaze": "청중을 바라보는 시간을 늘려 보세요",
    "script_independence": "대본을 이어 읽기보다 문장 끝에서 고개를 들어 보세요",
    "pacing_rhythm": "말 속도와 장별 시간 배분을 점검해 보세요",
    "vocal_delivery": "평소보다 조금 더 크게 말해 보세요",
    "fluency": "군더더기 대신 잠깐 쉬어 보세요",
    "content_delivery": "빠뜨린 핵심 내용을 다시 챙겨 보세요",
}
NOTHING_TEXT = "이번 Take는 평가할 수 있는 항목이 부족했습니다."


def _evidence(
    dim: str,
    take_result: dict[str, Any],
    script_coverage: dict[str, Any] | None,
    cfg: ReviewConfig,
    slide_number: int | None = None,
) -> dict[str, Any]:
    """차원 대표 지표(다음 미션 지표, 없으면 차원 표의 첫 지표)의 입력 값. 없으면 빈 dict"""
    metric = cfg.next_mission.mission_metric.get(dim) or DIMENSIONS[dim].metrics[0]
    _, value, _ = find_metric(take_result, script_coverage, metric, slide_number)
    if value is None and slide_number is not None:
        _, value, _ = find_metric(take_result, script_coverage, metric)
    return {metric: value} if value is not None else {}


def build_default_text(
    dimension_states: dict[str, str],
    burden: Burden,
    take_result: dict[str, Any],
    script_coverage: dict[str, Any] | None,
    cfg: ReviewConfig,
) -> dict[str, Any]:
    """{overall_summary, strengths, improvements}

    - strengths: STRENGTH · IMPROVED 차원 (차원 표 순서)
    - improvements: PRIORITY 차원 (부담 큰 순), 부담이 몰린 장이 있으면 장 번호
    """
    strengths = []
    for dim, state in dimension_states.items():
        if state == "STRENGTH":
            text = STRENGTH_TEXT[dim]
        elif state == "IMPROVED":
            text = IMPROVED_TEXT.format(label=DIMENSION_LABELS[dim])
        else:
            continue
        strengths.append(
            {
                "area": DIMENSIONS[dim].areas[0],
                "text": text,
                "evidence": _evidence(dim, take_result, script_coverage, cfg),
            }
        )

    # PRIORITY 차원 목록은 반복 문제와 같다 (부담 큰 순 · 영역 · 몰린 장)
    priority_issues = build_recurring_issues(dimension_states, burden, cfg)
    improvements = []
    for it in priority_issues:
        dim, slide = it["dimension"], it["slide_number"]
        where = f"{slide}번 장에서 " if slide is not None else ""
        improvements.append(
            {
                "area": it["area"],
                "slide_number": slide,
                "text": where + IMPROVEMENT_TEXT[dim],
                "evidence": _evidence(dim, take_result, script_coverage, cfg, slide),
            }
        )

    good = [
        DIMENSION_LABELS[d] for d, s in dimension_states.items() if s in ("STRENGTH", "IMPROVED")
    ]
    bad = [
        (f"{it['slide_number']}번 장 " if it["slide_number"] is not None else "")
        + DIMENSION_LABELS[it["dimension"]]
        for it in priority_issues
    ]
    parts = []
    if good:
        parts.append(f"좋았던 점은 {', '.join(good)}입니다.")
    if bad:
        parts.append(f"먼저 다듬을 점은 {', '.join(bad)}입니다.")
    summary = " ".join(parts) if parts else NOTHING_TEXT

    return {"overall_summary": summary, "strengths": strengths, "improvements": improvements}
