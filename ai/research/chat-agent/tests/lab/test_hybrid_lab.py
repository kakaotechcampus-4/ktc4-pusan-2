"""연구 모듈의 하이브리드 검색 연결 시험 — 카드 벡터 검사 · 질문 임베딩 캐시 · 평가 실행기 설정 (기능 문서 6-10).

네트워크를 쓰지 않는다: 임베딩 함수는 시험이 정한 벡터를 돌려주는 대역이다. 카드 벡터는 저장소의 `guides/embeddings.json`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from chat_agent_lab import evals as ev
from chat_agent_lab import retrieval as rt
from chat_agent_lab import scenario as scn

MODEL = "text-embedding-3-small"
DIMS = 1536


def test_with_search_vectors_checks_the_repo_file_against_the_served_cards() -> None:
    corpus = scn.build_corpus_for(None, "all")
    got = scn.with_search_vectors(corpus, model=MODEL, dims=DIMS)
    assert got is not None and got.has_vectors and not corpus.has_vectors
    approved = scn.with_search_vectors(
        scn.build_corpus_for(None, "approved"), model=MODEL, dims=DIMS
    )
    assert (
        approved is not None and approved.has_vectors
    )  # 파일에는 검수 전 카드까지 있어도 실린 카드만 본다
    with pytest.raises(ValueError, match="MODEL_MISMATCH"):
        scn.with_search_vectors(corpus, model="other", dims=DIMS)
    with pytest.raises(ValueError, match="DIM_MISMATCH"):
        scn.with_search_vectors(corpus, model=MODEL, dims=512)
    assert scn.with_search_vectors(None, model=MODEL, dims=DIMS) is None


def test_embed_queries_batches_once_and_caches_by_model_dims_query(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_batch(*, texts: list[str], **kw: Any) -> list[list[float]]:
        calls.append(list(texts))
        return [[float(len(t)), 1.0] for t in texts]

    kw: dict[str, Any] = {
        "base_url": "u",
        "api_key": "k",
        "dims": 2,
        "cache": tmp_path / "c.json",
        "embed_batch": fake_batch,
    }
    first = rt.embed_queries(["가나", "가나다", "가나"], model="m", **kw)
    assert calls == [["가나", "가나다"]] and first["가나다"] == [3.0, 1.0]
    again = rt.embed_queries(["가나다", "새 질문"], model="m", **kw)
    assert calls[1:] == [["새 질문"]] and again["가나다"] == [3.0, 1.0]
    rt.embed_queries(["가나"], model="m", **kw)
    assert len(calls) == 2  # 캐시만으로
    rt.embed_queries(["가나"], model="other", **kw)  # 모델이 다르면 다시
    assert len(calls) == 3


def test_hybrid_searcher_passes_the_query_vector(tmp_path: Path) -> None:
    corpus = scn.with_search_vectors(scn.build_corpus_for(None, "all"), model=MODEL, dims=DIMS)
    assert corpus is not None
    zero = [0.0] * DIMS
    zero[0] = 1.0
    got = rt.hybrid_searcher({"zzqq": zero})(corpus, "zzqq", None)
    assert got == [h.card.source_id for h in corpus.search("zzqq", query_vector=zero)]


def test_runner_config_defaults_to_keyword_and_hybrid_world_gets_vectors() -> None:
    assert ev.RunnerConfig().search == "keyword"
    sc = next(s for s in scn.load_scenarios() if s.id == "guide-nervous")
    calls: list[tuple[str, float]] = []

    def embed(text: str, timeout: float) -> list[float]:
        calls.append((text, timeout))
        return [1.0] + [0.0] * (DIMS - 1)

    plain = ev._Ctx(sc, _noop_llm(), b"s" * 32, ev.RunnerConfig(repeat=1))
    assert not plain.be.app.corpus.has_vectors and plain.be.app.embed_query is None
    cfg = ev.RunnerConfig(repeat=1, search="hybrid", embed_query=embed, embedding_model=MODEL)
    hyb = ev._Ctx(sc, _noop_llm(), b"s" * 32, cfg)
    assert hyb.be.app.corpus.has_vectors and hyb.be.app.embed_query is embed
    bad = ev.RunnerConfig(repeat=1, search="hybrid", embed_query=embed, embedding_model="other")
    with pytest.raises(ValueError, match="MODEL_MISMATCH"):
        ev._Ctx(sc, _noop_llm(), b"s" * 32, bad)


def _noop_llm() -> Any:
    from .test_evals import _refuse_llm

    return _refuse_llm()


def test_freeze_info_records_search_and_retry_treats_old_results_as_keyword() -> None:
    sc = [next(s for s in scn.load_scenarios() if s.id == "guide-nervous")]
    kw = ev.freeze_info(sc, None, "OPENAI")
    assert (kw["search"], kw["embedding_model"], kw["embed_dims"]) == ("keyword", None, None)
    hy = ev.freeze_info(sc, None, "OPENAI", search="hybrid", embedding_model=MODEL, embed_dims=DIMS)
    assert (hy["search"], hy["embedding_model"], hy["embed_dims"]) == ("hybrid", MODEL, DIMS)
    assert ev._RETRY_OLD_DEFAULTS["search"] == "keyword"


def test_retry_refuses_to_mix_search_modes(tmp_path: Path) -> None:
    sc = next(s for s in scn.load_scenarios() if s.id == "guide-nervous")
    info = ev.freeze_info([sc], None, "OPENAI")
    info.update({"set": "dev", "repeat": 1, "baseline": False, "followups": False, "fake": False})
    info["reasoning"] = {"default": "low"}
    info.update({"started_at": "t0", "finished_at": "t1", "model": None})
    res = ev.run_suite(
        [sc], llm_factory=lambda s, r: _noop_llm(), secret=b"s" * 32, cfg=ev.RunnerConfig(repeat=1)
    )
    ev.write_report(tmp_path, res, ev.summarize(res, None, None, repeat=1), info, None)

    def plan(**kw: Any) -> Any:
        a = argparse.Namespace(
            set="dev",
            repeat=1,
            baseline=False,
            followups=False,
            bundle="OPENAI",
            fake=False,
            corpus="approved",
            **kw,
        )
        return ev.retry_plan(tmp_path, [sc], a, ())

    plan(search_mode="keyword")  # 같은 방식이면 합친다
    with pytest.raises(ValueError, match="search"):
        plan(search_mode="hybrid", embedding_model=MODEL, embed_dims=DIMS)
    assert (
        json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["info"]["search"]
        == "keyword"
    )
