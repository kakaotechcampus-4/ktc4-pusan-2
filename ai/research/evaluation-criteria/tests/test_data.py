"""평가 데이터 data/examples.json — 정답이 계약에 맞고 13절이 말하는 경우를 담는지 본다."""

import json
from pathlib import Path

import pytest

from pitch_coach_ai.features.evaluation_criteria import check_targets
from pitch_coach_ai.features.evaluation_criteria.config import DEFAULT
from pitch_coach_ai.features.evaluation_criteria.schemas import Criteria, EvaluationCriteriaRequest, Target
from pitch_coach_ai.features.evaluation_criteria.prompts import SYSTEM_PROMPT

DATA = Path(__file__).resolve().parents[1] / "data" / "examples.json"
EXAMPLES = json.loads(DATA.read_text(encoding="utf-8"))
IDS = [e["case"] for e in EXAMPLES]


def test_every_example_has_text_and_expected():
    for e in EXAMPLES:
        assert set(e) == {"case", "text", "expected"}
        assert set(e["expected"]) == {"values", "targets", "excluded"}


@pytest.mark.parametrize("example", EXAMPLES, ids=IDS)
def test_expected_answers_follow_the_contract(example):
    EvaluationCriteriaRequest(standard_text=example["text"])
    Criteria.model_validate(example["expected"]["values"])
    # 정답 목표는 metric · operator · value 만 둔다 (14절 숫자 목표 추출 정확도)
    targets = [Target(display="정답", **t) for t in example["expected"]["targets"]]
    assert check_targets(targets, ["정답"]) == targets


@pytest.mark.parametrize("example", EXAMPLES, ids=IDS)
def test_every_target_has_a_value_for_its_element(example):
    # 목표가 있으면 그 요소의 values 도 null 이 아니다
    element_of = {m: e for e, metrics in DEFAULT.element_metrics.items() for m in metrics}
    values = example["expected"]["values"]
    for t in example["expected"]["targets"]:
        assert values[element_of[t["metric"]]] is not None


def test_examples_cover_what_section_13_asks():
    values = [e["expected"]["values"] for e in EXAMPLES]
    targets = [t for e in EXAMPLES for t in e["expected"]["targets"]]
    metrics = {t["metric"] for t in targets}
    operators = {t["operator"] for t in targets}

    # 요소마다: 9개 요소가 한 번 이상 나온다
    assert all(any(v[k] is not None for v in values) for k in Criteria.model_fields)
    # script_used 세 값
    assert {v["script_used"] for v in values} == {True, False, None}
    # 숫자 목표: 지표 9개와 연산자 4개가 한 번 이상 나온다
    assert metrics == {
        "cpm", "pause_count", "long_silence_count", "voice_diff_db", "filler_count",
        "filler_per_min", "audience_ratio", "reading_ratio", "content_coverage",
    }  # fmt: skip
    assert operators == {"LTE", "GTE", "LT", "GT"}
    # 숫자 없는 기준 · 측정할 수 없는 기준만 있는 문장
    assert any(e["expected"]["targets"] == [] and any(e["expected"]["values"].values()) for e in EXAMPLES)
    assert any(not any(v is not None for v in e["expected"]["values"].values()) for e in EXAMPLES)


@pytest.mark.parametrize("example", EXAMPLES, ids=IDS)
def test_examples_are_not_in_the_prompt(example):
    # 프롬프트 예시와 같은 문장이면 평가 점수가 부풀려진다
    assert example["text"] not in SYSTEM_PROMPT
