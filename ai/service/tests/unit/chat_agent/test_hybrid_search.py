"""하이브리드 검색(키워드 + 임베딩) 시험 — 카드 벡터 검사 · RRF 합치기 · 질문 임베딩 실행당 한 번 · 설정 · 서비스 준비 (기능 문서 6-10 · 11-1).

네트워크를 쓰지 않는다: 카드 벡터는 카드마다 한 방향(one-hot)인 가짜이고, 질문 임베딩 함수는 시험이 정한 벡터를 돌려주는 대역이다.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Sequence
from typing import Any

import pytest
from pydantic import SecretStr

from pitch_coach_ai.api import chat_agent as api
from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG, load_config
from pitch_coach_ai.features.chat_agent.corpus import (
    Corpus,
    card_embed_input,
    card_embed_sha256,
    parse_card_vectors,
)
from pitch_coach_ai.features.chat_agent.schemas import ToolDetail
from pitch_coach_ai.settings import Settings

from . import fakes as F
from .test_tools import make_ctx, run

MODEL, DIMS = "emb-test", 64


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    return F.load_corpus()


def _one_hot(i: int, dims: int = DIMS) -> list[float]:
    return [1.0 if k == i else 0.0 for k in range(dims)]


def _data(corpus: Corpus, *, dims: int = DIMS) -> dict[str, Any]:
    """카드 i 번째는 i 번 축 한 방향인 벡터(길이는 일부러 1이 아니게 3배)."""
    return {
        "model": MODEL,
        "dimensions": dims,
        "cards": {
            c.source_id: {
                "input_sha256": card_embed_sha256(c),
                "vector": [3 * x for x in _one_hot(i, dims)],
            }
            for i, c in enumerate(corpus.cards)
        },
    }


@pytest.fixture(scope="module")
def vcorpus(corpus: Corpus) -> Corpus:
    vectors, reason = parse_card_vectors(_data(corpus), corpus, model=MODEL, dims=DIMS)
    assert reason is None and vectors is not None
    return corpus.with_vectors(vectors)


def _axis(corpus: Corpus, source_id: str) -> int:
    return [c.source_id for c in corpus.cards].index(source_id)


# ── 카드 벡터 검사 ────────────────────────────────────────────────────────


def test_parse_card_vectors_normalizes_and_with_vectors_keeps_the_original(corpus: Corpus) -> None:
    vectors, reason = parse_card_vectors(_data(corpus), corpus, model=MODEL, dims=DIMS)
    assert (
        reason is None
        and vectors is not None
        and set(vectors) == {c.source_id for c in corpus.cards}
    )
    assert all(math.isclose(math.hypot(*v), 1.0) for v in vectors.values())
    carried = corpus.with_vectors(vectors)
    assert carried.has_vectors and not corpus.has_vectors
    assert carried.version == corpus.version and carried.hash == corpus.hash


def test_card_embed_input_is_title_topics_text(corpus: Corpus) -> None:
    c = corpus.cards[0]
    assert card_embed_input(c) == f"{c.title}\n{' · '.join(c.topics)}\n{c.text}"


def _bad(corpus: Corpus, mutate: Any, **kw: Any) -> str | None:
    data = _data(corpus)
    mutate(data)
    vectors, reason = parse_card_vectors(data, corpus, **{"model": MODEL, "dims": DIMS, **kw})
    assert vectors is None
    return reason


def test_parse_card_vectors_reasons(corpus: Corpus) -> None:
    first = corpus.cards[0].source_id
    assert _bad(corpus, lambda d: None, model="other") == "MODEL_MISMATCH"
    assert _bad(corpus, lambda d: None, dims=32) == "DIM_MISMATCH"
    assert _bad(corpus, lambda d: d["cards"].pop(first)) == "MISSING_CARD"
    assert (
        _bad(corpus, lambda d: d["cards"][first].update(input_sha256="0" * 64)) == "HASH_MISMATCH"
    )
    assert _bad(corpus, lambda d: d["cards"][first].update(vector=[1.0])) == "INVALID"
    assert (
        _bad(corpus, lambda d: d["cards"][first]["vector"].__setitem__(0, float("nan")))
        == "INVALID"
    )
    assert _bad(corpus, lambda d: d["cards"][first]["vector"].__setitem__(0, "1")) == "INVALID"
    assert _bad(corpus, lambda d: d["cards"][first].update(vector=[0.0] * DIMS)) == "INVALID"
    assert _bad(corpus, lambda d: d.update(cards=[])) == "INVALID"
    assert parse_card_vectors([], corpus, model=MODEL, dims=DIMS) == (None, "INVALID")


def test_real_embeddings_file_matches_the_cards() -> None:
    """저장소에 둔 `guides/embeddings.json` 이 모든 카드의 입력 해시 · 모델 · 차원과 맞는다(카드를 고치고 다시 만들지 않으면 깨진다)."""
    from importlib import resources

    corpus = F.load_corpus()
    data = json.loads(
        resources.files("pitch_coach_ai.features.chat_agent")
        .joinpath("guides", "embeddings.json")
        .read_text(encoding="utf-8")
    )
    vectors, reason = parse_card_vectors(
        data, corpus, model=data["model"], dims=DEFAULT_CONFIG.embed_dimensions
    )
    assert reason is None and vectors is not None


# ── 검색: 합치기 ──────────────────────────────────────────────────────────


KEYWORD_Q = "발표 전에 너무 긴장돼요"


def test_without_query_vector_or_card_vectors_it_is_the_keyword_search(
    corpus: Corpus, vcorpus: Corpus
) -> None:
    base = corpus.search(KEYWORD_Q)
    assert base  # 키워드로 걸리는 질문
    assert vcorpus.search(KEYWORD_Q) == base
    assert corpus.search(KEYWORD_Q, query_vector=_one_hot(0)) == base
    out = vcorpus.search_detailed(KEYWORD_Q)
    assert (out.mode, out.vector_candidates) == ("KEYWORD", 0)


def test_card_found_by_both_lists_ranks_first_and_scores_are_rrf(vcorpus: Corpus) -> None:
    kw = vcorpus.search(KEYWORD_Q)
    top = kw[0].card.source_id
    out = vcorpus.search_detailed(KEYWORD_Q, query_vector=_one_hot(_axis(vcorpus, top)))
    k = DEFAULT_CONFIG.hybrid_rrf_k
    assert out.mode == "HYBRID" and out.vector_candidates == 1
    assert out.hits[0].card.source_id == top
    assert out.hits[0].score == round(1 / (k + 1) + 1 / (k + 1), 6)
    # 키워드 후보는 그대로 뒤따른다(점수는 1/(k+순위))
    rest = [h.card.source_id for h in out.hits[1:]]
    assert rest == [h.card.source_id for h in kw[1:]][: len(rest)]
    assert out.hits[1].score == round(1 / (k + 2), 6)


def test_vector_only_card_is_added_and_ranks_by_its_vector_rank(vcorpus: Corpus) -> None:
    wide = vcorpus.with_cfg(load_config(max_search_results=50))
    kw_ids = [h.card.source_id for h in wide.search(KEYWORD_Q)]
    extra = next(c.source_id for c in vcorpus.cards if c.source_id not in kw_ids)
    out = wide.search_detailed(KEYWORD_Q, query_vector=_one_hot(_axis(vcorpus, extra)), limit=50)
    ids = [h.card.source_id for h in out.hits]
    assert out.vector_candidates == 1 and out.keyword_candidates == len(kw_ids)
    assert sorted(ids) == sorted([*kw_ids, extra])
    # 벡터 1위(1/61)는 키워드 1위(1/61)와 같은 점수라 source_id 순이고, 키워드 2위(1/62)보다는 앞이다
    assert ids.index(extra) == (0 if extra < kw_ids[0] else 1)
    assert out.hits[ids.index(extra)].score == round(1 / 61, 6)


def test_vector_only_quote_is_the_first_sentence_and_a_substring(vcorpus: Corpus) -> None:
    card = next(c for c in vcorpus.cards if c.kind == "method" and c.text.count(".") >= 2)
    out = vcorpus.search_detailed("zzqq", query_vector=_one_hot(_axis(vcorpus, card.source_id)))
    assert [h.card.source_id for h in out.hits] == [card.source_id]
    quote = out.hits[0].card.quote
    assert card.text.startswith(quote) and quote in card.text
    first_end = card.text.index(".") + 1
    assert quote.startswith(card.text[:first_end])
    assert len(quote) <= 300


def test_no_material_only_when_both_candidate_lists_are_empty(vcorpus: Corpus) -> None:
    # 키워드도 안 걸리고 코사인도 문턱 아래(미사용 축)면 결과 없음
    out = vcorpus.search_detailed("zzqq", query_vector=_one_hot(DIMS - 1))
    assert out.hits == [] and (out.keyword_candidates, out.vector_candidates) == (0, 0)
    # 합친 점수에는 문턱이 없다: 벡터 한 장만 걸려도 결과에 든다
    out = vcorpus.search_detailed("zzqq", query_vector=_one_hot(0))
    assert len(out.hits) == 1


def test_vector_candidates_are_capped_and_cosine_gated(vcorpus: Corpus) -> None:
    n = 7
    qv = [1.0] * n + [0.0] * (DIMS - n)  # 카드 7장과 코사인 1/√7 ≈ 0.378
    loose = vcorpus.with_cfg(load_config(hybrid_cosine_min=0.35))
    assert loose.search_detailed("zzqq", query_vector=qv).vector_candidates == 5
    strict = vcorpus.with_cfg(load_config(hybrid_cosine_min=0.5))
    assert strict.search_detailed("zzqq", query_vector=qv).vector_candidates == 0
    wide = vcorpus.with_cfg(load_config(hybrid_vector_top=7, hybrid_cosine_min=0.35))
    assert wide.search_detailed("zzqq", query_vector=qv, limit=7).vector_candidates == 7
    assert vcorpus.has_vectors  # with_cfg 는 원래 묶음을 바꾸지 않는다


def test_query_vector_is_checked(vcorpus: Corpus) -> None:
    with pytest.raises(ValueError):
        vcorpus.search("긴장", query_vector=[1.0, 0.0])
    with pytest.raises(ValueError):
        vcorpus.search("긴장", query_vector=[float("nan")] * DIMS)


# ── 도구: 질문 임베딩은 실행당 한 번 ───────────────────────────────────────────


class Embedder:
    """질문 임베딩 대역: 부른 횟수와 시간 제한을 적고, 정한 벡터(또는 예외)를 돌려준다."""

    def __init__(self, vec: Sequence[float] | Exception, clock: Any = None, takes: float = 0.0):
        self.vec, self.calls, self.clock, self.takes = vec, [], clock, takes

    def __call__(self, texts: Sequence[str], timeout: float) -> list[Sequence[float]]:
        self.calls.append((list(texts), timeout))
        if self.clock is not None:
            self.clock.advance(self.takes)
        if isinstance(self.vec, Exception):
            raise self.vec
        return [self.vec for _ in texts]


def _ctx(vcorpus: Corpus, embed: Any, *, left: float | None = 30.0, clock: F.Clock | None = None):
    ctx = make_ctx(corpus=vcorpus, corpus_version="guides-7")
    ctx.embed_query = embed
    if left is not None:
        ctx.research_left = lambda: left
    if clock is not None:
        ctx.monotonic = clock.monotonic
    return ctx


def _diag(res: Any) -> tuple[Any, Any, Any]:
    return res.diag["embed"], res.diag["embed_ms"], res.diag["search_mode"]


def test_embeds_once_and_reuses_the_vector_only_for_the_same_query(vcorpus: Corpus) -> None:
    clock = F.Clock()
    emb = Embedder(_one_hot(0), clock, 0.25)
    ctx = _ctx(vcorpus, emb, clock=clock)
    res, _ = run(ctx, "search_guides", {"query": KEYWORD_Q})
    assert _diag(res) == ("OK", 250, "HYBRID") and ctx.embed_used
    assert emb.calls == [([KEYWORD_Q], DEFAULT_CONFIG.embed_timeout_seconds)]
    # 같은 질문(화면만 같고 캐시 열쇠는 다른 경우를 흉내: 캐시를 비운다) → 벡터를 다시 쓴다
    ctx.tool_cache.clear()
    res, _ = run(ctx, "search_guides", {"query": KEYWORD_Q})
    assert _diag(res) == ("OK", None, "HYBRID") and len(emb.calls) == 1
    # 다른 질문 → 다시 부르지 않고 키워드만
    res, _ = run(ctx, "search_guides", {"query": "시선 처리는 어떻게 해요"})
    assert _diag(res) == ("USED", None, "KEYWORD") and len(emb.calls) == 1


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (TimeoutError(), "TIMEOUT"),
        (RuntimeError("x"), "ERROR"),
        (type("APITimeoutError", (Exception,), {})(), "TIMEOUT"),
    ],
)
def test_embed_failure_falls_back_to_keyword_and_counts_as_used(
    vcorpus: Corpus, exc: Exception, code: str
) -> None:
    emb = Embedder(exc)
    ctx = _ctx(vcorpus, emb)
    res, _ = run(ctx, "search_guides", {"query": KEYWORD_Q})
    assert res.ok and res.data["items"]  # 키워드 결과는 그대로
    assert res.diag["embed"] == code and res.diag["search_mode"] == "KEYWORD"
    assert res.diag["embed_ms"] is not None
    res, _ = run(ctx, "search_guides", {"query": "시선 처리는 어떻게 해요"})
    assert res.diag["embed"] == "USED" and len(emb.calls) == 1  # 다시 시도하지 않는다


def test_wrong_dimension_vector_is_an_error_not_a_crash(vcorpus: Corpus) -> None:
    ctx = _ctx(vcorpus, Embedder([1.0, 0.0]))
    res, _ = run(ctx, "search_guides", {"query": KEYWORD_Q})
    assert res.ok and _diag(res)[0] == "ERROR" and res.diag["search_mode"] == "KEYWORD"


def test_embed_timeout_is_min_of_config_and_research_time_left(vcorpus: Corpus) -> None:
    emb = Embedder(_one_hot(0))
    run(_ctx(vcorpus, emb, left=1.2), "search_guides", {"query": KEYWORD_Q})
    assert emb.calls[0][1] == 1.2


def test_embed_skipped_when_research_time_is_short(vcorpus: Corpus) -> None:
    emb = Embedder(_one_hot(0))
    ctx = _ctx(vcorpus, emb, left=DEFAULT_CONFIG.embed_min_seconds - 0.01)
    res, _ = run(ctx, "search_guides", {"query": KEYWORD_Q})
    assert _diag(res) == ("BUDGET", None, "KEYWORD") and emb.calls == []
    assert not ctx.embed_used  # 부르지 않았으니 쓴 것으로 세지 않는다


def test_embed_off_without_embedder_or_card_vectors(corpus: Corpus, vcorpus: Corpus) -> None:
    res, _ = run(_ctx(vcorpus, None), "search_guides", {"query": KEYWORD_Q})
    assert _diag(res) == ("OFF", None, "KEYWORD")
    emb = Embedder(_one_hot(0))
    res, _ = run(_ctx(corpus, emb), "search_guides", {"query": KEYWORD_Q})
    assert _diag(res) == ("OFF", None, "KEYWORD") and emb.calls == []


# ── 진단 · 서명 상태 ──────────────────────────────────────────────────────


def test_tool_detail_keeps_the_closed_shape_without_embed_fields() -> None:
    # BE 가 받는 TOOL detail 의 닫힌 모양은 바뀌지 않는다(임베딩 관측은 서비스 로그에만, 11-5)
    assert set(ToolDetail().model_dump()) == {
        "target_ids",
        "revisions",
        "outcome",
        "result_bytes",
        "query_chars",
        "error_code",
    }
    with pytest.raises(ValueError):
        ToolDetail(embed="OK")


def test_loop_state_embed_used_defaults_false_for_old_states() -> None:
    from pitch_coach_ai.features.chat_agent.loop import LoopState

    assert LoopState.load({"plan_set": True}).embed_used is False
    st = LoopState()
    st.embed_used = True
    assert LoopState.load(st.dump()).embed_used is True


def _turn_deps(llm: Any, corpus: Corpus, embed: Any, log: list[dict[str, Any]]) -> Any:
    from dataclasses import replace

    return replace(
        F.deps(llm, corpus=corpus, corpus_version="guides-7", log=log), embed_query=embed
    )


def test_turn_records_embed_fields_in_the_tool_step_and_embeds_once(vcorpus: Corpus) -> None:
    emb = Embedder(_one_hot(_axis(vcorpus, vcorpus.search(KEYWORD_Q)[0].card.source_id)))
    llm = F.FakeLlm(
        [
            F.plan(steps=["자료 찾기", "답 쓰기"]),
            F.tools(
                ("search_guides", {"query": KEYWORD_Q}),
                ("search_guides", {"query": "시선 처리는 어떻게 해요"}),
            ),
            F.done(),
            F.answer(
                [F.claim("GENERAL", "중요한 문장 앞에서 잠깐 멈추면 청중이 따라오기 쉬워요.")],
                route="NO_GUIDE",
            ),
        ]
    )
    from pitch_coach_ai.features.chat_agent.turn import handle_turn

    log: list[dict[str, Any]] = []
    resp = handle_turn(
        json.dumps(F.request(user_message="발표 전에 너무 긴장돼요"), ensure_ascii=False).encode(),
        _turn_deps(llm, vcorpus, emb, log),
        secret=F.SECRET,
    )
    assert resp.lines is not None
    lines = [json.loads(x) for x in resp.lines]
    steps = [
        s
        for s in lines[-1]["data"]["diagnostics"]["steps"]
        if s["kind"] == "TOOL" and s["code"] == "search_guides"
    ]
    assert all("embed" not in s["detail"] and "search_mode" not in s["detail"] for s in steps)
    el = [x for x in log if x.get("event") == "agent_embed"]
    assert [(x["outcome"], x["mode"]) for x in el] == [("OK", "HYBRID"), ("USED", "KEYWORD")]
    assert el[0]["ms"] is not None and el[1]["ms"] is None
    assert KEYWORD_Q not in json.dumps(el, ensure_ascii=False)
    assert len(emb.calls) == 1
    assert KEYWORD_Q not in json.dumps(lines[-1]["data"]["diagnostics"], ensure_ascii=False)


# ── 설정 ──────────────────────────────────────────────────────────────────


def _s(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)


def test_embedding_settings_none_all_partial() -> None:
    assert _s().bundle("embedding") is None
    assert (
        _s(
            openai_embedding_base_url="", openai_embedding_api_key="", openai_embedding_model=""
        ).bundle("embedding")
        is None
    )
    full = _s(
        openai_embedding_base_url="https://e.example/v1",
        openai_embedding_api_key="k-secret",
        openai_embedding_model="text-embedding-3-small",
    )
    b = full.bundle("embedding")
    assert b is not None and b.model == "text-embedding-3-small"
    assert "k-secret" not in repr(b)
    with pytest.raises(ValueError, match="OPENAI_EMBEDDING_"):
        _s(openai_embedding_model="text-embedding-3-small")


def test_new_config_fields_defaults() -> None:
    c = DEFAULT_CONFIG
    assert (c.hybrid_rrf_k, c.hybrid_vector_top, c.embed_timeout_seconds) == (60, 5, 2.0)
    assert (c.embed_min_seconds, c.embed_dimensions) == (0.3, 1536)


# ── 서비스 준비 ───────────────────────────────────────────────────────────


def _emb_settings(model: str = "text-embedding-3-small") -> Settings:
    return _s(
        openai_embedding_base_url="https://e.example/v1",
        openai_embedding_api_key=SecretStr("k"),
        openai_embedding_model=model,
    )


def test_service_attaches_vectors_from_the_repo_file_when_settings_exist(
    caplog: pytest.LogCaptureFixture,
) -> None:
    corpus = api.load_corpus()
    with caplog.at_level(logging.INFO, logger="pitch_coach_ai.chat_agent"):
        got, embed = api.attach_embeddings(corpus, _emb_settings())
    assert got is not None and got.has_vectors and callable(embed)
    assert got.version == corpus.version and not corpus.has_vectors
    assert "chat_agent_embeddings_unavailable" not in caplog.text


def test_service_is_keyword_only_without_settings(caplog: pytest.LogCaptureFixture) -> None:
    corpus = api.load_corpus()
    with caplog.at_level(logging.INFO, logger="pitch_coach_ai.chat_agent"):
        got, embed = api.attach_embeddings(corpus, _s())
    assert got is corpus and embed is None
    assert "chat_agent_embeddings_unavailable" not in caplog.text
    assert len([r for r in caplog.records if r.name == "pitch_coach_ai.chat_agent"]) == 1


@pytest.mark.parametrize("model", ["other-model"])
def test_service_keyword_only_and_logs_the_reason_code_on_a_bad_file(
    model: str, caplog: pytest.LogCaptureFixture
) -> None:
    corpus = api.load_corpus()
    with caplog.at_level(logging.INFO, logger="pitch_coach_ai.chat_agent"):
        got, embed = api.attach_embeddings(corpus, _emb_settings(model))
    assert got is corpus and embed is None
    assert "chat_agent_embeddings_unavailable reason=MODEL_MISMATCH" in caplog.text


def test_service_bad_dims_and_missing_file_are_keyword_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, caplog: pytest.LogCaptureFixture
) -> None:
    corpus = api.load_corpus()
    cfg = load_config(embed_dimensions=512)
    with caplog.at_level(logging.INFO, logger="pitch_coach_ai.chat_agent"):
        got, embed = api.attach_embeddings(corpus, _emb_settings(), cfg)
    assert embed is None and "reason=DIM_MISMATCH" in caplog.text
    monkeypatch.setattr(api, "GUIDES", tmp_path)  # 파일 없음
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="pitch_coach_ai.chat_agent"):
        got, embed = api.attach_embeddings(corpus, _emb_settings())
    assert (
        embed is None
        and "chat_agent_embeddings_unavailable reason=FileNotFoundError" in caplog.text
    )


def test_runtime_passes_the_embedder_into_turn_deps() -> None:
    f = lambda texts, timeout: [[0.0]]  # noqa: E731
    rt = api.AgentRuntime(
        llm=None,
        corpus=None,
        embed_query=f,
        secret=F.SECRET,
        previous_secret=None,
        max_concurrency=1,
    )
    assert rt.deps().embed_query is f


def test_agree_gate_keeps_only_cards_both_signals_back(vcorpus: Corpus) -> None:
    """AGREE: 키워드가 걸리지 않은 카드는 코사인이 아주 높을 때만, 코사인이 낮은 카드는 키워드가 아주 강할 때만 싣는다(8회차 M2b)."""
    n = 7
    qv = [1.0] * n + [0.0] * (
        DIMS - n
    )  # 코사인 ≈ 0.378: 합의 문턱(0.30)은 넘지만 강한 코사인(0.60)은 못 넘음
    agree = vcorpus.with_cfg(load_config(hybrid_gate="AGREE"))
    out = agree.search_detailed("zzqq", query_vector=qv)  # 키워드가 아무 카드에도 안 걸림
    assert out.hits == [] and out.vector_candidates == 0
    strong = agree.search_detailed(
        "zzqq", query_vector=_one_hot(0)
    )  # 코사인 1.0 → 강한 코사인만으로 싣는다
    assert len(strong.hits) == 1
