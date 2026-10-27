"""강점 · 개선점 후보와 기본 문장 (LLM 없이).

후보마다 영역 · 장 · 근거를 정하고 기본 문장을 붙인다. LLM 이 없거나 실패하면 기본 문장을 그대로
쓰고, LLM 이 성공해도 빠뜨리거나 숫자 검사(core.check_numbers)에 걸린 문장은 그 후보의 기본 문장으로
바꾼다.

문장 안 숫자는 장 번호뿐이고, 지표 값은 evidence 에 입력 그대로 싣는다 (숫자 검사에 걸리지 않게).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from .burden import Burden, focus
from .config import ReviewConfig
from .evaluable import measured_areas
from .metrics import find_metric
from .recurring import build_recurring_issues
from .vocab import DIMENSIONS, METRIC_TO_DIMENSION

#: 미션 설명용 지표 이름 (명세에 표시 이름이 없어 정한 것. FE 표시 이름이 정해지면 맞춘다)
METRIC_LABELS = {
    "audience_ratio": "청중 응시 비율",
    "script_ratio": "대본 응시 비율",
    "cpm": "말 속도(분당 글자 수)",
    "long_silence_count": "긴 침묵",
    "duration_ms": "발표 시간",
    "slide_duration_ms": "머문 시간",
    "voice_diff_db": "평소 대비 음량(dB)",
    "filler_per_min": "분당 군더더기",
    "filler_count": "군더더기 횟수",
    "content_coverage": "핵심 내용 전달률",
}
OPERATOR_WORDS = {"LT": "미만", "LTE": "이하", "GT": "초과", "GTE": "이상"}

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
#: 판단할 수 있는 차원이 모두 STABLE 이라 강점 · 개선점 후보가 없을 때
STABLE_TEXT = "이번 Take는 전반적으로 보통이었습니다. 두드러진 강점이나 먼저 고칠 점은 없었습니다."


def _evidence(
    dim: str,
    burden: Burden,
    take_result: dict[str, Any],
    script_coverage: dict[str, Any] | None,
    measured: set[str],
    cfg: ReviewConfig,
    area: str | None = None,
    slide_number: int | None = None,
) -> tuple[str, dict[str, Any], int | None]:
    """(영역, 근거, 근거를 찾은 장). 잰 영역의 지표 중 값이 있는 첫 지표를 근거로 싣는다.

    - 지표 순서: 다음 미션 지표(차원에서 고른 문제의 mission_target) → 차원 표의 지표 순
    - area 가 있으면(개선점) 그 영역의 지표만, 없으면(강점) 잰 영역 어디든
    - slide_number 가 있으면 그 장 값이 있는 지표를 먼저 찾고, 없으면 Take 값 (찾은 장 None)
    - 못 찾으면 (area 또는 잰 영역 중 첫 영역, {}, None)
    """
    spec = DIMENSIONS[dim]
    _, issue, _ = focus(dim, burden, cfg)
    rule = cfg.next_mission.mission_target.get(issue) if issue is not None else None
    metrics = dict.fromkeys(m for m in (rule.metric if rule else None, *spec.metrics) if m)
    slides = (slide_number, None) if slide_number is not None else (None,)
    for slide in slides:
        for metric in metrics:
            found, value, _ = find_metric(take_result, script_coverage, metric, slide)
            if value is None or found not in measured or (area and found != area):
                continue
            return found, {metric: value}, slide
    fallback = area or next((a for a in spec.areas if a in measured), spec.areas[0])
    return fallback, {}, None


def _improved_slide(
    dim: str,
    recurring_issues: list[dict[str, Any]],
    mission_results: list[dict[str, Any]],
) -> int | None:
    """IMPROVED 강점의 장: 나아진 근거의 장.

    그 차원의 직전 반복 문제 → 이룬 장 미션 순. 없으면 None
    """
    for it in recurring_issues:
        if it.get("dimension") == dim and it.get("slide_number") is not None:
            return it["slide_number"]
    for m in mission_results:
        if (
            m["status"] == "ACHIEVED"
            and m["slide_number"] is not None
            and METRIC_TO_DIMENSION.get(m["metric"]) == dim
        ):
            return m["slide_number"]
    return None


@dataclass(frozen=True)
class TextCandidate:
    """강점 · 개선점 후보 하나 (명세 6-2).

    item 은 응답 항목 {area, slide_number, text, evidence} 이고 text 는 기본 문장이다.
    area · slide_number · evidence 는 규칙 단계가 정해 기본 문장과 LLM 문장이 같이 쓴다.
    """

    dimension: str
    #: 그 차원의 상태. 강점은 STRENGTH · IMPROVED, 개선점은 PRIORITY
    state: str
    item: dict[str, Any]


@dataclass(frozen=True)
class TextCandidates:
    #: STRENGTH · IMPROVED 차원 (차원 표 순서)
    strengths: list[TextCandidate]
    #: PRIORITY 차원 (반복 문제와 같은 순서 · 영역 · 장)
    improvements: list[TextCandidate]
    #: 판단할 수 있는(NOT_EVALUABLE 이 아닌) 차원이 있는지. 후보가 없을 때 기본 요약을 고른다
    evaluable: bool = False


def text_candidates(
    dimension_states: dict[str, str],
    burden: Burden,
    take_result: dict[str, Any],
    script_coverage: dict[str, Any] | None,
    cfg: ReviewConfig,
    recurring_issues: list[dict[str, Any]] | None = None,
    mission_results: list[dict[str, Any]] | None = None,
) -> TextCandidates:
    """강점 · 개선점 후보와 그 기본 문장.

    - strengths: STRENGTH · IMPROVED 차원 (차원 표 순서). area 는 근거 지표를 찾은 잰 영역.
      IMPROVED 면 나아진 근거의 장(직전 반복 문제 → 이룬 장 미션, _improved_slide)의 값을 먼저
      찾아 그 장을 싣고, 장 값이 없으면(이번에 재지 못한 장) Take 값과 장 번호 null.
      STRENGTH 는 그 차원의 Take 전체에 문제가 없다는 뜻이라 장 번호 null, 근거는 Take 값
    - improvements: PRIORITY 차원 (부담 큰 순), 부담이 몰린 장이 있으면 장 번호.
      area 는 부담이 가장 큰 영역이고 근거도 그 영역의 지표
    - 못 잰 영역(측정 비율 기준 미만)은 area · 근거에 쓰지 않는다
    """
    # 판단 가능한 content_delivery 만 강점 · 개선점에 오므로 CONTENT 는 잰 영역으로 본다
    measured = measured_areas(take_result) | {"CONTENT"}
    strengths = []
    for dim, state in dimension_states.items():
        if state == "STRENGTH":
            text = STRENGTH_TEXT[dim]
        elif state == "IMPROVED":
            text = IMPROVED_TEXT.format(label=DIMENSION_LABELS[dim])
        else:
            continue
        slide = None
        if state == "IMPROVED":
            slide = _improved_slide(dim, recurring_issues or [], mission_results or [])
        area, evidence, found_slide = _evidence(
            dim, burden, take_result, script_coverage, measured, cfg, slide_number=slide
        )
        item = {"area": area, "slide_number": found_slide, "text": text, "evidence": evidence}
        strengths.append(TextCandidate(dim, state, item))

    # PRIORITY 차원 목록은 반복 문제와 같다 (부담 큰 순 · 영역 · 몰린 장).
    # 개선점은 반복 문제와 같은 장을 가리켜야 해서 장 값이 없어도 장 번호를 그대로 둔다 (6-1 8번)
    improvements = []
    for it in build_recurring_issues(dimension_states, burden, cfg):
        dim, slide = it["dimension"], it["slide_number"]
        where = f"{slide}번 장에서 " if slide is not None else ""
        item = {
            "area": it["area"],
            "slide_number": slide,
            "text": where + IMPROVEMENT_TEXT[dim],
            "evidence": _evidence(
                dim, burden, take_result, script_coverage, measured, cfg, it["area"], slide
            )[1],
        }
        improvements.append(TextCandidate(dim, dimension_states[dim], item))
    evaluable = any(s != "NOT_EVALUABLE" for s in dimension_states.values())
    return TextCandidates(strengths, improvements, evaluable)


def default_summary(candidates: TextCandidates) -> str:
    """기본 요약 문장. 강점 · 개선점 후보의 차원 이름(개선점은 장 번호도)을 잇는다.

    후보가 없으면 판단할 수 있는 차원이 있을 때(모두 STABLE) '전반적으로 보통',
    없을 때만 '평가할 수 있는 항목이 부족'이다 — LLM 이 실패한 Take 가 측정 부족처럼 보이지 않게
    """
    good = [DIMENSION_LABELS[c.dimension] for c in candidates.strengths]
    bad = [
        (f"{c.item['slide_number']}번 장 " if c.item["slide_number"] is not None else "")
        + DIMENSION_LABELS[c.dimension]
        for c in candidates.improvements
    ]
    parts = []
    if good:
        parts.append(f"좋았던 점은 {', '.join(good)}입니다.")
    if bad:
        parts.append(f"먼저 다듬을 점은 {', '.join(bad)}입니다.")
    if parts:
        return " ".join(parts)
    return STABLE_TEXT if candidates.evaluable else NOTHING_TEXT


def default_text(candidates: TextCandidates) -> dict[str, Any]:
    """{overall_summary, strengths, improvements} — 후보를 모두 기본 문장으로"""
    return {
        "overall_summary": default_summary(candidates),
        "strengths": [copy.deepcopy(c.item) for c in candidates.strengths],
        "improvements": [copy.deepcopy(c.item) for c in candidates.improvements],
    }


def build_default_text(
    dimension_states: dict[str, str],
    burden: Burden,
    take_result: dict[str, Any],
    script_coverage: dict[str, Any] | None,
    cfg: ReviewConfig,
) -> dict[str, Any]:
    """{overall_summary, strengths, improvements} — text_candidates 를 모두 기본 문장으로"""
    return default_text(
        text_candidates(dimension_states, burden, take_result, script_coverage, cfg)
    )
