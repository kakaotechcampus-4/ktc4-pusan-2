"""말 속도 판정 — 연구용 대역.

옛 코치 평가기(PR #131 의 `coach.evaluators.speech`)의 CPM 규칙 · 기준값을 #154 계약 모양으로
옮긴 것이다. 기능 모듈이 나오면 이 대역 대신 그 모듈을 쓴다. CPM 은 옛 평가기와 같게 두고
출력 모양과 집계만 더했다.

CPM = 글자 수(공백 · 군더더기 제외) ÷ 단어 발화 시간(군더더기 단어도 시간에는 넣는다) × 60초.
군더더기 판정은 같은 요청의 `fillers`(군더더기 모듈의 결과)로 받는다. 상태를 갖지 않는다.
"""

from __future__ import annotations

from typing import Any

from coach.config import criteria_version as _criteria_version
from coach.schemas import IssueCriteria, JudgmentIssue, JudgmentResult, TallyItem
from coach.vocab import FeedbackType, Issue

from ._common import HIGHER, LOWER, In, Section, clean, parse, ramp, ratio

VERSION = "pace-0.2"
"""연구용 대역 버전."""


class Config(Section):
    """옛 `SpeechConfig` 의 CPM 값 그대로. onset · offset 은 옛 리뷰 근거 설정의 지연 값과 같다."""

    window_ms: int = 15_000
    slow_cpm: float = 275.0
    #: 옛 평가기에 없던 값. 코치는 PACE_SLOW 를 기록만 한다
    slow_cpm_bad: float = 200.0
    fast_cpm: float = 350.0
    fast_cpm_bad: float = 450.0
    min_speak_ms: int = 4_000
    min_words: int = 5
    recent_window_ms: int = 6_000
    recent_min_speak_ms: int = 2_500
    #: 단어 사이가 이보다 길면 멈춤으로 센다
    pause_min_ms: int = 2_000
    onset_lag_ms: int = 7_500
    offset_lag_ms: int = 7_500


DEFAULT = Config()


class Word(In):
    word: str
    start_ms: int
    end_ms: int


class FillerMark(In):
    """군더더기 판정. is_filler 가 None 이면 보류."""

    start_ms: int
    end_ms: int
    is_filler: bool | None = None


class PaceInput(In):
    words: list[Word] = []
    stt_status: str = "ok"
    stt_ok_since_ms: int | None = None
    #: None 이면 군더더기 판정이 실패한 것(빈 목록은 군더더기 없음)
    fillers: list[FillerMark] | None = []
    since_ms: int = 0
    words_since_ms: int = -1


def criteria(config: Config = DEFAULT) -> dict[str, IssueCriteria]:
    return {
        Issue.PACE_FAST: IssueCriteria(
            metric="cpm",
            direction=HIGHER,
            threshold=config.fast_cpm,
            bad=config.fast_cpm_bad,
            onset_lag_ms=config.onset_lag_ms,
            offset_lag_ms=config.offset_lag_ms,
        ),
        Issue.PACE_SLOW: IssueCriteria(
            metric="cpm",
            direction=LOWER,
            threshold=config.slow_cpm,
            bad=config.slow_cpm_bad,
            onset_lag_ms=config.onset_lag_ms,
            offset_lag_ms=config.offset_lag_ms,
        ),
    }


def summarize(
    tally: dict[str, dict[str, float]], config: Config = DEFAULT
) -> dict[str, dict[str, float | None]]:
    """합친 집계를 CPM · 멈춤 수 · 측정 비율로 바꾼다."""
    s = tally.get("SPEED", {})
    speak = s.get("speak_ms", 0)
    cpm = round(s.get("chars", 0) / speak * 60_000, 1) if speak > 0 else None
    return {
        "SPEED": {
            "cpm": cpm,
            "pause_count": s.get("pause_count", 0),
            "measured_ratio": ratio(s.get("ok_ms", 0), s.get("total_ms", 0)),
        }
    }


