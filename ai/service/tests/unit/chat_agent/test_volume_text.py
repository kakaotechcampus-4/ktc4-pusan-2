"""소리 크기 표현 단위 시험 — 값에 견주는 때를 붙인다(#1182, 기능 문서 6-9 '소리 크기 표기').

소리 크기(`voice_diff_db`)는 기준 목소리와의 차이(dB)라 "-1.1dB"만으로는 무엇과 견준 값인지 알 수 없다. 그 Take 의 기준 음량
출처(`areas.VOLUME.take.base_level_source`)로 '점검 때 · 발표 처음 · 기준'을 붙이고, 숫자는 크기만 쓴다(음수는 크기로 본다,
`architecture.md` 0-2 ⑤). 도구 결과는 예시 요청과 가상 기록(fixtures.py, 출처 `CALIBRATION`)으로 실제로 돌린다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pitch_coach_ai.features.chat_agent import actions, compose, templates
from pitch_coach_ai.features.chat_agent import claims as clm
from pitch_coach_ai.features.chat_agent.checks import check_numbers
from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG as CFG

from .test_tools import make_ctx, run


@pytest.mark.parametrize(
    ("value", "base", "text"),
    [
        (-1.13, "CALIBRATION", "점검 때보다 1.1dB 작게"),
        (0.5, "CALIBRATION", "점검 때보다 0.5dB 크게"),
        (0.04, "CALIBRATION", "점검 때와 같게"),  # 화면 자리수(0.1dB)로 0 이면 같게
        (-1.0, "TAKE", "발표 처음보다 1dB 작게"),
        (0, "TAKE", "발표 처음과 같게"),  # 받침 있는 말 뒤는 '과'
        (-1.1, None, "기준보다 1.1dB 작게"),  # 출처를 모르면 '기준'
        (0, None, "기준과 같게"),
    ],
)
def test_voice_diff_says_what_it_is_compared_with(
    value: float, base: str | None, text: str
) -> None:
    assert clm.format_metric_value("voice_diff_db", value, CFG, base=base) == text


def test_volume_base_reads_the_takes_base_level_source() -> None:
    def snap(take: object) -> SimpleNamespace:
        return SimpleNamespace(results=SimpleNamespace(take_result=take))

    def volume(src: object) -> SimpleNamespace:
        return snap({"areas": {"VOLUME": {"take": {"base_level_source": src}}}})

    assert clm.volume_base(volume("CALIBRATION")) == "CALIBRATION"  # type: ignore[arg-type]
    assert clm.volume_base(volume("TAKE")) == "TAKE"  # type: ignore[arg-type]
    # 못 잡음 · 모르는 값 · 결과 없음 · 스냅샷 없음은 None → '기준'
    for s in (volume(None), volume("ELSEWHERE"), snap(None), snap({"areas": {}}), None):
        assert clm.volume_base(s) is None  # type: ignore[arg-type]


def test_tool_display_uses_the_takes_base() -> None:
    """모델에 넘기는 화면 표기도 견주는 때를 단다(가상 기록의 출처는 CALIBRATION, 값 -0.5)."""
    ctx = make_ctx()
    res, _ = run(ctx, "get_take_report", {"take_id": "t-5", "section": "slides", "slide_number": 3})
    vals = {v["metric"]: v for v in res.data["items"][0]["values"]}
    voice = vals["voice_diff_db"]
    assert (voice["value"], voice["display"]) == (-0.5, "점검 때보다 0.5dB 작게")


def test_sentences_ending_with_the_volume_text_read_naturally() -> None:
    """'…작게'로 끝나는 표기 뒤에는 '예요' 대신 '나왔어요'(기록 문장 · 판단 문장 템플릿)."""
    assert compose.be("점검 때보다 1.1dB 작게") == "점검 때보다 1.1dB 작게 나왔어요"
    assert compose.be("점검 때와 같게") == "점검 때와 같게 나왔어요"
    assert compose.be("28%") == "28%예요"
    # 소리 크기 끝말(작게 · 크게 · 같게)에만 붙는다: 다른 '…게' 표기는 '나왔어요'가 붙지 않는다(#1183 리뷰)
    assert templates.be("빠르게") == "빠르게예요"
    assert compose.be("일정하게") == "일정하게예요"
    assert templates.be("점검 때보다 0.5dB 크게") == "점검 때보다 0.5dB 크게 나왔어요"
    assert (
        templates.fill(templates.PRESSURE, evidence="Take 5 소리 크기 점검 때보다 1.1dB 작게")
        == "답답하실 수 있지만 기록은 Take 5 소리 크기 점검 때보다 1.1dB 작게 나왔어요."
    )
    # 다른 값은 지금처럼 받침에 맞춘 '예요 · 이에요'
    assert templates.fill(templates.PRESSURE, evidence="Take 5 청중 시선 31%, 우선 개선") == (
        "답답하실 수 있지만 기록은 Take 5 청중 시선 31%, 우선 개선이에요."
    )


def test_number_check_accepts_the_magnitude_in_the_new_text() -> None:
    """숫자 검사는 그대로다: -1.1 ↔ '1.1dB 작게'(0-2 ⑤). 다른 크기는 걸린다."""
    assert check_numbers(
        "Take 5 소리 크기는 점검 때보다 1.1dB 작게 나왔어요", [-1.1], take_numbers=[5]
    )
    assert not check_numbers(
        "Take 5 소리 크기는 점검 때보다 2dB 작게 나왔어요", [-1.1], take_numbers=[5]
    )


def test_reassess_volume_threshold_is_signed_against_the_base() -> None:
    """다시 평가 기준 문구의 소리 크기 문턱값은 '기준 대비 -1dB 이상'처럼 부호로(비교 말과 겹치지 않게)."""
    text = actions._target_text  # noqa: SLF001 — 코드가 만드는 기준 문구
    assert text({"metric": "voice_diff_db", "operator": "GTE", "value": -1}, CFG) == (
        "소리 크기 기준 대비 -1dB 이상"
    )
    assert text({"metric": "voice_diff_db", "operator": "GT", "value": 3}, CFG) == (
        "소리 크기 기준 대비 +3dB 초과"
    )
    assert text({"metric": "voice_diff_db", "operator": "LTE", "value": 0}, CFG) == (
        "소리 크기 기준 대비 0dB 이하"
    )
