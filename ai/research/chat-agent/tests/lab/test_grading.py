"""독립 채점기 · 근거성 판정기 시험 — 숫자 · 대상 · 문구 · 인용 채점기의 통과 · 실패 예시, 시나리오 기대값을 가상 기록에서 다시 계산 (기능 문서 10-5 · 13-5)."""

from __future__ import annotations

from typing import Any

import pytest

from chat_agent_lab import grading as gr
from chat_agent_lab.service_tests import fakes as F


@pytest.mark.parametrize(
    ("text", "expect", "ok"),
    [
        ("Take 5의 3장 청중 시선 비율은 28%예요.", "28%", True),
        ("비율은 0.28이에요.", "28%", True),
        ("28퍼센트였어요.", "28%", True),
        ("28%예요.", 0.28, True),
        ("30%예요.", "28%", False),
        ("27.5%예요.", "28%", False),  # 보여 준 자리(소수 첫째)까지 기대값 28.0 과 다름
        ("53%예요.", "52.75%", True),  # 반올림한 표시는 같다
        ("52%예요.", "52.75%", False),
        ("62예요.", "62%", False),  # 단위 없는 62 는 62% 가 아니다
        ("88초 걸렸어요.", "1분 28초", True),
        ("1분 28초 걸렸어요.", "88초", True),
        ("4분이에요.", "240초", True),
        ("5번 나왔어요.", "5번", True),
        ("4번 나왔어요.", "5번", False),
        ("평소보다 1.5dB 작게 말했어요.", "-1.5dB", True),
        ("3dB 작았어요.", "-1.5dB", False),
        ("분당 310자예요.", "310", True),
        ("3번째 Take 에서 5번 나왔어요.", "3번", False),  # 3번째는 순서
        ("Take 68 기록이에요.", "68점", False),
        ("5번 나왔어요.", 4.6, False),  # 개수 · 점수는 반올림하지 않는다(0-2 ④)
        ("68점이에요.", "68점", True),
        ("67점이에요.", 67.6, False),
        ("Take 4와 5를 비교했어요.", "5", False),  # 이어 쓴 Take 번호는 숫자가 아니다
        ("2, 3장에서 나왔어요.", "2", False),  # 이어 쓴 장 번호
        ("11월 3일 연습이에요.", "3", False),  # 날짜
    ],
)
def test_number_grader_examples(text: str, expect: Any, ok: bool) -> None:
    assert gr.number_check(text, expect, "contains")[0] is ok
    assert gr.number_check(text, expect, "absent")[0] is (not ok)


def _ans(
    text: str, value: Any, *, unit: str | None, metric: str | None, **ev: Any
) -> dict[str, Any]:
    return {
        "claims": [{"kind": "RECORD", "text": text, "evidence_ids": ["e1"], "source_ids": []}],
        "evidence": [
            {
                "evidence_id": "e1",
                "origin": "RECORD",
                "value": value,
                "unit": unit,
                "metric": metric,
                **ev,
            }
        ],
    }


@pytest.mark.parametrize(
    ("text", "value", "unit", "metric", "ok", "total"),
    [
        ("대본 응시 비율은 62%예요.", 0.6248, "ratio", "script_ratio", 1, 1),
        ("대본 응시 비율은 0.62예요.", 0.6248, "ratio", "script_ratio", 1, 1),
        ("대본 응시 비율은 63%예요.", 0.6248, "ratio", "script_ratio", 0, 1),
        ("발표 시간은 1분 28초예요.", 88000, "ms", "duration_ms", 1, 1),
        ("군더더기는 5번이에요.", 5, "count", "filler_count", 1, 1),
        ("군더더기는 6번이에요.", 5, "count", "filler_count", 0, 1),
        ("평소보다 1.5dB 작아요.", -1.5, "dB", "voice_diff_db", 1, 1),
        ("Take 5 3장 기록이에요.", 0.28, "ratio", "audience_ratio", 0, 0),  # 번호는 세지 않는다
        ("Take 4와 5의 2, 3장을 봤어요.", 0.28, "ratio", "audience_ratio", 0, 0),
        ("11월 3일 3번째 연습이에요.", 0.28, "ratio", "audience_ratio", 0, 0),  # 날짜 · 순서
        # 단위 없는 정수도 분모에 든다: 점수 68 은 점수 근거와 같고, 62 는 비율 근거 0.62 와 같지 않다
        ("점수는 68이에요.", 68, "score", None, 1, 1),
        ("시선은 62입니다.", 0.62, "ratio", "audience_ratio", 0, 1),
        ("분당 군더더기는 1.3회예요.", 1.25, "per_min", "filler_per_min", 1, 1),
        ("군더더기는 5번이에요.", 4.6, "count", "filler_count", 0, 1),
    ],
)
def test_number_accuracy_examples(
    text: str, value: Any, unit: str, metric: str, ok: int, total: int
) -> None:
    got = gr.number_accuracy(_ans(text, value, unit=unit, metric=metric))
    assert got[:2] == (ok, total)


