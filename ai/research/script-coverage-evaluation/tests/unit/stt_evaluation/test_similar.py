"""비슷한 말 찾기. 기대값은 노트북 원본 코드(셀 17)의 예시를 돌려 얻은 값이다."""

import pytest

from script_coverage.shared.facts import extract_critical_facts
from script_coverage.shared.rubric import EvaluationRubric
from script_coverage.shared.text import normalize_script
from script_coverage.stt_evaluation.align import align_sentences, positioned_tokens, without_spans
from script_coverage.stt_evaluation.normalize import normalize_stt
from script_coverage.stt_evaluation.similar import find_similar_items, phonetic_distance

SCRIPT = "노쇼 비율은 18%에서 7%로 낮아졌습니다. 시범 운영은 30분 단위로 했습니다."
RAW = (
    "음 노조 비율은 십팔 퍼센트에서 칠 퍼센트로 낮아졌습니다 어 시험 운영은 사십 분 단위로 했습니다"
)


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("재고", "제고", 0.16666666666666666),
        ("노쇼", "노조", 0.3333333333333333),
        ("월요일", "금요일", 0.5),
        ("예측", "추천", 0.6666666666666666),
        ("좌석", "자석", 0.16666666666666666),
        ("모델", "모텔", 0.16666666666666666),
        ("가나", "가나", 0.0),
        ("", "가", 1.0),
    ],
)
def test_phonetic_distance(a, b, expected):
    assert phonetic_distance(a, b) == pytest.approx(expected)


@pytest.fixture(scope="module")
def demo():
    script = normalize_script(SCRIPT)
    stt = normalize_stt(RAW)
    s_tokens, t_tokens = positioned_tokens(script), positioned_tokens(stt)
    rubric = EvaluationRubric.model_construct(
        critical_facts=extract_critical_facts(script), key_points=[]
    )
    for i, f in enumerate(rubric.critical_facts, 1):
        f.id = f"CF{i}"
    alignments = align_sentences(
        script,
        [(t.key, t.sentence) for t in s_tokens],
        stt,
        [(t.key, t.sentence) for t in t_tokens],
        ["evidence", "evidence"],
    )
    items = find_similar_items(rubric, script, s_tokens, stt, t_tokens, alignments, [], RAW)
    return {"s_tokens": s_tokens, "alignments": alignments, "items": items}


def test_alignments(demo):
    assert [(a.sentence_index, a.stt_ids, a.coverage) for a in demo["alignments"]] == [
        (0, [0], 0.8),
        (1, [1], 0.6),
    ]


def test_find_similar_items_demo(demo):
    got = [
        (i.item_id, i.kind, i.script_text, i.stt_text, i.script_sentence_index, i.script_span)
        for i in demo["items"]
    ]
    assert got == [
        ("R1", "word", "노쇼", "노조", 0, (0, 2)),
        ("R2", "word", "시범", "시험", 1, (25, 27)),
        ("R3", "number", "30분", "사십 분", 1, (32, 35)),
    ]


def test_similar_items_stt_positions(demo):
    got = [(i.stt_sentence_index, i.stt_span, i.stt_raw_span) for i in demo["items"]]
    assert got == [(0, (0, 2), (2, 4)), (1, (30, 32), (34, 36)), (1, (37, 41), (41, 45))]


def test_raw_spans_slice_the_raw_text(demo):
    assert [RAW[slice(*i.stt_raw_span)] for i in demo["items"]] == ["노조", "시험", "사십 분"]


def test_similar_items_rule_guess_and_signals(demo):
    assert [i.rule_guess for i in demo["items"]] == ["asr_error"] * 3
    assert all(i.signals for i in demo["items"])


def test_without_spans_drops_overlapping_tokens(demo):
    got = without_spans(demo["s_tokens"], [(0, 3)])
    assert got == [
        ("비율", 0),
        ("<percentage:18%>", 0),
        ("<percentage:7%>", 0),
        ("낮", 0),
        ("시범", 1),
        ("운영", 1),
        ("<duration:30분>", 1),
        ("단위", 1),
        ("하", 1),
    ]