def judge(
    inputs: PaceInput | dict[str, Any], t_ms: int, config: Config = DEFAULT
) -> list[JudgmentResult]:
    """t_ms 시점의 말 속도 판정. 결과는 항상 하나다."""
    inp = parse(PaceInput, inputs)
    cfg = config
    stt_ok = inp.stt_status == "ok"
    common: dict[str, Any] = {
        "evaluator": "pace",
        "area": FeedbackType.SPEED,
        "t_ms": t_ms,
        "counted_until_ms": max(t_ms, inp.since_ms),
        "criteria_version": _criteria_version(VERSION, cfg),
    }
    words = sorted(inp.words, key=lambda w: (w.start_ms, w.end_ms))

    if inp.fillers is None:
        # 군더더기 판정이 실패했다 — 군더더기가 글자 수에 섞이지 않게 새 단어를 세지 않고 단어
        # 커서도 두며, 시간(ok_ms · total_ms)은 STT 상태대로 센다
        return [
            JudgmentResult(
                **common,
                words_counted_until_ms=inp.words_since_ms,
                measurable=False,
                state="UNKNOWN",
                metrics={"cpm": None, "cpm_short": None, "speak_ms": None},
                tally=_time_tally(inp, t_ms, counted_ok=stt_ok),
            )
        ]

    marks = {(m.start_ms, m.end_ms): m.is_filler for m in inp.fillers}
    fillers = {k for k, v in marks.items() if v is True}

    # STT 가 불량이었다가 돌아온 직후에는 창을 돌아온 뒤로 줄인다
    window = cfg.window_ms
    if inp.stt_ok_since_ms is not None and t_ms - inp.stt_ok_since_ms < window:
        window = t_ms - inp.stt_ok_since_ms
    raw_cpm, speak_ms, n_words = _cpm(words, fillers, t_ms, window)
    recent, recent_ms, recent_n = _cpm(words, fillers, t_ms, min(window, cfg.recent_window_ms))
    cpm_short = (
        round(recent, 1)
        if stt_ok and recent is not None and recent_ms >= cfg.recent_min_speak_ms and recent_n >= 3
        else None
    )
    enough = raw_cpm is not None and speak_ms >= cfg.min_speak_ms and n_words >= cfg.min_words
    cpm = round(raw_cpm, 1) if enough and raw_cpm is not None else None
    shown = cpm if stt_ok else None

    if shown is None:
        state = "UNKNOWN"
    elif shown < cfg.slow_cpm:
        state = "SLOW"
    elif shown > cfg.fast_cpm:
        state = "FAST"
    else:
        state = "NORMAL"

    issues: list[JudgmentIssue] = []
    # STT 를 믿을 수 없으면 이슈를 내지 않는다 (measurable 이 false 면 측정 불가 신호뿐이어야 한다)
    if stt_ok and cpm is not None and cpm > cfg.fast_cpm:
        issues.append(
            JudgmentIssue(
                issue_type=Issue.PACE_FAST,
                area=FeedbackType.SPEED,
                severity=ramp(cpm, cfg.fast_cpm, cfg.fast_cpm_bad),
                confidence=0.6 + 0.4 * min(1.0, speak_ms / (2 * cfg.min_speak_ms)),
                persistence_sec=0.0,
                threshold=cfg.fast_cpm,
                bad=cfg.fast_cpm_bad,
                actionable=True,
                evidence={"cpm": cpm, "window_ms": cfg.window_ms, "speak_ms": speak_ms},
            )
        )
    elif stt_ok and cpm is not None and cpm < cfg.slow_cpm:
        issues.append(
            JudgmentIssue(
                issue_type=Issue.PACE_SLOW,
                area=FeedbackType.SPEED,
                severity=ramp(cpm, cfg.slow_cpm, cfg.slow_cpm_bad),
                confidence=0.6 + 0.4 * min(1.0, speak_ms / (2 * cfg.min_speak_ms)),
                persistence_sec=0.0,
                threshold=cfg.slow_cpm,
                bad=cfg.slow_cpm_bad,
                actionable=True,
                evidence={"cpm": cpm, "window_ms": cfg.window_ms, "speak_ms": speak_ms},
            )
        )

    items, cursor = _word_tally(words, marks, inp, stt_ok, cfg)
    return [
        JudgmentResult(
            **common,
            words_counted_until_ms=cursor,
            measurable=stt_ok and cpm is not None,
            state=state,
            metrics={"cpm": shown, "cpm_short": cpm_short, "speak_ms": speak_ms},
            tally=[*items, *_time_tally(inp, t_ms, counted_ok=stt_ok)],
            issues=issues,
        )
    ]