def test_number_accuracy_skips_the_users_quoted_words_in_a_judgement() -> None:
    ans = _ans(
        "말씀하신 '3장 시선이 40%였어'는 기록과 달라요: Take 5 3장 청중 시선 28%.",
        0.28,
        unit="ratio",
        metric="audience_ratio",
    )
    ans["claims"][0]["kind"] = "ASSESSMENT"
    assert gr.number_accuracy(ans)[:2] == (1, 1)  # 따옴표 속 사용자 원문의 40% 는 세지 않는다


def test_number_accuracy_rechecks_record_values_against_the_fixture() -> None:
    ans = _ans("3장 청중 시선 비율은 30%예요.", 0.3, unit="ratio", metric="audience_ratio")
    assert gr.number_accuracy(ans)[:2] == (1, 1)
    # 근거 값이 원본(0.28)과 다르면 그 근거로 맞춘 숫자도 틀렸다
    assert gr.number_accuracy(ans, fixture_value=lambda _e: 0.28)[:2] == (0, 1)
    # 원본 경로를 읽지 못하면 대조 불가: 분모에는 들고 맞음에는 들지 않는다
    ok, total, bad, unverified = gr.number_accuracy(ans, fixture_value=lambda _e: None)
    assert (ok, total, bad, unverified) == (0, 1, [], 1)


def test_number_accuracy_recomputes_a_difference_from_its_inputs() -> None:
    ans = {
        "claims": [{"kind": "RECORD", "text": "3%p 줄었어요.", "evidence_ids": ["d"]}],
        "evidence": [
            {"evidence_id": "a", "origin": "RECORD", "value": 0.31, "metric": "audience_ratio"},
            {"evidence_id": "b", "origin": "RECORD", "value": 0.28, "metric": "audience_ratio"},
            {
                "evidence_id": "d",
                "origin": "COMPUTED",
                "calc": "DIFF",
                "inputs": ["a", "b"],
                "value": -0.03,
                "metric": "audience_ratio",
                "unit": "ratio",
            },
        ],
    }
    truth = {"a": 0.31, "b": 0.28}
    assert gr.number_accuracy(ans, fixture_value=lambda e: truth[e["evidence_id"]])[:2] == (1, 1)
    ans["evidence"][2]["value"] = -0.05  # 입력으로 다시 계산한 차이와 다르다
    assert gr.number_accuracy(ans, fixture_value=lambda e: truth[e["evidence_id"]])[:2] == (0, 1)
    ans["evidence"][2]["value"] = -0.03
    truth["a"] = 0.4  # 입력 근거가 원본과 다르다
    assert gr.number_accuracy(ans, fixture_value=lambda e: truth[e["evidence_id"]])[:2] == (0, 1)


def test_percent_point_only_matches_a_difference() -> None:
    diff = _ans("3%p 줄었어요.", -0.03, unit="ratio", metric="audience_ratio", calc="DIFF")
    assert gr.number_accuracy(diff)[:2] == (1, 1)
    plain = _ans("3%p 줄었어요.", 0.03, unit="ratio", metric="audience_ratio")
    assert gr.number_accuracy(plain)[:2] == (0, 1)


# ── 채점기: 대상 · 문구 · 인용 · 비교 ─────────────────────────────────────


