"""평가기 — 측정값이 문제로 바뀌는 계산."""

from __future__ import annotations

import pytest

from coach.evaluators.speech import compute_cpm
from coach.renderer import format_duration
from coach.schemas import Word
from coach.state import CoachState, dump_state


def _cands(resp):
    return {c.issue_type.value: c for c in resp.candidates}


def test_cpm_matches_stt_live_formula():
    # 글자 수 ÷ 실제로 말한 시간(침묵 제외) × 60초 — stt-live v1 과 같다
    ws = [Word(w="가나다", start_ms=i * 1000, end_ms=i * 1000 + 600) for i in range(10)]
    cpm, speak_ms, n = compute_cpm(ws, 10_000, 15_000)
    assert speak_ms == 6000 and n == 10
    assert cpm == pytest.approx(30 / 6 * 60)  # 침묵 4초를 빼서 300


def test_cpm_ignores_fillers():
    ws = [
        Word(w="가나다", start_ms=0, end_ms=600),
        Word(w="음", start_ms=600, end_ms=900, filler=True),
    ]
    cpm, speak_ms, _ = compute_cpm(ws, 1000, 15_000)
    assert speak_ms == 600 and cpm == pytest.approx(300)


# ── 음량 · 침묵 ───────────────────────────────────────────────────────────


def _state_with_chars(slide: int, chars: int) -> dict:
    """이 장에서 chars 자를 말한 상태. 진행도를 글자 수로 재려면 요청에 speech 도 있어야 한다."""
    st = CoachState(slide_number=slide, slide_chars={str(slide): chars}, last_final_end_ms=10**9)
    return dump_state(st)


def test_format_duration():
    assert format_duration(65_000) == "1분 5초"
    assert format_duration(50_000) == "50초"
    assert format_duration(120_000) == "2분"
    assert format_duration(-5) == "0초"
