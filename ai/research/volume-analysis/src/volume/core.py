"""소리 크기 · 침묵 판정 (순수 함수).

파일 · DB · 네트워크 · 시계를 쓰지 않는다. 시간은 입력의 ``t_ms`` 뿐이고, 같은 입력에는 같은 결과다.
상태를 갖지 않는다 — 기준 음량 표본과 값은 코치가 ``coach_state`` 에 들고 다닌다.
"""

from __future__ import annotations

import hashlib
import json
import math
from statistics import median
from typing import Any

from .config import Config
from .schemas import (
    Criteria,
    IssueCriteria,
    LongSilenceIssue,
    PauseJudgment,
    PauseMetrics,
    PauseSummary,
    PauseTally,
    PauseTallyItem,
    Summary,
    VoiceRecord,
    VolumeInput,
    VolumeJudgment,
    VolumeLowIssue,
    VolumeMetrics,
    VolumeSummary,
    VolumeTally,
    VolumeTallyItem,
)
from .version import FEATURE_VERSION


def judge(
    inputs: VolumeInput | dict[str, Any], t_ms: int, config: Config = Config()
) -> list[VolumeJudgment | PauseJudgment]:
    """``t_ms`` 시점의 1초 판정. 결과는 ``VOLUME`` · ``PAUSE`` 순서로 둘이다."""
    inp = inputs if isinstance(inputs, VolumeInput) else VolumeInput.model_validate(inputs)
    records = _dedupe(inp.records)
    seen = [r for r in records if _end(r) <= t_ms]
    base = inp.base_level_db
    common: dict[str, Any] = {
        "evaluator": "volume",
        "t_ms": t_ms,
        "counted_until_ms": max(t_ms, inp.since_ms),
        "criteria_version": _criteria_version(config),
    }
    volume_tally, pause_tally = _tally(records, inp.since_ms, t_ms, base, config)

    # VOLUME: 짧은 창의 말한 1초들로 에너지 Leq 를 내고 기준 음량과 비교한다
    level_db, diff = _smoothed(seen, t_ms, base, config)
    volume_issues: list[VolumeLowIssue] = []
    if diff is None:
        volume_state = "UNKNOWN"
    elif diff < config.low_diff_db:
        volume_state = "LOW"
        volume_issues.append(
            VolumeLowIssue(
                issue_type="VOLUME_LOW",
                area="VOLUME",
                severity=_ramp(diff, config.low_diff_db, config.low_diff_bad_db),
                confidence=1.0,
                persistence_sec=_low_run_ms(seen, base, config) / 1000,
                threshold=config.low_diff_db,
                bad=config.low_diff_bad_db,
                evidence={"voice_diff_db": diff},
            )
        )
    else:
        volume_state = "NORMAL"
    volume = VolumeJudgment(
        **common,
        area="VOLUME",
        measurable=diff is not None,
        state=volume_state,
        metrics=VolumeMetrics(level_db=level_db, voice_diff_db=diff),
        tally=volume_tally,
        issues=volume_issues,
    )

    # PAUSE: 마지막 1초 끝의 연속 무음을 오디오가 다시 흐른 뒤로 잘라 본다
    silence = _silence(seen)
    pause_issues: list[LongSilenceIssue] = []
    if silence is None:
        pause_state = "UNKNOWN"
    elif silence > config.long_silence_ms:
        pause_state = "SILENT"
        pause_issues.append(
            LongSilenceIssue(
                issue_type="LONG_SILENCE",
                area="PAUSE",
                severity=_ramp(silence, config.long_silence_ms, config.long_silence_bad_ms),
                confidence=1.0,
                persistence_sec=silence / 1000,
                threshold=config.long_silence_ms,
                bad=config.long_silence_bad_ms,
                evidence={"silence_ms": silence},
            )
        )
    else:
        pause_state = "NORMAL"
    pause = PauseJudgment(
        **common,
        area="PAUSE",
        measurable=silence is not None,
        state=pause_state,
        metrics=PauseMetrics(silence_ms=silence),
        tally=pause_tally,
        issues=pause_issues,
    )
    return [volume, pause]


def speaking_levels(
    records: list[VoiceRecord | dict[str, Any]], config: Config = Config()
) -> list[float]:
    """음량 1초 기록 → 그중 말한 1초의 ``level_db`` (시각 순). 기준 음량 표본으로 코치가 모은다.

    말한 1초를 고르는 기준값을 이 모듈이 갖게 해서, 코치가 판정 기준을 따로 두지 않게 한다.
    ``level_db`` 가 null 인 1초도 뺀다.
    """
    parsed = [r if isinstance(r, VoiceRecord) else VoiceRecord.model_validate(r) for r in records]
    return [r.level_db for r in _dedupe(parsed) if _spoken(r, config) and r.level_db is not None]


