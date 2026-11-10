"""평가의 자료 묶음 모드 시험 — approved(서비스 조건) · all(검수 전 초안 포함) · none 이 월드 · 채점 원문에 같이 걸리는지 (P-0008 5-4).

approved 는 서비스 계층(`api/chat_agent.py` `load_corpus`)이 실제로 싣는 카드와 같아야 한다. 카드 검수 상태가 바뀌어도
이 시험이 그대로 맞도록 기대값을 서비스 함수의 결과에서 얻는다.
"""

from __future__ import annotations

from pitch_coach_ai.api.chat_agent import load_corpus

from chat_agent_lab import scenario as scn

BY_ID = {s.id: s for s in scn.load_scenarios()}


def _ids(corpus: object) -> set[str]:
    return {c.source_id for c in corpus.cards} if corpus is not None else set()  # type: ignore[attr-defined]


def test_approved_mode_serves_exactly_what_the_service_serves() -> None:
    served = load_corpus()
    got = scn.build_corpus_for(None, "approved")
    assert _ids(got) == _ids(served)
    assert (got.version if got else "none") == (served.version if served else "none")


def test_all_mode_opens_draft_cards_and_none_mode_has_no_corpus() -> None:
    every = scn.build_corpus_for(None, "all")
    assert every is not None
    assert _ids(every) == set(scn.corpus_texts(None))  # 파일의 모든 카드(검수 전 포함)
    assert scn.build_corpus_for(None, "none") is None


def test_grading_texts_follow_the_mode() -> None:
    sc = BY_ID["guide-nervous"]
    for mode in scn.CORPUS_MODES:
        w = scn.build_world(sc, corpus_mode=mode)
        if mode == "all":
            assert "g-07" in w.corpus_texts
        else:
            # 서비스에 실리지 않은 카드의 인용은 맞는 인용으로 채점하지 않는다
            assert set(w.corpus_texts) == _ids(w.corpus)


def test_injected_card_is_served_only_when_the_service_serves_a_corpus() -> None:
    """주입 시나리오의 더한 카드는 '검수를 통과한 카드에 지시가 섞인 경우'라 approved 에서 검수 완료로 싣는다.
    서비스가 묶음을 못 만들면(자료 없음) 더한 카드만 따로 싣지 않는다."""
    sc = BY_ID["inj-guide"]
    assert sc.fixtures.corpus is not None and sc.fixtures.corpus.extra_cards
    extra = {c["source_id"] for c in sc.fixtures.corpus.extra_cards}
    w = scn.build_world(sc, corpus_mode="approved")
    if load_corpus() is None:
        assert w.corpus is None and w.corpus_texts == {}
    else:
        assert extra <= _ids(w.corpus)
        assert all(c.review == "APPROVED" for c in w.corpus.cards)  # type: ignore[union-attr]
        assert _ids(w.corpus) - extra == _ids(load_corpus())
    w_all = scn.build_world(sc, corpus_mode="all")
    assert extra <= _ids(w_all.corpus)
