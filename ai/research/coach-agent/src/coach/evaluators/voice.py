"""음량 · 침묵 — FE 음량 측정값으로 '목소리가 작음', '말이 멈춤'을 찾는다.

음량은 순간값이 크게 흔들리므로(volume-analysis v1 의 알려진 한계) 말하는 동안의
최근 smoothing_ms 평균으로 판단합니다.

'작음'은 평소 목소리 대비입니다. FE 가 기준 대비 값(relative_db)을 보내면 그대로 쓰고, 측정한
레벨(level_db)을 보내면 baseline_db 와의 차이를 씁니다. baseline_db 도 없으면 이번 Take 첫 발화
baseline_samples 초의 중앙값을 평소 목소리로 잡아 coach_state 에 둡니다 — 개인 캘리브레이션이
없어도 '평소보다 작아짐'을 잴 수 있습니다.
"""

from __future__ import annotations

from statistics import fmean, median

from ..schemas import VoiceInput
from ..vocab import Issue
from .base import Detection, Tick, ramp


def relative_db(tick: Tick, voice: VoiceInput, speaking: bool) -> float | None:
    """이번 1초의 기준 대비 음량. 말하지 않았거나 기준을 아직 못 잡았으면 None."""
    if not speaking:
        return None
    if voice.relative_db is not None:
        return voice.relative_db
    if voice.level_db is None:
        return None
    if voice.baseline_db is not None:
        return round(voice.level_db - voice.baseline_db, 2)
    st = tick.state
    if st.voice_baseline_db is None:
        st.voice_baseline_samples.append(voice.level_db)
        if len(st.voice_baseline_samples) < tick.cfg.voice.baseline_samples:
            return None
        st.voice_baseline_db = round(median(st.voice_baseline_samples), 2)
        st.voice_baseline_samples = []
    return round(voice.level_db - st.voice_baseline_db, 2)


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

    tick.relative_db = relative_db(tick, voice, speaking) if voice.audio_live else None
    db: float | None = None
    if voice.audio_live:
        since = tick.t - cfg.smoothing_ms
        values = [
            s.relative_db
            for s in tick.history_since(since)
            if s.speaking and s.relative_db is not None
        ]
        if tick.relative_db is not None:
            values.append(tick.relative_db)
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
