"""말의 구성(규칙): 대본 밖 구간 · 범주 · 위치 · 화면 목록 · 반복 · 다른 장에서 말함."""

from types import SimpleNamespace as NS

from pitch_coach_ai.features.script_coverage.shared.text import normalize_script
from pitch_coach_ai.features.script_coverage.stt_evaluation import speech_mix as speech_mix_module
from pitch_coach_ai.features.script_coverage.stt_evaluation.align import positioned_tokens
from pitch_coach_ai.features.script_coverage.stt_evaluation.schemas import (
    MIX_CATEGORIES,
    Alignment,
)
from pitch_coach_ai.features.script_coverage.stt_evaluation.speech_mix import (
    build_offscript,
    build_speech_mix,
    mix_display,
    said_in_other_slides,
)

THIS = "안녕하세요. 저희 서비스는 발표 연습을 도와줍니다. 이용자는 매주 연습합니다."
OTHER = "결제 구조는 구독 모델입니다. 월 구독료는 오천 원입니다. 환불은 언제든 가능합니다."


def rubric(script: str, kps=()):
    norm = normalize_script(script)
    return NS(
        normalized_script=norm.text,
        sentences=norm.sentences,
        core_claim="발표 연습 서비스",
        key_points=[NS(id=i, sentence_indices=idx) for i, idx in kps],
    )


def run(stt: str, aligned: list[list[int]], others=None, script=THIS):
    norm = normalize_script(stt)
    ptokens = positioned_tokens(norm)
    alignments = [
        Alignment(sentence_index=i, stt_ids=ids, coverage=1.0) for i, ids in enumerate(aligned)
    ]
    off = build_offscript(rubric(script), norm, ptokens, alignments, others or {})
    return norm, ptokens, off


def text_of(norm, span):
    return norm.sentences[span.stt_sentence_index].text[span.start : span.end]


def test_unaligned_greeting_not_in_script_is_procedural():
    norm, _, off = run(
        "감사합니다. 저희 서비스는 발표 연습을 도와줍니다.",
        [[], [1]],
        script="저희 서비스는 발표 연습을 도와줍니다.",
    )
    [span] = off.spans
    assert (span.id, span.category, span.source, span.display_category) == (
        "O1",
        "procedural",
        "rule",
        "procedural",
    )
    assert (span.stt_sentence_index, text_of(norm, span)) == (0, "감사합니다.")  # 문장 전체


def test_greeting_that_is_in_the_script_is_script_content():
    _, _, off = run("안녕하세요. 저희 서비스는 발표 연습을 도와줍니다.", [[], [1]])
    assert off.spans == []
    assert off.state == "ok"


def test_tail_inside_an_aligned_sentence_is_not_a_span_by_default():
    # 짝 있는 문장 안의 꼬리는 대부분 바꿔 말한 대본 내용이라 기본으로 후보로 보지 않는다 (config)
    stt = "저희 서비스는 발표 연습을 도와줍니다. 이용자는 매주 연습하고 어제 점심에 비빔밥을 먹었습니다."
    _, _, off = run(stt, [[], [0], [1]])
    assert off.spans == []


def test_off_script_tail_inside_an_aligned_sentence(monkeypatch):
    monkeypatch.setattr(speech_mix_module, "OFFSCRIPT_IN_SENTENCE", True)
    stt = "저희 서비스는 발표 연습을 도와줍니다. 이용자는 매주 연습하고 어제 점심에 비빔밥을 먹었습니다."
    norm, _, off = run(stt, [[], [0], [1]])
    [span] = off.spans
    assert (span.category, span.stt_sentence_index, span.tokens) == ("off_script", 1, 4)
    assert text_of(norm, span) == "어제 점심에 비빔밥을 먹"
    assert "OFFSCRIPT_DISABLED" in off.reason_codes


def test_single_foreign_token_in_aligned_sentence_is_not_a_span():
    _, _, off = run("저희 서비스는 발표 연습을 도와줍니다.", [[0]])
    assert off.spans == []


def test_other_slide_sentence_gets_number():
    stt = "저희 서비스는 발표 연습을 도와줍니다. 결제 구조는 구독 모델입니다."
    others = {2: rubric(OTHER)}
    _, _, off = run(stt, [[], [0]], others)
    [span] = off.spans
    assert (span.category, span.other_slide_number, span.display_category) == (
        "other_slide",
        2,
        "other_slide",
    )


