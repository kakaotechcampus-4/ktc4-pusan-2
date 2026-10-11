"""음량 · 침묵 판정 — 연구용 대역.

옛 코치 평가기(PR #131 의 `coach.evaluators.voice`)의 규칙 · 기준값을 #155 계약 모양으로
옮긴 것이다.
기능 모듈이 나오면 이 대역 대신 그 모듈을 쓴다. 숫자는 옛 평가기와 같게 두고 출력 모양만 바꿨다.

결과는 둘이다 — 작은 목소리(VOLUME)와 긴 침묵(PAUSE). 상태를 갖지 않는다. 옛 평가기가
history 에 둔 최근 음량 값과 오디오 재개 시각은 입력 기록 30초에서 다시 계산한다.
기준 음량은 호출하는 쪽이 `baseline()` 으로 잡아 `base_level_db` 로 넘긴다.
"""

from __future__ import annotations

import math
from statistics import fmean, median
from typing import Any

from coach.config import criteria_version as _criteria_version
from coach.schemas import IssueCriteria, JudgmentIssue, JudgmentResult, TallyItem
from coach.vocab import FeedbackType, Issue

from ._common import HIGHER, LOWER, In, Section, parse, ramp, ratio

VERSION = "volume-0.2"
"""연구용 대역 버전."""


class Config(Section):
    """옛 `VoiceConfig` 의 값 그대로. pause_silence_ms 는 `PolicyConfig` 에서 가져왔다."""

    low_relative_db: float = -6.0
    low_relative_db_bad: float = -15.0
    smoothing_ms: int = 5_000
    min_samples: int = 3
    baseline_samples: int = 15
    long_silence_ms: int = 5_000
    long_silence_bad_ms: int = 15_000
    #: 이보다 조용한 시간이 짧으면 말하는 중으로 본다
    pause_silence_ms: int = 300
    #: 옛 리뷰 근거 설정의 지연 값과 같다
    volume_lag_ms: int = 2_500
    silence_onset_lag_ms: int = 5_000
    silence_offset_lag_ms: int = 0


DEFAULT = Config()


class Record(In):
    t_ms: int
    duration_ms: int = 1_000
    level_db: float | None = None
    voiced_ms: int = 0
    silence_ms: int = 0
    audio_live: bool = True

    @property
    def end_ms(self) -> int:
        return self.t_ms + self.duration_ms


class VolumeInput(In):
    records: list[Record] = []
    base_level_db: float | None = None
    since_ms: int = 0


def baseline(levels: list[float], config: Config = DEFAULT) -> float | None:
    """말한 1초의 level_db 표본에서 평소 목소리를 잡는다. 표본이 모자라면 None."""
    if len(levels) < config.baseline_samples:
        return None
    return round(median(levels[: config.baseline_samples]), 2)


def criteria(config: Config = DEFAULT) -> dict[str, IssueCriteria]:
    return {
        Issue.VOLUME_LOW: IssueCriteria(
            metric="voice_diff_db",
            direction=LOWER,
            threshold=config.low_relative_db,
            bad=config.low_relative_db_bad,
            onset_lag_ms=config.volume_lag_ms,
            offset_lag_ms=config.volume_lag_ms,
        ),
        Issue.LONG_SILENCE: IssueCriteria(
            metric="silence_ms",
            direction=HIGHER,
            threshold=config.long_silence_ms,
            bad=config.long_silence_bad_ms,
            onset_lag_ms=config.silence_onset_lag_ms,
            offset_lag_ms=config.silence_offset_lag_ms,
        ),
    }


def summarize(
    tally: dict[str, dict[str, float]], config: Config = DEFAULT
) -> dict[str, dict[str, float | None]]:
    """합친 집계를 Take 음량 · 기준 대비 · 작은 소리 비율 · 긴 침묵 수로 바꾼다."""
    v = tally.get("VOLUME", {})
    voiced = v.get("voiced_ms", 0)
    energy = v.get("voiced_energy", 0.0)
    level_voiced = v.get("level_voiced_ms", 0)
    level = round(10 * math.log10(energy / voiced), 2) if voiced > 0 and energy > 0 else None
    diff = (
        round(level - v.get("base_db_ms", 0.0) / level_voiced, 2)
        if level is not None and level_voiced > 0
        else None
    )
    live_ratio = ratio(v.get("audio_live_ms", 0), v.get("total_ms", 0))
    base_ratio = ratio(level_voiced, voiced)
    measured = (
        min(live_ratio, base_ratio) if live_ratio is not None and base_ratio is not None else None
    )
    p = tally.get("PAUSE", {})
    return {
        "VOLUME": {
            "level_db": level,
            "voice_diff_db": diff,
            "low_ratio": ratio(v.get("low_ms", 0), voiced),
            "measured_ratio": measured,
        },
        "PAUSE": {
            "long_silence_count": p.get("long_silence_count", 0),
            "measured_ratio": ratio(p.get("audio_live_ms", 0), p.get("total_ms", 0)),
        },
    }


