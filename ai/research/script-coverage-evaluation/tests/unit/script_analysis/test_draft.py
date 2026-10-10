from script_coverage.script_analysis.draft import (
    importance_from_roles,
    key_term_rejection,
    link_facts_to_key_points,
    make_key_points,
    merge_key_terms,
    new_rubric,
    resolve_roles,
    validate_rubric,
)
from script_coverage.script_analysis.schemas import KeyPointDraft, SentenceLabel, SlideScript
from script_coverage.shared.facts import extract_critical_facts
from script_coverage.shared.text import normalize_script
from tests.unit.shared.slides import SLIDES

# 기대값은 원본 노트북 셀 코드를 같은 입력으로 돌려 얻은 것이다


def test_resolve_roles_fills_missing_and_drops_invalid():
    labels = [
        SentenceLabel(id=0, role="claim"),
        SentenceLabel(id=2, role="skip"),
        SentenceLabel(id=9, role="evidence"),
        SentenceLabel(id=-1, role="detail"),
    ]
    warnings: list[str] = []
    assert resolve_roles(labels, 4, warnings) == ["claim", "detail", "skip", "detail"]
    assert warnings == ["missing_sentence_roles:1,3"]


def test_importance_from_roles():
    assert importance_from_roles(["detail", "evidence"]) == "high"
    assert importance_from_roles(["skip"]) == "normal"
    assert importance_from_roles([]) == "normal"
    assert importance_from_roles(["claim", "evidence"]) == "critical"


def test_make_key_points():
    norm = normalize_script(SLIDES[("가상대본1", 4)])
    roles = ["claim", "evidence", "detail", "skip"]
    warnings: list[str] = []
    drafts = [
        KeyPointDraft(content="a", sentence_ids=[1, 0, 5], key_terms=["XGBoost"]),
        KeyPointDraft(content="b", sentence_ids=[], key_terms=[]),
        KeyPointDraft(content="c", sentence_ids=[2, 3], key_terms=[]),
    ]
    kps = make_key_points(drafts, roles, norm, warnings)
    assert [(k.id, k.importance, k.sentence_indices, k.source_span, k.key_terms) for k in kps] == [
        ("KP1", "critical", [0, 1], (0, 112), ["XGBoost"]),
        ("KP2", "normal", [], None, []),
        ("KP3", "normal", [2, 3], (113, 218), []),
    ]
    assert warnings == ["invalid_sentence_id:KP1:5"]


DECK2 = [normalize_script(SLIDES[("가상대본2", n)]).text for n in (1, 8, 9)]


def test_key_term_rejection():
    assert key_term_rejection("프로젝트 제목 같은 긴 구절", DECK2) == "phrase"
    assert key_term_rejection("폐기", DECK2) == "common_in_deck"
    assert key_term_rejection("빵집", DECK2) == "common_in_deck"
    assert key_term_rejection("에이전트", DECK2) is None
    assert key_term_rejection("SeatFlow", DECK2) is None
    assert key_term_rejection("폐기", [DECK2[1]]) is None


def _slide4_key_points(norm):
    drafts = [
        KeyPointDraft(
            content="예측 모델은 약 1,240만 건으로 학습했다",
            sentence_ids=[0],
            key_terms=["제휴 도서관"],
        ),
        KeyPointDraft(
            content="XGBoost 를 선택했다",
            sentence_ids=[1],
            key_terms=["XGBoost", "없는말", "시계열 교차검증"],
        ),
        KeyPointDraft(
            content="평균 오차는 좌석 2.4개이고 99% 이다", sentence_ids=[2], key_terms=[]
        ),
    ]
    return make_key_points(drafts, ["claim", "evidence", "detail", "detail"], norm, [])


def test_merge_link_validate():
    norm = normalize_script(SLIDES[("가상대본1", 4)])
    kps = _slide4_key_points(norm)
    warnings: list[str] = []
    merged = merge_key_terms(kps, extract_critical_facts(norm), norm, [norm.text], warnings)
    assert [(f.id, f.type, f.value, f.source, f.spans) for f in merged] == [
        ("CF1", "term", "제휴 도서관", "llm", [(7, 13)]),
        ("CF2", "quantity", "3곳", "rule", [(14, 16)]),
        ("CF3", "duration", "2년", "rule", [(18, 20)]),
        ("CF4", "quantity", "약 1,240만 건", "rule", [(30, 40)]),
        ("CF5", "proper_noun", "XGBoost", "rule", [(96, 103)]),
        ("CF6", "term", "시계열 교차검증", "llm", [(169, 177)]),
        ("CF7", "duration", "30분", "rule", [(186, 189)]),
        ("CF8", "quantity", "2.4개", "rule", [(209, 213)]),
        ("CF9", "ratio", "3:1", "rule", [(234, 237)]),
    ]
    assert warnings == ["key_term_not_found:KP2:없는말"]

    link_facts_to_key_points(kps, merged, warnings)
    assert [(k.id, k.fact_ids) for k in kps] == [
        ("KP1", ["CF1", "CF2", "CF3", "CF4"]),
        ("KP2", ["CF5", "CF6"]),
        ("KP3", []),
    ]
    assert [(f.id, f.key_point_ids, f.importance) for f in merged] == [
        ("CF1", ["KP1"], "critical"),
        ("CF2", ["KP1"], "critical"),
        ("CF3", ["KP1"], "critical"),
        ("CF4", ["KP1"], "critical"),
        ("CF5", ["KP2"], "high"),
        ("CF6", ["KP2"], "high"),
        ("CF7", [], "normal"),
        ("CF8", [], "normal"),
        ("CF9", [], "normal"),
    ]
    assert warnings == [
        "key_term_not_found:KP2:없는말",
        "unsupported_number_in_key_point:KP3:99%",
    ]

    check: list[str] = []
    validate_rubric(kps, merged, ["claim", "claim", "detail", "detail"], check)
    assert check == ["too_many_claims:2", "uncovered_sentences:3", "unlinked_facts:CF7,CF8,CF9"]


def test_validate_rubric_without_key_points():
    warnings: list[str] = []
    validate_rubric([], [], ["evidence"], warnings)
    assert warnings == ["uncovered_sentences:0", "no_key_points"]


def test_merge_key_terms_drops_nested_names():
    norm = normalize_script("서울·대전 권역 안의 서울과 SeatFlow 입니다.")
    kps = make_key_points(
        [KeyPointDraft(content="x", sentence_ids=[0], key_terms=["서울", "서울·대전 권역"])],
        ["claim"],
        norm,
        [],
    )
    warnings: list[str] = []
    merged = merge_key_terms(kps, [], norm, [norm.text], warnings)
    assert [(f.id, f.value, f.spans) for f in merged] == [
        ("CF1", "서울·대전 권역", [(0, 8)]),
        ("CF2", "서울", [(12, 14)]),
    ]
    assert warnings == []


def test_new_rubric_meta_follows_config_hash_formula():
    slide = SlideScript(slide_number=7, script=SLIDES[("가상대본1", 7)])
    norm = normalize_script(slide.script)
    facts = extract_critical_facts(norm)
    for i, fact in enumerate(facts, 1):
        fact.id = f"CF{i}"
    rubric = new_rubric(
        "t", slide, norm, [], ["claim", "evidence", "skip"], "c", [], facts, [], "test-model"
    )
    assert rubric.meta.rubric_id == "fd10aeab54b498bf"
    assert rubric.meta.llm_model == "test-model"
    assert rubric.meta.llm_config_hash == "082c56a55231"
    assert rubric.meta.final_config_hash == ""
    assert rubric.meta.unit_config_hash == ""
