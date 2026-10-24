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
    JudgmentResult,
    Tier,
    Word,
    WordJudgment,
)
from .version import FEATURE_VERSION

__all__ = ["judge", "normalize"]

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
        # 끊긴 동안의 듬성한 단어로 잘못 세지 않게 단어를 판정하지 않는다 (filler.md 7절)
        return [
            JudgmentResult(
                **common,
                words_counted_until_ms=inp.words_since_ms,
                measurable=False,
                state="UNKNOWN",
                words=[],
                metrics=FillerMetrics(recent_filler_count=None, filler_per_min=None),
                tally=[],
                issues=[],
            )
        ]

    accepted = _select_window(inp)
    judged = _judge_words(accepted, inp, config)
    return [
        JudgmentResult(
            **common,
            words_counted_until_ms=inp.words_since_ms,
            measurable=True,
            state="NORMAL",
            words=[j.out for j in judged],
            metrics=FillerMetrics(recent_filler_count=None, filler_per_min=None),
            tally=[],
            issues=[],
        )
    ]


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


def _select_window(inp: FillerInput) -> list[Word]:
    """판정할 단어(받아들인 단어)만 순서대로 고른다."""
    accepted: list[Word] = []
    last_end: int | None = None
    for w in _sorted_window(inp):
        if _is_accepted(w, last_end, inp.words_since_ms):
            accepted.append(w)
            last_end = w.end_ms
    return accepted


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


def _judge_words(ws: list[Word], inp: FillerInput, config: FillerConfig) -> list[_Judged]:
    """받아들인 단어마다 층 · 군더더기 여부 · 이유를 정한다."""
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
# 기준 버전
# --------------------------------------------------------------------------


def criteria_version(config: FillerConfig) -> str:
    """`filler-<버전>+<config 해시 12자리>` (filler.md 9절). 사전 · 기준값을 바꾸면 해시가 바뀐다.

    해시는 config 를 키 순서로 정렬한 JSON 의 sha256 앞 12자리다
    (코치 `coach/config.py` 의 식과 같다).
    """
    blob = json.dumps(config.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return f"{FEATURE_VERSION}+{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:12]}"
