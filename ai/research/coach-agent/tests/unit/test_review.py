"""리뷰 에이전트로 넘기는 근거 — finalize → build_review_evidence."""

from __future__ import annotations

import json

from coach import build_review_evidence, finalize
from coach.config import load_config

from .conftest import Session, gaze_off, gaze_on


def _finish(s: Session, t_ms: int):
    fin = finalize({"take_id": "test-take", "t_ms": t_ms, "coach_state": s.state}, s.config)
    return s.events + list(fin.events), fin


def test_finalize_closes_open_episodes_and_pending_outcomes(session: Session):
    session.run(10_000, 15_000, **gaze_on(0.9))  # 13초 개입, 효과 재기 전에 끝남
    events, fin = _finish(session, 16_000)
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


def test_review_evidence_labels_segments():
    session = Session(plan={})  # 시선 구간만 보려고 시간 계획은 뺀다
    session.run(10_000, 13_000, **gaze_on(0.9))  # 개입
    session.run(14_000, 30_000, **gaze_off(0.1))  # 효과 있음 → 구간 닫힘
    session.run(31_000, 40_000, mode="EXAM", **gaze_on(0.9))  # 실전 모드에서 다시
    events, _ = _finish(session, 41_000)
    ev = build_review_evidence("test-take", events)

    hints = [(s.area.value, s.hint.value) for s in ev.segments]
    assert hints == [("GAZE", "COACHED_EFFECTIVE"), ("GAZE", "UNADDRESSED")]
    unaddressed = ev.segments[1]
    assert "EXAM_MODE" in unaddressed.suppressed_reasons
    assert unaddressed.evidence["script_ratio"] == 0.9

    s = ev.summary
    assert (s.interventions, s.praises, s.effective, s.ineffective) == (2, 1, 1, 0)
    assert s.effective_rate == 1.0
    assert s.episodes == 2 and s.episodes_unaddressed == 1
    gaze = next(t for t in ev.by_type if t.area.value == "GAZE")
    assert (gaze.interventions, gaze.effective, gaze.episodes) == (1, 1, 2)
    iv = ev.interventions[0]
    assert iv.outcome.value == "EFFECTIVE" and iv.outcome_metric == "script_ratio"


def test_gave_up_segment_is_flagged_for_review():
    s = Session(load_config(policy={"cooldown_ms": 20_000}), slide=2, plan={})
    s.run(10_000, 70_000, **gaze_on(0.9))
    events, _ = _finish(s, 71_000)
    ev = build_review_evidence("test-take", events)
    assert [seg.hint.value for seg in ev.segments] == ["GAVE_UP"]
    assert ev.summary.gave_up == 1
    assert [c.change.value for c in ev.strategy_changes] == ["ESCALATED", "GAVE_UP"]


def test_accepts_events_as_stored_json(session: Session):
    """BE 는 이벤트를 JSON 으로 저장했다가 넘긴다."""
    session.run(10_000, 30_000, **gaze_on(0.9))
    events, _ = _finish(session, 31_000)
    stored = json.loads(json.dumps([e.model_dump(mode="json") for e in events]))
    assert build_review_evidence("test-take", stored) == build_review_evidence("test-take", events)


def test_empty_take():
    ev = build_review_evidence("test-take", [])
    assert ev.summary.interventions == 0 and ev.summary.effective_rate is None
    assert ev.segments == [] and ev.by_type == []
