"""코치 요청의 창 · 지금 장 · replay 입력. 순수 함수다 (DB 도 네트워크도 모른다).

**창을 고르는 규칙은 AI 의 replay 와 같다** (`ai/research/coach-agent/src/coach/recovery.py` 의
`inputs_at`). 발표 중 BE 가 보낸 창과 Take 뒤 원자료로 다시 만든 창이 같아야, 응답이 빠져 replay
로 만든 Take 결과가 정상 진행과 같다(coach-agent.md 1-2). 지금 장도 발표 중과 replay 가 같은 장 변경
기록(`take_slide_events`, WS `slide`)으로 정한다 (`slide_at`).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pitch_coach_backend.module.take.dto import (
    MetricSecond,
    SlideEvent,
    TranscriptSegment,
)

# 발표 중 창 (architecture.md 2절). AI 의 judges.py 와 같은 값이다
GAZE_VOICE_WINDOW_MS = 30_000

WORDS_WINDOW_MS = 60_000


# ── 창 ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class FinalWord:
    """STT 확정 단어. `final_at_ms` 는 그 단어가 확정된 Take 시각(replay 에서 넣는 순서)."""

    word: str
    start_ms: int
    end_ms: int
    final_at_ms: int


def slide_changes(
    events: Iterable[SlideEvent], *, normalize_start: bool = True
) -> list[dict[str, int]]:
    """장 변경 기록 전체를 시각 순서로 둔다. 같은 시각은 먼저 온 것만.

    처음 알게 된 장은 `started_ms` 를 0 으로 싣는다 — 그 앞의 장을 모르므로 Take 시작부터 그 장으로
    본다. 이 보정은 replay (5-2)에만 쓰며 실시간 창은 `normalize_start=False` 로 실제 전환 시각을
    유지한다. FE 는 Take 시작에 1번 장을 0 으로 보내므로 보통은 이미 0 이다.
    """
    picked: dict[int, int] = {}
    for event in events:
        picked.setdefault(event.started_ms, event.slide_number)
    changes = [{"number": picked[t], "started_ms": t} for t in sorted(picked)]
    if changes and normalize_start:
        changes[0] = {**changes[0], "started_ms": 0}
    return changes


def slide_at(t_ms: int, changes: Sequence[Mapping[str, int]]) -> dict[str, int] | None:
    """`t_ms` 요청의 `inputs.slide` (architecture.md 1-3 2번). `slide_changes` 의 결과에서
    `started_ms` 가 `t_ms` 이하인 것 중 가장 늦은 것이다. 없으면 None — 코치가 마지막으로 알던 장을
    쓴다. `metrics.slide_number` 는 참고값이라 쓰지 않는다."""
    current: Mapping[str, int] | None = None
    for change in changes:
        if change["started_ms"] > t_ms:
            break
        current = change
    return dict(current) if current is not None else None


def window_inputs(
    t_ms: int,
    *,
    gaze_records: Iterable[Mapping[str, Any]],
    voice_records: Iterable[Mapping[str, Any]],
    words: Iterable[FinalWord],
    utterance_ends: Iterable[int],
    stt_status: str,
    slide: Mapping[str, int] | None,
) -> dict[str, Any]:
    """`t_ms` 요청의 `inputs` (5-2). 넘긴 것 중 창 안의 것만 고른다.

    - 시선 · 음량: 30초 안에 시작해 `t_ms` 까지 끝난 1초 기록. 같은 1초가 또 있으면 먼저 것
    - 단어: 60초 안에 끝났고 `t_ms` 까지 끝난 확정 단어. 넣을지는 끝난 시각 `end_ms` 로 정한다
    - 문장 끝: 60초 안의 것

    단어는 부르는 쪽이 그때까지 받은 것만 넘긴다(확정 시각으로 거르지 않는다). finalize 의 마지막
    창은 STT 를 닫은 뒤에 확정된 단어까지 넘기면 된다. Take 끝(`t_ms`)보다 늦게 끝난 것으로 찍힌
    단어는 마지막 창에서도 빠진다 — AI 의 replay 마지막 창과 예시 요청이 그렇게 고른다.
    """
    record_lo = t_ms - GAZE_VOICE_WINDOW_MS
    word_lo = t_ms - WORDS_WINDOW_MS
    return {
        "gaze_records": _records_between(gaze_records, record_lo, t_ms),
        "voice_records": _records_between(voice_records, record_lo, t_ms),
        "words": [
            {"word": w.word, "start_ms": w.start_ms, "end_ms": w.end_ms}
            for w in sorted(words, key=lambda w: (w.start_ms, w.end_ms))
            if word_lo < w.end_ms <= t_ms
        ],
        "utterance_ends": sorted({u for u in utterance_ends if word_lo <= u <= t_ms}),
        "stt_status": stt_status,
        "slide": dict(slide) if slide is not None else None,
    }


def _records_between(
    records: Iterable[Mapping[str, Any]], lo_ms: int, hi_ms: int
) -> list[dict[str, Any]]:
    """[lo_ms 에 시작, hi_ms 까지 끝남] 인 1초 기록을 시작 순으로. 같은 시작은 먼저 온 것만."""
    picked: dict[int, dict[str, Any]] = {}
    for record in records:
        start = record["t_ms"]
        if start in picked or start < lo_ms:
            continue
        if start + record.get("duration_ms", 1000) <= hi_ms:
            picked[start] = dict(record)
    return [picked[start] for start in sorted(picked)]


# ── replay ────────────────────────────────────────────────────────────
def replay_input(
    seconds: Iterable[MetricSecond],
    segments: Iterable[TranscriptSegment],
    utterance_ends: Iterable[int],
    slides: Iterable[SlideEvent],
    *,
    t_end_ms: int,
) -> dict[str, Any]:
    """Take 전체 원자료 `replay` (5-2 finalize). 발표 중 보낸 창이 이것에서 다시 나오게 만든다.

    `t_end_ms` 는 finalize 요청의 `t_ms`(Take 끝)다.

    - STT 상태 변경: 각 `metrics` 를 받았을 때의 상태가 바뀐 시각. 발표 중 요청도 그 시각의 상태를
      실었으므로 그 사이의 짧은 끊김은 양쪽 다 모른다. 처음 알게 된 상태는 Take 시작(0)부터로 둔다 —
      AI 는 이력보다 앞을 '연결 중'으로 보고 그동안의 단어를 세지 않는데, 발표 중 첫 요청은 그
      상태로 첫 1초의 단어를 셌다
    - 장 변경: 장 변경 기록 전체 (`slide_changes`, 발표 중 `slide_at` 과 같은 규칙)
    - 단어: 구간의 확정 시각을 구간 안 단어마다 붙인다(12-1). 확정 시각이 없는 옛 구간은 구간 끝을
      대신 쓴다 — 실제보다 일찍 들어가지만 순서는 그대로다. STT 를 닫은 뒤에 확정된 단어는 확정
      시각이 Take 끝보다 늦다. AI 는 마지막 창에 확정 시각이 Take 끝 이하인 단어만 넣으므로 Take
      끝으로 자른다 — 정상 finalize 의 마지막 창에 들어간 단어가 replay 에서 빠지지 않게(AI 예시도
      이렇게 만든다)
    """
    ordered = sorted(seconds, key=lambda s: s.t_ms)
    stt_status_changes: list[dict[str, Any]] = []
    for second in ordered:
        status = second.stt_status
        if status is None:
            continue
        if not stt_status_changes:
            stt_status_changes.append({"t_ms": 0, "status": status})
        elif stt_status_changes[-1]["status"] != status:
            stt_status_changes.append({"t_ms": second.t_ms, "status": status})

    words: list[dict[str, Any]] = []
    for segment in segments:
        final_at_ms = segment.final_at_ms if segment.final_at_ms is not None else segment.end_ms
        final_at_ms = min(final_at_ms, t_end_ms)
        words.extend(
            {
                "word": w["word"],
                "start_ms": w["start_ms"],
                "end_ms": w["end_ms"],
                "final_at_ms": final_at_ms,
            }
            for w in segment.words
        )
    words.sort(key=lambda w: (w["final_at_ms"], w["start_ms"], w["end_ms"]))

    return {
        "gaze_records": _first_per_start(s.gaze for s in ordered),
        "voice_records": _first_per_start(s.voice for s in ordered),
        "stt_status_changes": stt_status_changes,
        "words": words,
        "utterance_ends": sorted(set(utterance_ends)),
        "slides": slide_changes(slides),
    }


def _first_per_start(records: Iterable[Mapping[str, Any] | None]) -> list[dict[str, Any]]:
    picked: dict[int, dict[str, Any]] = {}
    for record in records:
        if record is not None and record["t_ms"] not in picked:
            picked[record["t_ms"]] = dict(record)
    return [picked[start] for start in sorted(picked)]
