"""claims.py 단위 시험: 주장 판정 표 · 여러 Take · 키 · 압력 · 대체 · 첫 문장 (기능 문서 6-3, 7-8 L6, L4-01).

시험 이름의 `fm_L6_xx`는 7-8 실패 행 번호다(10-3 '실패 행을 시험으로 옮기기').
"""

from __future__ import annotations

import pytest

from chat_agent import templates
from chat_agent.claims import (
    UNSET,
    AssessmentDraft,
    DraftSubject,
    claim_key,
    claim_link_key,
    code_verdict,
    finalize,
    goal_conflict,
    normalize_quote,
    round_display,
    split_check,
    to_base_unit,
)
from chat_agent.claims import _aggregate as aggregate
from chat_agent.config import DEFAULT_CONFIG as CFG
from chat_agent.schemas import Subject

from .snapshots import make_take, make_version_of, new_registry, takes_map

VERSION_OF = make_version_of("p-1")
POS, NEG = "POSITIVE", "NEGATIVE"


def mk(
    quote="시선 잘 봤어",
    *,
    op="OVERALL",
    dim="audience_gaze",
    metric=None,
    slide=None,
    takes=(),
    numbers=(),
    relation=None,
    polarity=POS,
    value=None,
    unit=None,
    verdict="SUPPORTED",
    type_="MEASURED",
    speaker="USER",
    label=None,
    evidence_ids=(),
    source_ids=(),
) -> AssessmentDraft:
    """모델이 낸 초안. 기본은 '시선 잘 봤어'(Take 전체 긍정 평가)이고 모델은 늘 맞장구(SUPPORTED)를 낸다."""
    return AssessmentDraft(
        quote=quote,
        type=type_,
        speaker=speaker,
        speaker_label=label,
        claim_op=op if type_ == "MEASURED" else None,
        subject=DraftSubject(
            dimension=dim,
            metric=metric,
            slide_number=slide,
            take_ids=list(takes),
            take_numbers=list(numbers),
            relation=relation,
        ),
        value=value,
        value_unit=unit,
        polarity=polarity,
        verdict=verdict,
        evidence_ids=list(evidence_ids),
        source_ids=list(source_ids),
    )


def run(d, *snaps, focus=None, reg=None, script_used=UNSET, vo=VERSION_OF):
    return code_verdict(
        d,
        takes=takes_map(*snaps),
        focus_take_id=focus,
        pitch_id="p-1",
        registry=reg or new_registry(),
        version_of=vo,
        cfg=CFG,
        script_used=script_used,
    )


def fin(
    drafts,
    message,
    *snaps,
    prior=(),
    focus=None,
    reg=None,
    vo=VERSION_OF,
    script_used=UNSET,
    corpus_version="corpus-1",
    guide_texts=None,
):
    return finalize(
        drafts,
        user_message=message,
        prior=list(prior),
        takes=takes_map(*snaps),
        focus_take_id=focus,
        pitch_id="p-1",
        registry=reg or new_registry(),
        version_of=vo,
        corpus_version=corpus_version,
        cfg=CFG,
        guide_texts=guide_texts,
        id_prefix="a-",
        script_used=script_used,
    )


def t5(**kw):
    return make_take(5, **kw)


# ── L6-02: Take 전체 평가 (차원 상태 표) ────────────────────────────────────

POSITIVE_TABLE = [
    ("STRENGTH", "SUPPORTED"),
    ("IMPROVED", "PARTLY"),
    ("STABLE", "PARTLY"),
    ("PRIORITY", "CONTRADICTED"),
    ("NOT_EVALUABLE", "UNVERIFIABLE"),
    (None, "UNVERIFIABLE"),  # 리뷰 없음
]
NEGATIVE_TABLE = [
    ("PRIORITY", "SUPPORTED"),
    ("IMPROVED", "PARTLY"),
    ("STABLE", "PARTLY"),
    ("STRENGTH", "CONTRADICTED"),
    ("NOT_EVALUABLE", "UNVERIFIABLE"),
    (None, "UNVERIFIABLE"),
]


def _dims(state):
    return None if state is None else {"audience_gaze": state}


@pytest.mark.parametrize(("state", "expected"), POSITIVE_TABLE)
@pytest.mark.parametrize("model_verdict", ["SUPPORTED", "CONTRADICTED", "PARTLY", "UNVERIFIABLE"])
def test_fm_L6_02_overall_positive_table(state, expected, model_verdict):
    d = mk("시선 잘 봤어", polarity=POS, verdict=model_verdict)
    cv = run(d, t5(dims=_dims(state)), focus="take-5")
    assert cv.verdict == expected  # 모델이 무엇을 내도 코드 판정으로 덮인다
    assert cv.verdict_source == "CODE"


@pytest.mark.parametrize(("state", "expected"), NEGATIVE_TABLE)
def test_fm_L6_02_overall_negative_table(state, expected):
    d = mk("시선이 별로였어", polarity=NEG, verdict="SUPPORTED")
    assert run(d, t5(dims=_dims(state)), focus="take-5").verdict == expected


def test_fm_L6_02_neutral_polarity_is_unverifiable():
    cv = run(mk(polarity="NEUTRAL"), t5(), focus="take-5")
    assert (cv.verdict, cv.reason_code) == ("UNVERIFIABLE", "POLARITY_NEUTRAL")


def test_fm_L6_02_evidence_phrase_and_sentence_match_the_doc_example():
    out = fin([mk()], "시선 잘 봤어", t5(), focus="take-5")
    o = out.outcomes[0]
    assert o.assessment.verdict == "CONTRADICTED" and o.assessment.verdict_source == "CODE"
    assert o.evidence_phrase == "Take 5 청중 시선 31%, 우선 개선"
    assert o.sentence == "말씀하신 '시선 잘 봤어'는 기록과 달라요: Take 5 청중 시선 31%, 우선 개선."
    assert o.assessment.assessment_id == "a-1"
    assert len(o.assessment.evidence_ids) == 2  # 차원 상태 + 청중 시선 값


# ── L6-03: 문제 주장 (ISSUE) ────────────────────────────────────────────────


def issue_draft(quote="속도가 너무 빨랐어", **kw):
    return mk(quote, op="ISSUE", dim="pacing_rhythm", **kw)


ISSUE_CASES = [
    # (이름, Take 인자, 기대 판정)
    ("fast_present", {"issues": [("PACE_FAST", 2, True)]}, "SUPPORTED"),
    ("only_opposite", {"issues": [("PACE_SLOW", 2, True)]}, "CONTRADICTED"),
    ("measured_no_problem", {}, "CONTRADICTED"),
    ("speed_unmeasured", {"unmeasured": {"SPEED": "NO_AUDIO"}}, "UNVERIFIABLE"),
    ("only_unreliable", {"issues": [("PACE_FAST", 2, False)]}, "UNVERIFIABLE"),
    ("omitted_segments", {"omitted_paths": ["take_result.problem_segments"]}, "UNVERIFIABLE"),
    ("no_result", {"with_take_result": False}, "UNVERIFIABLE"),
    # 리뷰 개선점(차원 PRIORITY)은 방향을 가르지 못해 근거가 아니다: 문제 구간이 없으면 반박
    ("review_not_evidence", {"dims": {"pacing_rhythm": "PRIORITY"}}, "CONTRADICTED"),
    # 다른 영역(TIME)을 못 쟀어도 속도 영역만 보면 된다
    (
        "other_area_unmeasured",
        {"unmeasured": {"TIME": "NO_PLAN"}, "issues": [("PACE_FAST", None, True)]},
        "SUPPORTED",
    ),
]


@pytest.mark.parametrize(("name", "kw", "expected"), ISSUE_CASES, ids=[c[0] for c in ISSUE_CASES])
def test_fm_L6_03_issue_table(name, kw, expected):
    cv = run(issue_draft(verdict="SUPPORTED"), t5(**kw), focus="take-5")
    assert cv.verdict == expected


def test_fm_L6_03_empty_list_of_unmeasured_data_is_not_no_problem():
    cv = run(issue_draft(), t5(unmeasured={"SPEED": "NO_AUDIO"}), focus="take-5")
    assert cv.verdict == "UNVERIFIABLE" and cv.reason_code == "AREA_UNMEASURED"
    assert cv.evidence_ids == []


def test_fm_L6_03_slow_claim_and_negative_polarity_and_no_issue_word():
    slow = issue_draft("너무 느렸어")
    assert run(slow, t5(issues=[("PACE_SLOW", 1, True)]), focus="take-5").verdict == "SUPPORTED"
    assert run(slow, t5(issues=[("PACE_FAST", 1, True)]), focus="take-5").verdict == "CONTRADICTED"
    not_fast = issue_draft("빠르지 않았어", polarity=NEG)
    assert (
        run(not_fast, t5(issues=[("PACE_FAST", 1, True)]), focus="take-5").verdict == "CONTRADICTED"
    )
    assert run(not_fast, t5(), focus="take-5").verdict == "SUPPORTED"
    assert run(issue_draft("그저 그랬어"), t5(), focus="take-5").reason_code == "NO_ISSUE_WORD"


def test_fm_L6_03_issue_in_slide_scope():
    snap = t5(issues=[("PACE_FAST", 2, True)])
    assert run(issue_draft(slide=2), snap, focus="take-5").verdict == "SUPPORTED"
    assert run(issue_draft(slide=1), snap, focus="take-5").verdict == "CONTRADICTED"


# ── L6-04: 판단 첫 문장 템플릿 ────────────────────────────────────────────


def test_fm_L6_04_first_sentence_is_the_code_template_for_each_verdict():
    msg = "시선 잘 봤어"
    contradicted = fin([mk()], msg, t5(dims=_dims("PRIORITY")), focus="take-5").outcomes[0]
    partly = fin([mk()], msg, t5(dims=_dims("STABLE")), focus="take-5").outcomes[0]
    unverifiable = fin([mk()], msg, t5(dims=None), focus="take-5").outcomes[0]
    supported = fin([mk()], msg, t5(dims=_dims("STRENGTH")), focus="take-5").outcomes[0]
    assert contradicted.sentence == templates.CONTRADICTED.format(
        quote=msg, evidence="Take 5 청중 시선 31%, 우선 개선"
    )
    assert partly.sentence == templates.PARTLY.format(
        quote=msg, evidence="Take 5 청중 시선 31%, 유지"
    )
    assert unverifiable.sentence == templates.UNVERIFIABLE.format(quote=msg)
    assert supported.sentence == templates.SUPPORTED.format(
        quote=msg, evidence="Take 5 청중 시선 31%, 강점"
    )
    assert unverifiable.assessment.evidence_ids == []