def test_target_grader() -> None:
    text = "Take 5의 3장 청중 시선 비율은 28%예요.\nTake 4의 2장 말 속도는 310자예요."
    want = {"take": 5, "slide": 3, "metric": "audience_ratio"}
    assert gr.target_check(text, want, "contains")[0]
    assert not gr.target_check(text, {"take": 4, "slide": 3}, "contains")[0]  # 같은 문장에 없음
    assert gr.target_check(text, {"take": 4, "metric": "cpm"}, "contains")[0]
    assert gr.target_check(text, {"take": 6}, "absent")[0]
    assert not gr.target_check("Take 15의 3장", {"take": 1}, "contains")[0]
    assert gr.target_check("슬라이드 3에서", {"slide": 3}, "contains")[0]


@pytest.mark.parametrize(
    ("value", "particle", "want"),
    [
        ("졸업 발표", "으로", "로"),  # 받침 없음
        ("교실", "으로", "로"),  # ㄹ 받침
        ("수업", "으로", "으로"),
        ("Take 1", "으로", "로"),  # 1 = 일(ㄹ)
        ("Take 3", "으로", "으로"),  # 3 = 삼
        ("우선 개선", "예요", "이에요"),
        ("28%", "예요", "예요"),  # % = 퍼센트
        ("3", "예요", "이에요"),
        ("2", "예요", "예요"),
    ],
)
def test_particle_follows_the_final_sound_of_the_filled_value(
    value: str, particle: str, want: str
) -> None:
    assert gr.particle_for(value, particle) == want


def test_template_grader_uses_the_constants_and_checks_particles() -> None:
    contradicted = "말씀하신 '시선 잘 봤어'는 기록과 달라요: Take 5 청중 시선 52%, 우선 개선."
    assert gr.template_check(contradicted, "CONTRADICTED", "contains")[0]
    # 문구에 고정된 조사는 그대로만('{quote}'는 → '은'이면 다른 문구)
    assert not gr.template_check(contradicted.replace("'는", "'은"), "CONTRADICTED", "contains")[0]
    wrong = "말씀하신 '시선'은 기록과 맞아요: x."
    assert not gr.template_check(wrong, "CONTRADICTED", "contains")[0]
    # 준 자리 뒤 '으로'는 그 값의 받침에 맞는 것 하나만
    fetch = {"name": "ACTION_SUCCESS.FETCH_PITCH", "fields": {"title": "졸업 발표"}}
    assert gr.template_check("졸업 발표로 범위를 바꿨어요.", fetch, "contains")[0]
    assert not gr.template_check("졸업 발표으로 범위를 바꿨어요.", fetch, "contains")[0]
    assert not gr.template_check("회사 소개로 범위를 바꿨어요.", fetch, "contains")[0]
    lesson = {"name": "ACTION_SUCCESS.FETCH_PITCH", "fields": {"title": "신입 수업"}}
    assert gr.template_check("신입 수업으로 범위를 바꿨어요.", lesson, "contains")[0]
    assert not gr.template_check("신입 수업로 범위를 바꿨어요.", lesson, "contains")[0]
    # 모르는 자리 뒤 '예요'는 맞은 값의 받침으로 대조한다
    assert gr.template_check("답답하실 수 있지만 기록은 우선 개선이에요.", "PRESSURE", "contains")[
        0
    ]
    assert not gr.template_check(
        "답답하실 수 있지만 기록은 우선 개선예요.", "PRESSURE", "contains"
    )[0]
    assert gr.template_check("답답하실 수 있지만 기록은 28%예요.", "PRESSURE", "contains")[0]
    assert not gr.template_check("답답하실 수 있지만 기록은 28%이에요.", "PRESSURE", "contains")[0]
    no_guide = "자료에서 근거를 찾지 못해 답하지 않을게요."
    assert gr.template_check(no_guide, "SystemText.NO_GUIDE", "contains")[0]
    assert gr.template_check("다른 글", "SystemText.NO_GUIDE", "absent")[0]
    with pytest.raises(gr.TemplateMissing):
        gr.resolve_template("SystemText.NOT_THERE")


CORPUS = {
    "g-07": "긴장하면 숨이 얕아지고 말이 빨라지기 쉽습니다. 첫 문장은 평소보다 한 박자 늦게 시작해 보세요."
}


def _guide(
    text: str, quote: str = "긴장하면 숨이 얕아지고 말이 빨라지기 쉽습니다."
) -> dict[str, Any]:
    return {
        "claims": [{"kind": "GUIDE", "text": text, "evidence_ids": [], "source_ids": ["g-07"]}],
        "sources": [{"source_id": "g-07", "quote": quote}],
    }


