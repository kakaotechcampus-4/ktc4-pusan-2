"""군더더기 판정 — 연구용 대역.

옛 코치 평가기(PR #131 의 `coach.evaluators.speech`)의 군더더기 규칙 · 기준값을 #152 계약 모양으로
옮긴 것이다.
기능 모듈이 나오면 이 대역 대신 그 모듈을 쓴다. 규칙은 소리뿐인 간투사(음 · 어 · 으 …)만 T1 로
셀 뿐이고, 사전(T2 · T3)과 판정 보류는 진짜 모듈의 일이라 여기에는 없다. 단어마다 바로 정한다.

상태를 갖지 않는다. 최근 60초의 단어를 입력으로 받아 개수를 다시 센다.
"""

from __future__ import annotations

import re
from typing import Any

from coach.config import criteria_version as _criteria_version
from coach.schemas import IssueCriteria, JudgmentIssue, JudgmentResult, TallyItem
from coach.vocab import FeedbackType, Issue

from ._common import HIGHER, In, Section, clean, parse, ramp, ratio

VERSION = "filler-0.1"
"""연구용 대역 버전."""

#: 단어 하나가 소리뿐인 간투사인가 (옛 `speech._FILLER_SOUND` 와 같다)
_FILLER_SOUND = re.compile(r"(?:음+|어+|으+음*|엄+|흠+|아+|에+)[.,?!~…]*")


class Config(Section):
    """옛 `SpeechConfig` 의 군더더기 값 그대로. onset · offset 은 옛 리뷰 근거 지연 값."""

    filler_window_ms: int = 60_000
    filler_threshold: int = 6
    filler_bad: int = 15
    onset_lag_ms: int = 30_000
    offset_lag_ms: int = 30_000


DEFAULT = Config()


class Word(In):
    word: str
    start_ms: int
    end_ms: int


class FillerInput(In):
    words: list[Word] = []
    utterance_ends: list[int] = []
    stt_status: str = "ok"
    stt_ok_since_ms: int | None = None
    since_ms: int = 0
    words_since_ms: int = -1


def criteria(config: Config = DEFAULT) -> dict[str, IssueCriteria]:
    return {
        Issue.FILLER_FREQUENT: IssueCriteria(
            metric="recent_filler_count",
            direction=HIGHER,
            threshold=config.filler_threshold,
            bad=config.filler_bad,
            onset_lag_ms=config.onset_lag_ms,
            offset_lag_ms=config.offset_lag_ms,
        )
    }


def summarize(
    tally: dict[str, dict[str, float]], config: Config = DEFAULT
) -> dict[str, dict[str, Any]]:
    """합친 집계를 군더더기 수 · 분당 수 · 층별 · 단어별 횟수로 바꾼다."""
    f = tally.get("FILLER", {})
    elapsed = f.get("elapsed_ms", 0)
    by_word = {
        k.removeprefix("filler_word:"): int(v) for k, v in f.items() if k.startswith("filler_word:")
    }
    return {
        "FILLER": {
            "filler_count": f.get("filler_count", 0),
            "filler_per_min": round(f.get("filler_count", 0) / elapsed * 60_000, 2)
            if elapsed > 0
            else None,
            "by_tier": {t: int(f.get(f"filler_{t.lower()}", 0)) for t in ("T1", "T2", "T3")},
            "by_word": by_word,
            "measured_ratio": ratio(elapsed, f.get("total_ms", 0)),
        }
    }


def judge(
    inputs: FillerInput | dict[str, Any], t_ms: int, config: Config = DEFAULT
) -> list[JudgmentResult]:
    """t_ms 시점의 군더더기 판정. 결과는 항상 하나다."""
    inp = parse(FillerInput, inputs)
    cfg = config
    stt_ok = inp.stt_status == "ok"
    since_ok = inp.stt_ok_since_ms
    # STT 를 믿을 수 없던 때의 단어는 뺀다
    words = sorted(
        (w for w in inp.words if since_ok is None or w.start_ms >= since_ok),
        key=lambda w: (w.start_ms, w.end_ms),
    )
    judged = [
        {
            "start_ms": w.start_ms,
            "end_ms": w.end_ms,
            "word": w.word,
            "tier": "T1" if _is_filler(w) else None,
            "is_filler": _is_filler(w),
            "reason": "채움말" if _is_filler(w) else "사전에 없음",
        }
        for w in words
    ]

    metrics: dict[str, float | None] = dict.fromkeys(("recent_filler_count", "filler_per_min"))
    issues: list[JudgmentIssue] = []
    state = "UNKNOWN"
    if stt_ok:
        recent = _count(judged, t_ms, cfg.filler_window_ms)
        observed = min(cfg.filler_window_ms, t_ms)
        metrics.update(
            recent_filler_count=recent,
            filler_per_min=round(recent * 60_000 / observed, 2) if observed >= 10_000 else None,
        )
        state = "HIGH" if recent >= cfg.filler_threshold else "NORMAL"
        if state == "HIGH":
            issues.append(
                JudgmentIssue(
                    issue_type=Issue.FILLER_FREQUENT,
                    area=FeedbackType.FILLER,
                    severity=ramp(recent, cfg.filler_threshold, cfg.filler_bad),
                    confidence=1.0,
                    persistence_sec=0.0,
                    threshold=cfg.filler_threshold,
                    bad=cfg.filler_bad,
                    actionable=True,
                    evidence={
                        "recent_filler_count": recent,
                        "filler_per_min": metrics["filler_per_min"],
                    },
                )
            )

    tally: list[TallyItem] = []
    cursor = inp.words_since_ms
    for w in sorted(inp.words, key=lambda w: (w.start_ms, w.end_ms)):
        if w.end_ms <= inp.words_since_ms:
            continue
        cursor = max(cursor, w.end_ms)
        if stt_ok and _is_filler(w) and (since_ok is None or w.start_ms >= since_ok):
            tally.append(
                TallyItem(
                    t_ms=w.start_ms,
                    values={"filler_count": 1, "filler_t1": 1, f"filler_word:{w.word.strip()}": 1},
                )
            )
    if t_ms > inp.since_ms:
        ok_ms = max(0, t_ms - max(inp.since_ms, since_ok or 0)) if stt_ok else 0
        tally.append(
            TallyItem(
                t_ms=inp.since_ms,
                values=clean({"elapsed_ms": ok_ms, "total_ms": t_ms - inp.since_ms}),
            )
        )

    return [
        JudgmentResult(
            evaluator="filler",
            area=FeedbackType.FILLER,
            t_ms=t_ms,
            counted_until_ms=max(t_ms, inp.since_ms),
            words_counted_until_ms=cursor,
            criteria_version=_criteria_version(VERSION, cfg),
            measurable=stt_ok,
            state=state,
            metrics=metrics,
            tally=tally,
            issues=issues,
            words=judged,
        )
    ]


def _is_filler(word: Word) -> bool:
    return _FILLER_SOUND.fullmatch(word.word.strip()) is not None


def _count(judged: list[dict[str, Any]], t_ms: int, window_ms: int) -> int:
    return sum(
        1
        for j in judged
        if j["is_filler"] and j["start_ms"] > t_ms - window_ms and j["end_ms"] <= t_ms
    )