# ── L6-06: 대본 사용 주장 ─────────────────────────────────────────────────

SCRIPT_CASES = [
    ("HIGHLIGHT", "SUPPORTED", "전체로 켜져"),
    ("KEYWORD", "PARTLY", "핵심어로 켜져"),
    ("OFF", "UNVERIFIABLE", "꺼져"),
]


@pytest.mark.parametrize(("mode", "expected", "word"), SCRIPT_CASES)
@pytest.mark.parametrize("script_used", [True, False, None, UNSET])
def test_fm_L6_06_script_mode_by_script_used(mode, expected, word, script_used):
    d = mk("대본 보고 한 발표였어", dim="script_independence", metric="script_mode", op="VALUE")
    cv = run(d, t5(script_mode=mode), focus="take-5", script_used=script_used)
    assert cv.verdict == expected
    assert cv.fact_sentences[0] == f"기록에는 화면 대본이 {word} 있었어요."
    not_allowed = templates.SystemText.SCRIPT_NOT_ALLOWED.value
    assert (not_allowed in cv.fact_sentences) == (script_used is False or script_used is None)


def test_fm_L6_06_script_claim_found_from_quote_without_metric_and_negative():
    d = mk("대본 보고 한 발표였어", dim=None, op="OVERALL")
    assert run(d, t5(script_mode="HIGHLIGHT"), focus="take-5").verdict == "SUPPORTED"
    neg = mk("대본 보고 한 발표 아니었어", dim=None, op="OVERALL", polarity=NEG)
    assert run(neg, t5(script_mode="HIGHLIGHT"), focus="take-5").verdict == "CONTRADICTED"
    assert run(neg, t5(script_mode="OFF"), focus="take-5").verdict == "UNVERIFIABLE"
    assert run(d, t5(script_mode=None), focus="take-5").reason_code == "SCRIPT_MODE_UNKNOWN"


def test_fm_L6_06_first_sentence_is_script_fact_and_unknown_never_matches_or_differs():
    # 기록으로는 화면 대본 표시만 안다: '기록과 맞아요/달라요'로 말하지 않고 실제로 봤는지는 모른다고 말한다
    msg = "대본 보고 한 발표였어"
    d = mk(msg, dim="script_independence", metric="script_mode", op="VALUE")
    expected = {
        "HIGHLIGHT": "기록에는 화면 대본이 전체로 켜져 있었어요. 실제로 대본을 봤는지는 기록으로 확인할 수 없어요.",
        "KEYWORD": "기록에는 화면 대본이 핵심어로 켜져 있었어요. 실제로 대본을 봤는지는 기록으로 확인할 수 없어요.",
        "OFF": "기록에는 화면 대본이 꺼져 있었어요. 실제로 대본을 봤는지는 기록으로 확인할 수 없어요.",
    }
    for mode, sentence in expected.items():
        o = fin([d], msg, t5(script_mode=mode), focus="take-5").outcomes[0]
        assert o.sentence == sentence
        assert "기록과 맞아요" not in o.sentence and "기록과 달라요" not in o.sentence
        assert o.extra_sentences == []
    o = fin([d], msg, t5(script_mode="KEYWORD"), focus="take-5", script_used=False).outcomes[0]
    assert o.assessment.verdict == "PARTLY"  # 판정 칸은 표 그대로
    assert o.extra_sentences == ["공식 평가에는 대본 사용 허용이 없어요."]
    none_used = fin([d], msg, t5(script_mode="OFF"), focus="take-5", script_used=None).outcomes[0]
    assert none_used.assessment.verdict == "UNVERIFIABLE"
    assert none_used.sentence.startswith("기록에는 화면 대본이 꺼져 있었어요.")
    assert none_used.extra_sentences == ["공식 평가에는 대본 사용 허용이 없어요."]


# ── L6-18: 장 · 구간 범위 ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("issues", "polarity", "expected"),
    [
        ([("GAZE_LOW_EYE_CONTACT", 3, True)], POS, "CONTRADICTED"),
        ([], POS, "PARTLY"),
        ([("GAZE_LOW_EYE_CONTACT", 2, True)], POS, "PARTLY"),  # 다른 장의 문제는 3장과 무관
        ([("GAZE_LOW_EYE_CONTACT", 3, True)], NEG, "SUPPORTED"),
        ([], NEG, "CONTRADICTED"),
        ([("GAZE_LOW_EYE_CONTACT", 3, False)], POS, "UNVERIFIABLE"),  # 믿을 수 없는 구간만
        ([("GAZE_LOW_EYE_CONTACT", 3, False)], NEG, "UNVERIFIABLE"),
    ],
)
def test_fm_L6_18_slide_scope_table(issues, polarity, expected):
    d = mk("3장은 잘했어", slide=3, polarity=polarity)
    # 전체 차원 상태는 STRENGTH 인데 3장 기록은 다를 수 있다: 장 범위는 장 기록으로만 판정한다
    cv = run(d, t5(dims=_dims("STRENGTH"), issues=issues), focus="take-5")
    assert cv.verdict == expected


def test_fm_L6_18_slide_not_measured_is_unverifiable():
    d = mk("3장은 잘했어", slide=3)
    assert run(d, t5(slide_numbers=(1, 2)), focus="take-5").verdict == "UNVERIFIABLE"
    assert run(d, t5(unmeasured={"GAZE": "X"}), focus="take-5").verdict == "UNVERIFIABLE"


def test_fm_L6_18_slide_partly_sentence_names_the_slide():
    o = fin([mk("3장은 잘했어", slide=3)], "3장은 잘했어", t5(), focus="take-5").outcomes[0]
    assert o.assessment.verdict == "PARTLY"
    assert o.evidence_phrase.startswith("Take 5 3장 청중 시선")


# ── L6-19: 값 · 비교 주장 ─────────────────────────────────────────────────


def value_draft(value, unit, quote="시선이 31%였어", **kw):
    return mk(quote, op="VALUE", metric="audience_ratio", value=value, unit=unit, **kw)


@pytest.mark.parametrize(
    ("value", "unit", "polarity", "expected"),
    [
        (31, "%", POS, "SUPPORTED"),
        (0.31, "ratio", POS, "SUPPORTED"),  # % 와 비율 정규화
        (40, "%", POS, "CONTRADICTED"),
        (31, "%", NEG, "CONTRADICTED"),  # "31%가 아니었어" 인데 실제 31%
        (40, "%", NEG, "SUPPORTED"),
        (31, None, POS, "UNVERIFIABLE"),  # 단위를 정하지 못함
        (31, "초", POS, "UNVERIFIABLE"),  # 이 지표에 맞지 않는 단위
        (None, "%", POS, "UNVERIFIABLE"),
    ],
)
def test_fm_L6_19_value_claim_table(value, unit, polarity, expected):
    cv = run(value_draft(value, unit, polarity=polarity), t5(audience_ratio=0.31), focus="take-5")
    assert cv.verdict == expected


@pytest.mark.parametrize(
    ("actual", "claimed", "expected"),
    [
        (0.285, 29, "SUPPORTED"),  # 28.5% 는 화면에서 29% 로 올림
        (0.285, 28, "CONTRADICTED"),
        (0.2849, 28, "SUPPORTED"),
        (0.2849, 29, "CONTRADICTED"),
        (0.3149, 31, "SUPPORTED"),
        (0.315, 31, "CONTRADICTED"),
        (0.3151, 32, "SUPPORTED"),
    ],
)
def test_fm_L6_19_value_rounding_boundary(actual, claimed, expected):
    cv = run(value_draft(claimed, "%"), t5(audience_ratio=actual), focus="take-5")
    assert cv.verdict == expected


def test_fm_L6_19_value_contradicted_shows_actual_value_and_unit_variants():
    cv = run(value_draft(40, "%"), t5(audience_ratio=0.31), focus="take-5")
    assert cv.evidence_phrase == "Take 5 청중 시선 31%"
    ms = mk(
        "발표는 92초였어",
        op="VALUE",
        dim="pacing_rhythm",
        metric="duration_ms",
        value=92,
        unit="초",
    )
    assert run(ms, t5(duration_ms=92000.0), focus="take-5").verdict == "SUPPORTED"
    minute = mk(
        "발표는 1분 반이었어",
        op="VALUE",
        dim="pacing_rhythm",
        metric="duration_ms",
        value=1.5,
        unit="분",
    )
    assert run(minute, t5(duration_ms=92000.0), focus="take-5").verdict == "CONTRADICTED"
    cpm = mk(
        "속도는 300이었어", op="VALUE", dim="pacing_rhythm", metric="cpm", value=300, unit="자/분"
    )
    assert run(cpm, t5(cpm=300.0), focus="take-5").verdict == "SUPPORTED"
    per_min = mk(
        "군더더기는 분당 1.34번",
        op="VALUE",
        dim="fluency",
        metric="filler_per_min",
        value=1.34,
        unit="회/분",
    )
    assert (
        run(per_min, t5(filler_per_min=1.3), focus="take-5").verdict == "SUPPORTED"
    )  # 표시 자리 1.3


def test_fm_L6_19_value_slide_and_unmeasured():
    d = value_draft(28, "%", "3장 시선은 28%였어", slide=3)
    assert run(d, t5(gaze_slides={3: 0.28}), focus="take-5").verdict == "SUPPORTED"
    assert (
        run(d, t5(gaze_slides={3: None}), focus="take-5").verdict == "UNVERIFIABLE"
    )  # null 은 0 이 아니다
    assert (
        run(value_draft(0, "%"), t5(audience_ratio=None), focus="take-5").verdict == "UNVERIFIABLE"
    )


