"""단계 사이 넘겨주기 세 가지 시험 — 계획의 자료 검색어 · 답 쓰기의 문장 후보 · 고치기 자료 (기능 문서 6-1 · 6-9 · 6-10).

각 넘겨주기는 설정 스위치(`plan_guide_queries` · `answer_guide_candidates` · `fix_materials`)로 따로 켜고 끈다. 스위치를 끄면
이전 동작과 같아야 한다. 가짜 모델과 실제 자료 묶음으로 보고, 질문 임베딩은 부른 글을 적는 대역이다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import pytest

from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG
from pitch_coach_ai.features.chat_agent.corpus import Corpus, sentence_spans
from pitch_coach_ai.features.chat_agent.loop import GUIDE_CANDIDATES_TAG

from . import fakes as F

# 조사 흐름(코드가 먼저 읽기)을 켠다(conftest 의 표지)
pytestmark = pytest.mark.prefetch

MSG = "청중과 눈을 잘 맞추려면 어떻게 해야 해?"


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    return F.load_corpus()


def _sents(text: str) -> list[str]:
    return [text[a:b].strip() for a, b in sentence_spans(text)]


def _cfg(**kw: Any) -> Any:
    return DEFAULT_CONFIG.model_copy(update=kw)


def _request_draft() -> dict[str, Any]:
    return F.draft(
        "청중과 눈을 잘 맞추려면 어떻게 해야 해",
        type_="REQUEST",
        claim_op=None,
        polarity="NEUTRAL",
        verdict="SUPPORTED",
    )


class Embedder:
    """질문 임베딩 대역: 부를 때마다 (글 목록, 시간 제한)을 적고 모두 같은 벡터를 돌려준다."""

    def __init__(self, dims: int, fail: Exception | None = None) -> None:
        self.vec = [1.0] + [0.0] * (dims - 1)
        self.calls: list[tuple[list[str], float]] = []
        self.fail = fail

    def __call__(self, texts: Sequence[str], timeout: float) -> list[list[float]]:
        self.calls.append((list(texts), timeout))
        if self.fail is not None:
            raise self.fail
        return [list(self.vec) for _ in texts]


def _vcorpus(corpus: Corpus) -> Corpus:
    """카드마다 한 축 방향인 가짜 벡터를 붙인 묶음(test_hybrid_search 와 같은 방식)."""
    from pitch_coach_ai.features.chat_agent.corpus import card_embed_sha256, parse_card_vectors

    dims = 64
    data = {
        "model": "emb-test",
        "dimensions": dims,
        "cards": {
            c.source_id: {
                "input_sha256": card_embed_sha256(c),
                "vector": [1.0 if k == i else 0.0 for k in range(dims)],
            }
            for i, c in enumerate(corpus.cards)
        },
    }
    vectors, reason = parse_card_vectors(data, corpus, model="emb-test", dims=dims)
    assert vectors is not None, reason
    return corpus.with_vectors(vectors)


def _turn(
    corpus: Corpus,
    script: list[Any],
    *,
    cfg: Any = DEFAULT_CONFIG,
    embed: Any = None,
    answers: list[Any] | None = None,
    fixes: list[Any] | None = None,
    req: dict[str, Any] | None = None,
    log: list[dict[str, Any]] | None = None,
) -> tuple[F.FakeLlm, dict[str, Any]]:
    llm = F.FakeLlm(script, answers=answers, fixes=fixes or [F.fix({})])
    dep = F.deps(llm, corpus=corpus, corpus_version=corpus.version, cfg=cfg, log=log)
    dep = replace(dep, embed_query=embed)
    from pitch_coach_ai.features.chat_agent.turn import handle_turn

    resp = handle_turn(
        json.dumps(req or F.request(user_message=MSG), ensure_ascii=False).encode(),
        dep,
        secret=F.SECRET,
    )
    assert resp.lines is not None
    data = json.loads(list(resp.lines)[-1])["data"]
    return llm, data


def _embed_logs(log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """서비스 로그의 `agent_embed` 줄(진단 기록 모양은 바꾸지 않는다)."""
    return [x for x in log if x.get("event") == "agent_embed"]


def _search_steps(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        s
        for s in data["diagnostics"]["steps"]
        if s["kind"] == "TOOL" and s["code"] == "search_guides"
    ]


def _answer_msgs(llm: F.FakeLlm) -> list[dict[str, Any]]:
    return next(c for c in llm.calls if c.purpose == "ANSWER").messages


# ── 1. 계획 → 조사: 자료 검색어 ───────────────────────────────────────────────


def test_planned_queries_are_searched_first_with_one_batched_embedding(corpus: Corpus) -> None:
    vc = _vcorpus(corpus)
    emb = Embedder(64)
    q = ["청중 눈맞춤 나누어", "대본 핵심어 메모"]
    log: list[dict[str, Any]] = []
    llm, data = _turn(
        vc,
        [F.plan(assessments=[_request_draft()], guide_queries=q), F.done()],
        embed=emb,
        log=log,
        answers=[
            F.answer(
                [F.claim("GENERAL", "중요한 문장 앞에서 잠깐 멈추면 좋아요.")], route="NO_GUIDE"
            )
        ],
    )
    steps = _search_steps(data)
    assert len(steps) == 2  # 모델은 검색 도구를 고르지 않았다(첫 조사 호출은 done)
    assert all("embed" not in s["detail"] and "search_mode" not in s["detail"] for s in steps)
    el = _embed_logs(log)
    assert [x["outcome"] for x in el] == ["OK", "OK"]
    assert [x["mode"] for x in el] == ["HYBRID", "HYBRID"]
    assert emb.calls == [(q, DEFAULT_CONFIG.embed_timeout_seconds)]  # 묶어서 한 번
    assert el[0]["ms"] is not None and el[1]["ms"] is None
    assert not any(x in json.dumps(el, ensure_ascii=False) for x in q)  # 질문 글은 남기지 않는다
    assert [c.purpose for c in llm.calls][:2] == ["RESEARCH", "RESEARCH"]  # 계획, 조사(끝)


def test_plan_guide_queries_switch_off_searches_nothing_first(corpus: Corpus) -> None:
    emb = Embedder(64)
    _llm, data = _turn(
        _vcorpus(corpus),
        [F.plan(assessments=[_request_draft()], guide_queries=["청중 눈맞춤"]), F.done()],
        cfg=_cfg(plan_guide_queries=False),
        embed=emb,
        answers=[
            F.answer(
                [F.claim("GENERAL", "중요한 문장 앞에서 잠깐 멈추면 좋아요.")], route="NO_GUIDE"
            )
        ],
    )
    assert _search_steps(data) == [] and emb.calls == []


def test_planned_queries_are_deduped_truncated_and_share_the_prefetch_limit(
    corpus: Corpus,
) -> None:
    llm, data = _turn(
        corpus,
        [
            F.plan(
                assessments=[_request_draft()],
                guide_queries=[
                    "청중 눈맞춤",
                    " 청중 눈맞춤 ",
                    "말 속도 쉼",
                    "대본 메모",
                    "긴장 호흡",
                ],
            ),
            F.done(),
        ],
        cfg=_cfg(max_guide_queries=3, research_prefetch_max=2),
        answers=[
            F.answer(
                [F.claim("GENERAL", "중요한 문장 앞에서 잠깐 멈추면 좋아요.")], route="NO_GUIDE"
            )
        ],
    )
    steps = _search_steps(data)
    assert len(steps) == 2  # 같은 글 하나로 합치고 상한(3) 안에서 prefetch 한도(2)에 걸린다


def test_planned_queries_fall_back_to_keyword_when_embedding_fails(corpus: Corpus) -> None:
    emb = Embedder(64, fail=TimeoutError())
    log: list[dict[str, Any]] = []
    _llm, data = _turn(
        _vcorpus(corpus),
        [
            F.plan(assessments=[_request_draft()], guide_queries=["청중 눈맞춤", "말 속도 쉼"]),
            F.done(),
        ],
        embed=emb,
        log=log,
        answers=[
            F.answer(
                [F.claim("GENERAL", "중요한 문장 앞에서 잠깐 멈추면 좋아요.")], route="NO_GUIDE"
            )
        ],
    )
    assert len(_search_steps(data)) == 2
    assert [(x["outcome"], x["mode"]) for x in _embed_logs(log)] == [
        ("TIMEOUT", "KEYWORD"),
        ("TIMEOUT", "KEYWORD"),
    ]
    assert len(emb.calls) == 1  # 다시 시도하지 않는다


def test_a_model_search_with_the_same_text_reuses_the_planned_vector(corpus: Corpus) -> None:
    emb = Embedder(64)
    log: list[dict[str, Any]] = []
    _llm, data = _turn(
        _vcorpus(corpus),
        [
            F.plan(assessments=[_request_draft()], guide_queries=["청중 눈맞춤"]),
            F.tools(("search_guides", {"query": "대본 핵심어 메모"})),
            F.done(),
        ],
        embed=emb,
        log=log,
        answers=[
            F.answer(
                [F.claim("GENERAL", "중요한 문장 앞에서 잠깐 멈추면 좋아요.")], route="NO_GUIDE"
            )
        ],
    )
    assert [(x["outcome"], x["mode"]) for x in _embed_logs(log)] == [
        ("OK", "HYBRID"),
        ("USED", "KEYWORD"),  # 다른 글은 실행당 한 번 규칙으로 키워드
    ]
    assert len(emb.calls) == 1


# ── 2. 조사 → 답 쓰기: 문장 후보 ─────────────────────────────────────────────


def _candidates(llm: F.FakeLlm) -> list[dict[str, str]] | None:
    last = _answer_msgs(llm)[-1]["content"]
    if not last.startswith(GUIDE_CANDIDATES_TAG):
        return None
    return json.loads(last[len(GUIDE_CANDIDATES_TAG) :].rsplit("\n</guide_candidates>", 1)[0])


def test_answer_gets_candidate_sentences_of_searched_cards_at_the_end(corpus: Corpus) -> None:
    llm, _ = _turn(
        corpus,
        [F.plan(assessments=[_request_draft()], guide_queries=["청중 눈맞춤 나누어"]), F.done()],
        answers=[
            F.answer(
                [F.claim("GENERAL", "중요한 문장 앞에서 잠깐 멈추면 좋아요.")], route="NO_GUIDE"
            )
        ],
    )
    cands = _candidates(llm)
    assert cands, "문장 후보가 마지막 메시지에 있어야 한다"
    assert len(cands) <= DEFAULT_CONFIG.max_guide_candidates
    for c in cands:
        card = corpus.get(c["source_id"])
        assert card is not None and c["title"] == card.title
        assert c["sentence"] in _sents(card.text)  # 원문 그대로
    assert cands[0]["source_id"] == "g-01"  # 찾은 순서


def test_candidate_cap_applies_to_total_sentences(corpus: Corpus) -> None:
    llm, _ = _turn(
        corpus,
        [F.plan(assessments=[_request_draft()], guide_queries=["청중 눈맞춤 나누어"]), F.done()],
        cfg=_cfg(max_guide_candidates=3),
        answers=[
            F.answer(
                [F.claim("GENERAL", "중요한 문장 앞에서 잠깐 멈추면 좋아요.")], route="NO_GUIDE"
            )
        ],
    )
    assert len(_candidates(llm) or []) == 3


def test_candidates_switch_off_leaves_the_answer_input_unchanged(corpus: Corpus) -> None:
    llm, _ = _turn(
        corpus,
        [F.plan(assessments=[_request_draft()], guide_queries=["청중 눈맞춤 나누어"]), F.done()],
        cfg=_cfg(answer_guide_candidates=False),
        answers=[
            F.answer(
                [F.claim("GENERAL", "중요한 문장 앞에서 잠깐 멈추면 좋아요.")], route="NO_GUIDE"
            )
        ],
    )
    assert _candidates(llm) is None
    assert not any(GUIDE_CANDIDATES_TAG in str(m["content"]) for m in _answer_msgs(llm))


def test_no_search_means_no_candidates(corpus: Corpus) -> None:
    llm, _ = _turn(
        corpus,
        [F.plan(assessments=[_request_draft()]), F.done()],
        answers=[
            F.answer(
                [F.claim("GENERAL", "중요한 문장 앞에서 잠깐 멈추면 좋아요.")], route="NO_GUIDE"
            )
        ],
    )
    assert _candidates(llm) is None


# ── 3. 검사 → 고치기: 이은 것만 ──────────────────────────────────────────────


def _fix_items(llm: F.FakeLlm) -> list[dict[str, Any]]:
    call = next(c for c in llm.calls if c.purpose == "FIX")
    last = call.messages[-1]["content"]
    assert last.startswith("<fix>")
    return json.loads(last.removeprefix("<fix>\n").rsplit("\n</fix>", 1)[0])


def _guide_fix_turn(corpus: Corpus, cfg: Any) -> F.FakeLlm:
    card = corpus.get("g-01")
    assert card is not None
    quote = _sents(card.text)[0]
    llm, _ = _turn(
        corpus,
        [F.plan(assessments=[_request_draft()], guide_queries=["청중 눈맞춤 나누어"]), F.done()],
        cfg=cfg,
        # 자료에 없는 숫자(120초)가 든 문장: 글만 고치면 풀리는 검사 실패 → 고치기 호출
        answers=[
            F.answer(
                [
                    F.claim(
                        "GUIDE",
                        "청중을 120초 동안 나누어 보세요.",
                        source_ids=["g-01"],
                        quote=quote,
                    )
                ]
            )
        ],
        fixes=[F.fix({0: None})],
    )
    return llm


def test_fix_gets_card_sentences_of_the_linked_card_only(corpus: Corpus) -> None:
    llm = _guide_fix_turn(
        corpus, _cfg(fix_materials=True)
    )  # 기본은 끔(8회차 기각), 켰을 때의 모양을 본다
    item = _fix_items(llm)[0]
    card = corpus.get("g-01")
    assert card is not None
    assert [x["sentence"] for x in item["card_sentences"]] == _sents(card.text)
    assert {x["source"] for x in item["card_sentences"]} == {"g-01"}  # 새 자료 id 는 주지 않는다
    # 답 쓰기의 전체 후보 메시지는 고치기에 가지 않는다
    fix_call = next(c for c in llm.calls if c.purpose == "FIX")
    assert not any(GUIDE_CANDIDATES_TAG in str(m["content"]) for m in fix_call.messages)


def test_fix_materials_switch_off_keeps_the_old_fix_input(corpus: Corpus) -> None:
    llm = _guide_fix_turn(corpus, _cfg(fix_materials=False))
    item = _fix_items(llm)[0]
    assert set(item) == {"index", "text", "check", "reason"}


def test_fix_gets_display_values_of_linked_evidence_only() -> None:
    """RECORD 문장이 고치기에 걸리면 그 문장에 이은 근거의 화면 표기 값만 더해 준다(다른 근거는 주지 않는다)."""
    drafts = [
        F.draft(
            "1장 시선",
            type_="REQUEST",
            claim_op=None,
            dimension="audience_gaze",
            metric="audience_ratio",
            slide_number=1,
            take_numbers=[5],
            polarity="NEUTRAL",
        )
    ]

    def wrong_number(r: Any) -> Any:
        eid = F.find_evidence(r, lambda o: o.get("metric") == "audience_ratio")
        return F.answer(
            [F.claim("RECORD", "Take 5의 1장 청중 시선 비율은 99%예요.", evidence_ids=[eid])]
        )

    for on in (True, False):
        llm = F.FakeLlm(
            [F.plan(assessments=drafts), F.done()],
            answers=[wrong_number],
            fixes=[F.fix({0: None})],
        )
        req = F.with_loaded(F.request(user_message="1장 시선 몇 %였어? Take 5"), "t-5")
        F.run_turn(req, llm, cfg=_cfg(fix_materials=on))
        item = _fix_items(llm)[0]
        if on:
            assert [r["what"] for r in item["values"]] == ["청중 시선"]
            assert item["values"][0]["take"] == 5 and item["values"][0]["slide"] == 1
        else:
            assert "values" not in item
