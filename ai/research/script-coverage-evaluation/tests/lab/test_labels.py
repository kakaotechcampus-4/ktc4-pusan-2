"""정답 라벨 비교 도구: 라벨 → 정답 변환, 표 지표 계산 (작은 입력으로)."""

import pandas as pd

from coverage_lab.stt_evaluation.checks import (
    LABEL_ANSWER,
    accuracy_row,
    confidence_check,
    conflict_check,
    conflict_pairs,
    first_status_check,
    unchecked_errors,
)
from coverage_lab.stt_evaluation.labels import (
    FACT_PRED,
    coverage_from,
    fact_accuracy,
    kp_accuracy,
    label_pairs,
    load_labels,
    pair_match,
    rule_only_sentence,
    truth_fact,
    truth_key_point,
)
from coverage_lab.stt_evaluation.stability import spread
from script_coverage.shared.facts import CriticalFact
from script_coverage.stt_evaluation.schemas import SentenceResult, SimilarItem


def _label(status, **extra):
    return {"status": status, "dropped_values": [], "changed_values": [], **extra}


def test_truth_key_point_from_sentence_labels():
    labels = {
        0: _label("verbatim"),
        1: _label("paraphrased"),
        2: _label("partial"),
        3: _label("missing"),
        4: _label("contradicted"),
    }
    assert truth_key_point([0, 1], labels) == "covered"
    assert truth_key_point([0, 2], labels) == "partial"
    assert truth_key_point([3], labels) == "missing"
    assert truth_key_point([0, 3], labels) == "partial"
    assert truth_key_point([0, 4], labels) == "contradicted"  # 모순이 하나라도 있으면
    assert truth_key_point([9], labels) == "missing"  # 라벨이 없는 문장만 있으면


def _fact(type_, value, normalized, sentences):
    return CriticalFact.model_construct(
        type=type_, value=value, normalized=normalized, sentence_indices=sentences
    )


def test_truth_fact_follows_label_value_lists():
    pct = _fact("percentage", "76%", "76%", [0])
    assert truth_fact(pct, {0: _label("verbatim")}) == "matched"
    changed = _label("contradicted", changed_values=[{"script": "76%", "stt": "67퍼센트"}])
    assert truth_fact(pct, {0: changed}) == "mismatched"
    asr = _label("verbatim", asr_errors=[{"script": "76%", "stt": "67퍼센트"}])
    assert truth_fact(pct, {0: asr}) == "asr"
    approx = _label("verbatim", approximated_values=[{"script": "76%", "stt": "칠십 퍼센트"}])
    assert truth_fact(pct, {0: approx}) == "approximate"
    assert truth_fact(pct, {0: _label("partial", dropped_values=["76%"])}) == "missing"
    assert truth_fact(pct, {0: _label("missing")}) == "missing"
    assert truth_fact(pct, {}) == "missing"  # 이 사실의 문장에 라벨이 없다
    # 같은 사실이 여러 문장에 나오면 한 번이라도 맞게 말했으면 matched
    twice = _fact("percentage", "76%", "76%", [0, 1])
    assert truth_fact(twice, {0: _label("missing"), 1: _label("verbatim")}) == "matched"


def test_truth_fact_compares_names_by_containment():
    name = _fact("proper_noun", "SeatFlow", "seatflow", [0])
    label = _label("verbatim", asr_errors=[{"script": "SeatFlow", "stt": "시트플로우"}])
    assert truth_fact(name, {0: label}) == "asr"


def _sentence(coverage, fact_statuses):
    return SentenceResult.model_construct(lexical_coverage=coverage, fact_statuses=fact_statuses)


def test_rule_only_sentence_baseline():
    assert rule_only_sentence(_sentence(0.9, {})) == "said"
    assert rule_only_sentence(_sentence(0.9, {"CF1": "matched", "CF2": "sound_alike"})) == "said"
    assert rule_only_sentence(_sentence(0.9, {"CF1": "mismatched"})) == "contradicted"
    assert rule_only_sentence(_sentence(0.9, {"CF1": "missing"})) == "partial"
    assert rule_only_sentence(_sentence(0.9, {"CF1": "approximate"})) == "partial"
    assert rule_only_sentence(_sentence(0.5, {})) == "partial"
    assert rule_only_sentence(_sentence(0.1, {})) == "missing"


def test_coverage_from_weights_and_floor():
    assert coverage_from([]) == 0.0
    assert coverage_from([("covered", "critical"), ("missing", "normal")]) == 3 / 4
    assert coverage_from([("covered", "high"), ("partial", "high")]) == 0.75
    assert coverage_from([("contradicted", "critical")]) == 0.0  # 음수가 되면 0


