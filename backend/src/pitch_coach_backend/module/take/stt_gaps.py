"""STT 끊김 구간 (`takes.stt_gap_ranges`)과 장별 끊김 시간.

Take 결과를 저장할 때 1초 기록의 STT 상태로 구간을 남기고 (`coach_service.save_take_result`),
대본 전달도 채점 요청이 장마다 그 구간과 겹친 시간을 싣는다 (`service/coverage.py`). AI 는 이
값으로 음성 인식이 끊긴 장과 말하지 않은 장을 가른다 (`script-coverage-evaluation.md` analyze 요청).

구간은 모두 Take 시각(ms)의 [시작, 끝) 이다.
"""

from collections.abc import Iterable, Mapping
from typing import Any

STT_GAP_VERSION = 1
# take_metric_seconds 한 행이 덮는 길이. `t_ms` 는 그 1초가 끝난 시각이다
SECOND_MS = 1000
STT_OK = "ok"

type Span = tuple[int, int]


def _merge(spans: Iterable[Span]) -> list[list[int]]:
    """겹치거나 맞닿은 구간을 합친다."""
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def stt_gap_ranges(seconds: Iterable[tuple[int, str]], duration_ms: int) -> dict[str, Any] | None:
    """1초 기록 `(t_ms, stt_status)` 로 STT 끊김 구간을 만든다. 기록이 하나도 없으면 None 이다 —
    모든 시간을 기록 없음으로 채우지 않고 '모름'으로 둔다.

    한 행은 `[t_ms − 1000, t_ms)` 를 덮고 `[0, duration_ms)` 로 자른다. 길이가 0 이하면 버린다.
      - `not_ok`     : STT 상태가 `ok` 가 아니던 구간 (연결 중 · 다시 연결 중 · 느림 · 닫힘)
      - `unobserved` : 어느 1초 기록도 덮지 않는 구간
    """
    rows = list(seconds)
    if not rows:
        return None
    observed: list[Span] = []
    not_ok: list[Span] = []
    for t_ms, stt_status in rows:
        start, end = max(0, t_ms - SECOND_MS), min(t_ms, duration_ms)
        if end <= start:
            continue
        observed.append((start, end))
        if stt_status != STT_OK:
            not_ok.append((start, end))
    unobserved: list[list[int]] = []
    cursor = 0
    for start, end in _merge(observed):
        if cursor < start:
            unobserved.append([cursor, start])
        cursor = end
    if cursor < duration_ms:
        unobserved.append([cursor, duration_ms])
    return {
        "version": STT_GAP_VERSION,
        "duration_ms": duration_ms,
        "not_ok": _merge(not_ok),
        "unobserved": unobserved,
    }


def _is_ms(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _spans(value: Any) -> list[Span] | None:
    if not isinstance(value, list):
        return None
    spans = []
    for item in value:
        if not (isinstance(item, list) and len(item) == 2 and all(map(_is_ms, item))):
            return None
        spans.append((item[0], item[1]))
    # 겹친 길이를 한 번씩 훑어 세려면 시작 순으로 놓인 겹치지 않는 구간이어야 한다
    return [(start, end) for start, end in _merge(spans)]


def _read(gaps: Any) -> tuple[int, list[Span], list[Span]] | None:
    """저장한 끊김 구간을 읽는다. 없거나 모양이 다르면 None ('모름')."""
    if not isinstance(gaps, Mapping) or gaps.get("version") != STT_GAP_VERSION:
        return None
    duration_ms = gaps.get("duration_ms")
    not_ok, unobserved = _spans(gaps.get("not_ok")), _spans(gaps.get("unobserved"))
    if not _is_ms(duration_ms) or not_ok is None or unobserved is None:
        return None
    return duration_ms, not_ok, unobserved


def slide_spans(slide_events: Iterable[tuple[int, int]], duration_ms: int) -> dict[int, list[Span]]:
    """장마다 화면에 있던 구간. `slide_events` 는 `(장 번호, 넘어온 시각)` 이다.

    한 이벤트는 다음 이벤트까지, 마지막은 Take 끝까지다. `[0, duration_ms)` 로 자르고 끝이 시작
    이하인 구간은 버린다. 첫 이벤트 전 구간은 어느 장에도 넣지 않고, 다시 온 장은 방문마다 더한다
    (장별 전사를 나누는 `coverage.slide_stt` 와 같은 규칙).
    """
    events = sorted(slide_events, key=lambda event: event[1])
    spans: dict[int, list[Span]] = {}
    for index, (number, started_ms) in enumerate(events):
        ended_ms = events[index + 1][1] if index + 1 < len(events) else duration_ms
        start, end = max(0, started_ms), min(ended_ms, duration_ms)
        if end > start:
            spans.setdefault(number, []).append((start, end))
    return spans


def _overlap(spans: list[Span], ranges: list[Span]) -> int:
    """두 구간 목록이 겹친 길이. 둘 다 시작 순으로 놓인 서로 겹치지 않는 구간이라 한 번씩만 훑는다.

    장 변경은 Take 당 1만 번까지 받는다. 장 방문마다 끊김 구간 전부를 맞대 보면 STT 가 자주 끊긴
    긴 Take 에서 수천만 번을 센다 — 그동안 채점 작업은 Take 행을 잠그고 있다 (`coverage._read_job`).
    """
    total = first = 0
    for start, end in spans:
        while first < len(ranges) and ranges[first][1] <= start:
            first += 1
        index = first
        while index < len(ranges) and ranges[index][0] < end:
            total += min(end, ranges[index][1]) - max(start, ranges[index][0])
            index += 1
    return total


def slide_stt_gaps(
    slide_numbers: Iterable[int], slide_events: Iterable[tuple[int, int]], gaps: Any
) -> dict[int, dict[str, int | None]]:
    """장마다 analyze 요청에 실을 장 길이 · STT 끊김 · 기록 없음 시간(ms).

    끊김 구간이 없으면(`stt_gap_ranges` null) 셋 다 None 이다. AI 는 '모름'으로 지금처럼 채점한다.
    한 번도 보이지 않은 장은 0 이다.
    """
    keys = ("duration_ms", "stt_gap_ms", "stt_unobserved_ms")
    read = _read(gaps)
    if read is None:
        return {number: dict.fromkeys(keys) for number in slide_numbers}
    duration_ms, not_ok, unobserved = read
    by_slide = slide_spans(slide_events, duration_ms)
    result: dict[int, dict[str, int | None]] = {}
    for number in slide_numbers:
        spans = by_slide.get(number, [])
        result[number] = {
            "duration_ms": sum(end - start for start, end in spans),
            "stt_gap_ms": _overlap(spans, not_ok),
            "stt_unobserved_ms": _overlap(spans, unobserved),
        }
    return result
