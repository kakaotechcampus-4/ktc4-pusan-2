import hashlib
import json
import unicodedata
from typing import Any

from .config import DEFAULT, PaceConfig
from .schemas import (
    FillerMark,
    IssueCriteria,
    JudgmentIssue,
    JudgmentResult,
    PaceInput,
    PaceMetrics,
    PaceSummary,
    SpeedSummary,
    Tally,
    TallyItem,
    Word,
)
from .version import FEATURE_VERSION

TICK_MS = 1_000


# --------------------------------------------------------------------------
# 진입 함수 (pace.md 4-2)
# --------------------------------------------------------------------------


def judge(
    inputs: PaceInput | dict[str, Any], t_ms: int, config: PaceConfig = DEFAULT
) -> list[JudgmentResult]:
    """t_ms 시점의 1초 판정. 결과는 늘 하나(영역 SPEED)다."""
    inp = inputs if isinstance(inputs, PaceInput) else PaceInput.model_validate(inputs)
    cfg = config
    stt_ok = inp.stt_status == "ok"
    common: dict[str, Any] = {
        "t_ms": t_ms,
        "counted_until_ms": max(t_ms, inp.since_ms),
        "criteria_version": _criteria_version(cfg),
    }

    if inp.fillers is None:
        # 군더더기 판정 실패: 군더더기가 글자 수에 섞이지 않게 새 단어를 세지 않고 단어 커서도 둔다
        return [
            JudgmentResult(
                **common,
                words_counted_until_ms=inp.words_since_ms,
                measurable=False,
                state="UNKNOWN",
                metrics=PaceMetrics(cpm=None, cpm_short=None, speak_ms=None),
                tally=_time_tally(inp, t_ms, counted_ok=False),
            )
        ]

    marks = {(f.start_ms, f.end_ms): f.is_filler for f in inp.fillers if f.is_filler is not False}
    # words는 정렬돼 온다는 보장이 없으므로 정렬
    words = sorted(inp.words, key=lambda w: (w.start_ms, w.end_ms))

    word_items, cursor = _word_tally(words, marks, inp, stt_ok, cfg)

    cpm = cpm_short = speak_ms = None
    window_ms = min(cfg.window_ms, max(0, t_ms - inp.stt_ok_since_ms))
    if stt_ok:
        valid = _valid(words)
        cpm, speak_ms = _window_cpm(
            valid,
            inp.fillers,
            t_ms,
            window_ms,
            inp.stt_ok_since_ms,
            cfg.min_speak_ms,
            cfg.min_words,
        )
        cpm_short, _ = _window_cpm(
            valid,
            inp.fillers,
            t_ms,
            min(cfg.short_window_ms, window_ms),
            inp.stt_ok_since_ms,
            cfg.short_min_speak_ms,
            cfg.short_min_words,
        )

    # 최종 판정
    measurable = stt_ok and cpm is not None
    issues: list[JudgmentIssue] = []
    if not measurable:
        state = "UNKNOWN"
    elif cpm > cfg.fast_cpm:
        state = "FAST"
        issues.append(
            _issue("PACE_FAST", cpm, cfg.fast_cpm, cfg.fast_cpm_bad, speak_ms, window_ms, cfg)
        )
    elif cpm < cfg.slow_cpm:
        state = "SLOW"
        issues.append(
            _issue("PACE_SLOW", cpm, cfg.slow_cpm, cfg.slow_cpm_bad, speak_ms, window_ms, cfg)
        )
    else:
        state = "NORMAL"

    return [
        JudgmentResult(
            **common,
            words_counted_until_ms=cursor,
            measurable=measurable,
            state=state,
            metrics=PaceMetrics(cpm=cpm, cpm_short=cpm_short, speak_ms=speak_ms),
            tally=[*word_items, *_time_tally(inp, t_ms, counted_ok=stt_ok)],
            issues=issues,
        )
    ]


