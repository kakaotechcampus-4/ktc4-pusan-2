"""코치의 판정 라운드를 연구용 대역 모듈로 돌려 본다 — 누적과 커서가 어긋나지 않는지."""

from __future__ import annotations

from coach.config import DEFAULT_CONFIG
from coach.judges import Judges, run
from coach.schemas import CoachRequest
from coach.state import initial_state
from coach_lab.judges import filler, gaze, pace, volume

ROUNDS = 20
WORD = "안녕하세요"  # 5자, 군더더기 아님


def lab_bundle() -> Judges:
    return Judges(gaze=gaze, pace=pace, volume=volume, filler=filler, baseline=volume.baseline)


def request(t: int, history: dict[str, list]) -> CoachRequest:
    """1초마다 시선 · 음량 기록과 단어 하나를 더하고, 창(30초 · 60초) 안의 것을 모두 싣는다."""
    history["gaze"].append({"t_ms": t - 1_000, "state": "CAMERA", "reliability": 0.9})
    history["voice"].append(
        {"t_ms": t - 1_000, "level_db": -30.0, "voiced_ms": 800, "silence_ms": 0}
    )
    history["words"].append({"word": WORD, "start_ms": t - 900, "end_ms": t - 400})
    return CoachRequest.model_validate(
        {
            "take_id": "lab",
            "t_ms": t,
            "plan": {
                "target_ms": 60_000,
                "min_ms": 55_000,
                "max_ms": 65_000,
                "slides": [{"slide_number": 1, "target_ms": 60_000, "script_chars": 600}],
            },
            "inputs": {
                "gaze_records": [r for r in history["gaze"] if r["t_ms"] >= t - 30_000],
                "voice_records": [r for r in history["voice"] if r["t_ms"] >= t - 30_000],
                "words": [w for w in history["words"] if w["start_ms"] >= t - 60_000],
                "slide": {"number": 1, "started_ms": 0},
            },
        }
    )


def test_twenty_rounds_with_lab_judges():
    judges = lab_bundle()
    state = initial_state()
    history: dict[str, list] = {"gaze": [], "voice": [], "words": []}
    out = None
    for i in range(1, ROUNDS + 1):
        out = run(request(i * 1_000, history), state, judges, DEFAULT_CONFIG)
        assert out.failed == set()

    total = ROUNDS * 1_000
    assert out is not None and out.stt_ok
    # 시간 합계는 요청 시각과 같다 — 겹친 창을 다시 받아도 두 번 세지 않는다
    assert state.totals["TIME"]["elapsed_ms"] == total
    assert state.totals["GAZE"]["total_ms"] == total
    assert state.totals["VOLUME"]["total_ms"] == total
    assert state.totals["PAUSE"]["total_ms"] == total
    assert state.totals["SPEED"]["chars"] == len(WORD) * ROUNDS
    # 한 장뿐이라 장 합계가 Take 합계와 같다
    assert state.slide_totals["1"]["TIME"]["elapsed_ms"] == total
    assert state.slide_totals["1"]["SPEED"]["chars"] == len(WORD) * ROUNDS
    assert state.slide_stt_ok_ms == {"1": total}
    for name in ("gaze", "volume", "timing"):
        assert state.cursors[name].since_ms == total
    assert state.cursors["pace"].words_since_ms == state.cursors["filler"].words_since_ms
    assert state.covered == [[0, total]]
    # 말한 1초 15개가 모여 기준 음량이 잡혔다
    assert state.base_level_db == -30.0 and state.base_level_source == "TAKE"
    assert out.criteria["pace"]["PACE_FAST"].threshold > 0
