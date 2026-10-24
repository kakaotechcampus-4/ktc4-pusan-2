"""군더더기 판정 코어 — 단어마다 군더더기인지 층과 함께 가른다.

하는 일: STT 확정 단어를 사전의 층(T1 · T2 · T3)과 단어 사이 간격 · 문장 끝 신호로 판정한다.
뒤 간격을 봐야 하는 단어는 다음 단어가 올 때까지 보류(`is_filler: None`)로 둔다 (filler.md 6-1).
누가 부르나: 코치(`/coach/evaluate` · `/coach/finalize` · `replay`), 테스트, 예시 도구.
입력과 출력: `judge(inputs, t_ms, config)` 는 `FillerInput`(또는 같은 모양의 dict)을 받아
`JudgmentResult` 하나를 담은 목록을 낸다. 시각은 모두 Take 시작 기준 ms 다.
처리 순서 (filler.md 6-1):
  1. STT 가 ok 가 아니면 단어를 판정하지 않는다.
  2. 창 고르기: `stt_ok_since_ms` 앞 · 시각이 거꾸로 · 겹친 단어를 뺀다.
  3. 단어마다 사전에서 층을 찾고 앞 · 뒤 간격을 잰다.
  4. 층별 규칙으로 군더더기 · 아님 · 보류를 정한다.
  5. 새 몫 세기: 판정이 정해진 새 군더더기를 `tally` 에 더하고 단어 커서를 보류 단어 앞까지 옮긴다.
  6. 시간 몫: `since_ms` 부터 `t_ms` 까지 1초마다 `total_ms` · `elapsed_ms` 를 낸다.
  7. 지표 · 상태 · 문제: 최근 창의 군더더기 수와 분당 수, 기준 이상이면 `FILLER_FREQUENT`.
`summarize` 는 장 · Take 의 `tally` 합계를 지표로, `criteria` 는 `FILLER_FREQUENT` 의 기준을,
`keyterms` 는 STT keyterm 단어 목록을 낸다(filler.md 6-4).
고칠 때 주의: 판정 규칙이나 이유 문자열의 뜻이 바뀌면 `version.py` 의 기능 버전을 올리고
filler.md 6-1 과 `examples/` 를 함께 고친다. 파일 · 환경변수 · 시계 · 난수 · print 를 쓰지 않는다
(같은 입력에 같은 결과, 서비스로 폴더째 옮겨도 고칠 곳이 없게 — filler.md 4-3).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

from .config import DEFAULT, FillerConfig, normalize
from .schemas import (
    FillerInput,
    FillerMetrics,
    FillerSummary,
    IssueCriteria,
    JudgmentIssue,
    JudgmentResult,
    Summary,
    Tally,
    TallyItem,
    Tier,
    Word,
    WordJudgment,
)
from .version import FEATURE_VERSION

TICK_MS = 1_000

__all__ = ["criteria", "criteria_version", "judge", "keyterms", "normalize", "summarize"]

# 판정 이유 (사람이 읽는다. 바꾸면 기능 버전을 올린다)
REASON_UNKNOWN_WORD = "사전에 없음"
REASON_T1 = "채움말"
REASON_GAP_BEFORE = "앞 간격이 기준 이상인 단독 단어"
REASON_GAP_AFTER = "뒤 간격이 기준 이상인 단독 단어"
REASON_GAP_BOTH = "앞뒤 간격이 기준 이상인 단독 단어"
REASON_REPEAT = "같은 단어가 바로 반복"
REASON_SENTENCE_START = "문장 첫 단어이고 뒤 간격이 기준 이상"
REASON_SHORT_GAP = "간격이 기준보다 짧음"
REASON_PENDING = "뒤 간격을 아직 알 수 없음"


# --------------------------------------------------------------------------
# 진입 함수 (filler.md 4-2)
# --------------------------------------------------------------------------


def judge(
    inputs: FillerInput | dict[str, Any], t_ms: int, config: FillerConfig = DEFAULT
) -> list[JudgmentResult]:
    """t_ms 시점의 1초 판정. 결과는 늘 하나(영역 FILLER)다.

    인자: inputs - 코치가 만든 입력(모델 또는 dict), t_ms - 판정 시각(ms, inputs.t_ms 와 같아야 함),
    config - 사전과 기준값. 반환: `JudgmentResult` 하나를 담은 목록.
    실패할 때: inputs 가 계약에 맞지 않으면 pydantic `ValidationError`, `t_ms` 가
    inputs.t_ms 와 다르면 `ValueError`. 두 경우 모두 코치가 그 영역만 잴 수 없음으로 둔다
    (filler.md 7절).
    """
    inp = inputs if isinstance(inputs, FillerInput) else FillerInput.model_validate(inputs)
    if inp.t_ms != t_ms:
        raise ValueError(f"inputs.t_ms({inp.t_ms}) 가 t_ms({t_ms}) 와 다르다")
    stt_ok = inp.stt_status == "ok"
    common: dict[str, Any] = {
        "t_ms": t_ms,
        "counted_until_ms": max(t_ms, inp.since_ms),
        "criteria_version": criteria_version(config),
    }

    if not stt_ok:
        # 끊긴 동안의 듬성한 단어로 잘못 세지 않게 단어를 판정하지 않고 시간만 센다 (filler.md 7절)
        return [
            JudgmentResult(
                **common,
                words_counted_until_ms=inp.words_since_ms,
                measurable=False,
                state="UNKNOWN",
                words=[],
                metrics=FillerMetrics(recent_filler_count=None, filler_per_min=None),
                tally=_time_tally(inp, t_ms, counted_ok=False),
                issues=[],
            )
        ]

    entries = _window(inp)
    judged = _judge_words(entries, inp, config)
    word_items, cursor = _word_tally(entries, judged, inp)
    recent, per_min = _recent_metrics(judged, inp, t_ms, config)
    state = "HIGH" if recent >= config.frequent_count else "NORMAL"
    issues = [_frequent_issue(judged, recent, per_min, t_ms, config)] if state == "HIGH" else []
    return [
        JudgmentResult(
            **common,
            words_counted_until_ms=cursor,
            measurable=True,
            state=state,
            words=[j.out for j in judged],
            metrics=FillerMetrics(recent_filler_count=recent, filler_per_min=per_min),
            tally=[*word_items, *_time_tally(inp, t_ms, counted_ok=True)],
            issues=issues,
        )
    ]


def summarize(tally: Tally, config: FillerConfig = DEFAULT) -> dict[str, dict[str, Any]]:
    """장 · Take 의 tally 합계 → {"FILLER": {filler_count, filler_per_min, by_tier, by_word,
    measured_ratio}} (filler.md 5-3 · 6-4).

    인자: tally - 코치가 더한 합계(영역 → 이름 → 합. `filler_count` · `filler_t1~t3` ·
    `filler_word:<단어>` · `elapsed_ms` · `total_ms`, 빠진 키는 0). config - 쓰지 않지만 다른 진입
    함수와 같은 모양으로 받는다. 반환: `FillerSummary` 를 dict 로 만든 것.
    분모가 0 이면 `filler_per_min` 은 None 이고, `measured_ratio` 만 0 이다(architecture.md 7절).
    `by_word` 는 센 단어만, 횟수 많은 순 · 같으면 단어 순이다.
    """
    f = tally.get("FILLER", {})
    count = int(f.get("filler_count", 0))
    elapsed = f.get("elapsed_ms", 0)
    total_ms = f.get("total_ms", 0)
    prefix = "filler_word:"
    words = {k[len(prefix) :]: int(v) for k, v in f.items() if k.startswith(prefix)}
    summary = FillerSummary(
        filler_count=count,
        filler_per_min=round(count / elapsed * 60_000, 2) if elapsed > 0 else None,
        by_tier={t: int(f.get(f"filler_{t.lower()}", 0)) for t in ("T1", "T2", "T3")},
        by_word=dict(sorted(words.items(), key=lambda kv: (-kv[1], kv[0]))),
        measured_ratio=round(elapsed / total_ms, 4) if total_ms > 0 else 0.0,
    )
    return Summary(FILLER=summary).model_dump()


def criteria(config: FillerConfig = DEFAULT) -> dict[str, IssueCriteria]:
    """`FILLER_FREQUENT` 의 판정 기준 (filler.md 5-3). Take 결과의 `criteria` 에 쓴다.

    반환: {"FILLER_FREQUENT": IssueCriteria}. threshold · bad 는 최근 창 군더더기 수, lag 는 ms.
    """
    return {
        "FILLER_FREQUENT": IssueCriteria(
            metric="recent_filler_count",
            direction="HIGHER_IS_WORSE",
            threshold=float(config.frequent_count),
            bad=float(config.frequent_bad),
            onset_lag_ms=config.onset_lag_ms,
            offset_lag_ms=config.offset_lag_ms,
        )
    }


def keyterms(config: FillerConfig = DEFAULT) -> list[str]:
    """STT keyterm 으로 넘기는 단어 목록 (filler.md 5-2). 순서는 BE 목록과 같다.

    BE 는 이 목록만 가져간다. 단어가 바뀌면 BE 의 목록도 바꿔야 하므로 `criteria_version` 과
    함께 파일로 만든다(filler.md 9절).
    """
    return list(config.keyterms)


# --------------------------------------------------------------------------
# 창 고르기 (filler.md 6-1 1)
# --------------------------------------------------------------------------


def _sorted_window(inp: FillerInput) -> list[Word]:
    """`stt_ok_since_ms` 앞 단어를 빼고 (start_ms, end_ms) 순으로 정렬한다.

    STT 가 불안정했던 동안의 단어는 출력에도 커서에도 쓰지 않는다. 입력이 정렬돼 온다는
    보장이 없으므로 정렬한다.
    """
    kept = [w for w in inp.words if w.start_ms >= inp.stt_ok_since_ms]
    return sorted(kept, key=lambda w: (w.start_ms, w.end_ms))


def _is_accepted(w: Word, last_end: int | None, words_since_ms: int) -> bool:
    """판정할 수 있는 단어인가. 아니면 `words` 에서 빠지고 커서만 넘어간다 (filler.md 7절)."""
    if w.end_ms < w.start_ms:  # 시각이 거꾸로
        return False
    if last_end is not None and w.start_ms < last_end:  # 앞 단어와 겹침
        return False
    # 새 단어인데 이미 센 구간에 걸쳐 시작하면, 센 단어와 겹친 것이라 믿지 않는다
    return not (w.end_ms > words_since_ms and w.start_ms < words_since_ms)


def _window(inp: FillerInput) -> list[tuple[Word, bool]]:
    """창 안 단어를 (start_ms, end_ms) 순으로 돌려준다. 짝의 뒤는 받아들였는지(판정할 단어인지).

    뺀 단어는 `words` 출력에 넣지 않지만, 커서 걷기(`_word_tally`)에서는 "커서만 넘기는 단어"로
    쓰므로 원래 순서와 함께 기억한다.
    """
    entries: list[tuple[Word, bool]] = []
    last_end: int | None = None
    for w in _sorted_window(inp):
        ok = _is_accepted(w, last_end, inp.words_since_ms)
        entries.append((w, ok))
        if ok:
            last_end = w.end_ms
    return entries


# --------------------------------------------------------------------------
# 단어별 판정 (filler.md 6-1 2~4)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Judged:
    out: WordJudgment
    #: 정규화한 단어 (filler_word:<단어> 키에 쓴다)
    key: str


def _tier_of(key: str, config: FillerConfig) -> Tier | None:
    if key in config.t1_words:
        return "T1"
    if key in config.t2_words:
        return "T2"
    if key in config.t3_words:
        return "T3"
    return None


def _judge_words(
    entries: list[tuple[Word, bool]], inp: FillerInput, config: FillerConfig
) -> list[_Judged]:
    """받아들인 단어마다 층 · 군더더기 여부 · 이유를 정한다."""
    ws = [w for w, ok in entries if ok]
    ends = inp.utterance_ends
    out: list[_Judged] = []
    for i, w in enumerate(ws):
        key = normalize(w.word)
        tier = _tier_of(key, config)
        if tier is None:
            is_filler, reason = False, REASON_UNKNOWN_WORD
        elif tier == "T1":
            # 채움말은 반복 · 간격과 관계없이 늘 군더더기다
            is_filler, reason = True, REASON_T1
        else:
            before, sentence_start = _before(ws, i, ends, inp.stt_ok_since_ms, config)
            after, repeat = _after(ws, i, key, ends, inp.take_end_ms)
            is_filler, reason = _judge_gap(tier, before, sentence_start, after, repeat, config)
        out.append(
            _Judged(
                WordJudgment(
                    start_ms=w.start_ms,
                    end_ms=w.end_ms,
                    word=w.word,
                    tier=tier,
                    is_filler=is_filler,
                    reason=reason,
                ),
                key,
            )
        )
    return out


def _before(
    ws: list[Word], i: int, ends: list[int], ok_since_ms: int, config: FillerConfig
) -> tuple[int | None, bool]:
    """앞 간격(ms, 모르면 None)과 문장 첫 단어 여부.

    앞 단어가 있으면 두 단어 사이 간격이다. 창의 첫 단어는 앞 단어가 없으므로, 그 단어 시작 전이면서
    `stt_ok_since_ms` 이상인 문장 끝 신호 중 가장 늦은 것을 앞 단어의 끝으로 본다.
    그런 신호가 없으면 앞 간격을 모르는 것으로 두고 앞 간격 조건은 맞지 않은 것으로 친다
    (놓침보다 오탐을 피한다).
    문장 첫 단어는 직전 문장 끝 신호 뒤의 첫 단어이거나 앞 간격이 `sentence_gap_ms` 이상인 단어다.
    """
    w = ws[i]
    if i > 0:
        prev_end = ws[i - 1].end_ms
        before: int | None = w.start_ms - prev_end
        has_signal = any(prev_end <= u <= w.start_ms for u in ends)
    else:
        near = [u for u in ends if ok_since_ms <= u <= w.start_ms]
        before = w.start_ms - max(near) if near else None
        has_signal = bool(near)
    sentence_start = has_signal or (before is not None and before >= config.sentence_gap_ms)
    return before, sentence_start


def _after(
    ws: list[Word], i: int, key: str, ends: list[int], take_end_ms: int | None
) -> tuple[float | None, bool]:
    """뒤 간격(ms, 아직 모르면 None)과 바로 반복 여부.

    다음 단어가 있으면 그 사이 간격이고, 같은 단어(정규화 뒤)가 문장 끝 신호 없이 이어지면 반복이다.
    마지막 단어는 자기 끝 이후의 문장 끝 신호, 없으면 `take_end_ms` 로 잰다. 신호 시각이 단어 끝과
    같으면 Deepgram UtteranceEnd(그 단어 뒤로 정해진 시간 넘게 말이 없었음)라 뒤 간격을 무한대,
    곧 기준 이상으로 본다 (filler.md 6-1 4).
    """
    w = ws[i]
    if i + 1 < len(ws):
        nxt = ws[i + 1]
        split = any(w.end_ms <= u <= nxt.start_ms for u in ends)
        return nxt.start_ms - w.end_ms, normalize(nxt.word) == key and not split
    later = [u for u in ends if u >= w.end_ms]
    if later:
        u = min(later)
        return (math.inf if u == w.end_ms else u - w.end_ms), False
    if take_end_ms is not None:
        return max(0, take_end_ms - w.end_ms), False
    return None, False


def _judge_gap(
    tier: Tier,
    before: int | None,
    sentence_start: bool,
    after: float | None,
    repeat: bool,
    config: FillerConfig,
) -> tuple[bool | None, str]:
    """T2 · T3 단어의 간격 규칙 (filler.md 6-1 3). 뒤 정보를 모르면 보류한다.

    보류는 앞 간격이 커도 한다(filler.md 5-5 경계 예시): 뒤 간격이 필요한 단어는 뒤를 모르고
    정하면 틀릴 수 있다.
    """
    if after is None:
        return None, REASON_PENDING
    gap = config.gap_ms
    before_ok = before is not None and before >= gap
    after_ok = after >= gap
    if repeat:
        return True, REASON_REPEAT
    if before_ok and after_ok:
        return True, REASON_GAP_BOTH
    if tier == "T2":
        if before_ok:
            return True, REASON_GAP_BEFORE
        if after_ok:
            return True, REASON_GAP_AFTER
        # 기능 문서 규칙(filler.md 6-1 3)을 그대로 옮긴 것이다. 뒤 간격 기준이 4번과 같아
        # 늘 4번에 먼저 걸려 결과를 바꾸지 않는다. 규칙 문장을 바꾸기 전까지 남겨 둔다
        if sentence_start and after_ok:
            return True, REASON_SENTENCE_START
    return False, REASON_SHORT_GAP


# --------------------------------------------------------------------------
# 새 몫 세기 · 단어 커서 · 시간 몫 (filler.md 6-1 5 · 7)
# --------------------------------------------------------------------------


def _word_tally(
    entries: list[tuple[Word, bool]], judged: list[_Judged], inp: FillerInput
) -> tuple[list[TallyItem], int]:
    """판정이 정해진 새 군더더기를 세고 단어 커서를 옮긴다.

    단어를 (start_ms, end_ms) 순으로 걸으며 `end_ms` 가 `words_since_ms` 보다 큰 새 단어만 본다.
    뺀 단어는 커서만 넘기고, 보류 단어를 만나면 멈춘다(뒤 단어는 보지 않는다). 멈출 때 커서는
    받은 값 · 판정이 정해진 단어의 end 최댓값 · 보류 단어 시작 이전에 끝난 뺀 단어의 end 최댓값
    이다(보류 단어보다 늦게 끝나는 뺀 단어는 넣지 않는다). 커서는 `max` 로만 옮겨서 시각이 이른
    단어가 와도 받은 값보다 작아지지 않는다. 한 번 센 몫은 바꾸지 않는다.
    몫의 `t_ms` 는 그 단어의 `start_ms` 다(코치가 말한 때의 장에 넣도록).
    """
    received = inp.words_since_ms
    decided_end = received  # 받아들여 판정이 정해진 단어의 end 최댓값
    dropped_ends: list[int] = []  # 뺀 단어의 end
    items: list[TallyItem] = []
    results = iter(judged)
    pending_start: int | None = None
    for w, ok in entries:
        j = next(results) if ok else None
        if w.end_ms <= received:
            continue
        if j is None:
            dropped_ends.append(w.end_ms)
            continue
        if j.out.is_filler is None:
            pending_start = w.start_ms
            break
        decided_end = max(decided_end, w.end_ms)
        if j.out.is_filler and j.out.tier is not None:
            items.append(
                TallyItem(
                    t_ms=w.start_ms,
                    values={
                        "filler_count": 1,
                        f"filler_{j.out.tier.lower()}": 1,
                        f"filler_word:{j.key}": 1,
                    },
                )
            )
    # 보류 단어에서 멈췄으면, 그 단어 시작 뒤에 끝나는 뺀 단어(겹친 단어 등)는 커서에 넣지 않는다:
    # 커서보다 끝이 이른 보류 단어가 남으면 다음 요청이 그 단어를 영영 세지 못한다
    # (architecture.md 7절). 멈추지 않았으면 뺀 단어도 모두 넘긴다.
    skipped = [e for e in dropped_ends if pending_start is None or e <= pending_start]
    cursor = max([decided_end, *skipped])
    return items, cursor


def _time_tally(inp: FillerInput, t_ms: int, counted_ok: bool) -> list[TallyItem]:
    """`since_ms` 부터 `t_ms` 까지 1초마다 시간 몫을 낸다.

    `total_ms` 는 그 1초(마지막 조각은 1000 미만일 수 있음), `elapsed_ms` 는 그중 STT 가 ok 였던
    부분(`stt_ok_since_ms` 이후)이다. 요청이 빠져 새 시간이 여러 초여도 1초마다 나눈다.
    """
    items: list[TallyItem] = []
    s = inp.since_ms
    while s < t_ms:
        dur = min(TICK_MS, t_ms - s)
        values: dict[str, int] = {}
        if counted_ok:
            elapsed = max(0, (s + dur) - max(s, inp.stt_ok_since_ms))
            if elapsed > 0:
                values["elapsed_ms"] = elapsed
        values["total_ms"] = dur
        items.append(TallyItem(t_ms=s, values=values))
        s += dur
    return items


# --------------------------------------------------------------------------
# 지표 · 상태 · 문제 (filler.md 5-4, 6-1 6, 14절)
# --------------------------------------------------------------------------


def _recent_fillers(judged: list[_Judged], t_ms: int, config: FillerConfig) -> list[WordJudgment]:
    """최근 창 안에 시작하고 끝난, 판정이 정해진 군더더기 단어 (filler.md 14절)."""
    lo = t_ms - config.window_ms
    return [
        j.out
        for j in judged
        if j.out.is_filler is True and j.out.start_ms >= lo and j.out.end_ms <= t_ms
    ]


def _recent_metrics(
    judged: list[_Judged], inp: FillerInput, t_ms: int, config: FillerConfig
) -> tuple[int, float | None]:
    """최근 창의 군더더기 수와 분당 수.

    분당 수는 창 안에서 STT 가 정상이던 시간으로 나눈다(Take 초반이나 STT 회복 직후에는 창보다
    짧다). 그 시간이 `min_rate_ms` 보다 짧으면 None 이다.
    """
    recent = len(_recent_fillers(judged, t_ms, config))
    observed = t_ms - max(t_ms - config.window_ms, 0, inp.stt_ok_since_ms)
    per_min = round(recent * 60_000 / observed, 2) if observed >= config.min_rate_ms else None
    return recent, per_min


def _frequent_issue(
    judged: list[_Judged], recent: int, per_min: float | None, t_ms: int, config: FillerConfig
) -> JudgmentIssue:
    """FILLER_FREQUENT 하나. 기준값에서 severity 0.5, 아주 나쁨 값에서 1.0 이고 그 사이는 직선이다.

    `persistence_sec` 는 창 안 군더더기의 `end_ms` 를 이른 순으로 놓고 `frequent_count` 번째 단어의
    `end_ms`(조건이 성립한 시각)부터 지금까지다. 창 안 단어만으로 본, 조건이 이어진 시간의
    하한이다(그 앞에 이미 성립했다가 창 밖으로 나간 단어가 있을 수 있다).
    """
    span = config.frequent_bad - config.frequent_count
    ratio = 1.0 if span <= 0 else min(1.0, max(0.0, (recent - config.frequent_count) / span))
    ends = sorted(w.end_ms for w in _recent_fillers(judged, t_ms, config))
    since = ends[config.frequent_count - 1]
    return JudgmentIssue(
        issue_type="FILLER_FREQUENT",
        severity=0.5 + 0.5 * ratio,
        confidence=1.0,
        persistence_sec=(t_ms - since) / 1000,
        threshold=float(config.frequent_count),
        bad=float(config.frequent_bad),
        evidence={"recent_filler_count": recent, "filler_per_min": per_min},
    )


# --------------------------------------------------------------------------
# 기준 버전
# --------------------------------------------------------------------------


def criteria_version(config: FillerConfig) -> str:
    """`filler-<버전>+<config 해시 12자리>` (filler.md 9절). 사전 · 기준값을 바꾸면 해시가 바뀐다.

    해시는 config 를 키 순서로 정렬한 JSON 의 sha256 앞 12자리다
    (코치 `coach/config.py` 의 식과 같다).
    """
    blob = json.dumps(config.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return f"{FEATURE_VERSION}+{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:12]}"
