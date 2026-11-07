"""설정 · 고정 문구 시험 (기능 문서 6-4 '설정은 깊게 불변', 7-9 · 7-10, 9절)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pitch_coach_ai.features.chat_agent import templates
from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG, load_config
from pitch_coach_ai.features.chat_agent.version import FEATURE_VERSION

#: 기능 문서. 연구 모듈에서는 저장소 루트의 docs/ai/features/chat-agent.md 다(서비스로 옮기면 경로가 바뀐다)
DOC = Path(__file__).resolve().parents[5] / "docs" / "ai" / "features" / "chat-agent.md"


def test_fm_L4_12_config_is_deeply_immutable() -> None:
    with pytest.raises(TypeError):
        DEFAULT_CONFIG.direction["cpm"] = "HIGHER"  # type: ignore[index]
    with pytest.raises(TypeError):
        DEFAULT_CONFIG.goal_metrics["cpm"] = None  # type: ignore[index]
    with pytest.raises(AttributeError):
        DEFAULT_CONFIG.agreement_words.append("좋네요")  # type: ignore[attr-defined]
    with pytest.raises(Exception):  # noqa: B017  # frozen 모델: 속성 대입 금지
        DEFAULT_CONFIG.max_tool_calls = 99  # type: ignore[misc]


def test_config_hash_and_versions_are_stable() -> None:
    assert DEFAULT_CONFIG.config_hash() == load_config().config_hash()
    assert load_config(max_tool_calls=6).config_hash() != DEFAULT_CONFIG.config_hash()
    assert DEFAULT_CONFIG.criteria_version() == f"{FEATURE_VERSION}+{DEFAULT_CONFIG.config_hash()}"
    assert re.fullmatch(r"goal-algo-1\+[0-9a-f]{12}", DEFAULT_CONFIG.goal_rule_version())
    # 한도 값을 바꿔도 목표 판정 규칙 판은 그대로다(저장된 목표에 고정되는 값만 들어감)
    assert load_config(max_tool_calls=6).goal_rule_version() == DEFAULT_CONFIG.goal_rule_version()
    assert load_config(goal_metrics={"audience_ratio": {"min_delta": 0.1}}).goal_rule_version() != (
        DEFAULT_CONFIG.goal_rule_version()
    )


def test_limits_match_feature_doc_values() -> None:
    c = DEFAULT_CONFIG
    assert (
        c.max_model_calls,
        c.reserved_answer_calls,
        c.max_tool_calls,
        c.max_tools_per_response,
    ) == (18, 2, 12, 8)
    assert (c.max_turn_calls, c.max_actions, c.max_save_actions, c.max_reassess_per_turn) == (
        6,
        5,
        1,
        1,
    )
    assert (c.max_input_tokens, c.max_output_tokens) == (72_000, 8_000)
    assert (c.turn_seconds, c.call_seconds, c.reserved_answer_seconds) == (120.0, 45.0, 24.0)
    assert (c.max_request_bytes, c.max_context_bytes, c.max_state_bytes) == (
        2 * 1024 * 1024,
        524_288,
        1_048_576,
    )
    assert (c.max_line_bytes, c.max_response_bytes, c.tail_reserve_bytes) == (
        2 * 1024 * 1024,
        16 * 1024 * 1024,
        4096,
    )
    assert (c.max_diagnostics_bytes, c.min_secret_bytes, c.max_json_depth) == (65_536, 32, 32)
    assert (c.state_ttl_seconds, c.state_ttl_ask_seconds) == (600, 2_100)


@pytest.mark.skipif(not DOC.exists(), reason="기능 문서는 연구 모듈 위치에서만 읽는다")
def test_system_texts_are_feature_doc_strings() -> None:
    """SYSTEM 문장은 7-9 · 7-10 의 고정 문구만이다(6-9 SYSTEM_TEXT)."""
    doc = DOC.read_text(encoding="utf-8")
    missing = [t.value for t in templates.SystemText if f'"{t.value}"' not in doc]
    assert missing == []


@pytest.mark.skipif(not DOC.exists(), reason="기능 문서는 연구 모듈 위치에서만 읽는다")
def test_objectivity_templates_are_feature_doc_strings() -> None:
    doc = DOC.read_text(encoding="utf-8")
    for tpl in (
        templates.CONTRADICTED,
        templates.PARTLY,
        templates.UNVERIFIABLE,
        templates.UNCOVERED_CLAUSE,
        templates.OTHER_SPEAKER,
        templates.PREFERENCE,
        templates.SUPPORTED,
        templates.PREFERENCE_FITS,
        templates.KNOWLEDGE_SUPPORTED,
    ):
        doc_form = tpl.replace("{evidence}", "{근거}").replace("{speaker}", "{화자}")
        assert f'"{doc_form}"' in doc, tpl


def test_assessment_sentence_picks_template_by_type_and_speaker() -> None:
    f = templates.assessment_sentence
    kw = dict(quote="시선 잘 봤어", evidence="Take 5 청중 시선 31%, 우선 개선")
    assert f(
        type_="MEASURED", speaker="USER", speaker_label=None, verdict="CONTRADICTED", **kw
    ) == ("말씀하신 '시선 잘 봤어'는 기록과 달라요: Take 5 청중 시선 31%, 우선 개선.")
    assert f(
        type_="MEASURED", speaker="USER", speaker_label=None, verdict="PARTLY", **kw
    ).startswith("말씀하신 '시선 잘 봤어'는 기록에서 일부만 확인돼요")
    assert f(
        type_="MEASURED", speaker="OTHER", speaker_label="친구", verdict="CONTRADICTED", **kw
    ).startswith("친구의 말씀은 확인할 수 없지만")
    assert f(
        type_="PREFERENCE", speaker="USER", speaker_label=None, verdict="PARTLY", **kw
    ).startswith("말씀하신 방식은 존중해요")
    # 근거가 없으면 단정하지 않는다
    assert f(
        type_="MEASURED",
        speaker="USER",
        speaker_label=None,
        verdict="CONTRADICTED",
        quote="q",
        evidence="",
    ) == ("말씀하신 'q'는 이 기록으로는 확인할 수 없어요.")
    assert f(
        type_="MEASURED",
        speaker="USER",
        speaker_label=None,
        verdict="CONTRADICTED",
        pressure=True,
        **kw,
    ).startswith("답답하실 수 있지만 기록은")


@pytest.mark.parametrize(
    "bad",
    [
        {"audience_ratio": {"min_delta": -0.05}},
        {"audience_ratio": {"min_delta": 0}},
        {"audience_ratio": {"min_delta": float("inf")}},
        {"cpm": {"min_delta": 0.5}},
        {"audience_ratio": {"operators": ["lt_base"]}},
    ],
)
def test_fm_L4_12_invalid_goal_metric_config_is_rejected_at_start(bad) -> None:
    with pytest.raises(ValueError):
        load_config(goal_metrics=bad)


def test_goal_rule_version_includes_algorithm_version(monkeypatch) -> None:
    from pitch_coach_ai.features.chat_agent import config as cfg_mod

    before = DEFAULT_CONFIG.goal_rule_version()
    monkeypatch.setattr(cfg_mod, "GOAL_ALGORITHM_VERSION", "goal-algo-2")
    assert DEFAULT_CONFIG.goal_rule_version() != before


def test_forbidden_claims_cover_doc_example_and_variants() -> None:
    words = DEFAULT_CONFIG.forbidden_claims
    for sentence in (
        "미션을 바꿔 뒀어요",
        "점수를 변경했어요",
        "공식 미션을 수정했어요",
        "발표가 시작됐어요",
    ):
        assert any(w in sentence for w in words), sentence
    for ok in ("점수는 바뀌지 않았어요", "미션은 리뷰가 정해요"):
        assert not any(w in ok for w in words), ok


def test_knowledge_supported_uses_guide_wording() -> None:
    s = templates.assessment_sentence(
        type_="KNOWLEDGE",
        speaker="USER",
        speaker_label=None,
        verdict="SUPPORTED",
        quote="쉼이 중요하다",
        evidence="자료 g-05",
    )
    assert "발표법 자료와 맞아요" in s and "기록과" not in s


def test_fill_chooses_particle_by_final_consonant() -> None:
    """7-9 · 7-10 문구의 '{x}으로' · '{x}예요'는 값의 받침에 맞춰 읽힌다."""
    from pitch_coach_ai.features.chat_agent.templates import ACTION_SUCCESS, PRESSURE, fill

    tpl = ACTION_SUCCESS["FETCH_PITCH"]
    assert fill(tpl, title="졸업 발표") == "졸업 발표로 범위를 바꿨어요."
    assert fill(tpl, title="학회 발표 연습") == "학회 발표 연습으로 범위를 바꿨어요."
    assert fill(tpl, title="최종 발표 (2차)") == "최종 발표 (2차)로 범위를 바꿨어요."
    assert fill(tpl, title="데모데이 3") == "데모데이 3으로 범위를 바꿨어요."
    assert fill(PRESSURE, evidence="우선 개선") == "답답하실 수 있지만 기록은 우선 개선이에요."
    assert (
        fill(PRESSURE, evidence="Take 5 청중 시선 28%")
        == "답답하실 수 있지만 기록은 Take 5 청중 시선 28%예요."
    )


def test_fill_does_not_reinterpret_braces_in_values() -> None:
    from pitch_coach_ai.features.chat_agent.templates import ACTION_SUCCESS, fill

    assert (
        fill(ACTION_SUCCESS["FETCH_PITCH"], title="발표 {A} {0:>3}")
        == "발표 {A} {0:>3}으로 범위를 바꿨어요."
    )  # 끝 숫자 3(삼)은 받침 있음