def change_draft(
    polarity=POS, takes=("take-4", "take-5"), relation="COMPARE", quote="지난번보다 늘었어", **kw
):
    return mk(
        quote,
        op="CHANGE",
        metric="audience_ratio",
        polarity=polarity,
        takes=takes,
        relation=relation,
        **kw,
    )


@pytest.mark.parametrize(
    ("prev", "cur", "polarity", "expected"),
    [
        (0.28, 0.33, POS, "SUPPORTED"),
        (0.33, 0.28, POS, "CONTRADICTED"),
        (0.28, 0.28, POS, "CONTRADICTED"),  # 같음
        (0.33, 0.28, NEG, "SUPPORTED"),
        (0.28, 0.33, NEG, "CONTRADICTED"),
        (0.28, 0.28, NEG, "CONTRADICTED"),
    ],
)
def test_fm_L6_19_change_claim_uses_compare_takes(prev, cur, polarity, expected):
    cv = run(
        change_draft(polarity),
        make_take(4, audience_ratio=prev),
        t5(audience_ratio=cur),
        focus="take-5",
    )
    assert cv.verdict == expected
    assert cv.checked_take_ids == ["take-4", "take-5"]


def test_fm_L6_19_change_phrase_and_evidence_come_from_comparison():
    reg = new_registry()
    cv = run(
        change_draft(),
        make_take(4, audience_ratio=0.28),
        t5(audience_ratio=0.33),
        focus="take-5",
        reg=reg,
    )
    assert cv.evidence_phrase == "Take 4 청중 시선 28% → Take 5 청중 시선 33%, 좋아졌어요"
    assert {reg.get(i).calc for i in cv.evidence_ids} >= {"DIFF", "OUTCOME", None}


def test_fm_L6_19_change_not_comparable_pairs_are_unverifiable():
    ok4 = make_take(4)
    for bad in (
        t5(script_version_id="sv-2"),
        t5(completed_fully=False),
        t5(standard_version=2),
        t5(audience_ratio=None),
        t5(criteria_versions={"GAZE": "gaze-9+x"}),
    ):
        cv = run(change_draft(), ok4, bad, focus="take-5")
        assert cv.verdict == "UNVERIFIABLE", bad
        assert cv.unchecked_take_ids == ["take-4", "take-5"] and cv.checked_take_ids == []
    # 방향이 없는 지표
    nd = mk(
        "멈춤이 늘었어",
        op="CHANGE",
        dim="pacing_rhythm",
        metric="pause_count",
        takes=("take-4", "take-5"),
        relation="COMPARE",
    )
    assert run(nd, ok4, t5(), focus="take-5").reason_code == "NO_DIRECTION"


def test_fm_L6_19_change_with_one_take_uses_previous_completed_take():
    d = change_draft(takes=("take-5",), relation=None)
    cv = run(
        d,
        make_take(3, audience_ratio=0.1),
        make_take(4, audience_ratio=0.28),
        t5(audience_ratio=0.33),
        focus="take-5",
    )
    assert cv.verdict == "SUPPORTED" and cv.checked_take_ids == ["take-4", "take-5"]
    assert run(d, t5(), focus="take-5").reason_code == "NO_PREVIOUS_TAKE"
    unfinished = make_take(4, completed=False, completed_fully=None)
    assert run(d, unfinished, t5(), focus="take-5").reason_code == "NO_PREVIOUS_TAKE"


def test_fm_L6_19_range_metric_change_and_changed_range():
    d = mk(
        "발표 시간이 좋아졌어",
        op="CHANGE",
        dim="pacing_rhythm",
        metric="duration_ms",
        takes=("take-4", "take-5"),
        relation="COMPARE",
    )
    a = make_take(4, duration_ms=360000.0)
    b = t5(duration_ms=340000.0)
    assert run(d, a, b, focus="take-5").verdict == "SUPPORTED"
    c = make_take(6, duration_ms=380000.0)
    worse = d.model_copy(
        update={"subject": d.subject.model_copy(update={"take_ids": ["take-5", "take-6"]})}
    )
    assert run(worse, b, c, focus="take-6").verdict == "CONTRADICTED"


# ── L6-17: 유형 · 대상 잘못 고름 ──────────────────────────────────────────


@pytest.mark.parametrize(
    "draft",
    [
        mk(dim="charisma"),  # 기록에 없는 차원 이름
        mk(dim=None, metric="charisma"),  # 기록에 없는 지표
        mk(dim="audience_gaze", metric="cpm"),  # 차원과 지표가 어긋남
        mk(dim=None),  # 대상을 정할 낱말도 없다 ("잘 봤어"는 측정 낱말이 아니다)
        mk(op="TREND"),  # claim_op 가 넷 밖
        mk(op=None),
        mk(slide=0),  # 장 번호가 양수가 아님
    ],
)
def test_fm_L6_17_unresolvable_target_is_unverifiable(draft):
    d = (
        draft.model_copy(update={"quote": "잘 봤어"})
        if draft.subject.dimension is None and draft.subject.metric is None
        else draft
    )
    cv = run(d, t5(), focus="take-5")
    assert (cv.verdict, cv.verdict_source) == ("UNVERIFIABLE", "CODE")
    assert cv.evidence_ids == []


def test_fm_L6_17_dimension_found_from_quote_when_unique_else_unverifiable():
    one = mk("시선 잘 봤어", dim=None)
    assert run(one, t5(), focus="take-5").verdict == "CONTRADICTED"
    two = mk("시선도 속도도 좋았어", dim=None)
    assert run(two, t5(), focus="take-5").reason_code == "NO_TARGET"


def test_fm_L6_17_reversed_direction_is_not_supported():
    # '느렸어'를 말했는데 기록은 '빠름' 문제뿐 → 모델이 방향을 뒤집어도 코드가 반박한다
    cv = run(issue_draft("너무 느렸어"), t5(issues=[("PACE_FAST", 1, True)]), focus="take-5")
    assert cv.verdict == "CONTRADICTED"


def test_fm_L6_17_slide_that_does_not_exist_is_unverifiable():
    assert run(mk("9장은 잘했어", slide=9), t5(), focus="take-5").verdict == "UNVERIFIABLE"


def test_fm_L6_17_type_mismatch_detection_in_split_check():
    msg = "시선이 별로였어"
    pref = mk(msg, type_="PREFERENCE", verdict="PARTLY")
    _, problems = split_check(msg, [pref], CFG)
    assert [(p.code, p.draft_index) for p in problems] == [("TYPE_MISMATCH", 0)]
    # 장 번호 · Take 번호를 말하는 절
    for text in ("3장 시선이 좋은 게 취향이야", "Take 5 시선이 마음에 들어"):
        _, p = split_check(text, [mk(text, type_="KNOWLEDGE", verdict="UNVERIFIABLE")], CFG)
        assert [x.code for x in p] == ["TYPE_MISMATCH"], text
    # 유형을 유지해도 되는 말: 선호 · 발표법 주장 · 행동 요청
    for text, typ in (
        ("빠르게 말하는 게 내 스타일이야", "PREFERENCE"),
        ("발표는 시선이 중요하다던데", "KNOWLEDGE"),
        ("시선 연습 목표로 잡아 줘", "REQUEST"),
        ("3장 시선 다시 평가해 줘", "REQUEST"),
    ):
        _, p = split_check(text, [mk(text, type_=typ, verdict="UNVERIFIABLE")], CFG)
        assert p == [], text
    # 다른 사람 평가 인용은 MEASURED 여야 한다
    other = mk("친구가 시선이 좋았대", type_="PREFERENCE", speaker="OTHER")
    assert [x.code for x in split_check(other.quote, [other], CFG)[1]] == ["TYPE_MISMATCH"]
    # MEASURED 면 문제 없음
    assert split_check(msg, [mk(msg, polarity=NEG)], CFG)[1] == []


def test_fm_L6_17_remaining_type_mismatch_is_forced_unverifiable_in_finalize():
    msg = "시선이 별로였어"
    d = mk(msg, type_="PREFERENCE", verdict="PARTLY", dim=None)
    out = fin([d], msg, t5(), focus="take-5")
    o = out.outcomes[0]
    assert (o.assessment.verdict, o.assessment.verdict_source, o.reason_code) == (
        "UNVERIFIABLE",
        "CODE",
        "TYPE_MISMATCH",
    )
    assert o.sentence == templates.UNVERIFIABLE.format(quote=msg)


# ── L6-16 · L6-01: 덮기와 형식 ────────────────────────────────────────────


def test_fm_L6_16_each_measured_clause_needs_a_covering_draft():
    msg = "시선도 좋았고 군더더기도 없었지?"
    one = mk("시선도 좋았고")
    valid, problems = split_check(msg, [one], CFG)
    assert valid == [one]
    assert [(p.code, p.quote) for p in problems] == [("UNCOVERED_CLAUSE", "군더더기도 없었지")]
    both = [one, mk("군더더기도 없었지", dim="fluency", polarity=NEG)]
    assert split_check(msg, both, CFG)[1] == []
    # 쉼표 · 문장부호로도 나뉜다
    assert [
        p.quote
        for p in split_check("시선 좋았어. 속도는 빨랐어, 쉼도 많았지", [mk("시선 좋았어")], CFG)[1]
    ] == [
        "속도는 빨랐어",
        "쉼도 많았지",
    ]


def test_fm_L6_16_finalize_adds_uncovered_sentence_and_no_judgement_for_it():
    msg = "시선도 좋았고 군더더기도 없었지?"
    out = fin([mk("시선도 좋았고")], msg, t5(), focus="take-5")
    assert len(out.outcomes) == 1
    assert out.uncovered_sentences == [
        "말씀하신 '군더더기도 없었지'는 이번 답에서 확인하지 못했어요."
    ]
    assert out.dropped_notice == []


def test_fm_L6_16_non_measured_clauses_need_no_draft():
    assert split_check("안녕하세요, 오늘 날씨가 좋네요", [], CFG) == ([], [])


