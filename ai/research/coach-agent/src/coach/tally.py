"""판정 결과의 tally 를 Take 합계와 장 번호별 합계에 모은다.

1초 몫은 그 1초의 t_ms, 단어 몫은 말한 시각(start_ms)의 장에 들어간다. 확정이 늦게 온 단어도
말한 시각의 장에 붙도록 최근 장 전환을 coach_state(slide_log)에 둔다. 다시 온 장은 장 번호
하나로 합친다.
"""

from __future__ import annotations

from .schemas import JudgmentResult, SlideNow
from .state import CoachState

#: 남겨 둘 최근 장 전환 수
SLIDE_LOG_KEEP = 10
TICK_MS = 1_000


def note_slide(state: CoachState, slide: SlideNow | None) -> None:
    """지금 장이 마지막 기록과 다르면 전환을 남긴다."""
    if slide is None:
        return
    if state.slide_log and state.slide_log[-1][0] == slide.number:
        return
    state.slide_log = [*state.slide_log, (slide.number, slide.started_ms)][-SLIDE_LOG_KEEP:]


def slide_at(state: CoachState, t_ms: int) -> int | None:
    """t_ms 에 보이던 장. 가장 늦게 시작한 장 중 시작 ≤ t_ms 인 것, 모든 시작보다 이르면 첫 장."""
    for number, start in reversed(state.slide_log):
        if start <= t_ms:
            return number
    return state.slide_log[0][0] if state.slide_log else None


def time_pieces(state: CoachState, since_ms: int, t_ms: int) -> list[tuple[int, int]]:
    """[since_ms, t_ms) 를 1초 이하 조각으로 나눈다. 장이 바뀐 시각에서도 자른다."""
    cuts = sorted({start for _, start in state.slide_log if since_ms < start < t_ms})
    pieces: list[tuple[int, int]] = []
    pos = since_ms
    while pos < t_ms:
        end = min(pos + TICK_MS, t_ms, *(c for c in cuts if c > pos))
        pieces.append((pos, end))
        pos = end
    return pieces


def add(state: CoachState, area: str, values: dict[str, float], slide: int | None) -> None:
    """Take 합계에 더하고, 장을 알면 그 장 합계에도 더한다."""
    targets = [state.totals.setdefault(area, {})]
    if slide is not None:
        targets.append(state.slide_totals.setdefault(str(slide), {}).setdefault(area, {}))
    for name, value in values.items():
        for target in targets:
            target[name] = target.get(name, 0) + value


def accumulate(state: CoachState, results: list[JudgmentResult], *, stt_trusted: bool) -> None:
    """모든 결과의 tally 를 더한다.

    stt_trusted: 이번 요청에서 STT 와 군더더기 · 속도 판정을 믿을 수 있었는가. TIME 조각 중
    STT 가 ok 로 돌아온 뒤의 것만 slide_stt_ok_ms 에 더한다 — 머문 시간(elapsed_ms)과 같은
    조각이라, 끊김이 없으면 두 값이 같다.
    """
    since_ok = state.stt_ok_since_ms or 0
    for result in results:
        area = result.area.value
        for piece in result.tally:
            slide = slide_at(state, piece.t_ms)
            add(state, area, piece.values, slide)
            if area == "TIME" and slide is not None and stt_trusted and piece.t_ms >= since_ok:
                key = str(slide)
                elapsed = int(piece.values.get("elapsed_ms", 0))
                state.slide_stt_ok_ms[key] = state.slide_stt_ok_ms.get(key, 0) + elapsed


def mark_covered(state: CoachState, prev_ms: int, t_ms: int, window_ms: int) -> None:
    """이번 요청이 센 구간 [max(prev, t − window), t] 를 covered 에 합친다.

    prev 는 요청 전의 시각 커서다. 응답이 창보다 오래 빠졌으면 그 사이 [prev, start) 는 센 구간에
    넣지 않는다 — 입력 창에 없어서 셀 수 없던 시간이다.
    """
    start = max(prev_ms, t_ms - window_ms)
    if start >= t_ms:
        return
    last = state.covered[-1] if state.covered else None
    if last is not None and last[1] >= start:
        last[1] = max(last[1], t_ms)
    else:
        state.covered.append([start, t_ms])
