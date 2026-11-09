"""F2 검증 보강 시험 — 벡터 파일 누락 · 벡터 파일 해시 동결 · 자료 파일 실패 · 근거성 판정 입력 · 가벼운 말 목표."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chat_agent_lab import app as app_mod
from chat_agent_lab import evals as ev
from chat_agent_lab import judge as jd
from chat_agent_lab import scenario as scn


def test_missing_vector_file_keeps_keyword_search_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        app_mod,
        "embedding_env",
        lambda _f=None: app_mod.EmbeddingEnv(base_url="http://x", api_key="k", model="m"),
    )
    monkeypatch.setattr(scn, "EMBEDDINGS_FILE", tmp_path / "missing.json")
    logs: list[dict[str, Any]] = []
    app = app_mod.from_env(env_file=tmp_path / "none.env", log=logs.append)
    assert app.embed_query is None and not app.corpus.has_vectors
    assert logs and logs[0]["cause"] == "FileNotFoundError"


def test_freeze_info_hashes_the_vector_file_and_retry_key_uses_it(tmp_path: Path) -> None:
    sc = [next(s for s in scn.load_scenarios() if s.id == "guide-nervous")]
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_bytes(b"1")
    b.write_bytes(b"2")
    kw = ev.freeze_info(sc, None, "OPENAI")
    assert kw["embeddings_sha256"] is None
    h1 = ev.freeze_info(sc, None, "OPENAI", search="hybrid", embeddings_file=a)
    h2 = ev.freeze_info(sc, None, "OPENAI", search="hybrid", embeddings_file=b)
    assert h1["embeddings_sha256"] != h2["embeddings_sha256"]
    assert ev._RETRY_OLD_DEFAULTS["embeddings_sha256"] is None


def test_approved_mode_treats_unreadable_guide_files_as_no_corpus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> Any:
        raise FileNotFoundError("x")

    monkeypatch.setattr(scn, "_guide_files", boom)
    assert scn.build_corpus_for(None, "approved") is None
    # 실은 자료가 없으면 채점 원문은 파일에 닿지 않고 빈 사전이다
    assert scn.corpus_texts(None, None, "approved") == {}
    assert scn.corpus_texts(None, None, "none") == {}


def test_groundedness_judge_needs_every_displayed_quote() -> None:
    class Boom:
        def invoke(self, *_a: Any, **_k: Any) -> Any:
            raise AssertionError("인용이 비면 판정기를 부르지 않는다")

    v = jd.judge_sentence(Boom(), "문장", {"g-01": "표시 인용", "g-02": ""})  # type: ignore[arg-type]
    assert v.supported == 0 and not v.called
    k1 = jd.sentence_key({"text": "t", "source_ids": ["g-01"], "cards": {"g-01": "A"}})
    k2 = jd.sentence_key({"text": "t", "source_ids": ["g-01"], "cards": {"g-01": "B"}})
    assert k1 != k2


def test_small_talk_is_its_own_unit_with_a_pass3_target() -> None:
    r = ev.UNITS["small_talk"]
    assert ev.TARGETS["pass_k_small_talk"]["unit"] == "small_talk"
    assert ev.TARGETS["pass_k_small_talk"]["value"] == 1.0
    assert "small_talk" in ev.UNIT_LABELS and callable(r)
