"""F2 보강 시험 — 임베딩 전체 호출 마감 · 벡터만 걸린 카드의 인용 · 카드 벡터 만들기 오류 출력."""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence

import openai
import pytest

from pitch_coach_ai.common import embedding
from pitch_coach_ai.features.chat_agent.corpus import Corpus

from . import fakes as F
from .test_hybrid_search import DIMS, _axis, _one_hot, corpus, vcorpus  # noqa: F401  # fixture 재사용


def test_whole_call_deadline_raises_api_timeout_even_if_sdk_ignores_it() -> None:
    release = threading.Event()

    def slow(texts: Sequence[str], timeout: float) -> list[list[float]]:
        release.wait(5)  # SDK 가 단계별 시간 제한만 지켜 전체가 늘어지는 경우를 흉내
        return [[0.0] for _ in texts]

    started = time.monotonic()
    try:
        with pytest.raises(openai.APITimeoutError):
            embedding.with_deadline(slow, ["질문"], 0.05)
        assert time.monotonic() - started < 1.0  # 호출이 끝나길 기다리지 않는다
    finally:
        release.set()


def test_deadline_passes_through_fast_results_and_errors() -> None:
    assert embedding.with_deadline(lambda t, _s: [[1.0]] * len(t), ["a", "b"], 1.0) == [[1.0]] * 2

    def boom(_t: Sequence[str], _s: float) -> list[list[float]]:
        raise ValueError("x")

    with pytest.raises(ValueError):
        embedding.with_deadline(boom, ["a"], 1.0)


def test_vector_only_card_quotes_first_sentence_even_with_weak_overlap_in_later_sentence(
    vcorpus: Corpus,  # noqa: F811
) -> None:
    ix = next(i for i in vcorpus._indexed if len(i.sentences) >= 2)
    first_end = ix.sentences[0][1]
    weak = set(ix.sentences[-1][2])  # 뒤 문장에만 겹침이 있는 질의 낱말
    only_vec = vcorpus._hit(ix, 0.01, weak, hybrid=True, keyword=False)
    assert ix.card.text.startswith(only_vec.card.quote)
    assert only_vec.card.quote.startswith(ix.card.text[: ix.sentences[0][1]])
    assert len(only_vec.card.quote) >= first_end - ix.sentences[0][0]
    # 키워드 후보였다면 겹친 문장을 인용한다(기존 동작 유지)
    kw = vcorpus._hit(ix, 0.01, weak, hybrid=True, keyword=True)
    assert kw.card.quote != "" and kw.card.quote in ix.card.text


def test_build_guide_embeddings_prints_only_exception_class_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys
) -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "tools" / "build_guide_embeddings.py"
    spec = importlib.util.spec_from_file_location("build_guide_embeddings_f2", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class _Bundle:
        base_url = "http://x"
        model = "m"

        class api_key:  # noqa: N801
            @staticmethod
            def get_secret_value() -> str:
                return "sk-synthetic-secret"

    class _S:
        def bundle(self, _name: str) -> _Bundle:
            return _Bundle()

    class AuthBoom(Exception):
        pass

    def fail(**_kw: object) -> None:
        raise AuthBoom("sk-synthetic-secret leaked in body")

    monkeypatch.setattr(mod, "Settings", _S)
    monkeypatch.setattr(mod, "embed_batch", fail)
    rc = mod.main(["--out", str(tmp_path / "o.json")])
    err = capsys.readouterr()
    assert rc != 0
    assert "AuthBoom" in err.err and "secret" not in err.err + err.out
    assert not (tmp_path / "o.json").exists()


_ = (F, DIMS, _one_hot, _axis)