def test_other_slide_ties_go_to_the_smaller_number():
    stt = "저희 서비스는 발표 연습을 도와줍니다. 결제 구조는 구독 모델입니다."
    others = {5: rubric(OTHER), 3: rubric(OTHER)}
    _, _, off = run(stt, [[], [0]], others)
    assert off.spans[0].other_slide_number == 3


def test_empty_stt_is_not_applicable():
    _, ptokens, off = run("", [])
    assert (off.state, off.reason_codes, off.spans) == ("not_applicable", ["NO_SPEECH"], [])
    mix = build_speech_mix(off, ptokens, None)
    assert (mix.state, mix.reason_codes, mix.tokens, mix.display) == (
        "not_applicable",
        ["NO_SPEECH"],
        0,
        [],
    )
    assert list(mix.categories) == list(MIX_CATEGORIES)


def test_mix_sums_to_total_and_display_sums_to_one(monkeypatch):
    monkeypatch.setattr(
        speech_mix_module, "OFFSCRIPT_IN_SENTENCE", True
    )  # 세 범주를 한 번에 보려고
    stt = (
        "감사합니다. 저희 서비스는 발표 연습을 도와줍니다. "
        "이용자는 매주 연습하고 어제 점심에 비빔밥을 먹었습니다. 결제 구조는 구독 모델입니다."
    )
    norm, ptokens, off = run(stt, [[], [1], [2]], {2: rubric(OTHER)})
    mix = build_speech_mix(off, ptokens, None)
    assert mix.tokens == len(ptokens)
    assert sum(mix.categories.values()) == mix.tokens
    assert all(c in mix.categories for c in MIX_CATEGORIES)
    assert mix.categories["script"] == mix.tokens - sum(s.tokens for s in off.spans)
    assert round(sum(i.share for i in mix.display), 3) == 1.0
    assert [i.category for i in mix.display] == [c for c in MIX_CATEGORIES if mix.categories[c]]
    assert [s.id for s in off.spans] == [f"O{i}" for i in range(1, len(off.spans) + 1)]
    cats = [(s.category, s.tokens) for s in off.spans]
    assert cats == [("procedural", 1), ("off_script", 4), ("other_slide", 4)]


def test_mix_display_merges_unvalidated_and_fixes_rounding():
    items = mix_display({"script": 1, "related_aside": 1, "unrelated": 1, "undetermined": 0})
    assert [(i.category, i.tokens) for i in items] == [("script", 1), ("off_script", 2)]
    assert [i.share for i in items] == [0.333, 0.667]
    thirds = mix_display({"script": 1, "other_slide": 1, "procedural": 1})
    assert round(sum(i.share for i in thirds), 3) == 1.0
    assert mix_display({}) == []


def test_repetitions_count_extra_repeats_after_fillers_are_removed():
    _, ptokens, off = run("저희 서비스는 발표 연습을 도와줍니다.", [[0]], script=THIS)
    assert build_speech_mix(off, ptokens, None).repetitions.count == 0
    raw = "그 그 그 결과는 음 좋아요 좋아요 그리고 그 음 그 결과"
    assert (
        build_speech_mix(off, ptokens, raw).repetitions.count == 4
    )  # 그 그 그 → 2, 좋아요 좋아요 → 1, 그 음 그(간투사를 지운 뒤) → 1


def test_same_input_same_output():
    stt = "감사합니다. 저희 서비스는 발표 연습을 도와줍니다. 이용자는 매주 연습합니다 어제 점심에 비빔밥을 먹었습니다."
    a, b = run(stt, [[], [1], [2]])[2], run(stt, [[], [1], [2]])[2]
    assert a == b


def test_said_in_other_slides():
    others = rubric(OTHER, kps=[("KP1", [0]), ("KP2", [1]), ("KP3", [2])])
    stt = "결제 구조는 구독 모델입니다 월 구독료는 오천 원입니다."
    norm, ptokens, off = run(stt, [], {2: others})
    assert [s.category for s in off.spans] == ["other_slide"] * 2
    found = said_in_other_slides(
        {1: (off, [s.text for s in norm.sentences]), 2: (off.model_copy(update={"spans": []}), [])},
        {1: rubric(THIS), 2: others},
    )
    assert found == {(2, "KP1"): [1], (2, "KP2"): [1]}  # KP3 은 말하지 않았다