def test_accuracy_helpers():
    table = pd.DataFrame(
        {
            "정답": ["covered", "covered", "missing", "contradicted"],
            "최종": ["covered", "missing", "missing", "covered"],
        }
    )
    assert kp_accuracy(table, "최종") == {"정확도": 0.5, "심각한 오판 비율": 0.5}
    facts = pd.DataFrame(
        {
            "정답": ["matched", "asr", "asr", "mismatched"],
            "최종": ["matched", "matched", "held", "missing"],
        }
    )
    # held 는 빼고, 인식 오류(asr)를 맞게 받아들인 것(matched)은 맞음: 3개 중 2개
    assert fact_accuracy(facts) == round(2 / 3, 3)
    assert FACT_PRED["sound_alike"] == "held" and FACT_PRED["unverified"] == "missing"


def _sentence_table():
    return pd.DataFrame(
        {
            "take_id": ["t"] * 4,
            "slide": [1, 1, 1, 2],
            "sentence": [0, 1, 2, 0],
            "정답": ["said", "contradicted", "said", "missing"],
            "LLM 1차": ["said", "said", "partial", "missing"],
            "최종": ["said", "contradicted", "partial", "missing"],
            "충돌": [[], ["sound_alike", "contradicted"], [], ["low_confidence"]],
            "확신도": ["high", "high", "high", "low"],
            "단어 비율": [0.9, 0.8, 0.5, 0.0],
            "근거 단어 비율": [0.9, 0.8, 0.4, 0.0],
        }
    )


def test_conflict_tables():
    table = _sentence_table()
    checked = conflict_check(table)
    # 두 조건이 같이 뜬 문장(1차 오답 → 교차 검증이 바로잡음)
    assert checked.loc["sound_alike"].to_dict() == {
        "발동": 1,
        "단독": 0,
        "1차 오답": 1,
        "단독 1차 오답": 0,
        "바로잡음": 1,
        "망침": 0,
    }
    # 하나만 뜬 문장(1차가 맞았고 그대로 맞음)
    assert checked.loc["low_confidence"].to_dict() == {
        "발동": 1,
        "단독": 1,
        "1차 오답": 0,
        "단독 1차 오답": 0,
        "바로잡음": 0,
        "망침": 0,
    }
    assert first_status_check(table).loc["said"].to_dict() == {
        "문장": 2,
        "1차 오답": 1,
        "다시 확인": 1,
        "다시 확인한 오답": 1,
    }
    assert conflict_pairs(table).to_dict(orient="records") == [
        {"조건 1": "contradicted", "조건 2": "sound_alike", "함께 뜬 문장": 1}
    ]
    assert list(confidence_check(table).index) == ["high", "low"]
    missed = unchecked_errors(table)  # 충돌 검사가 다시 확인하지 않은 1차 오답
    assert list(zip(missed["slide"], missed["sentence"], strict=True)) == [(1, 2)]


def test_accuracy_row_shape():
    kp = pd.DataFrame({"정답": ["covered"], "최종": ["covered"]})
    fact = pd.DataFrame({"정답": ["matched", "asr"], "최종": ["matched", "held"]})
    slide = pd.DataFrame({"정답 coverage": [0.5], "최종 coverage": [0.75]})
    row = accuracy_row(_sentence_table(), kp, fact, slide)
    assert row["대본 문장 정확도"] == 0.75
    assert row["Key Point 정확도"] == 1.0
    assert row["보류한 사실"] == 1
    assert row["슬라이드 내용 점수 평균 오차"] == 0.25


def test_label_answer_maps_cause_to_user_answer():
    assert LABEL_ANSWER == {"asr_error": "as_script", "speaker_error": "as_stt"}


def test_pair_match_ignores_spacing_and_punctuation():
    item = SimilarItem(
        item_id="R1",
        kind="number",
        script_text="약 18%",
        stt_text="약 이십팔 퍼센트",
        script_sentence_index=0,
        script_span=(0, 1),
        stt_sentence_index=0,
        stt_span=(0, 1),
        stt_raw_span=None,
        key_point_ids=[],
        signals=[],
        rule_guess="asr_error",
    )
    assert pair_match(item, "약 18%", "약 이십팔 퍼센트")
    assert pair_match(item, "18%", "이십팔 퍼센트")
    assert not pair_match(item, "30분", "삼 분")


def test_real_label_file_is_read():
    labels = load_labels("가상대본1_take4")
    assert labels[6][2]["status"] == "contradicted"
    assert labels[6][2]["changed_values"] == [{"script": "76%", "stt": "67퍼센트"}]
    assert ("speaker_error", "76%", "67퍼센트") in label_pairs("가상대본1_take4")[6]


def test_spread():
    assert spread([]) == 0.0
    assert spread([3.0, 7.5, 5.0]) == 4.5
