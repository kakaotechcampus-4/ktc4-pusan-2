"""자료 근거 문장(GUIDE)의 인용 문장(quote) 시험 — 검사 · 고치기 없이 버림 · 보이는 자료 인용 (기능 문서 6-9 · 6-10).

흐름은 가짜 모델과 실제 자료 묶음으로 본다. 인용 문장(초안의 `quote`)은 응답 `Claim`에 나가지 않는다.
"""

from __future__ import annotations

from typing import Any

import pytest

from pitch_coach_ai.features.chat_agent.corpus import sentence_spans
from pitch_coach_ai.features.chat_agent.schemas import FinalData

from . import fakes as F
from .fakes import load_corpus
from .test_stream_answer import assert_prefix_invariant

MSG = "청중과 눈을 잘 맞추려면 어떻게 해야 해?"
G1 = "중요한 문장 앞에서 잠깐 멈추면 청중이 따라오기 쉬워요."


@pytest.fixture(scope="module")
def corpus() -> Any:
    return load_corpus()


def _request_draft() -> dict[str, Any]:
    return F.draft(
        "청중과 눈을 잘 맞추려면 어떻게 해야 해",
        type_="REQUEST",
        claim_op=None,
        polarity="NEUTRAL",
        verdict="SUPPORTED",
    )


def _sentences(text: str) -> list[str]:
    return [text[a:b].strip() for a, b in sentence_spans(text)]


def _card(corpus: Any, n: int = 3) -> Any:
    """문장이 n개 이상인 발표법 카드."""
    return next(c for c in corpus.cards if c.kind == "method" and len(_sentences(c.text)) >= n)


def _run(corpus: Any, card: Any, claims: list[dict[str, Any]], *, fixes: list[Any] | None = None):  # noqa: ANN202
    """카드 제목으로 찾아 그 카드를 이번 실행에 읽은 자료로 만든 뒤 답을 쓴다."""
    search = ("search_guides", {"query": card.title})
    assert card.source_id in [h.card.source_id for h in corpus.search(card.title)]
    llm = F.FakeLlm(
        [F.plan(assessments=[_request_draft()]), F.tools(search), F.done()],
        answers=[F.answer(claims)],
        fixes=fixes or [F.fix({})],
        stream_chunk=7,
    )
    status, lines, data = F.run_turn(
        F.request(user_message=MSG), llm, corpus=corpus, corpus_version=corpus.version
    )
    assert status == 200 and data is not None
    fixes_called = [c for c in llm.calls if c.purpose == "FIX"]
    return FinalData.model_validate(data), data, lines, fixes_called


def test_guide_without_quote_is_dropped_without_a_fix_and_not_general(corpus: Any) -> None:
    card = _card(corpus)
    fd, data, _lines, fixes = _run(
        corpus,
        card,
        [F.claim("GUIDE", "눈을 맞추며 말해 보세요.", source_ids=[card.source_id])],
    )
    assert fixes == []  # 인용 문장이 없으면 글만 고쳐서는 풀리지 않는다
    assert not any(c["kind"] in ("GUIDE", "GENERAL") for c in data["message"]["claims"])
    assert fd.message.sources == []  # 인용되지 않은 자료는 나오지 않는다


def test_guide_with_quote_not_in_source_is_dropped_without_a_fix(corpus: Any) -> None:
    card = _card(corpus)
    _fd, data, _lines, fixes = _run(
        corpus,
        card,
        [
            F.claim(
                "GUIDE",
                "눈을 맞추며 말해 보세요.",
                source_ids=[card.source_id],
                quote="이 문장은 자료 원문에 없어요.",
            )
        ],
    )
    assert fixes == []
    assert not any(c["kind"] in ("GUIDE", "GENERAL") for c in data["message"]["claims"])


def test_valid_quote_passes_and_its_sentence_is_the_displayed_source(corpus: Any) -> None:
    card = _card(corpus)
    third = _sentences(card.text)[2]
    fd, data, lines, fixes = _run(
        corpus,
        card,
        [
            F.claim(
                "GUIDE",
                "청중과 눈을 맞추며 말해 보세요.",
                source_ids=[card.source_id],
                quote=third,
            )
        ],
    )
    assert fd.status == "DONE" and fixes == []
    guide = [c for c in data["message"]["claims"] if c["kind"] == "GUIDE"]
    assert len(guide) == 1
    assert "quote" not in guide[0]  # 응답 Claim 모양은 그대로
    srcs = data["message"]["sources"]
    assert [s["source_id"] for s in srcs] == [card.source_id]
    assert srcs[0]["quote"] == third  # 카드 앞 300자가 아니라 인용한 문장
    assert srcs[0]["quote"] != card.text[:300]
    # 문장 줄이 보낸 자료 카드도 같은 인용이다
    sent = [s for ln in lines if ln.get("event") == "claim" for s in ln["data"]["sources"]]
    assert sent and sent[0]["quote"] == third
    assert_prefix_invariant(lines, data)


def test_guide_survivors_cite_only_cards_of_emitted_guide_claims(corpus: Any) -> None:
    """살아남은 GUIDE 문장이 인용한 카드만, 첫 인용 순서로 나온다. 다른 종류(null quote)는 영향이 없다."""
    card = _card(corpus)
    sents = _sentences(card.text)
    fd, data, _lines, _fixes = _run(
        corpus,
        card,
        [
            F.claim(
                "GUIDE",
                "문장 하나를 풀어 쓴 조언이에요.",
                source_ids=[card.source_id],
                quote=sents[1],
            ),
            F.claim("GUIDE", "근거 없는 말이에요.", source_ids=[card.source_id]),
            F.claim(
                "GUIDE", "같은 카드의 다른 문장이에요.", source_ids=[card.source_id], quote=sents[0]
            ),
        ],
    )
    assert fd.status == "DONE"
    kinds = [c["kind"] for c in data["message"]["claims"]]
    # 같은 카드의 다른 문장을 인용한 뒤 문장은 보이는 인용과 어긋나므로 버린다(test_quote_lock.py)
    assert kinds.count("GUIDE") == 1
    assert [s["quote"] for s in data["message"]["sources"]] == [sents[1]]  # 첫 인용 문장
