"""음량 · 침묵 — FE 음량 측정값으로 '목소리가 작음', '말이 멈춤'을 찾는다.

음량은 순간값이 크게 흔들리므로(volume-analysis v1 의 알려진 한계) 말하는 동안의
최근 smoothing_ms 평균으로 판단합니다.
"""

from __future__ import annotations

from statistics import fmean

from ..vocab import Issue
from .base import Detection, Tick, ramp


def evaluate(tick: Tick) -> None:
    voice = tick.req.current.voice
    if voice is None:
        return
    cfg = tick.cfg.voice
    pause_ms = tick.cfg.policy.pause_silence_ms

    speaking = voice.audio_live and voice.silence_ms < pause_ms
    tick.speaking = speaking if voice.audio_live else None

    # 오디오가 멈췄다 돌아오면 FE 침묵 시간에 멈춰 있던 시간이 섞여 온다.
    # 돌아온 순간 '말이 멈췄어요'가 뜨지 않게, 되살아난 뒤의 침묵만 센다
    st = tick.state
    if voice.audio_live and not st.audio_live:
        st.audio_live_since_ms = tick.t
    st.audio_live = voice.audio_live
    silence = voice.silence_ms
    if voice.audio_live and st.audio_live_since_ms is not None:
        silence = min(silence, tick.t - st.audio_live_since_ms)

    db: float | None = None
    if voice.audio_live:
        since = tick.t - cfg.smoothing_ms
        values = [
            s.relative_db
            for s in tick.history_since(since)
            if s.speaking and s.relative_db is not None
        ]
        if speaking and voice.relative_db is not None:
            values.append(voice.relative_db)
        db = round(fmean(values), 2) if len(values) >= cfg.min_samples else None

    tick.metrics["relative_db"] = db
    tick.metrics["silence_ms"] = silence if voice.audio_live else None

    if db is not None and db < cfg.low_relative_db:
        tick.detections.append(
            Detection(
                issue=Issue.VOLUME_LOW,
                severity=ramp(db, cfg.low_relative_db, cfg.low_relative_db_bad),
                confidence=1.0,
                sensor_ok=voice.audio_live,
                slide_number=tick.slide_number,
                metric="relative_db",
                evidence={"relative_db": db, "smoothing_ms": cfg.smoothing_ms},
            )
        )

    # 오디오가 멈춰 있으면 침묵이 길게 잡힌다. 후보는 만들되 SENSOR_UNUSABLE 로 버려진다 —
    # 사용자는 말하는데 '말이 멈췄어요'가 뜨는 것이 가장 나쁜 오탐이다 (FE 코치 주석)
    if silence > cfg.long_silence_ms:
        tick.detections.append(
            Detection(
                issue=Issue.LONG_SILENCE,
                severity=ramp(silence, cfg.long_silence_ms, cfg.long_silence_bad_ms),
                confidence=1.0,
                sensor_ok=voice.audio_live,
                slide_number=tick.slide_number,
                metric="silence_ms",
                evidence={"silence_ms": silence},
            )
        )
