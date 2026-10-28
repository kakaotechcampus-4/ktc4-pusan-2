"""기준값 config.py — 요소 → 지표 연결이 스키마 · 프롬프트와 같은지 본다 (evaluation-criteria.md 5-3).

연결의 원본은 config.py 다. 스키마의 지표 enum 과 프롬프트의 평가 요소 표가 어긋나면 이 테스트가 깨진다.
"""

import re
from typing import get_args

from pitch_coach_ai.features.evaluation_criteria.config import DEFAULT
from pitch_coach_ai.features.evaluation_criteria.prompts import SYSTEM_PROMPT
from pitch_coach_ai.features.evaluation_criteria.schemas import Criteria, Metric

METRICS = set(get_args(Metric))


def test_every_element_except_script_used_has_metrics():
    assert set(DEFAULT.element_metrics) == set(Criteria.model_fields) - {"script_used"}


def test_every_metric_belongs_to_exactly_one_element():
    linked = [m for metrics in DEFAULT.element_metrics.values() for m in metrics]
    assert sorted(linked) == sorted(METRICS)


def test_value_ranges_cover_every_metric_once():
    # voice_diff_db 는 평소 목소리 대비라 범위가 없다
    groups = [
        DEFAULT.ratio_metrics,
        DEFAULT.count_metrics,
        DEFAULT.positive_metrics,
        {"voice_diff_db"},
    ]
    assert sorted(m for g in groups for m in g) == sorted(METRICS)


def test_prompt_element_table_matches_config():
    rows = {}
    for line in SYSTEM_PROMPT.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and cells[0] in Criteria.model_fields:
            # 소문자 · 밑줄로만 된 단어가 지표 이름이다 ("dB" 같은 단위는 빼고)
            rows[cells[0]] = tuple(re.findall(r"\b[a-z][a-z_]*\b", cells[-1]))
    assert set(rows) == set(Criteria.model_fields)
    assert rows.pop("script_used") == ()
    assert rows == DEFAULT.element_metrics


def test_prompt_display_limit_matches_config():
    assert f"{DEFAULT.max_display_chars}자 이내" in SYSTEM_PROMPT
    assert f"{DEFAULT.max_display_criteria}개까지" in SYSTEM_PROMPT
