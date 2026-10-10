"""finalize 가 내는 이벤트 — 열린 구간 · 효과 재기를 닫고, 이벤트 id 가 겹치지 않는다."""

from __future__ import annotations

from coach import finalize

from .conftest import Session, gaze_on


def _finish(s: Session, t_ms: int):
    fin = finalize(
        {"take_id": "test-take", "t_ms": t_ms, "coach_state": s.state}, s.judges, s.config
    )
    return s.events + list(fin.events), fin


def test_finalize_closes_open_episodes_and_pending_outcomes(session: Session):
    session.run(10_000, 15_000, **gaze_on(0.9))  # 13초 개입, 효과 재기 전에 끝남
    events, fin = _finish(session, 15_000)
    kinds = [e.kind for e in fin.events]
    assert kinds.count("OUTCOME") == 1 and kinds.count("EPISODE") == 1
    outcome = next(e for e in fin.events if e.kind == "OUTCOME")
    assert outcome.outcome.value == "NOT_MEASURED"
    episode = next(e for e in fin.events if e.kind == "EPISODE")
    assert episode.closed_by == "TAKE_END"
    assert (episode.start_ms, episode.end_ms) == (10_000, 15_000)


def test_event_ids_are_unique_across_decide_and_finalize(session: Session):
    session.run(10_000, 40_000, **gaze_on(0.9))
    events, _ = _finish(session, 41_000)
    ids = [e.event_id for e in events]
    assert len(ids) == len(set(ids))