def summarize(tally: Tally, config: PaceConfig = DEFAULT) -> dict[str, dict[str, Any]]:
    """장 · Take 의 tally 합계 → {"SPEED": {cpm, pause_count, measured_ratio}}."""
    s = tally.get("SPEED", {})
    speak_ms = s.get("speak_ms", 0)
    total_ms = s.get("total_ms", 0)
    return PaceSummary(
        SPEED=SpeedSummary(
            cpm=round(s.get("chars", 0) / speak_ms * 60_000, 1) if speak_ms > 0 else None,
            pause_count=int(s.get("pause_count", 0)),
            measured_ratio=round(s.get("ok_ms", 0) / total_ms, 4) if total_ms > 0 else 0.0,
        )
    ).model_dump()


def criteria(config: PaceConfig = DEFAULT) -> dict[str, IssueCriteria]:
    """PACE_FAST · PACE_SLOW 의 판정 기준."""
    return {
        "PACE_FAST": IssueCriteria(
            direction="HIGHER_IS_WORSE",
            threshold=config.fast_cpm,
            bad=config.fast_cpm_bad,
            onset_lag_ms=config.onset_lag_ms,
            offset_lag_ms=config.offset_lag_ms,
        ),
        "PACE_SLOW": IssueCriteria(
            direction="LOWER_IS_WORSE",
            threshold=config.slow_cpm,
            bad=config.slow_cpm_bad,
            onset_lag_ms=config.onset_lag_ms,
            offset_lag_ms=config.offset_lag_ms,
        ),
    }


# --------------------------------------------------------------------------
# CPM 계산
# --------------------------------------------------------------------------


def _valid(words: list[Word]) -> list[Word]:
    """시각이 거꾸로이거나 앞 단어의 끝보다 일찍 시작하는(겹친) 단어를 뺀다.

    words 는 start_ms 순이어야 한다.
    """
    out: list[Word] = []
    prev_end = None
    for w in words:
        if w.end_ms >= w.start_ms and (prev_end is None or w.start_ms >= prev_end):
            out.append(w)
        prev_end = w.end_ms if prev_end is None else max(prev_end, w.end_ms)
    return out


def _window_cpm(
    words: list[Word],
    fillers: list[FillerMark],
    t_ms: int,
    window_ms: int,
    ok_since_ms: int,
    min_speak_ms: int,
    min_words: int,
) -> tuple[float | None, int]:
    """창 [t_ms − window_ms, t_ms] 안 단어의 (cpm, 발화 시간). 기준 미만이면 cpm 은 None."""
    lo = max(t_ms - window_ms, ok_since_ms)
    in_window = [w for w in words if w.start_ms >= lo and w.end_ms <= t_ms]
    cpm, speak_ms, n_words = _compute_cpm(in_window, fillers)
    if cpm is None or speak_ms < min_speak_ms or n_words < min_words:
        return None, speak_ms
    return round(cpm, 1), speak_ms


def _compute_cpm(words: list[Word], fillers: list[FillerMark]) -> tuple[float | None, int, int]:
    """(cpm, 발화 시간, 단어 수). cpm = 군더더기가 아닌 단어의 글자 수 ÷ 그 단어들의 발화 시간(분).

    군더더기(is_filler True) · 보류(None) 단어는 글자 수 · 발화 시간 모두 뺀다(단어와는
    (start_ms, end_ms) 로 맞춘다). 시각이 거꾸로인 단어(end_ms < start_ms)도 뺀다.
    발화 시간이 0 이면 cpm 은 None.
    """
    excluded = {(f.start_ms, f.end_ms) for f in fillers if f.is_filler is not False}
    chosen = [w for w in words if (w.start_ms, w.end_ms) not in excluded and w.end_ms >= w.start_ms]
    chars = sum(_count_chars(w.word) for w in chosen)
    speak_ms = sum(w.end_ms - w.start_ms for w in chosen)
    cpm = chars / speak_ms * 60_000 if speak_ms > 0 else None
    return cpm, speak_ms, len(chosen)


def _count_chars(text: str) -> int:
    """공백과 문장부호(유니코드 P 범주)를 뺀 코드 포인트 수.

    대본 파싱 script_chars 와 같은 정의다.
    """
    return sum(
        1 for ch in text if not ch.isspace() and not unicodedata.category(ch).startswith("P")
    )


# --------------------------------------------------------------------------
# tally (새 단어 몫 · 1초 몫)
# --------------------------------------------------------------------------


