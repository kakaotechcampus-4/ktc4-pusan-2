"""장 방문의 SLIDE 이벤트 — 방문의 영역별 합계를 내보낸다.

방문은 tally.note_slide 가 열고 닫고, tally.accumulate 가 조각을 방문에 더한다. 방문이 끝났어도
그 장에서 말한 단어는 STT 확정이 늦으면 아직 오지 않았을 수 있다. 그래서 STT 두 모듈(군더더기 ·
속도)의 단어 커서가 방문 끝을 지나 단어가 다 확정된 뒤에 내보낸다. Take 가 끝나면 남은 방문을
모두 낸다.
"""

from __future__ import annotations

from .events import EventSink
from .schemas import SlideEvent
from .state import CoachState, SlideVisit

#: 방문의 단어가 다 확정됐는지 볼 모듈 (STT 로 재는 두 모듈)
_WORD_MODULES = ("filler", "pace")


def _words_final(st: CoachState, visit: SlideVisit) -> bool:
    """두 모듈의 단어 커서가 모두 방문 끝을 지났는가. 커서가 아직 없으면 지나지 않은 것이다."""
    if visit.end_ms is None:
        return False
    for name in _WORD_MODULES:
        cur = st.cursors.get(name)
        if cur is None or cur.words_since_ms < visit.end_ms:
            return False
    return True


def _emit(visit: SlideVisit, t_ms: int, sink: EventSink) -> None:
    sink.emit(
        SlideEvent,
        t_ms=t_ms,
        slide_number=visit.slide_number,
        start_ms=visit.start_ms,
        end_ms=visit.end_ms if visit.end_ms is not None else t_ms,
        target_ms=visit.target_ms,
        tally=visit.tally,
    )


def emit_ready(st: CoachState, t_ms: int, sink: EventSink) -> None:
    """끝난 방문 중 단어가 다 확정된 것을 시간순으로 낸다. 앞 방문이 남으면 뒤 방문도 기다린다."""
    while st.visits and _words_final(st, st.visits[0]):
        _emit(st.visits.pop(0), t_ms, sink)


def finish(st: CoachState, t_ms: int, sink: EventSink) -> None:
    """Take 끝: 지금 방문을 t_ms 에 끝내고 남은 방문을 모두 낸다. 커서는 보지 않는다."""
    if st.visits and st.visits[-1].end_ms is None:
        st.visits[-1].end_ms = max(st.visits[-1].start_ms, t_ms)
    visits, st.visits = st.visits, []
    for visit in visits:
        _emit(visit, t_ms, sink)