def test_fm_L6_01_quote_must_be_a_substring_of_the_message():
    msg = "시선 잘 봤어"
    bad = mk("시선 잘 봤지?")
    valid, problems = split_check(msg, [bad, mk("시선 잘 봤어")], CFG)
    assert [p.code for p in problems] == ["QUOTE_NOT_IN_MESSAGE"] and problems[0].draft_index == 0
    assert len(valid) == 1
    assert [p.code for p in split_check(msg, [mk("  ")], CFG)[1]] == [
        "EMPTY_QUOTE",
        "UNCOVERED_CLAUSE",
    ]
    shaped = mk("시선 잘 봤어", type_="PREFERENCE").model_copy(update={"claim_op": "OVERALL"})
    assert split_check(msg, [shaped], CFG)[1][0].code == "CLAIM_OP_ON_NON_MEASURED"


def test_fm_L6_01_finalize_drops_bad_draft_and_adds_notice():
    msg = "시선 잘 봤어"
    out = fin([mk("시선 잘 봤지?")], msg, t5(), focus="take-5")
    assert out.outcomes == []
    assert out.dropped_notice == ["말씀하신 내용은 기록과 맞춰 보지 못했어요."]
    assert out.uncovered_sentences == ["말씀하신 '시선 잘 봤어'는 이번 답에서 확인하지 못했어요."]


# ── L6-20: 여러 Take ──────────────────────────────────────────────────────

S, P, C, U = "STRENGTH", "STABLE", "PRIORITY", None


def three(states):
    return [make_take(n, dims=_dims(s)) for n, s in zip((3, 4, 5), states, strict=True)]


ALL_CASES = [
    ((S, S, S), "SUPPORTED"),
    ((S, S, U), "PARTLY"),  # 못 읽은 Take 가 있는데 나머지가 모두 SUPPORTED
    ((S, P, S), "PARTLY"),
    ((S, C, P), "PARTLY"),
    ((C, C, C), "CONTRADICTED"),
    ((C, C, U), "CONTRADICTED"),  # 확인한 Take 가 모두 CONTRADICTED
    ((P, P, P), "PARTLY"),
    ((S, C, U), "PARTLY"),
    ((U, U, U), "UNVERIFIABLE"),
]
SOME_CASES = [
    ((S, C, C), "SUPPORTED"),
    ((C, C, S), "SUPPORTED"),
    ((C, C, C), "CONTRADICTED"),
    ((C, P, C), "PARTLY"),  # PARTLY + CONTRADICTED
    ((P, P, P), "PARTLY"),  # 모두 PARTLY
    ((P, U, U), "PARTLY"),  # PARTLY + 확인하지 못한 Take
    ((C, C, U), "UNVERIFIABLE"),  # 확인한 Take 는 모두 CONTRADICTED 인데 못 읽은 Take 가 있음
    ((C, U, U), "UNVERIFIABLE"),
    ((U, U, U), "UNVERIFIABLE"),
]


@pytest.mark.parametrize(("states", "expected"), ALL_CASES)
def test_fm_L6_20_all_relation(states, expected):
    snaps = three(states)
    d = mk("최근 세 번 시선 다 좋았어", relation="ALL", takes=[s.summary.take_id for s in snaps])
    cv = run(d, *snaps)
    assert cv.verdict == expected
    assert (
        aggregate(
            "ALL",
            [
                {
                    "STRENGTH": "SUPPORTED",
                    "STABLE": "PARTLY",
                    "PRIORITY": "CONTRADICTED",
                    None: "UNVERIFIABLE",
                }[s]
                for s in states
            ],
        )
        == expected
    )


@pytest.mark.parametrize(("states", "expected"), SOME_CASES)
def test_fm_L6_20_some_relation(states, expected):
    snaps = three(states)
    d = mk(
        "최근 세 번 중에 시선 좋았던 적 있어",
        relation="SOME",
        takes=[s.summary.take_id for s in snaps],
    )
    assert run(d, *snaps).verdict == expected


def test_fm_L6_20_some_partly_plus_contradicted_is_partly():
    snaps = three((P, C, C))
    d = mk(
        "최근 세 번 중에 시선 좋았던 적 있어",
        relation="SOME",
        takes=[s.summary.take_id for s in snaps],
    )
    assert run(d, *snaps).verdict == "PARTLY"


def test_fm_L6_20_unchecked_takes_are_reported_separately():
    snaps = three((S, S, U))
    d = mk("최근 세 번 시선 다 좋았어", relation="ALL", takes=[s.summary.take_id for s in snaps])
    cv = run(d, *snaps)
    assert cv.checked_take_ids == ["take-3", "take-4"] and cv.unchecked_take_ids == ["take-5"]
    assert cv.unchecked_take_numbers == [5]
    assert "(확인하지 못한 Take 5)" in cv.evidence_phrase


def test_fm_L6_20_take_not_loaded_counts_as_unchecked():
    snaps = three((S, S, S))[:2]
    d = mk("최근 세 번 시선 다 좋았어", relation="ALL", takes=["take-3", "take-4", "take-5"])
    cv = run(d, *snaps)
    assert cv.verdict == "PARTLY" and cv.unchecked_take_ids == ["take-5"]
    only_missing = mk("시선 다 좋았어", relation="ALL", takes=["take-8", "take-9"])
    assert run(only_missing, *snaps).verdict == "UNVERIFIABLE"


def test_fm_L6_20_relation_is_required_for_multiple_takes():
    snaps = three((S, S, S))
    d = mk("시선 좋았어", relation=None, takes=[s.summary.take_id for s in snaps])
    assert run(d, *snaps).reason_code == "RELATION_MISSING"


def test_fm_L6_20_compare_with_non_change_op_is_unverifiable():
    snaps = three((S, S, S))
    d = mk("시선 좋아졌어", relation="COMPARE", takes=["take-4", "take-5"])
    assert run(d, *snaps).reason_code == "COMPARE_NEEDS_CHANGE"
    too_many = change_draft(takes=("take-3", "take-4", "take-5"))
    assert run(too_many, *snaps).reason_code == "TOO_MANY_TAKES"


def test_fm_L6_20_compare_with_unloaded_take_is_unverifiable():
    cv = run(change_draft(takes=("take-4", "take-5")), t5(), focus="take-5")
    assert cv.verdict == "UNVERIFIABLE" and cv.reason_code == "TAKE_NOT_LOADED"


# ── 대상 Take 고르기 ─────────────────────────────────────────────────────


def test_target_take_pointed_then_focus_then_latest_completed():
    t4 = make_take(4, dims=_dims("STRENGTH"))
    t5_ = t5(dims=_dims("PRIORITY"))
    incomplete = make_take(6, dims=_dims("STRENGTH"), completed=False, completed_fully=None)
    pointed = mk(takes=["take-4"])
    assert run(pointed, t4, t5_, focus="take-5").verdict == "SUPPORTED"
    assert run(mk(numbers=[4]), t4, t5_, focus="take-5").verdict == "SUPPORTED"
    assert run(mk(), t4, t5_, focus="take-5").verdict == "CONTRADICTED"  # 지금 보던 Take
    cv = run(mk(), t4, t5_, incomplete)  # 가장 최근 완료 Take = 5 (6 은 완료 아님)
    assert cv.verdict == "CONTRADICTED" and cv.target_take_ids == ["take-5"]
    assert run(mk(), t4, t5_, focus="take-missing").target_take_ids == ["take-5"]
    assert run(mk()).reason_code == "NO_TARGET_TAKE"


def test_target_take_number_not_loaded_is_unchecked_by_number():
    cv = run(mk(numbers=[9]), t5(), focus="take-5")
    assert cv.verdict == "UNVERIFIABLE" and cv.unchecked_take_numbers == [9]


# ── 비측정 유형: 모델 판정 유지 + 근거 조건 ────────────────────────────────


def make_evidence(reg):
    return reg.record(
        pitch_id="p-1", take_id="take-5", take_number=5, kind="TAKE_RESULT", revision=1,
        path="take_result.areas.SPEED.take.cpm", value=380.0, metric="cpm",
    ).evidence_id  # fmt: skip


PREF_MSG = "빠르게 말하는 게 내 스타일이야"


def pref(model_verdict="SUPPORTED", quote=PREF_MSG, dim="pacing_rhythm", **kw):
    return mk(quote, type_="PREFERENCE", dim=dim, verdict=model_verdict, **kw)


@pytest.mark.parametrize("model_verdict", ["SUPPORTED", "PARTLY", "CONTRADICTED", "UNVERIFIABLE"])
def test_fm_L6_15_preference_conflicting_problem_is_partly_whatever_the_model_says(model_verdict):
    snap = t5(issues=[("PACE_FAST", 2, True)])
    cv = run(pref(model_verdict), snap, focus="take-5")
    assert (cv.verdict, cv.verdict_source) == ("PARTLY", "CODE")  # 어긋남 = PARTLY
    assert cv.evidence_ids and cv.evidence_phrase == "Take 5 말이 빠름"


@pytest.mark.parametrize("model_verdict", ["SUPPORTED", "PARTLY", "CONTRADICTED", "UNVERIFIABLE"])
def test_fm_L6_15_preference_measured_without_problem_is_supported(model_verdict):
    cv = run(pref(model_verdict), t5(), focus="take-5")
    assert (cv.verdict, cv.verdict_source) == ("SUPPORTED", "CODE")  # 어울림 = SUPPORTED
    assert cv.evidence_ids  # 기록에서 읽은 근거가 있다
    # 반대 방향 문제만 있으면 이 선호(빠르게)와는 어울린다
    assert run(pref(), t5(issues=[("PACE_SLOW", 1, True)]), focus="take-5").verdict == "SUPPORTED"


def test_fm_L6_15_preference_without_usable_evidence_is_unverifiable():
    for snap in (
        t5(unmeasured={"SPEED": "NO_AUDIO"}),
        t5(issues=[("PACE_FAST", 2, False)]),  # 믿을 수 없는 구간뿐
        t5(with_take_result=False),
    ):
        for v in ("PARTLY", "SUPPORTED", "CONTRADICTED"):
            cv = run(pref(v), snap, focus="take-5")
            assert cv.verdict == "UNVERIFIABLE" and cv.evidence_ids == [], v
    assert run(pref(), focus="take-5").reason_code == "NO_TARGET_TAKE"
    assert run(pref(dim=None, quote="좋아해"), t5(), focus="take-5").verdict == "UNVERIFIABLE"