def _nonspace_len(text: str) -> int:
    return len("".join(text.split()))


def _cpm(
    words: list[Word], fillers: set[tuple[int, int]], t_ms: int, window_ms: int
) -> tuple[float | None, int, int]:
    """(cpm, 말한 ms, 단어 수). 창 안의 군더더기 아닌 단어만 센다 (`speech.compute_cpm` 과 같다)."""
    lo = t_ms - window_ms
    chosen = [
        w
        for w in words
        if (w.start_ms, w.end_ms) not in fillers and w.start_ms >= lo and w.end_ms <= t_ms
    ]
    chars = sum(_nonspace_len(w.word) for w in chosen)
    speak_ms = sum(max(0, w.end_ms - w.start_ms) for w in chosen)
    if speak_ms <= 0:
        return None, 0, len(chosen)
    return chars / speak_ms * 60_000, speak_ms, len(chosen)


def _word_tally(
    words: list[Word],
    marks: dict[tuple[int, int], bool | None],
    inp: PaceInput,
    stt_ok: bool,
    cfg: Config,
) -> tuple[list[TallyItem], int]:
    """새 단어(end_ms > words_since_ms)의 집계와 새 단어 커서. 판정이 보류된 단어 앞에서 멈춘다."""
    since_ok = inp.stt_ok_since_ms
    cursor = inp.words_since_ms
    items: list[TallyItem] = []
    prev: Word | None = None
    for w in words:
        key = (w.start_ms, w.end_ms)
        if w.end_ms > inp.words_since_ms:
            # STT 를 믿을 수 없던 때의 단어는 세지 않는다. 판정 보류와 상관없이 커서만 넘긴다
            usable = stt_ok and (since_ok is None or w.start_ms >= since_ok)
            if usable and key in marks and marks[key] is None:
                break
            cursor = max(cursor, w.end_ms)
            if usable:
                values = {
                    "chars": 0 if marks.get(key) else _nonspace_len(w.word),
                    "speak_ms": max(0, w.end_ms - w.start_ms),
                }
                # 앞 단어도 STT 를 믿을 수 있던 때의 것일 때만 멈춤으로 센다
                if prev is not None and (since_ok is None or prev.start_ms >= since_ok):
                    gap = w.start_ms - prev.end_ms
                    if gap > cfg.pause_min_ms:
                        values |= {"pause_count": 1, "pause_ms": gap}
                items.append(TallyItem(t_ms=w.start_ms, values=clean(values)))
        prev = w
    return items, cursor


def _time_tally(inp: PaceInput, t_ms: int, *, counted_ok: bool) -> list[TallyItem]:
    """새 시간 [since_ms, t_ms) 의 전체 시간과 STT 가 ok 였던 시간. 조각 하나로 낸다."""
    since = inp.since_ms
    if t_ms <= since:
        return []
    ok_from = max(since, inp.stt_ok_since_ms or 0)
    ok_ms = max(0, t_ms - ok_from) if counted_ok else 0
    return [TallyItem(t_ms=since, values=clean({"ok_ms": ok_ms, "total_ms": t_ms - since}))]