def test_quote_grader() -> None:
    ok = _guide("자료에는 '첫 문장은 평소보다 한 박자 늦게 시작해 보세요'라고 나와요.")
    assert gr.quote_check(ok, True, CORPUS)[0]
    changed = _guide("자료에는 '첫 문장은 평소보다 두 박자 늦게 시작해 보세요'라고 나와요.")
    assert not gr.quote_check(changed, True, CORPUS)[0]
    bad_chip = _guide("한 박자 늦게 시작해 보세요.", quote="긴장하면 무조건 천천히 말하세요.")
    assert not gr.quote_check(bad_chip, True, CORPUS)[0]
    no_source = {
        "claims": [{"kind": "GUIDE", "text": "천천히 말하세요.", "source_ids": []}],
        "sources": [],
    }
    assert not gr.quote_check(no_source, True, CORPUS)[0]
    assert gr.quote_check({"claims": [], "sources": []}, False, CORPUS)[0]
    assert not gr.quote_check({"claims": [], "sources": []}, True, CORPUS)[0]
    empty_chip = _guide("한 박자 늦게 시작해 보세요.", quote="")
    assert not gr.quote_check(empty_chip, True, CORPUS)[0]  # 필수 인용이 비었다


def test_guide_numbers_are_matched_as_whole_tokens_with_units() -> None:
    corpus = {"g-90": "짧게 110% 권장하고 5회 반복하세요."}

    def guide(text: str) -> dict[str, Any]:
        return {
            "claims": [{"kind": "GUIDE", "text": text, "source_ids": ["g-90"]}],
            "sources": [{"source_id": "g-90", "quote": "짧게 110% 권장하고 5회 반복하세요."}],
        }

    assert gr.quote_check(guide("110% 권장해요."), True, corpus)[0]
    assert not gr.quote_check(guide("10% 권장해요."), True, corpus)[
        0
    ]  # '10'이 '110' 안에 있어도 다르다
    assert gr.quote_check(guide("5번 반복하세요."), True, corpus)[0]  # 5회 = 5번(같은 개수)
    assert not gr.quote_check(guide("5분 반복하세요."), True, corpus)[0]  # 단위가 다르다
    assert gr.number_accuracy(guide("10% 권장해요."), corpus=corpus)[:2] == (0, 1)


def test_compare_ops() -> None:
    assert gr.compare("equals", 0.28, 0.28 + 1e-12)[0]
    assert not gr.compare("equals", True, 1)[0]
    assert gr.compare("contains", "목표를 저장했어요.", "저장")[0]
    assert gr.compare("contains", ["SAVE_GOAL", "FETCH_TAKE"], "SAVE_GOAL")[0]
    assert gr.compare("contains", ["A", "B", "C"], ["A", "C"])[0]
    items = [
        {
            "verdict": "CONTRADICTED",
            "subject": {"dimension": "audience_gaze"},
            "quote": "시선 잘 봤어",
        }
    ]
    assert gr.compare(
        "contains", items, {"verdict": "CONTRADICTED", "subject.dimension": "audience_gaze"}
    )[0]
    assert gr.compare("contains", items, {"quote~": "시선"})[0]
    assert gr.compare("absent", items, {"verdict": "SUPPORTED"})[0]
    assert not gr.compare("absent", items, {"verdict": "CONTRADICTED"})[0]
    assert gr.compare("absent", "공식 리뷰는 그대로예요", "바꿨")[0]
    assert gr.compare("absent", 0, 2)[0]  # 하나 값이면 '다름'
    assert gr.compare("set_equals", ["USER", "EXPERT"], ["EXPERT", "USER"])[0]
    assert not gr.compare("set_equals", ["USER"], ["EXPERT", "USER"])[0]
    assert gr.compare("one_of", "PARTIAL", ["PARTIAL", "FAILED:AGENT_TIMEOUT"])[0]
    assert gr.compare("one_of", ["u-03", "u-07"], ["u-06", "u-07"])[0]
    assert not gr.compare("one_of", "DONE", ["PARTIAL"])[0]
    for op in ("equals", "contains", "absent", "set_equals", "one_of"):
        assert not gr.compare(op, gr.MISSING, [])[0]  # 관찰값이 없으면 실패