def test_fm_L6_15_preference_never_contradicted_and_ignores_model_evidence_ids():
    reg = new_registry()
    fake = make_evidence(reg)  # 모델이 댄 근거는 판정에 쓰지 않는다
    cv = run(
        pref("SUPPORTED", evidence_ids=[fake]),
        t5(issues=[("PACE_FAST", None, True)]),
        reg=reg,
        focus="take-5",
    )
    assert cv.verdict == "PARTLY" and fake not in cv.evidence_ids


def test_fm_L6_15_preference_uses_its_own_template_never_the_measured_one():
    snap = t5(issues=[("PACE_FAST", 2, True)])
    o = fin([pref("PARTLY")], PREF_MSG, snap, focus="take-5").outcomes[0]
    assert o.sentence == "말씀하신 방식은 존중해요. 다만 이 피치의 기록에서는 Take 5 말이 빠름예요."
    assert "기록과 달라요" not in o.sentence
    fits = fin([pref("PARTLY")], PREF_MSG, t5(), focus="take-5").outcomes[0]
    assert fits.assessment.verdict == "SUPPORTED"
    assert fits.sentence.startswith("말씀하신 방식은 이 피치의 기록과도 어울려요")
    nothing = fin(
        [pref("SUPPORTED")], PREF_MSG, t5(unmeasured={"SPEED": "X"}), focus="take-5"
    ).outcomes[0]
    assert (
        nothing.sentence
        == "말씀하신 '빠르게 말하는 게 내 스타일이야'는 이 기록으로는 확인할 수 없어요."
    )


GUIDE = {"guide-pace-1": "발표 속도는 빠를수록 좋은 것이 아니라 청중이 따라올 수 있는 속도가 좋다."}


def kn(**kw):
    return mk("발표는 빠를수록 좋다던데", type_="KNOWLEDGE", dim=None, **kw)


def read_guides():
    reg = new_registry()
    reg.source("guide-pace-1", "corpus-1")
    reg.source("guide-eye-1", "corpus-1")
    return reg


def test_knowledge_needs_a_read_source_with_text_for_supported_or_contradicted():
    reg = read_guides()
    texts = {**GUIDE, "guide-eye-1": "청중과 눈을 맞추면 신뢰가 높아진다."}
    ok = run_guides(kn(verdict="CONTRADICTED", source_ids=["guide-pace-1"]), reg, GUIDE)
    assert ok.verdict == "CONTRADICTED"
    assert (
        run_guides(kn(verdict="SUPPORTED", source_ids=["guide-pace-1"]), reg, GUIDE).verdict
        == "SUPPORTED"
    )
    for v in ("SUPPORTED", "CONTRADICTED"):
        assert run_guides(kn(verdict=v), reg, GUIDE).verdict == "UNVERIFIABLE"  # 인용 없음
        assert (
            run_guides(kn(verdict=v, source_ids=["never-read"]), reg, GUIDE).verdict
            == "UNVERIFIABLE"
        )
        # 원문을 주지 않았다 → 뒷받침을 확인할 수 없다
        assert (
            run_guides(kn(verdict=v, source_ids=["guide-pace-1"]), reg, None).reason_code
            == "NO_GUIDE_TEXT"
        )
        # 일부 인용만 원문이 있음
        assert (
            run_guides(
                kn(verdict=v, source_ids=["guide-pace-1", "guide-eye-1"]), reg, GUIDE
            ).verdict
            == "UNVERIFIABLE"
        )
    assert run_guides(kn(verdict="UNVERIFIABLE"), reg, GUIDE).verdict == "UNVERIFIABLE"
    assert (
        run_guides(
            kn(verdict="CONTRADICTED", source_ids=["guide-pace-1", "guide-eye-1"]), reg, texts
        ).verdict
        == "CONTRADICTED"
    )


def test_fm_L6_12_irrelevant_citation_does_not_support_a_knowledge_claim():
    reg = read_guides()
    eye_only = {"guide-eye-1": "청중과 눈을 맞추면 신뢰가 높아진다."}
    for v in ("SUPPORTED", "CONTRADICTED"):
        cv = run_guides(kn(verdict=v, source_ids=["guide-eye-1"]), reg, eye_only)
        assert (cv.verdict, cv.reason_code) == ("UNVERIFIABLE", "IRRELEVANT_SOURCE")


def test_knowledge_topic_includes_measure_words_of_its_dimension():
    """'빠를수록'처럼 활용으로 앞 두 글자가 달라져도 같은 차원(말 속도)의 낱말('속도')을 공유하면 관련 자료다."""
    reg = read_guides()
    pace = {"guide-pace-1": "청중이 따라올 수 있는 속도에 쉼을 섞는다."}
    d = mk(
        "발표는 빨리 할수록 좋다며", type_="KNOWLEDGE", dim="pacing_rhythm", verdict="CONTRADICTED",
        source_ids=["guide-pace-1"],
    )  # fmt: skip
    assert run_guides(d, reg, pace).verdict == "CONTRADICTED"
    # 차원이 없으면 주장 낱말만 본다(관련 없는 인용으로 동의하지 않는다)
    no_dim = d.model_copy(update={"subject": d.subject.model_copy(update={"dimension": None})})
    assert run_guides(no_dim, reg, pace).reason_code == "IRRELEVANT_SOURCE"


def run_guides(d, reg, texts):
    return code_verdict(
        d, takes={}, focus_take_id=None, pitch_id="p-1", registry=reg, version_of=VERSION_OF,
        cfg=CFG, guide_texts=texts,
    )  # fmt: skip


def test_knowledge_finalize_carries_corpus_version_into_basis():
    reg = read_guides()
    out = fin(
        [kn(verdict="CONTRADICTED", source_ids=["guide-pace-1"])],
        "발표는 빠를수록 좋다던데",
        reg=reg,
        guide_texts=GUIDE,
    )
    a = out.assessments[0]
    assert a.verdict == "CONTRADICTED" and a.evidence_basis.sources[0].corpus_version == "corpus-1"
    other = fin(
        [kn(verdict="CONTRADICTED", source_ids=["guide-pace-1"])],
        "발표는 빠를수록 좋다던데",
        reg=read_guides(),
        guide_texts=GUIDE,
        corpus_version="corpus-2",
    ).assessments[0]
    reg2 = (
        new_registry()
    )  # 자료 판이 바뀌면 근거 지문이 바뀐다 (등록부에 읽은 판이 있으면 그 판을 쓴다)
    reg2.source("guide-pace-1", "corpus-2")
    third = fin(
        [kn(verdict="CONTRADICTED", source_ids=["guide-pace-1"])],
        "발표는 빠를수록 좋다던데",
        reg=reg2,
        guide_texts=GUIDE,
    ).assessments[0]
    assert third.evidence_basis.fingerprint != a.evidence_basis.fingerprint
    assert other.evidence_basis.sources[0].corpus_version == "corpus-1"


def test_request_keeps_model_verdict_and_has_no_sentence_when_supported():
    d = mk("시선 연습 목표로 잡아 줘", type_="REQUEST", verdict="SUPPORTED")
    o = fin([d], "시선 연습 목표로 잡아 줘").outcomes[0]
    assert (o.assessment.verdict, o.assessment.verdict_source, o.sentence) == (
        "SUPPORTED",
        "MODEL",
        "",
    )
    assert o.assessment.claim_op is None


@pytest.mark.parametrize(
    ("message", "verdict", "silent"),
    [
        ("목표 저장하지 마", "UNVERIFIABLE", True),
        ("대본은 어떤 형식으로 넣어?", "UNVERIFIABLE", True),
        ("시선 연습 목표로 잡아 줘", "SUPPORTED", True),
        # 근거 없이 어긋난다고 하면 문장을 만들지 않는다
        ("시선 연습 목표로 잡아 줘", "PARTLY", True),
    ],
)
def test_request_and_info_question_get_no_unverifiable_sentence(
    message: str, verdict: str, silent: bool
) -> None:
    """요청 · 정보 질문은 사실 주장이 아니다: '이 기록으로는 확인할 수 없어요'를 붙이지 않는다(판단은 남긴다)."""
    o = fin([mk(message, type_="REQUEST", dim=None, verdict=verdict)], message).outcomes[0]
    assert o.assessment.verdict == verdict and o.assessment.type == "REQUEST"
    assert (o.sentence == "") is silent
    assert "확인할 수 없어요" not in o.sentence


def test_request_against_record_with_evidence_keeps_template_sentence() -> None:
    """근거가 있고 기록과 어긋난 요청만 템플릿 문장을 쓴다."""
    msg = "시선 연습 목표로 잡아 줘"
    reg = new_registry()
    ev = reg.screen(
        pitch_id="p-1", snapshot_id="snap-1", path="screen.draft.time_limit_sec", value=300,
        saved=False,
    ).evidence_id  # fmt: skip
    o = fin(
        [mk(msg, type_="REQUEST", dim=None, verdict="PARTLY", evidence_ids=[ev])], msg, reg=reg
    ).outcomes[0]
    assert o.sentence.startswith(f"말씀하신 '{msg}'는 기록에서 일부만 확인돼요")


# ── L6-21: 다른 사람 평가 인용 ────────────────────────────────────────────


def test_fm_L6_21_other_speaker_keeps_label_and_judges_content_only():
    msg = "친구가 시선이 좋았다고 했어"
    d = mk("시선이 좋았다고", speaker="OTHER", label="친구")
    o = fin([d], msg, t5(), focus="take-5").outcomes[0]
    assert o.assessment.speaker == "OTHER" and o.assessment.speaker_label == "친구"
    assert o.assessment.verdict == "CONTRADICTED"  # 발언 여부가 아니라 내용만 판정
    assert (
        o.sentence
        == "친구의 말씀은 확인할 수 없지만, 기록에서는 Take 5 청중 시선 31%, 우선 개선이에요."
    )
    un = fin([d], msg, t5(dims=None), focus="take-5").outcomes[0]
    assert un.sentence == templates.UNVERIFIABLE.format(quote="시선이 좋았다고")


