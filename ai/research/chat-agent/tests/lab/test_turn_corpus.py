"""실제 자료 묶음(guides)을 쓰는 루프 시험 — 자료 검색 → GUIDE 문장 → 인용 대조 · 자료 카드 (기능 문서 6-9 · 6-10).

가짜 모델(tests/unit/fakes.py)로 돌리고 자료 묶음은 실제 파일을 읽는다(chat_agent_lab.app.load_corpus).
"""

from __future__ import annotations

from typing import Any

import pytest

from chat_agent.schemas import FinalData
from chat_agent_lab.app import load_corpus
from tests.unit import fakes as F

MSG = "청중과 눈을 잘 맞추려면 어떻게 해야 해?"
SEARCH = ("search_guides", {"query": "청중 눈맞춤"})


@pytest.fixture(scope="module")
def corpus():  # noqa: ANN201
    return load_corpus()


def request_draft() -> dict[str, Any]:
    return F.draft(
        "청중과 눈을 잘 맞추려면 어떻게 해야 해",
        type_="REQUEST",
        claim_op=None,
        polarity="NEUTRAL",
        verdict="SUPPORTED",
    )


def first_item(r: Any) -> dict[str, Any]:
    for res in reversed(F.tool_results(r)):
        items = (res.get("data") or {}).get("items")
        if items:
            return items[0]
    raise AssertionError("검색 결과가 없다")


def run(corpus: Any, answer: Any, *, fixes: list[Any] | None = None) -> FinalData:
    llm = F.FakeLlm(
        [F.plan(assessments=[request_draft()]), F.tools(SEARCH), F.done()],
        answers=[answer],
        fixes=fixes or [F.fix({})],
    )
    _, _, data = F.run_turn(
        F.request(user_message=MSG), llm, corpus=corpus, corpus_version=corpus.version
    )
    return FinalData.model_validate(data)


def test_guide_sentence_with_verbatim_quote_passes_and_shows_source(corpus: Any) -> None:
    def answer(r: Any) -> Any:
        it = first_item(r)
        return F.answer(
            [
                F.claim(
                    "GUIDE",
                    f'자료에서는 "{it["quote"][:25]}"라고 안내해요.',
                    source_ids=[it["source_id"]],
                )
            ]
        )

    fd = run(corpus, answer)
    assert fd.status == "DONE"
    g = [c for c in fd.message.claims if c.kind == "GUIDE"]  # type: ignore[union-attr]
    assert g and fd.message.sources and fd.message.sources[0].source_id == g[0].source_ids[0]  # type: ignore[union-attr]
    assert fd.basis.corpus_version == corpus.version


def test_guide_sentence_with_altered_quote_is_dropped(corpus: Any) -> None:
    """L4-29 · 6-9: 인용이 자료 원문에 글자 그대로 없으면 걸리고, 고치지 못하면 지운다."""

    def answer(r: Any) -> Any:
        it = first_item(r)
        return F.answer(
            [
                F.claim(
                    "GUIDE",
                    '자료에서는 "시선은 반드시 3초씩 고정하세요"라고 안내해요.',
                    source_ids=[it["source_id"]],
                )
            ]
        )

    fd = run(corpus, answer, fixes=[F.fix({0: None})])
    assert "3초씩" not in fd.message.text  # type: ignore[union-attr]


def test_guide_sentence_citing_unread_source_is_dropped(corpus: Any) -> None:
    """이번 실행에서 읽지 않은 자료 id 는 근거가 아니다(STALE_EVIDENCE · QUOTE_MISMATCH)."""
    unread = next(c.source_id for c in corpus.cards if c.source_id.startswith("u-"))

    def answer(_r: Any) -> Any:
        return F.answer([F.claim("GUIDE", "자료에 그렇게 나와 있어요.", source_ids=[unread])])

    fd = run(corpus, answer, fixes=[F.fix({0: None})])
    assert not any(c.kind == "GUIDE" for c in fd.message.claims)  # type: ignore[union-attr]


def test_no_guide_route_without_claims_is_refused(corpus: Any) -> None:
    fd = run(corpus, F.answer([], route="NO_GUIDE"))
    assert fd.status == "REFUSED"
