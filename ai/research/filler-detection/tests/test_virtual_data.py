"""가상 평가 데이터 — 커밋된 words.json 이 원본 · seed 로 만든 것과 같고 정답이 고루 있는지 본다."""

from __future__ import annotations

import importlib.util
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGENERATE = "uv run python tools/make_virtual_data.py --seed 0"

_spec = importlib.util.spec_from_file_location(
    "make_virtual_data", ROOT / "tools" / "make_virtual_data.py"
)
assert _spec and _spec.loader
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


SOURCE = _load(ROOT / "data" / "virtual" / "source.json")
DATA = _load(ROOT / "data" / "virtual" / "words.json")


def test_committed_words_file_is_what_the_tool_makes_from_the_source():
    assert tool.build(SOURCE, DATA["seed"]) == DATA, f"words.json 이 오래됐다: {REGENERATE}"
    assert DATA["seed"] == 0


def test_words_file_text_is_what_the_tool_writes():
    # Windows 체크아웃이 줄 끝을 CRLF 로 바꿔도 통과하도록 LF 로 맞춰 비교한다
    raw = (ROOT / "data" / "virtual" / "words.json").read_bytes().replace(b"\r\n", b"\n")
    assert raw == tool.dumps(DATA).encode("utf-8"), f"파일 모양이 다르다: {REGENERATE}"


def test_source_ids_are_unique_and_every_text_has_a_word():
    ids = [u["id"] for u in SOURCE]
    assert len(ids) == len(set(ids))
    assert all(u["text"].split() for u in SOURCE)


def test_all_utterances_lie_on_one_take_timeline_in_source_order():
    [take] = DATA["takes"]
    assert [u["id"] for u in SOURCE] == list(dict.fromkeys(w["utt"] for w in take["words"]))
    words = take["words"]
    prev_end = -1
    for w in words:
        assert w["start_ms"] <= w["end_ms"]
        assert w["start_ms"] >= prev_end
        prev_end = w["end_ms"]
    assert take["take_end_ms"] == prev_end + 1500
    assert take["utterance_ends"] == sorted(set(take["utterance_ends"]))


def test_each_utterance_start_follows_a_sentence_end_signal_and_a_long_pause():
    [take] = DATA["takes"]
    words, ends = take["words"], set(take["utterance_ends"])
    firsts = [i for i, w in enumerate(words) if i and w["utt"] != words[i - 1]["utt"]]
    assert len(firsts) == len(SOURCE) - 1
    for i in firsts:
        gap = words[i]["start_ms"] - words[i - 1]["end_ms"]
        assert words[i - 1]["end_ms"] in ends
        assert 1_100 - 1 <= gap <= 1_800 + 1


def test_data_size_and_per_tier_balance():
    words = [w for t in DATA["takes"] for w in t["words"]]
    assert 700 <= len(words) <= 1000
    c = Counter((w["label_tier"], w["label_filler"]) for w in words)
    assert c[("T1", True)] >= 40
    assert c[("T2", True)] >= 30 and c[("T2", False)] >= 30
    assert c[("T3", True)] >= 30 and c[("T3", False)] >= 40
    # 사전에 없는 군더더기가 있어야 재현율 밖으로 따로 셀 수 있다
    assert c[(None, True)] >= 3