def baseline(levels: list[float], config: Config = Config()) -> float | None:
    """말한 1초의 ``level_db`` 표본 → 기준 음량. 표본이 모자라면 None.

    앞에서 ``baseline_samples`` 개의 중앙값이다. 코치는 값이 나오면 고정하고 표본을 비운다.
    """
    if len(levels) < config.baseline_samples:
        return None
    return round(median(levels[: config.baseline_samples]), 2)


def summarize(tally: dict[str, dict[str, float]], config: Config = Config()) -> Summary:
    """장 · Take 의 ``tally`` 합계(영역 → 이름 → 합) → 영역별 지표.

    분모가 0이면 null. ``measured_ratio`` 만 분모가 0이면 0이다
    (코치가 측정 비율 기준 미만으로 본다).
    """
    v = tally.get("VOLUME", {})
    voiced = v.get("voiced_ms", 0)
    energy = v.get("voiced_energy", 0)
    level_voiced = v.get("level_voiced_ms", 0)
    level = round(10 * math.log10(energy / voiced), 2) if voiced > 0 and energy > 0 else None
    # 기준 음량이 있던 동안의 발화로 가중한 평균 기준 음량과 비교한다
    diff = (
        round(level - v.get("base_db_ms", 0) / level_voiced, 2)
        if level is not None and level_voiced > 0
        else None
    )
    # 기준 음량이 없던 동안의 발화는 잰 것으로 보지 않는다. 말한 소리가 없으면 0
    volume_measured = 0.0
    if voiced > 0:
        live_ratio = _ratio(v.get("audio_live_ms", 0), v.get("total_ms", 0)) or 0.0
        volume_measured = min(live_ratio, _ratio(level_voiced, voiced) or 0.0)

    p = tally.get("PAUSE", {})
    return Summary(
        VOLUME=VolumeSummary(
            level_db=level,
            voice_diff_db=diff,
            low_ratio=_ratio(v.get("low_ms", 0), voiced),
            measured_ratio=volume_measured,
        ),
        PAUSE=PauseSummary(
            long_silence_count=round(p.get("long_silence_count", 0)),
            measured_ratio=_ratio(p.get("audio_live_ms", 0), p.get("total_ms", 0)) or 0.0,
        ),
    )


def criteria(config: Config = Config()) -> Criteria:
    """``VOLUME_LOW`` · ``LONG_SILENCE`` 의 판정 기준. 코치가 Take 결과의 ``criteria`` 에 남긴다."""
    return Criteria(
        VOLUME_LOW=IssueCriteria(
            metric="voice_diff_db",
            direction="LOWER_IS_WORSE",
            threshold=config.low_diff_db,
            bad=config.low_diff_bad_db,
            onset_lag_ms=config.volume_lag_ms,
            offset_lag_ms=config.volume_lag_ms,
        ),
        LONG_SILENCE=IssueCriteria(
            metric="silence_ms",
            direction="HIGHER_IS_WORSE",
            threshold=config.long_silence_ms,
            bad=config.long_silence_bad_ms,
            onset_lag_ms=config.silence_onset_lag_ms,
            offset_lag_ms=config.silence_offset_lag_ms,
        ),
    )


def _ratio(num: float, den: float) -> float | None:
    """분모가 0이면 None. 소수 넷째 자리까지."""
    return round(num / den, 4) if den > 0 else None


