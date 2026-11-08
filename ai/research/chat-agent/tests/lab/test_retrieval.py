"""검색 평가 시험 — 문항 파일 모양 · 지표 계산 (기능 문서 6-10, P-0008 5-2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chat_agent_lab import retrieval as rt
from chat_agent_lab import scenario as scn


def test_items_file_is_valid_and_balanced() -> None:
    items = rt.load_items()
    kinds = {k: [i for i in items if i.kind == k] for k in ("method", "usage", "none")}
    assert all(kinds.values())
    for group in kinds.values():  # 고르기(dev)와 확인(test)이 반씩
        assert (
            abs(sum(i.split == "dev" for i in group) - sum(i.split == "test" for i in group)) <= 1
        )
    every = set(scn.corpus_texts(None))
    for i in items:
        assert set(i.expected) | set(i.acceptable) <= every, i.id


def test_metrics_count_only_cards_the_mode_serves(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "a",
                    "query": "긴장",
                    "expected": ["g-07"],
                    "acceptable": [],
                    "screen": None,
                    "kind": "method",
                    "split": "dev",
                    "style": "keyword",
                },
                {
                    "id": "b",
                    "query": "요리",
                    "expected": [],
                    "acceptable": [],
                    "screen": None,
                    "kind": "none",
                    "split": "dev",
                    "style": "keyword",
                },
            ]
        ),
        encoding="utf-8",
    )
    items = rt.load_items(path)
    corpus = scn.build_corpus_for(None, "all")
    rep = rt.evaluate(items, corpus, lambda c, q, s: ["g-04", "g-07"] if q == "긴장" else ["g-01"])
    assert rep["recall@1"]["value"] == 0.0 and rep["recall@3"]["value"] == 1.0
    assert rep["mrr"] == 0.5
    assert rep["none_accuracy"]["value"] == 0.0  # 자료 없음 질문에 카드를 냄
    none_rep = rt.evaluate(items, None)
    assert none_rep["positives"] == 0 and none_rep["none_accuracy"]["value"] == 1.0


def test_kind_and_expected_must_agree(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "x",
                    "query": "q",
                    "expected": [],
                    "acceptable": [],
                    "screen": None,
                    "kind": "method",
                    "split": "dev",
                    "style": "user",
                }
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        rt.load_items(path)