def judge(
    inputs: VolumeInput | dict[str, Any], t_ms: int, config: Config = DEFAULT
) -> list[JudgmentResult]:
    """t_ms 시점의 음량 · 침묵 판정. 결과는 VOLUME, PAUSE 순서로 둘이다."""
    inp = parse(VolumeInput, inputs)
    cfg = config
    records = _dedupe(inp.records)
    seen = [r for r in records if r.end_ms <= t_ms]
    last = seen[-1] if seen else None
    live = last is not None and last.audio_live
    base = inp.base_level_db
    cv = _criteria_version(VERSION, cfg)
    tally_volume, tally_pause = _tally(records, inp, t_ms, cfg)
    common: dict[str, Any] = {
        "evaluator": "volume",
        "t_ms": t_ms,
        "counted_until_ms": max(t_ms, inp.since_ms),
        "criteria_version": cv,
    }

    # 음량: 말하는 동안의 최근 smoothing_ms 평균
    level_db, diff = _smoothed(seen, t_ms, base, cfg)
    volume_issues: list[JudgmentIssue] = []
    if live and diff is not None and diff < cfg.low_relative_db:
        volume_issues.append(
            JudgmentIssue(
                issue_type=Issue.VOLUME_LOW,
                area=FeedbackType.VOLUME,
                severity=ramp(diff, cfg.low_relative_db, cfg.low_relative_db_bad),
                confidence=1.0,
                persistence_sec=0.0,
                threshold=cfg.low_relative_db,
                bad=cfg.low_relative_db_bad,
                evidence={"voice_diff_db": diff, "smoothing_ms": cfg.smoothing_ms},
            )
        )
    volume_state = "UNKNOWN"
    if diff is not None:
        volume_state = "LOW" if diff < cfg.low_relative_db else "NORMAL"
    volume = JudgmentResult(
        **common,
        area=FeedbackType.VOLUME,
        measurable=live and base is not None and diff is not None,
        state=volume_state,
        metrics={"level_db": level_db, "voice_diff_db": diff},
        tally=tally_volume,
        issues=volume_issues,
    )

    # 침묵: 되살아난 뒤의 침묵만 센다. 오디오가 죽어 있으면 이슈를 내지 않는다
    silence = _silence(seen, t_ms)
    pause_issues: list[JudgmentIssue] = []
    if live and silence is not None and silence > cfg.long_silence_ms:
        pause_issues.append(
            JudgmentIssue(
                issue_type=Issue.LONG_SILENCE,
                area=FeedbackType.PAUSE,
                severity=ramp(silence, cfg.long_silence_ms, cfg.long_silence_bad_ms),
                confidence=1.0,
                persistence_sec=silence / 1000,
                threshold=cfg.long_silence_ms,
                bad=cfg.long_silence_bad_ms,
                evidence={"silence_ms": silence},
            )
        )
    if not live or silence is None:
        pause_state = "UNKNOWN"
    else:
        pause_state = "SILENT" if silence > cfg.long_silence_ms else "NORMAL"
    pause = JudgmentResult(
        **common,
        area=FeedbackType.PAUSE,
        measurable=live,
        state=pause_state,
        metrics={"silence_ms": silence if live else None},
        tally=tally_pause,
        issues=pause_issues,
    )
    return [volume, pause]


def _dedupe(records: list[Record]) -> list[Record]:
    """시간순으로 두고, 같은 1초가 두 번 오면 먼저 온 것을 쓴다."""
    out: dict[int, Record] = {}
    for r in records:
        out.setdefault(r.t_ms, r)
    return [out[k] for k in sorted(out)]


