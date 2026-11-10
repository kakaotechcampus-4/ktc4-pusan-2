"""대본 밖 구간의 LLM 분류: 메시지 · 반영 · 빠진 id 검사 (가짜 LLM)."""

from types import SimpleNamespace as NS

import pytest

from pitch_coach_ai.features.script_coverage.shared.llm_step import llm_error_code
from pitch_coach_ai.features.script_coverage.shared.text import normalize_script
from pitch_coach_ai.features.script_coverage.stt_evaluation.align import positioned_tokens
from pitch_coach_ai.features.script_coverage.stt_evaluation.offscript import (
    IncompleteOffscript,
    OffscriptItem,
    OffscriptJudgment,
    apply_offscript_judgments,
    check_offscript,
    classify_offscript,
    offscript_config_hash,
    offscript_message,
)
from pitch_coach_ai.features.script_coverage.stt_evaluation.schemas import Alignment
from pitch_coach_ai.features.script_coverage.stt_evaluation.speech_mix import (
    build_offscript,
    build_speech_mix,
)

from ..fakes import FakeLLM

SCRIPT = "저희 서비스는 발표 연습을 도와줍니다."
STT = (
    "저희 서비스는 발표 연습을 도와줍니다. 어제 점심에 비빔밥을 먹었습니다. 오늘 날씨 정말 덥네요."
)


def setup():
    norm = normalize_script(SCRIPT)
    rubric = NS(
        normalized_script=norm.text,
        sentences=norm.sentences,
        core_claim="발표 연습 서비스",
        key_points=[],
    )
    stt = normalize_script(STT)
    off = build_offscript(
        rubric,
        stt,
        positioned_tokens(stt),
        [Alignment(sentence_index=0, stt_ids=[0], coverage=1.0)],
        {},
    )
    return rubric, stt, off


def answer(*pairs):
    return OffscriptJudgment(
        items=[OffscriptItem(span_id=i, category=c, reason="r") for i, c in pairs]
    )


def test_message_lists_the_target_spans():
    rubric, stt, off = setup()
    assert [s.id for s in off.spans] == ["O1", "O2"]
    message = offscript_message(rubric, off, [s.text for s in stt.sentences])
    assert "발표 연습 서비스" in message and "[S0] " + SCRIPT in message
    assert "[O1] 어제 점심에 비빔밥을 먹" in message and "[O2] 오늘 날씨" in message


def test_no_message_without_targets():
    rubric, stt, _ = setup()
    empty = build_offscript(rubric, normalize_script(""), [], [], {})
    assert offscript_message(rubric, empty, []) is None


def test_classify_calls_the_llm_once_and_apply_changes_the_spans():
    rubric, stt, off = setup()
    llm = FakeLLM(answer(("O1", "related_aside"), ("O2", "procedural")))
    result = classify_offscript(
        offscript_message(rubric, off, [s.text for s in stt.sentences]), llm
    )
    assert llm.calls == 1
    check_offscript(off, result)
    new = apply_offscript_judgments(off, result)
    assert [(s.category, s.display_category, s.source) for s in new.spans] == [
        ("related_aside", "off_script", "llm"),
        ("procedural", "procedural", "llm"),
    ]
    assert new.reason_codes == ["NOT_VALIDATED"]
    assert "OFFSCRIPT_DISABLED" in off.reason_codes  # 원본은 그대로
    mix = build_speech_mix(new, positioned_tokens(stt), None)
    assert mix.categories["related_aside"] == new.spans[0].tokens
    assert [i.category for i in mix.display] == ["script", "procedural", "off_script"]
    assert sum(mix.categories.values()) == mix.tokens


@pytest.mark.parametrize(
    "bad",
    [
        answer(("O1", "unrelated")),
        answer(("O1", "unrelated"), ("O1", "unrelated"), ("O2", "unrelated")),
    ],
)
def test_missing_or_duplicate_ids_are_invalid_output(bad):
    _, _, off = setup()
    with pytest.raises(IncompleteOffscript) as e:
        check_offscript(off, bad)
    assert llm_error_code(e.value) == "LLM_INVALID_OUTPUT"


def test_extra_ids_are_ignored_and_ids_are_case_insensitive():
    _, _, off = setup()
    result = answer(("o1", "unrelated"), ("O2", "undetermined"), ("O9", "procedural"))
    check_offscript(off, result)
    assert [s.category for s in apply_offscript_judgments(off, result).spans] == [
        "unrelated",
        "undetermined",
    ]


def test_config_hash_depends_on_model_and_is_stable():
    assert offscript_config_hash("a") == offscript_config_hash("a") != offscript_config_hash("b")
