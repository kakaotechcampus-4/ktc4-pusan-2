"""평가 스크립트(`tools/evaluate.py`) — 이어 부르기 · 지표 · 중복 검사 · 결과 파일 최신 검사."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from filler.config import DEFAULT
from filler.core import criteria_version

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "reports" / "results" / "filler_eval.json"
WORDS = ROOT / "data" / "virtual" / "words.json"
REGENERATE = (
    "uv run python tools/evaluate.py --data data/virtual/words.json "
    "--out reports/results/filler_eval.json"
)

_spec = importlib.util.spec_from_file_location("evaluate_tool", ROOT / "tools" / "evaluate.py")
assert _spec and _spec.loader
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)


def _w(
    word: str, start: int, end: int, gold: bool, tier: str | None, utt: str = "u1"
) -> dict[str, Any]:
    return {
        "utt": utt,
        "word": word,
        "start_ms": start,
        "end_ms": end,
        "label_filler": gold,
        "label_tier": tier,
    }


def _small() -> dict[str, Any]:
    """손으로 만든 발화 하나. 기대하는 판정은 아래 주석과 같다."""
    words = [
        _w("음", 0, 200, True, "T1"),  # T1 은 늘 센다
        _w("그", 250, 400, False, "T2"),  # 앞뒤 간격 50 ms: 세지 않음
        _w("서비스", 450, 800, False, None),
        _w("그니까", 1700, 2100, True, "T3"),  # 앞뒤 간격 900 ms: 센다
        _w("엄", 3000, 3200, True, None),  # 사전에 없는 정답 군더더기: 못 센다
        _w("진짜", 4300, 4600, False, "T3", "u2"),  # 정상 단어지만 앞뒤 1000 ms: 잘못 센다
        _w("좋아요", 5600, 5900, False, None, "u2"),
        _w("약간", 5950, 6200, True, "T3", "u2"),  # 정답이지만 앞 간격 50 ms: 놓친다
    ]
    return {
        "seed": 0,
        "takes": [
            {"id": "t1", "words": words, "utterance_ends": [], "take_end_ms": 7700},
        ],
    }


def _repeat() -> dict[str, Any]:
    words = [
        _w("그", 0, 100, True, "T2"),
        _w("그", 150, 250, False, "T2"),
        _w("서비스는", 300, 700, False, None),
    ]
    return {
        "seed": 0,
        "takes": [{"id": "r1", "words": words, "utterance_ends": [], "take_end_ms": 2200}],
    }


def test_tier_metrics_on_hand_made_utterance():
    result = tool.evaluate(_small())
    t = result["by_tier"]
    assert t["T1"] == {"tp": 1, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0}
    # 분모가 0 이면 null
    assert t["T2"] == {"tp": 0, "fp": 0, "fn": 0, "precision": None, "recall": None}
    assert t["T3"] == {"tp": 1, "fp": 1, "fn": 1, "precision": 0.5, "recall": 0.5}
    assert result["all"] == {"tp": 2, "fp": 1, "fn": 1, "precision": 0.6667, "recall": 0.6667}
    assert result["out_of_dictionary"] == 1
    assert (result["n_utterances"], result["n_words"]) == (2, 8)
    assert result["criteria_version"] == criteria_version(DEFAULT)


def test_errors_list_missed_and_wrongly_counted_words_with_context():
    errors = {e["word"]: e for e in tool.evaluate(_small())["errors"]}
    assert set(errors) == {"엄", "진짜", "약간"}
    assert errors["진짜"]["counted"] is True and errors["진짜"]["label_filler"] is False
    assert errors["약간"]["counted"] is False and errors["약간"]["label_tier"] == "T3"
    assert errors["엄"]["label_tier"] is None and errors["엄"]["reason"] == "사전에 없음"
    # 오류 목록은 단어가 속한 발화 id 를 적는다
    assert errors["진짜"]["id"] == "u2" and errors["엄"]["id"] == "u1"
    assert errors["진짜"]["context"] == "그니까 엄 [진짜] 좋아요 약간"
    assert errors["약간"]["context"] == "진짜 좋아요 [약간]"
    assert errors["진짜"]["reason"] == "앞뒤 간격이 기준 이상인 단독 단어"


def test_streaming_calls_give_the_same_words_as_one_call():
    data = _small()
    streamed, _ = tool.stream_take(data["takes"][0])
    assert set(streamed) == set(tool.single_call_take(data["takes"][0]))
    assert tool.evaluate(data)["streaming_matches_single_call"] is True


def test_requests_only_carry_confirmed_words_and_signals():
    utt = _small()["takes"][0]
    utt["utterance_ends"] = [800]
    early = tool._request(utt, 1_500, 0, 0, final=False)
    # 단어는 end_ms + 800 <= t, 신호는 u + 1000 <= t (1500 < 1800 이라 아직 없음)
    assert [w["word"] for w in early["words"]] == ["음", "그"]
    assert early["utterance_ends"] == []
    assert early["take_end_ms"] is None
    later = tool._request(utt, 1_800, 1_000, 400, final=False)
    assert [w["word"] for w in later["words"]] == ["음", "그", "서비스"]
    assert later["utterance_ends"] == [800]
    final = tool._request(utt, utt["take_end_ms"], 7_000, 6_200, final=True)
    assert len(final["words"]) == 8 and final["take_end_ms"] == 7700
    assert (final["since_ms"], final["words_since_ms"]) == (7_000, 6_200)


def test_a_take_longer_than_the_window_gives_the_same_words_as_one_call():
    # 4초마다 "음" 한 번씩 130초: 창(60초)이 Take 를 따라 움직여도 한 번씩만 센다
    words = [_w("음", 4_000 * k, 4_000 * k + 200, True, "T1") for k in range(33)]
    take = {"id": "long", "words": words, "utterance_ends": [], "take_end_ms": 134_000}
    streamed, _ = tool.stream_take(take)
    assert sorted(streamed) == list(range(33))
    assert set(streamed) == set(tool.single_call_take(take))


def test_double_counting_stops_the_evaluation(monkeypatch):
    real = tool.judge

    def forgetful(inp, t, config):
        # 코치가 단어 커서를 넘기지 않은 것처럼 커서를 0 으로 되돌린다
        [result] = real(inp, t, config)
        return [result.model_copy(update={"words_counted_until_ms": 0})]

    monkeypatch.setattr(tool, "judge", forgetful)
    with pytest.raises(RuntimeError, match="중복 집계"):
        tool.stream_take(_small()["takes"][0])


def test_repeat_rule_counts_the_earlier_word_only():
    rule = tool.evaluate(_repeat())["repeat_rule"]
    assert rule == {
        "T_F": {"pairs": 1, "first_counted": 1, "second_counted": 0, "first_by_repeat": 1}
    }


def test_sweep_rows_change_with_gap_ms_exactly_where_the_verdict_flips():
    # 정답 군더더기 "진짜" 의 앞뒤 간격이 정확히 500 ms: 기준이 500 이하면 세고 501 부터는 못 센다
    words = [
        _w("가나", 0, 300, False, None),
        _w("진짜", 800, 1100, True, "T3"),
        _w("다라", 1600, 1900, False, None),
    ]
    take = {"id": "s", "words": words, "utterance_ends": [], "take_end_ms": 3400}
    rows = tool.sweep_gap_ms({"seed": 0, "takes": [take]}, gaps=[400, 500, 501, 600])
    assert [r["gap_ms"] for r in rows] == [400, 500, 501, 600]
    got = [(r["by_tier"]["T3"]["tp"], r["by_tier"]["T3"]["fn"]) for r in rows]
    assert got == [(1, 0), (1, 0), (0, 1), (0, 1)]
    assert [r["by_tier"]["T3"]["recall"] for r in rows] == [1.0, 1.0, 0.0, 0.0]


def test_precision_and_recall_use_different_denominators():
    # T3: 센 것 3개 중 정답 2개, 정답 5개 중 센 것 2개 → 정밀도 2/3, 재현율 2/5
    def row(gold: bool, counted: str | None) -> dict[str, Any]:
        return {"label_filler": gold, "label_tier": "T3", "counted": counted}

    rows = [row(True, "T3")] * 2 + [row(False, "T3")] + [row(True, None)] * 3
    by_tier, total, ood = tool.tier_metrics(rows)
    assert by_tier["T3"] == {"tp": 2, "fp": 1, "fn": 3, "precision": 0.6667, "recall": 0.4}
    assert total["precision"] == 0.6667 and total["recall"] == 0.4 and ood == 0


def _tally(t_ms: int, key: str, tier: str) -> Any:
    from types import SimpleNamespace

    values = {"filler_count": 1, f"filler_{tier.lower()}": 1, f"filler_word:{key}": 1}
    return SimpleNamespace(tally=[SimpleNamespace(t_ms=t_ms, values=values)])


def test_tally_goes_to_the_word_with_the_same_start_and_the_same_text():
    # 시작이 같은 두 단어: 시작 시각만 보면 뒤의 "정상" 에 붙는다
    take = {
        "words": [
            _w("음", 0, 200, True, "T1"),
            _w("정상", 0, 400, False, None),
        ]
    }
    counted: dict[int, str] = {}
    tool._collect(take, _tally(0, "음", "T1"), counted)
    assert counted == {0: "T1"}


def test_counted_tier_must_match_the_label_tier():
    take = {"words": [_w("음", 0, 200, True, "T2", "u9")]}
    with pytest.raises(ValueError, match="층 불일치"):
        tool._collect(take, _tally(0, "음", "T1"), {})


def test_tally_without_a_matching_word_stops_the_evaluation():
    take = {"words": [_w("음", 0, 200, True, "T1")]}
    with pytest.raises(RuntimeError, match="맞는 단어가 없다"):
        tool._collect(take, _tally(0, "어", "T1"), {})


# --------------------------------------------------------------------------
# 커밋된 결과 파일
# --------------------------------------------------------------------------


def test_committed_result_was_made_with_the_current_criteria():
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    assert result["criteria_version"] == criteria_version(DEFAULT), (
        f"결과 파일이 오래됐다. 다시 만든다: {REGENERATE}"
    )


def test_committed_result_is_what_the_tool_makes_from_the_committed_data():
    data = json.loads(WORDS.read_text(encoding="utf-8"))
    fresh = json.loads(tool.dumps(tool.run(data)))
    committed = json.loads(RESULT.read_text(encoding="utf-8"))
    assert committed == fresh, f"결과 파일이 지금 코드 · 데이터와 다르다: {REGENERATE}"
    assert committed["streaming_matches_single_call"] is True
    assert [s["gap_ms"] for s in committed["sweep_gap_ms"]] == tool.SWEEP_GAP_MS
    # 기본 기준값(400 ms)의 민감도 행은 본 결과와 같아야 한다
    base = next(s for s in committed["sweep_gap_ms"] if s["gap_ms"] == DEFAULT.gap_ms)
    assert base["by_tier"] == committed["by_tier"] and base["all"] == committed["all"]