def _speaking(r: Record, cfg: Config) -> bool:
    """말하는 중인 1초 — 오디오가 흐르고, 조용한 시간이 짧고, 레벨이 있다."""
    return r.audio_live and r.silence_ms < cfg.pause_silence_ms and r.level_db is not None


def _smoothed(
    seen: list[Record], end_ms: int, base: float | None, cfg: Config
) -> tuple[float | None, float | None]:
    """(level_db, voice_diff_db). end_ms 에서 끝나는 최근 smoothing_ms 의 말하는 1초 평균."""
    if not seen:
        return None, None
    window = [r for r in seen if r.end_ms > end_ms - cfg.smoothing_ms and _speaking(r, cfg)]
    levels = [r.level_db for r in window if r.level_db is not None]
    level = round(fmean(levels), 2) if levels else None
    # level_db 는 마지막 1초의 오디오와 상관없이 구하고, 기준 대비는 오디오가 흐를 때만 낸다.
    # 기준 음량이 Take 중간에 생기면 대역은 창 전체에 쓴다 — 옛 평가기는 기준을 잡기 전 1초를
    # 평균에서 뺐다(기준이 생긴 직후 1~2초만 다를 수 있다)
    if not seen[-1].audio_live or base is None or len(levels) < cfg.min_samples:
        return level, None
    return level, round(fmean(round(lv - base, 2) for lv in levels), 2)


def _silence(seen: list[Record], end_ms: int) -> int | None:
    """마지막 기록의 침묵 ms. 오디오가 되살아난 뒤의 시간으로 자른다 (끊긴 시간은 침묵이 아니다)."""
    if not seen:
        return None
    last = seen[-1]
    if not last.audio_live:
        return last.silence_ms
    dead = [i for i, r in enumerate(seen) if not r.audio_live]
    if not dead:
        return last.silence_ms
    # 되살아난 첫 1초가 끝난 시각부터 센다 (옛 평가기는 되살아난 첫 판단 시각부터 센다)
    revived_at = seen[dead[-1] + 1].end_ms
    return min(last.silence_ms, max(0, end_ms - revived_at))


def _tally(
    records: list[Record], inp: VolumeInput, t_ms: int, cfg: Config
) -> tuple[list[TallyItem], list[TallyItem]]:
    """새 1초 기록(t_ms ≥ since_ms)의 VOLUME · PAUSE 집계. 기록이 없는 시간은 total_ms 만 센다."""
    new = [r for r in records if r.t_ms >= inp.since_ms and r.end_ms <= t_ms]
    base = inp.base_level_db
    volume: list[TallyItem] = []
    pause: list[TallyItem] = []

    def gap(start: int, end: int) -> None:
        volume.append(TallyItem(t_ms=start, values={"total_ms": end - start}))
        pause.append(TallyItem(t_ms=start, values={"total_ms": end - start}))

    pos = inp.since_ms
    for r in new:
        if r.t_ms > pos:
            gap(pos, r.t_ms)
        pos = max(pos, r.end_ms)
        upto = [x for x in records if x.end_ms <= r.end_ms]
        _, diff = _smoothed(upto, r.end_ms, base, cfg)
        energy = 10 ** (r.level_db / 10) * r.voiced_ms if r.level_db is not None else 0.0
        level_voiced = r.voiced_ms if base is not None else 0
        live_ms = r.duration_ms if r.audio_live else 0
        volume.append(
            TallyItem(
                t_ms=r.t_ms,
                values={
                    "voiced_ms": r.voiced_ms,
                    "voiced_energy": energy,
                    "low_ms": r.voiced_ms if diff is not None and diff < cfg.low_relative_db else 0,
                    "level_voiced_ms": level_voiced,
                    "base_db_ms": base * level_voiced if base is not None else 0,
                    "audio_live_ms": live_ms,
                    "total_ms": r.duration_ms,
                },
            )
        )
        silence = _silence(upto, r.end_ms) if r.audio_live else None
        crossed = silence is not None and silence - r.duration_ms <= cfg.long_silence_ms < silence
        pause.append(
            TallyItem(
                t_ms=r.t_ms,
                values={
                    "long_silence_count": 1 if crossed else 0,
                    "audio_live_ms": live_ms,
                    "total_ms": r.duration_ms,
                },
            )
        )
    if pos < t_ms and t_ms > inp.since_ms:
        gap(pos, t_ms)
    return volume, pause
