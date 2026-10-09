"""원자료로 Take 를 처음부터 다시 판정한다 — finalize 의 replay.

응답이 창보다 오래 빠졌거나 coach_state 를 잃으면 센 합계로는 Take 를 덮지 못한다(409). 그때 BE 가
Take 전체 원자료를 실어 finalize 를 다시 부르면, BE 가 1초마다 보냈을 /coach/evaluate 요청을 여기서
다시 만들어 처음부터 판정한다. 창 길이는 judges.py 의 통신 규약 값과 같다.
"""

from __future__ import annotations

from typing import Any

from .judges import GAZE_VOICE_WINDOW_MS, WORDS_WINDOW_MS
from .schemas import CoachInputs, CoachRequest, FinalizeRequest, ReplayInput, SlideNow, WordIn
from .vocab import Mode

#: 다시 만드는 요청의 간격 (FE 1초 기록과 같다)
TICK_MS = 1_000


def ticks(t_end_ms: int) -> range:
    """다시 판정할 요청 시각: 1초마다, Take 끝 직전까지. Take 끝은 finalize 의 마지막 창이다."""
    return range(TICK_MS, t_end_ms, TICK_MS)


def inputs_at(raw: ReplayInput, t_ms: int) -> CoachInputs:
    """t_ms 요청에 BE 가 실었을 창: 시선 · 음량은 끝난 지 30초 안의 1초 기록, 단어는 확정된 시각이
    지났고 60초 안에 끝난 것, 문장 끝은 60초 안의 것, STT 상태와 장은 그때의 것."""
    lo_rec = t_ms - GAZE_VOICE_WINDOW_MS
    gaze = [r for r in raw.gaze_records if lo_rec <= r.t_ms and r.t_ms + r.duration_ms <= t_ms]
    voice = [r for r in raw.voice_records if lo_rec <= r.t_ms and r.t_ms + r.duration_ms <= t_ms]
    lo_word = t_ms - WORDS_WINDOW_MS
    words = [
        WordIn(word=w.word, start_ms=w.start_ms, end_ms=w.end_ms)
        for w in raw.words
        if w.final_at_ms <= t_ms and lo_word < w.end_ms <= t_ms
    ]
    ends = [u for u in raw.utterance_ends if lo_word <= u <= t_ms]
    status = "ok"
    for change in sorted(raw.stt_status_changes, key=lambda c: c.t_ms):
        if change.t_ms <= t_ms:
            status = change.status
    slide: SlideNow | None = None
    for s in sorted(raw.slides, key=lambda s: s.started_ms):
        if s.started_ms <= t_ms:
            slide = s
    return CoachInputs(
        gaze_records=gaze,
        voice_records=voice,
        words=words,
        utterance_ends=ends,
        stt_status=status,
        slide=slide,
    )


def request_at(
    req: FinalizeRequest,
    raw: ReplayInput,
    t_ms: int,
    coach_state: dict[str, Any] | None,
    mode: Mode = Mode.EXAM,
) -> CoachRequest:
    """t_ms 에 BE 가 보냈을 evaluate 요청. 다시 판정할 때는 말을 걸지 않도록 실전 모드로 만든다."""
    return CoachRequest(
        take_id=req.take_id,
        t_ms=t_ms,
        mode=mode,
        plan=req.plan,
        missions=req.missions,
        recurring_issues=req.recurring_issues,
        coaching_plan=req.coaching_plan,
        script_used=req.script_used,
        inputs=inputs_at(raw, t_ms),
        calibration=req.calibration,
        coach_state=coach_state,
    )