def _word_tally(
    words: list[Word],
    marks: dict[tuple[int, int], bool | None],
    inp: PaceInput,
    stt_ok: bool,
    cfg: PaceConfig,
) -> tuple[list[TallyItem], int]:
    """새 단어(end_ms > 단어 커서)의 몫과 새 단어 커서. 보류 단어 앞에서 멈춘다."""
    ok_since = inp.stt_ok_since_ms
    cursor = inp.words_since_ms
    items: list[TallyItem] = []
    prev: Word | None = None
    valid_ids = {id(w) for w in _valid(words)}
    for w in words:
        if w.end_ms <= cursor:
            # 이미 센 단어. 다음 단어의 멈춤 간격을 재는 앞 단어로만 쓴다
            # (시각이 거꾸로이거나 겹친 단어는 건너뛴다)
            if id(w) in valid_ids:
                prev = w
            continue
        if w.end_ms < w.start_ms or w.start_ms < cursor:
            # 시각이 거꾸로이거나 겹친 단어: 계산에서 빼고 커서만 넘긴다
            cursor = w.end_ms
            continue
        usable = stt_ok and w.start_ms >= ok_since
        key = (w.start_ms, w.end_ms)
        if usable and key in marks and marks[key] is None:
            break
        cursor = w.end_ms
        if usable:
            is_filler = marks.get(key) is True
            values = {
                "chars": 0 if is_filler else _count_chars(w.word),
                "speak_ms": 0 if is_filler else w.end_ms - w.start_ms,
            }
            if prev is not None and prev.start_ms >= ok_since:
                gap = w.start_ms - prev.end_ms
                if gap > cfg.pause_min_ms:
                    values |= {"pause_count": 1, "pause_ms": gap}
            # 값이 0인 키는 빼되, 항목은 단어마다 하나 남긴다 (pace.md 5-3)
            values = {k: v for k, v in values.items() if v}
            items.append(TallyItem(t_ms=w.start_ms, values=values))
        prev = w
    return items, cursor


def _time_tally(inp: PaceInput, t_ms: int, *, counted_ok: bool) -> list[TallyItem]:
    """since_ms 부터 t_ms 까지 1초마다 {ok_ms, total_ms}. ok_ms 는 stt_ok_since_ms 이후 부분."""
    items: list[TallyItem] = []
    start = inp.since_ms
    while start < t_ms:
        end = min(start + TICK_MS, t_ms)
        ok_ms = max(0, end - max(start, inp.stt_ok_since_ms)) if counted_ok else 0
        values = {k: v for k, v in {"ok_ms": ok_ms, "total_ms": end - start}.items() if v}
        items.append(TallyItem(t_ms=start, values=values))
        start = end
    return items


# --------------------------------------------------------------------------
# 문제 · 버전
# --------------------------------------------------------------------------


def _issue(
    issue_type: str,
    cpm: float,
    threshold: float,
    bad: float,
    speak_ms: int,
    window_ms: int,
    cfg: PaceConfig,
) -> JudgmentIssue:
    base = cfg.confidence_base
    return JudgmentIssue(
        issue_type=issue_type,
        severity=_ramp(cpm, threshold, bad),
        confidence=round(base + (1 - base) * min(1.0, speak_ms / cfg.confidence_full_speak_ms), 4),
        persistence_sec=0.0,
        threshold=threshold,
        bad=bad,
        evidence={"cpm": cpm, "window_ms": window_ms},
    )


def _ramp(value: float, threshold: float, bad: float) -> float:
    """기준값에서 0.5, 아주 나쁨 값에서 1.0, 그 사이 직선, 1.0 에서 자른다."""
    if bad == threshold:
        return 1.0
    frac = (value - threshold) / (bad - threshold)
    return round(0.5 + 0.5 * min(1.0, max(0.0, frac)), 4)


def _criteria_version(config: PaceConfig = DEFAULT) -> str:
    """pace-<버전>+<기준값 해시 12자리>. 기준값을 바꾸면 해시가 바뀐다."""
    blob = json.dumps(config.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return f"{FEATURE_VERSION}+{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:12]}"