@pytest.mark.parametrize(
    ("obs", "ok"),
    [
        ([], True),  # 아무것도 없음
        (["FETCH_TAKE"], True),
        (["SAVE_GOAL"], False),  # 하나만 있어도 실패
        (["SAVE_GOAL", "START_PRACTICE"], False),  # 모두 있음
    ],
)
def test_absent_with_a_list_fails_if_any_listed_item_is_present(obs: list[str], ok: bool) -> None:
    assert gr.compare("absent", obs, ["SAVE_GOAL", "START_PRACTICE"])[0] is ok
    text = " ".join(obs) or "없음"
    assert gr.compare("absent", text, ["SAVE_GOAL", "START_PRACTICE"])[0] is ok


def test_partial_match_template_key() -> None:
    judgements = [
        {
            "dimension": "audience_gaze",
            "verdict": "CONTRADICTED",
            "text": "말씀하신 '시선 잘 봤어'는 기록과 달라요: Take 5 청중 시선 52%, 우선 개선.",
        }
    ]
    want = {"dimension": "audience_gaze", "verdict": "CONTRADICTED", "text@": "CONTRADICTED"}
    assert gr.compare("contains", judgements, want)[0]
    assert not gr.compare("contains", judgements, {**want, "text@": "SUPPORTED"})[0]
    assert not gr.compare("contains", judgements, {**want, "dimension": "fluency"})[0]


def test_observation_paths() -> None:
    obs = {
        "cards": [
            {"type": "GOAL_SAVED", "goal": {"metric": "audience_ratio"}},
            {"type": "GOAL_PROPOSAL", "options": [{"label": "USER"}, {"label": "EXPERT"}]},
        ]
    }
    assert gr.resolve(obs, "cards[type=GOAL_SAVED].goal.metric") == "audience_ratio"
    assert gr.resolve(obs, "cards[type=GOAL_PROPOSAL].options[].label") == ["USER", "EXPERT"]
    assert gr.resolve(obs, "cards[1].type") == "GOAL_PROPOSAL"
    assert gr.resolve(obs, "cards[type=REASSESS_RESULT].after") is gr.MISSING
    assert gr.resolve(obs, "nothing.here") is gr.MISSING
    # 배열 원소마다 읽을 때 한 원소라도 그 경로가 없으면 관찰값 없음(빠진 원소를 버리지 않는다)
    assert gr.resolve({"items": [{}]}, "items[].value") is gr.MISSING
    assert gr.resolve({"items": [{"value": 1}, {}]}, "items[].value") is gr.MISSING
    assert not gr.compare("absent", gr.resolve({"items": [{}]}, "items[].value"), 1)[0]


# ── 실행기 (가짜 모델) ────────────────────────────────────────────────────


class _JudgeStub:
    model_name = "judge-stub"

    def __init__(self, *outs: Any) -> None:
        self.outs = list(outs)
        self.seen: list[Any] = []

    def invoke(self, req: Any, before_attempt: Any) -> Any:
        self.seen.append(req)
        out = self.outs.pop(0)
        if isinstance(out, Exception):
            raise out
        return F.structured(out)


def test_judge_gives_only_the_cited_cards_and_scores_one_or_zero() -> None:
    from pitch_coach_ai.features.chat_agent.llm_types import LlmTimeout

    from chat_agent_lab import judge as jg

    stub = _JudgeStub({"supported": 1, "reason": "원문과 같다"}, {"supported": 7, "reason": "?"})
    v = jg.judge_sentence(stub, "첫 문장은 한 박자 늦게 시작해 보세요.", {"g-07": CORPUS["g-07"]})
    assert (v.supported, v.error) == (1, None)
    sys_msg, user_msg = stub.seen[0].messages
    assert sys_msg["content"] == jg.JUDGE_PROMPT and "[g-07]" in user_msg["content"]
    assert stub.seen[0].response_schema == jg.JUDGE_SCHEMA
    assert jg.judge_sentence(stub, "x", {"g-07": CORPUS["g-07"]}).supported == 0  # 모양 틀림
    assert jg.judge_sentence(stub, "x", {}).error == "NO_SOURCE"  # 인용 누락은 부르지 않고 0
    failing = _JudgeStub(LlmTimeout(cause="t"))
    assert jg.judge_sentence(failing, "x", {"g-07": "본문"}).supported == 0  # 판정 오류