# ── 키 (L6-09 · L6-10) ────────────────────────────────────────────────────


def key_args(**over):
    base = dict(
        type="MEASURED",
        subject=Subject(
            dimension="audience_gaze", metric="audience_ratio", take_ids=["take-5"], relation=None
        ),
        claim_op="VALUE",
        polarity=POS,
        value=31,
        value_unit="%",
        quote="시선이 31%였어",
        cfg=CFG,
    )
    base.update(over)
    return base


def test_fm_L6_09_claim_key_same_claim_same_key_and_unit_normalized():
    a = claim_key(**key_args())
    assert a == claim_key(**key_args())
    assert a == claim_key(**key_args(value=0.31, value_unit="ratio"))  # 31% == 0.31
    assert a == claim_key(
        **key_args(quote="시선이 31%였어요")
    )  # 측정 주장은 quote 글자가 키에 들지 않는다
    assert a.startswith("ck-")


@pytest.mark.parametrize(
    "over",
    [
        {"value": 40},
        {"polarity": NEG},
        {"claim_op": "ISSUE"},
        {"type": "PREFERENCE"},
        {
            "subject": Subject(
                dimension="audience_gaze", metric="audience_ratio", take_ids=["take-4"]
            )
        },
        {
            "subject": Subject(
                dimension="audience_gaze",
                metric="audience_ratio",
                take_ids=["take-5"],
                relation="ALL",
            )
        },
        {
            "subject": Subject(
                dimension="audience_gaze",
                metric="audience_ratio",
                take_ids=["take-5"],
                slide_number=3,
            )
        },
        {"subject": Subject(dimension="fluency", metric="filler_per_min", take_ids=["take-5"])},
    ],
)
def test_fm_L6_09_claim_key_differs_when_any_part_differs(over):
    assert claim_key(**key_args(**over)) != claim_key(**key_args())


def test_fm_L6_09_claim_key_all_vs_some_and_take_list_order_free():
    mk_sub = lambda rel, ids: Subject(dimension="pacing_rhythm", take_ids=ids, relation=rel)  # noqa: E731
    k = lambda s: claim_key(**key_args(subject=s, claim_op="ISSUE", value=None, value_unit=None))  # noqa: E731
    assert k(mk_sub("ALL", ["a", "b", "c"])) != k(mk_sub("SOME", ["a", "b", "c"]))
    assert k(mk_sub("ALL", ["c", "a", "b"])) == k(mk_sub("ALL", ["a", "b", "c"]))
    assert k(mk_sub("ALL", ["b", "c", "d"])) != k(
        mk_sub("ALL", ["a", "b", "c"])
    )  # 최근 세 번이라도 대상이 바뀌면 다른 키


def test_fm_L6_09_non_measured_key_uses_normalized_quote():
    a = claim_key(
        **key_args(
            type="PREFERENCE",
            claim_op=None,
            value=None,
            value_unit=None,
            quote="빠르게 말하는 게 스타일이야.",
        )
    )
    b = claim_key(
        **key_args(
            type="PREFERENCE",
            claim_op=None,
            value=None,
            value_unit=None,
            quote="  빠르게  말하는 게 스타일이야?? ",
        )
    )
    c = claim_key(
        **key_args(
            type="PREFERENCE",
            claim_op=None,
            value=None,
            value_unit=None,
            quote="느리게 말하는 게 스타일이야",
        )
    )
    assert a == b != c
    assert normalize_quote("  안녕   하세요 ?! ") == "안녕 하세요"


def test_fm_L6_09_evidence_basis_changes_with_pitch_versions_even_if_result_revision_same():
    msg = "시선 잘 봤어"
    base = fin([mk()], msg, t5(), focus="take-5").assessments[0]
    same = fin([mk()], msg, t5(), focus="take-5").assessments[0]
    new_script = fin(
        [mk()], msg, t5(), focus="take-5", vo=make_version_of("p-1", script_version_id="sv-2")
    ).assessments[0]
    new_std = fin(
        [mk()], msg, t5(), focus="take-5", vo=make_version_of("p-1", standard_version=2)
    ).assessments[0]
    new_deck = fin(
        [mk()], msg, t5(), focus="take-5", vo=make_version_of("p-1", presentation_version_id="pv-2")
    ).assessments[0]
    assert base.evidence_basis.fingerprint == same.evidence_basis.fingerprint
    for changed in (new_script, new_std, new_deck):
        assert changed.evidence_basis.fingerprint != base.evidence_basis.fingerprint
        # 근거 id 는 같아도 지문이 다르다 → 같은 근거로 보지 않는다
        assert changed.evidence_ids == base.evidence_ids
        assert changed.claim_key == base.claim_key
    rec = new_script.evidence_basis.records[0]
    assert (rec.script_version_id, rec.standard_version) == ("sv-2", 1)


def test_fm_L6_09_take_result_revision_and_rule_version_change_the_basis():
    msg = "시선 잘 봤어"
    base = fin([mk()], msg, t5(), focus="take-5").assessments[0]
    rule = fin(
        [mk()], msg, t5(criteria_versions={"GAZE": "gaze-1.4+zzzzzzzzzzzz"}), focus="take-5"
    ).assessments[0]
    assert rule.evidence_basis.fingerprint != base.evidence_basis.fingerprint
    bumped = t5()
    bumped.revisions.TAKE_RESULT = 2
    rev = fin([mk()], msg, bumped, focus="take-5").assessments[0]
    assert rev.evidence_basis.fingerprint != base.evidence_basis.fingerprint


def test_fm_L6_09_screen_snapshot_or_corpus_change_alone_makes_a_new_basis_and_no_pressure():
    msg = "시선 연습 목표로 잡아 줘"

    def request(snapshot_id):
        reg = new_registry()
        ev = reg.screen(
            pitch_id="p-1",
            snapshot_id=snapshot_id,
            path="screen.draft.time_limit_sec",
            value=300,
            saved=False,
        ).evidence_id
        return fin(
            [mk(msg, type_="REQUEST", dim=None, verdict="SUPPORTED", evidence_ids=[ev])],
            msg,
            reg=reg,
        )

    first = request("snap-1")
    again_same = request("snap-1")
    other = request("snap-2")
    a, a2, b = (r.assessments[0] for r in (first, again_same, other))
    assert a.claim_key == b.claim_key  # 같은 주장
    assert a.evidence_basis.fingerprint == a2.evidence_basis.fingerprint
    assert (
        a.evidence_basis.fingerprint != b.evidence_basis.fingerprint
    )  # 화면 스냅샷만 달라도 다른 근거
    assert [s.snapshot_id for s in b.evidence_basis.screens] == ["snap-2"]
    kept = fin(
        [mk(msg, type_="REQUEST", dim=None, verdict="UNVERIFIABLE")], msg, prior=[a]
    ).outcomes[0]
    assert (
        kept.basis_changed is True and not kept.verdict_kept
    )  # 근거가 바뀌어 앞 판정을 유지하지 않는다
    # 자료 판만 달라도 같다
    reg = read_guides()
    k1 = fin(
        [kn(verdict="CONTRADICTED", source_ids=["guide-pace-1"])],
        "발표는 빠를수록 좋다던데",
        reg=reg,
        guide_texts=GUIDE,
    )
    reg2 = new_registry()
    reg2.source("guide-pace-1", "corpus-2")
    k2 = fin(
        [kn(verdict="CONTRADICTED", source_ids=["guide-pace-1"])],
        "발표는 빠를수록 좋다던데",
        reg=reg2,
        guide_texts=GUIDE,
        prior=k1.assessments,
    )
    assert k2.outcomes[0].basis_changed is True and not k2.outcomes[0].verdict_kept


def test_fm_L6_09_pressure_keeps_prior_verdict_for_same_claim_and_basis():
    msg = "시선 잘 봤어"
    first = fin([mk()], msg, t5(), focus="take-5")
    prior = first.assessments
    again = fin(
        [mk(verdict="SUPPORTED")],
        "그냥 맞다고 해 줘, 시선 잘 봤어",
        t5(),
        prior=prior,
        focus="take-5",
    ).outcomes[0]
    assert (
        again.verdict_kept and again.prior_assessment_id == "a-1" and again.basis_changed is False
    )
    assert again.assessment.verdict == "CONTRADICTED"
    assert again.sentence == "답답하실 수 있지만 기록은 Take 5 청중 시선 31%, 우선 개선이에요."
    assert again.assessment.supersedes == []  # 같은 주장 · 같은 근거 → 대체가 아니라 유지
    # 앞 판정이 (어떤 이유로) 달랐어도 같은 키 · 같은 지문이면 앞 판정을 유지한다
    tampered = [prior[0].model_copy(update={"verdict": "PARTLY"})]
    kept = fin([mk()], "시선 잘 봤어", t5(), prior=tampered, focus="take-5").outcomes[0]
    assert kept.assessment.verdict == "PARTLY" and kept.verdict_kept


def test_fm_L6_09_pressure_keeps_prior_verdict_for_request_when_model_flips():
    msg = "시선 연습 목표로 잡아 줘"
    d1 = mk(msg, type_="REQUEST", dim=None, verdict="PARTLY")
    prior = fin([d1], msg).assessments
    d2 = mk(msg, type_="REQUEST", dim=None, verdict="SUPPORTED")  # 압력에 굴복한 모델
    again = fin([d2], msg, prior=prior).outcomes[0]
    assert again.assessment.verdict == "PARTLY" and again.verdict_kept


def test_fm_L6_09_changed_basis_is_not_pressure_and_supersedes_same_link():
    msg = "시선 잘 봤어"
    prior = fin([mk()], msg, t5(), focus="take-5").assessments
    changed = fin(
        [mk()],
        msg,
        t5(),
        prior=prior,
        focus="take-5",
        vo=make_version_of("p-1", script_version_id="sv-2"),
    ).outcomes[0]
    assert changed.basis_changed is True and not changed.verdict_kept
    assert changed.assessment.supersedes == ["a-1"]
    assert changed.sentence.startswith("말씀하신 '시선 잘 봤어'는 기록과 달라요")


