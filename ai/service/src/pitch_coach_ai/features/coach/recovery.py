"""원자료로 Take 를 처음부터 다시 판정한다 — finalize 의 replay.

응답이 창보다 오래 빠졌거나 coach_state 를 잃으면 센 합계로는 Take 를 덮지 못한다(409). 그때 BE 가
Take 전체 원자료를 실어 finalize 를 다시 부르면, BE 가 1초마다 보냈을 /coach/evaluate 요청을 여기서
다시 만들어 처음부터 판정한다. 요청 시각은 실시간과 같게 1초 기록이 끝난 시각이고(ticks), 단어는
확정 시각이 요청 시각보다 이른 것만 싣는다(inputs_at). 창 길이는 judges.py 의 통신 규약 값과 같다.
"""

from __future__ import annotations

from typing import Any

from .judges import GAZE_VOICE_WINDOW_MS, WORDS_WINDOW_MS
from .schemas import CoachInputs, CoachRequest, FinalizeRequest, ReplayInput, SlideNow, WordIn
from .vocab import Mode

#: 기록이 없는 구간을 채우는 요청의 간격 (FE 1초 기록과 같다)
TICK_MS = 1_000


def ticks(raw: ReplayInput, t_end_ms: int) -> list[int]:
    """다시 판정할 요청 시각. 실시간에서 BE 가 요청을 만든 시각, 곧 1초 기록이 끝난 시각이다.

    - 0 을 첫 요청으로 둔다. 기록이 없지만 Take 시작 때의 장 · STT 상태를 남긴다(첫 1초 안에 장이
      바뀌거나 STT 가 돌아와도 실제 진행과 같게).
    - 시선 · 음량 1초 기록의 끝(t_ms + duration_ms) 중 Take 끝 앞의 것을 모두 요청 시각으로 둔다.
      1초 격자가 1000 의 배수가 아닐 수 있어(architecture.md 6-1) `0, 1000, …` 으로 부르면 아직
      끝나지 않은 기록을 시각 커서가 건너뛴다.
    - 기록이 없는 구간(첫 기록 전 포함)은 지난 요청 시각에서 1,000 ms 간격으로 채운다. 그 시각이
      어느 1초 기록 안(시작 < 시각 < 끝)이면 넣지 않는다 — 같은 이유다.
    - Take 끝(t_end_ms)은 넣지 않는다. 그 시각은 finalize 의 마지막 창이다.

    1000 의 배수 격자에 기록이 빈틈없이 있으면 `0, 1000, …, t_end_ms − 1000` 이다.
    """
    if t_end_ms <= 0:
        return []
    records = [*raw.gaze_records, *raw.voice_records]
    spans = [(r.t_ms, r.t_ms + r.duration_ms) for r in records]
    ends = sorted({end for _, end in spans if 0 < end < t_end_ms})
    out = [0]
    pos = 0
    for target in [*ends, t_end_ms]:
        fill = pos + TICK_MS
        while fill < target:
            if not any(start < fill < end for start, end in spans):
                out.append(fill)
            fill += TICK_MS
        if target < t_end_ms:
            out.append(target)
        pos = target
    return out


def inputs_at(raw: ReplayInput, t_ms: int, *, last_window: bool = False) -> CoachInputs:
    """t_ms 요청에 BE 가 실었을 창: 시선 · 음량은 끝난 지 30초 안의 1초 기록, 단어는 확정된 시각이
    지났고 60초 안에 끝난 것, 문장 끝은 60초 안의 것, STT 상태와 장은 그때의 것. STT 상태 이력이
    그 시각보다 늦게 시작하면 그 전은 연결 중(connecting)으로 본다.

    단어의 확정 시각 조건은 두 가지다.
    - 다시 만든 1초 요청(기본): `final_at_ms < t_ms`. 실시간에서 BE 는 확정 직전의 `metrics.t_ms` 를
      `final_at_ms` 로 두고 그 단어를 다음 요청부터 싣는다(기능 문서 12-2).
    - finalize 의 마지막 창(`last_window=True`): `final_at_ms <= t_ms`. BE 는 Take 끝보다 늦은 확정
      시각을 Take 끝으로 잘라 싣고, 마지막 창에는 Take 끝까지 끝난 확정 단어를 모두 싣는다.
    """
    lo_rec = t_ms - GAZE_VOICE_WINDOW_MS
    gaze = [r for r in raw.gaze_records if lo_rec <= r.t_ms and r.t_ms + r.duration_ms <= t_ms]
    voice = [r for r in raw.voice_records if lo_rec <= r.t_ms and r.t_ms + r.duration_ms <= t_ms]
    lo_word = t_ms - WORDS_WINDOW_MS
    final_by = t_ms + 1 if last_window else t_ms
    # 확정 시각으로 고른 뒤 말한 순서로 둔다 — 실시간 창도 (start_ms, end_ms) 순서다
    words = [
        WordIn(word=w.word, start_ms=w.start_ms, end_ms=w.end_ms)
        for w in sorted(raw.words, key=lambda w: (w.start_ms, w.end_ms))
        if w.final_at_ms < final_by and lo_word < w.end_ms <= t_ms
    ]
    ends = [u for u in raw.utterance_ends if lo_word <= u <= t_ms]
    # 처음 알려진 상태 전(이력이 없으면 Take 내내)은 연결 중 — 믿을 수 있다고 가정하지 않는다
    status = "connecting"
    for change in sorted(raw.stt_status_changes, key=lambda c: c.t_ms):
        if change.t_ms <= t_ms:
            status = change.status
    slide: SlideNow | None = None
    for s in sorted(raw.slides, key=lambda s: s.started_ms):
        if s.started_ms <= t_ms:
            slide = s
    # replay 를 읽을 때 이미 검사한 값이라 다시 검사하지 않는다(model_construct). 그래서 BE 가 같은
    # 1초를 되풀이해 실었어도 창 한도(BE 가 보내는 요청의 한도)에 걸리지 않고, 실시간과 같이 판정
    # 모듈이 읽을 수 있는 기록 중 먼저 온 것을 쓴다
    return CoachInputs.model_construct(
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