def _criteria_version(config: Config) -> str:
    """<feature_version>+<config 해시 12자리>. 기준값을 바꾸면 해시가 바뀐다."""
    blob = json.dumps(config.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return f"{FEATURE_VERSION}+{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:12]}"


def _end(r: VoiceRecord) -> int:
    return r.t_ms + r.duration_ms


def _dedupe(records: list[VoiceRecord]) -> list[VoiceRecord]:
    """시각 순으로 두고, 같은 1초가 두 번 오면 먼저 온 것을 쓴다."""
    out: dict[int, VoiceRecord] = {}
    for r in records:
        out.setdefault(r.t_ms, r)
    return [out[k] for k in sorted(out)]


def _spoken(r: VoiceRecord, config: Config) -> bool:
    """말한 1초: voiced_ms 가 기준 이상이고 오디오가 흐른 1초 (architecture.md 6-2)."""
    return r.audio_live and r.voiced_ms >= config.voiced_min_ms


def _smoothed(
    seen: list[VoiceRecord], end_ms: int, base: float | None, config: Config
) -> tuple[float | None, float | None]:
    """(level_db, voice_diff_db). end_ms 에서 끝나는 짧은 창의 말한 1초들로 낸다.

    level_db 는 dB 평균이 아니라 에너지 평균(Leq)이다. voice_diff_db 는 잴 수 있을 때만 낸다 —
    마지막 1초에 오디오가 흐르고, 기준 음량이 있고, 말한 1초가 기준 개수 이상.
    """
    window = [
        r
        for r in seen
        if end_ms - config.smoothing_ms < _end(r) <= end_ms
        and _spoken(r, config)
        and r.level_db is not None
    ]
    voiced = sum(r.voiced_ms for r in window)
    if voiced <= 0:
        return None, None
    energy = sum(10 ** (r.level_db / 10) * r.voiced_ms for r in window)  # type: ignore[operator]
    level = round(10 * math.log10(energy / voiced), 2)
    last_live = bool(seen) and seen[-1].audio_live
    if not last_live or base is None or len(window) < config.min_voiced_seconds:
        return level, None
    return level, round(level - base, 2)


def _low_run_ms(seen: list[VoiceRecord], base: float | None, config: Config) -> int:
    """마지막 1초부터 거꾸로, 짧은 창의 voice_diff_db 가 작음 기준 미만이었던 시간."""
    run = 0
    for i in range(len(seen) - 1, -1, -1):
        _, diff = _smoothed(seen[: i + 1], _end(seen[i]), base, config)
        if diff is None or diff >= config.low_diff_db:
            break
        run += seen[i].duration_ms
    return run


def _silence(seen: list[VoiceRecord]) -> int | None:
    """마지막 1초 끝의 연속 무음. 오디오가 멈췄으면 None.

    받은 창 안에 오디오가 끊긴 1초가 있으면, 다시 흐른 첫 1초의 시작부터만 침묵으로 본다
    (끊긴 시간은 침묵이 아니다). 창 안에 끊김이 없으면 자르지 않는다.
    """
    if not seen or not seen[-1].audio_live:
        return None
    last = seen[-1]
    dead = [i for i, r in enumerate(seen) if not r.audio_live]
    if not dead:
        return last.silence_ms
    revived_at = seen[dead[-1] + 1].t_ms
    return min(last.silence_ms, max(0, _end(last) - revived_at))


def _ramp(value: float, threshold: float, bad: float) -> float:
    """심각도 기준값에서 0.5, 아주 나쁨 값에서 1.0, 그 사이는 직선, 1.0 에서 자른다."""
    if bad == threshold:
        return 1.0
    frac = (value - threshold) / (bad - threshold)
    return round(0.5 + 0.5 * min(1.0, max(0.0, frac)), 4)


def _tally(
    records: list[VoiceRecord], since_ms: int, t_ms: int, base: float | None, config: Config
) -> tuple[list[VolumeTallyItem], list[PauseTallyItem]]:
    """새 1초(t_ms ≥ since_ms)의 VOLUME · PAUSE 몫. 기록이 없는 시간은 total_ms 만 센다.

    '말한 1초'로 거르지 않고 기록이 있는 모든 1초를 더한다. 한 번 센 몫은 바꾸지 않는다.
    """
    new = [r for r in records if r.t_ms >= since_ms and _end(r) <= t_ms]
    volume: list[VolumeTallyItem] = []
    pause: list[PauseTallyItem] = []

    def gap(start: int, end: int) -> None:
        volume.append(VolumeTallyItem(t_ms=start, values=VolumeTally(total_ms=end - start)))
        pause.append(PauseTallyItem(t_ms=start, values=PauseTally(total_ms=end - start)))

    pos = since_ms
    for r in new:
        if r.t_ms > pos:
            gap(pos, r.t_ms)
        pos = max(pos, _end(r))
        # 그 1초 시점까지의 기록만 보고 센다
        upto = [x for x in records if _end(x) <= _end(r)]
        _, diff = _smoothed(upto, _end(r), base, config)
        level_voiced = r.voiced_ms if base is not None else 0
        live_ms = r.duration_ms if r.audio_live else 0
        volume.append(
            VolumeTallyItem(
                t_ms=r.t_ms,
                values=VolumeTally(
                    voiced_ms=r.voiced_ms,
                    voiced_energy=(
                        10 ** (r.level_db / 10) * r.voiced_ms if r.level_db is not None else 0
                    ),
                    low_ms=r.voiced_ms if diff is not None and diff < config.low_diff_db else 0,
                    level_voiced_ms=level_voiced,
                    base_db_ms=base * level_voiced if base is not None else 0,
                    audio_live_ms=live_ms,
                    total_ms=r.duration_ms,
                ),
            )
        )
        # 잘린 침묵이 이 1초 안에서 긴 침묵 기준을 처음 넘으면 1 (한 침묵은 한 번)
        silence = _silence(upto)
        crossed = (
            silence is not None and silence - r.duration_ms <= config.long_silence_ms < silence
        )
        pause.append(
            PauseTallyItem(
                t_ms=r.t_ms,
                values=PauseTally(
                    long_silence_count=1 if crossed else 0,
                    audio_live_ms=live_ms,
                    total_ms=r.duration_ms,
                ),
            )
        )
    if pos < t_ms and t_ms > since_ms:
        gap(pos, t_ms)
    return volume, pause