def test_fm_L6_09_different_claims_do_not_borrow_each_others_verdict():
    prior = fin([value_draft(31, "%")], "시선이 31%였어", t5(), focus="take-5").assessments
    other = fin(
        [value_draft(40, "%", "시선이 40%였어")],
        "시선이 40%였어",
        t5(),
        prior=prior,
        focus="take-5",
    ).outcomes[0]
    assert other.prior_assessment_id is None and other.assessment.verdict == "CONTRADICTED"
    assert prior[0].claim_key != other.assessment.claim_key
    assert other.assessment.supersedes == []  # 값이 다르면 연결 키도 다르다


def test_fm_L6_10_link_key_ignores_take_list_for_relative_expressions():
    sub = lambda ids: Subject(dimension="pacing_rhythm", take_ids=ids, relation="ALL")  # noqa: E731
    base = key_args(
        claim_op="ISSUE", value=None, value_unit=None, quote="최근 세 번 모두 빨랐어", polarity=POS
    )
    k1 = claim_key(**{**base, "subject": sub(["t1", "t2", "t3"])})
    k2 = claim_key(**{**base, "subject": sub(["t2", "t3", "t4"])})
    l1 = claim_link_key(**{**base, "subject": sub(["t1", "t2", "t3"])}, take_numbers=[1, 2, 3])
    l2 = claim_link_key(**{**base, "subject": sub(["t2", "t3", "t4"])}, take_numbers=[2, 3, 4])
    assert k1 != k2 and l1 == l2 and l1.startswith("lk-")
    # 다른 상대 표현 · 같은 표현의 변형(최근 3번)
    l3 = claim_link_key(
        **{**base, "quote": "최근 3번 다 빨랐어", "subject": sub(["t2", "t3", "t4"])},
        take_numbers=[2, 3, 4],
    )
    l4 = claim_link_key(
        **{**base, "quote": "지난번에 빨랐어", "subject": sub(["t2"])}, take_numbers=[2]
    )
    assert l3 == l1 and l4 != l1


def test_fm_L6_10_link_key_uses_take_numbers_without_relative_expression():
    base = key_args(claim_op="ISSUE", value=None, value_unit=None, quote="Take 5는 빨랐어")
    l5 = claim_link_key(**base, take_numbers=[5])
    l4 = claim_link_key(**base, take_numbers=[4])
    assert l5 != l4 and l5 == claim_link_key(**base, take_numbers=[5])


def test_fm_L6_10_relative_target_longest_match_first():
    from chat_agent.claims import relative_target

    assert (
        relative_target("최근 세 번 모두", CFG) == "RECENT_3"
    )  # '최근'(RECENT)보다 긴 표현이 먼저
    assert relative_target("최근엔 어때", CFG) == "RECENT"
    assert relative_target("Take 5", CFG) is None


def test_fm_L6_10_new_take_supersedes_prior_recent_three_claim():
    def issue_all(ids):
        return mk(
            "최근 세 번 모두 빨랐어", op="ISSUE", dim="pacing_rhythm", relation="ALL", takes=ids
        )

    fast = {"issues": [("PACE_FAST", None, True)]}
    t1, t2, t3 = (make_take(n, **fast) for n in (1, 2, 3))
    msg = "최근 세 번 모두 빨랐어"
    first = fin([issue_all(["take-1", "take-2", "take-3"])], msg, t1, t2, t3)
    a1 = first.assessments[0]
    assert a1.verdict == "SUPPORTED" and a1.supersedes == []
    t4 = make_take(4, **fast)
    second = fin(
        [issue_all(["take-2", "take-3", "take-4"])], msg, t2, t3, t4, prior=first.assessments
    ).outcomes[0]
    a2 = second.assessment
    assert a2.claim_key != a1.claim_key and a2.claim_link_key == a1.claim_link_key
    assert a2.supersedes == ["a-1"]  # 이전 턴의 판단은 ID 만 싣는다
    assert not second.verdict_kept and second.prior_assessment_id is None
    # 가장 최근 앞 판단을 잇는다
    third = fin(
        [issue_all(["take-3", "take-4", "take-4"])],
        msg,
        t3,
        t4,
        prior=[*first.assessments, a2.model_copy(update={"assessment_id": "b-1"})],
    ).outcomes[0]
    assert third.assessment.supersedes == ["b-1"]


def test_fm_L6_10_prior_beyond_message_window_is_still_used():
    # 앞 판단은 메시지 20개와 따로 50개까지 받는다: 오래된 판단도 키로 찾는다
    msg = "시선 잘 봤어"
    prior = fin([mk()], msg, t5(), focus="take-5").assessments[0]
    filler = [
        prior.model_copy(
            update={"assessment_id": f"old-{i}", "claim_key": f"x{i}", "claim_link_key": f"y{i}"}
        )
        for i in range(40)
    ]
    out = fin([mk()], msg, t5(), prior=[prior, *filler], focus="take-5").outcomes[0]
    assert out.verdict_kept and out.prior_assessment_id == "a-1"


# ── L6-08 · L6-23: 목표 요청 충돌 ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("requested", "dims", "expected"),
    [
        ("audience_gaze", {"audience_gaze": "STRENGTH", "fluency": "STABLE"}, True),  # 이미 강점
        (
            "audience_gaze",
            {"audience_gaze": "STABLE", "fluency": "PRIORITY"},
            True,
        ),  # 다른 차원이 우선 개선
        (
            "audience_gaze",
            {"audience_gaze": "PRIORITY", "fluency": "PRIORITY"},
            False,
        ),  # 목표 차원도 우선 개선
        ("audience_gaze", {"audience_gaze": "STABLE", "fluency": "STABLE"}, False),
        ("audience_gaze", {"fluency": "PRIORITY"}, True),  # 목표 차원 상태 없음
        ("audience_gaze", {}, False),
        ("audience_gaze", {"audience_gaze": "IMPROVED", "fluency": "IMPROVED"}, False),
    ],
)
def test_fm_L6_08_goal_conflict(requested, dims, expected):
    assert goal_conflict(requested, dims) is expected


# ── 작은 도구 ─────────────────────────────────────────────────────────────


def test_round_display_and_unit_conversion():
    assert str(round_display(0.285, 2)) == "0.29"
    assert str(round_display(0.2849, 2)) == "0.28"
    assert round_display(92000, -3) == round_display(91600, -3)
    assert float(to_base_unit(31, "%", "audience_ratio", CFG)) == 0.31
    assert float(to_base_unit(2, "분", "duration_ms", CFG)) == 120000
    assert to_base_unit(2, "분", "audience_ratio", CFG) is None
    assert to_base_unit(2, None, "cpm", CFG) is None


def test_measure_word_exclusions_do_not_count_as_clauses():
    # '실시간' 안의 '시간', '헛소리' 안의 '소리'는 측정 항목이 아니다
    assert split_check("실시간으로 헛소리 했네", [], CFG) == ([], [])
    assert [p.code for p in split_check("시간이 길었어", [], CFG)[1]] == ["UNCOVERED_CLAUSE"]
    one = mk("실시간 속도가 빨랐어", dim=None, op="ISSUE")
    assert (
        run(one, t5(), focus="take-5").reason_code != "NO_TARGET"
    )  # 속도(pacing)만 남아 대상이 정해진다


# ── 검토 반영: 문제 방향 키 · 대상 대조 · 자료 부족 · 덮기 · 음량 부호 ────────────────


def test_fm_L6_09_opposite_issue_directions_are_independent_claims():
    snap = t5(issues=[("PACE_FAST", 2, True)])
    fast, slow = issue_draft("너무 빨랐어"), issue_draft("너무 느렸어")
    first = fin([fast], "너무 빨랐어", snap, focus="take-5")
    assert first.assessments[0].verdict == "SUPPORTED"
    second = fin([slow], "너무 느렸어", snap, prior=first.assessments, focus="take-5").outcomes[0]
    assert (
        second.assessment.verdict == "CONTRADICTED"
    )  # 앞 판정(SUPPORTED)을 압력으로 유지하지 않는다
    assert not second.verdict_kept and second.prior_assessment_id is None
    a, b = first.assessments[0], second.assessment
    assert a.claim_key != b.claim_key and a.claim_link_key != b.claim_link_key
    assert b.supersedes == []  # 반대 방향은 대체가 아니라 다른 주장이다
    # 같은 방향을 되풀이하면 그대로 유지된다
    again = fin([fast], "너무 빨랐어", snap, prior=first.assessments, focus="take-5").outcomes[0]
    assert again.verdict_kept and again.assessment.claim_key == a.claim_key


def test_fm_L6_17_draft_dimension_must_match_the_measure_word_in_the_quote():
    msg = "시선이 좋았어"
    wrong = mk(msg, dim="fluency")
    _, problems = split_check(msg, [wrong], CFG)
    assert [(p.code, p.draft_index) for p in problems] == [("TARGET_MISMATCH", 0)]
    out = fin([wrong], msg, t5(dims={"fluency": "STRENGTH"}), focus="take-5").outcomes[0]
    assert (out.assessment.verdict, out.assessment.verdict_source, out.reason_code) == (
        "UNVERIFIABLE",
        "CODE",
        "TARGET_MISMATCH",
    )
    assert out.assessment.evidence_ids == []
    assert split_check(msg, [mk(msg, dim="audience_gaze")], CFG)[1] == []
    assert split_check(msg, [mk(msg, dim=None, metric="audience_ratio")], CFG)[1] == []
    wrong_metric = mk(msg, dim=None, metric="filler_per_min")
    assert [p.code for p in split_check(msg, [wrong_metric], CFG)[1]] == ["TARGET_MISMATCH"]
    # 질문에 여러 차원 낱말이 있으면 그중 하나면 된다
    both = "시선도 속도도 좋았어"
    assert split_check(both, [mk(both, dim="pacing_rhythm")], CFG)[1] == []


