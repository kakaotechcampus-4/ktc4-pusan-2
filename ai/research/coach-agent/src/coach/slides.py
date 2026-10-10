"""장별 누적 — 한 장에 머무는 동안의 측정값을 모아, 장이 바뀌면 SLIDE 이벤트로 내보낸다.

리뷰 에이전트의 장별 표(시선 · 속도 · 군더더기 · 음량 · 시간)와
미션 판정(예: "6번 장 대본 응시 30% 이하")의 원천입니다.

평균은 '값 × 시간'의 합으로 남깁니다. 요청 간격이 흔들려도 시간 가중 평균이 됩니다.
센서를 믿을 수 없던 시간은 따로 셉니다 — 리뷰가 그 장을 '판단 불가'로 둘 수 있게.
"""

from __future__ import annotations

from .evaluators.base import Tick
from .events import EventSink
from .schemas import SlideEvent
from .state import CoachState, SlideAcc


def switch(tick: Tick, sink: EventSink) -> None:
    """장이 바뀌었으면 앞 장을 닫고 새 장 누적을 시작한다."""
    st = tick.state
    acc = st.slide_acc
    if acc is not None and acc.slide_number == tick.slide_number:
        return
    if acc is not None:
        close(st, tick.t, sink)
    plan = tick.slide_plan(tick.slide_number)
    st.slide_acc = SlideAcc(
        slide_number=tick.slide_number,
        start_ms=tick.t,
        target_ms=plan.target_ms if plan else None,
        script_chars=plan.script_chars if plan else None,
    )


def accumulate(tick: Tick) -> None:
    acc = tick.state.slide_acc
    if acc is None:
        return
    dt = tick.dt_ms
    m = tick.metrics
    acc.total_ms += dt

    # 시선은 늘 들어온다 — 비율이 없으면 '믿을 수 없던 시간'으로 센다
    ratio = m.get("script_ratio")
    if ratio is not None:
        acc.gaze_valid_ms += dt
        acc.gaze_script_ms += ratio * dt
    else:
        acc.gaze_unusable_ms += dt

    if tick.stt_ok:
        acc.speech_ok_ms += dt
    cpm = m.get("cpm")
    if cpm is not None:
        acc.cpm_ms += dt
        acc.cpm_weighted += cpm * dt
    acc.filler_count += tick.filler_new

    voices = tick.req.inputs.voice_records
    latest = max(voices, key=lambda r: r.t_ms, default=None)
    if latest is not None and latest.audio_live:
        acc.audio_live_ms += dt
        if tick.speaking:
            acc.speaking_ms += dt
            if tick.voice_diff_db is not None:
                acc.db_ms += dt
                acc.db_weighted += tick.voice_diff_db * dt
        silence = m.get("silence_ms")
        long_silence = tick.criteria.get("volume", {}).get("LONG_SILENCE")
        if silence is not None and long_silence is not None and silence > long_silence.threshold:
            acc.long_silence_ms += dt


def close(st: CoachState, t_ms: int, sink: EventSink) -> None:
    acc = st.slide_acc
    if acc is None:
        return
    st.slide_acc = None
    key = str(acc.slide_number) if acc.slide_number is not None else ""
    chars = st.slide_totals.get(key, {}).get("SPEED", {}).get("chars", 0)
    fields = acc.model_dump()
    fields.pop("start_ms")
    sink.emit(
        SlideEvent,
        t_ms=t_ms,
        start_ms=acc.start_ms,
        end_ms=t_ms,
        chars_total=int(chars),
        **fields,
    )