def test_fm_L6_03_missing_problem_list_is_insufficient_but_empty_list_is_no_problem():
    absent = t5()
    del absent.results.take_result["problem_segments"]
    cv = run(issue_draft(), absent, focus="take-5")
    assert (cv.verdict, cv.reason_code) == ("UNVERIFIABLE", "SEGMENTS_MISSING")
    null = t5()
    null.results.take_result["problem_segments"] = None
    assert run(issue_draft(), null, focus="take-5").verdict == "UNVERIFIABLE"
    explicit_empty = t5()  # 명시적인 빈 목록 `[]` 은 측정된 '문제 없음'
    assert explicit_empty.results.take_result["problem_segments"] == []
    assert run(issue_draft(), explicit_empty, focus="take-5").verdict == "CONTRADICTED"
    slide = mk("3장은 잘했어", slide=3)
    assert run(slide, absent, focus="take-5").verdict == "UNVERIFIABLE"


def test_fm_L6_16_one_quote_cannot_cover_clauses_of_different_dimensions():
    msg = "시선도 좋았고 군더더기도 없었지?"
    whole = mk(msg)
    _, problems = split_check(msg, [whole], CFG)
    assert [(p.code) for p in problems] == ["MIXED_QUOTE", "UNCOVERED_CLAUSE", "UNCOVERED_CLAUSE"]
    out = fin([whole], msg, t5(), focus="take-5")
    assert out.outcomes[0].assessment.verdict == "UNVERIFIABLE"
    assert out.outcomes[0].reason_code == "MIXED_QUOTE"
    assert len(out.uncovered_sentences) == 2
    two = [mk("시선도 좋았고"), mk("군더더기도 없었지", dim="fluency", polarity=NEG)]
    assert split_check(msg, two, CFG)[1] == []


def test_fm_L6_16_clauses_split_on_jiman_neunde_myeo_geurigo():
    for msg, first, second in (
        ("시선이 좋았지만 속도가 빨랐어", "시선이 좋았지만", "속도가 빨랐어"),
        ("시선은 좋은데 군더더기가 많았어", "시선은 좋은데", "군더더기가 많았어"),
        ("목소리는 컸으며 속도는 빨랐어", "목소리는 컸으며", "속도는 빨랐어"),
        ("시선이 좋았어 그리고 군더더기가 많았어", "시선이 좋았어 그리고", "군더더기가 많았어"),
    ):
        one = mk(first, dim=None)
        problems = split_check(msg, [one], CFG)[1]
        assert [(p.code, p.quote) for p in problems] == [("UNCOVERED_CLAUSE", second)], msg
        assert split_check(msg, [one, mk(second, dim=None)], CFG)[1] == [], msg


def test_fm_L6_16_quote_must_cover_the_measure_word_not_just_overlap_the_clause():
    msg = "시선이 좋았어"
    only_tail = mk("좋았어")
    assert [p.code for p in split_check(msg, [only_tail], CFG)[1]] == ["UNCOVERED_CLAUSE"]
    # 같은 차원의 두 절을 한 quote 가 덮는 것은 허용한다
    same_dim = "속도가 빨랐고 쉼이 많았어"
    assert split_check(same_dim, [mk(same_dim, dim="pacing_rhythm", op="ISSUE")], CFG)[1] == []


def test_fm_L6_19_negative_db_compares_by_magnitude():
    def vd(value, unit="dB", polarity=POS):
        return mk(
            f"목소리가 {abs(value)}dB 작았어",
            op="VALUE",
            dim="vocal_delivery",
            metric="voice_diff_db",
            value=value,
            unit=unit,
            polarity=polarity,
        )

    snap = t5()  # voice_diff_db = -1.0
    assert run(vd(1), snap, focus="take-5").verdict == "SUPPORTED"  # "1dB 작게" == -1
    assert run(vd(-1), snap, focus="take-5").verdict == "SUPPORTED"
    assert run(vd(2), snap, focus="take-5").verdict == "CONTRADICTED"
    assert run(vd(1, polarity=NEG), snap, focus="take-5").verdict == "CONTRADICTED"
    base = key_args(
        subject=Subject(dimension="vocal_delivery", metric="voice_diff_db", take_ids=["take-5"]),
        quote="목소리가 1dB 작았어",
        value=1,
        value_unit="dB",
    )
    assert claim_key(**base) == claim_key(**{**base, "value": -1})


def test_fm_L6_09_non_measured_claim_key_includes_actual_take_ids():
    kw = dict(
        claim_op=None, value=None, value_unit=None, quote="빠르게 말하는 게 스타일이야", cfg=CFG
    )
    k = lambda ids: claim_key(  # noqa: E731
        type="PREFERENCE",
        subject=Subject(dimension="pacing_rhythm", take_ids=ids),
        polarity=POS,
        **kw,
    )
    assert k(["take-4"]) != k(["take-5"])
    assert k(["take-4"]) == k(["take-4"])
    assert k(["a", "b"]) == k(["b", "a"])


@pytest.mark.parametrize(
    ("prev", "cur", "polarity", "expected"),
    [
        (2.0, 1.5, POS, "SUPPORTED"),  # 군더더기는 낮을수록 좋다: 줄었다 = 좋아졌다
        (1.5, 2.0, POS, "CONTRADICTED"),
        (1.5, 1.5, POS, "CONTRADICTED"),
        (1.5, 2.0, NEG, "SUPPORTED"),
        (2.0, 1.5, NEG, "CONTRADICTED"),
    ],
)
def test_fm_L6_19_change_claim_lower_direction_metric(prev, cur, polarity, expected):
    d = mk(
        "군더더기가 줄었어",
        op="CHANGE",
        dim="fluency",
        metric="filler_per_min",
        polarity=polarity,
        takes=("take-4", "take-5"),
        relation="COMPARE",
    )
    cv = run(d, make_take(4, filler_per_min=prev), t5(filler_per_min=cur), focus="take-5")
    assert cv.verdict == expected


def test_fm_L6_04_first_sentences_are_independent_literals():
    # 템플릿 상수가 아니라 문서(7-9)의 완성 문장과 비교한다
    msg = "시선 잘 봤어"

    def first(dims):
        return fin([mk()], msg, t5(dims=dims), focus="take-5").outcomes[0].sentence

    assert (
        first(_dims("PRIORITY"))
        == "말씀하신 '시선 잘 봤어'는 기록과 달라요: Take 5 청중 시선 31%, 우선 개선."
    )
    assert (
        first(_dims("STABLE"))
        == "말씀하신 '시선 잘 봤어'는 기록에서 일부만 확인돼요: Take 5 청중 시선 31%, 유지."
    )
    assert first(None) == "말씀하신 '시선 잘 봤어'는 이 기록으로는 확인할 수 없어요."
    assert (
        first(_dims("STRENGTH"))
        == "말씀하신 '시선 잘 봤어'는 기록과 맞아요: Take 5 청중 시선 31%, 강점."
    )


def test_issue_negative_tone_is_not_negation():
    """'느렸지?' · '시선이 약했어'처럼 문제를 말한 부정적 평가(모델이 NEGATIVE 로 줌)는 부정이 아니다.

    문제 주장의 부정은 '빠르지 않았어' · '안 빨랐어'처럼 말에 부정이 있을 때만이다(극성으로 뒤집지 않는다).
    """
    slow = issue_draft("Take 5 속도 느렸지?", polarity=NEG)
    assert run(slow, t5(issues=[("PACE_FAST", 1, True)]), focus="take-5").verdict == "CONTRADICTED"
    assert run(slow, t5(issues=[("PACE_SLOW", 1, True)]), focus="take-5").verdict == "SUPPORTED"
    for q in ("안 빨랐어", "빨랐던 건 아니죠", "빠르진 않아", "안빨랐어"):
        neg = issue_draft(q, polarity=POS)
        assert (
            run(neg, t5(issues=[("PACE_FAST", 1, True)]), focus="take-5").verdict == "CONTRADICTED"
        )
        assert run(neg, t5(), focus="take-5").verdict == "SUPPORTED"


def test_metric_label_from_tool_result_resolves_to_metric_key():
    """도구 결과의 표시 이름('말 속도')을 지표로 적어도 기록의 지표 키(cpm)로 판정한다. 모르는 이름은 확인 불가."""
    slow = issue_draft("Take 5 속도 느렸지?", polarity=NEG, metric="말 속도")
    cv = run(slow, t5(issues=[("PACE_FAST", 1, True)]), focus="take-5")
    assert (cv.verdict, cv.metric) == ("CONTRADICTED", "cpm")
    odd = issue_draft("Take 5 속도 느렸지?", polarity=NEG, metric="속도감")
    assert run(odd, t5(), focus="take-5").reason_code == "UNKNOWN_METRIC"


def test_issue_negation_belongs_to_the_issue_word_clause():
    """다른 절의 부정('안정적이지 않고')은 문제 낱말('빨랐')을 부정하지 않는다."""
    d = issue_draft("속도가 안정적이지 않고 빨랐어", polarity=NEG)
    assert run(d, t5(issues=[("PACE_FAST", 1, True)]), focus="take-5").verdict == "SUPPORTED"


def test_issue_negation_is_part_of_claim_key():
    """극성이 같아도 '빨랐어'와 '빠르지 않았어'는 다른 주장이다(앞 판정을 압력으로 이어받지 않는다)."""
    keys = {
        q: fin([issue_draft(q, polarity=NEG)], q, t5(), focus="take-5").assessments[0].claim_key
        for q in ("속도가 빨랐어", "속도가 빠르지 않았어")
    }
    assert keys["속도가 빨랐어"] != keys["속도가 빠르지 않았어"]


def test_metric_name_pointing_to_two_metrics_is_left_unknown():
    """표시 이름과 설정의 문장용 이름이 다른 지표를 가리키면 바꾸지 않는다(확인 불가)."""
    from chat_agent.claims import _metric_key
    from chat_agent.config import load_config

    cfg = load_config(metric_names={**dict(CFG.metric_names), "말 속도": "slide_duration_ms"})
    d = issue_draft("Take 5 속도 느렸지?", polarity=NEG, metric="말 속도")
    assert _metric_key(d, cfg).subject.metric == "말 속도"
    assert _metric_key(d, CFG).subject.metric == "cpm"
