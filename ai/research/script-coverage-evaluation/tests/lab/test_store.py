"""store: 평가 기준 · 평가 결과 저장과 읽기, 비슷한 말 표, 사용자 확인."""

import json
from datetime import datetime

import pytest

from coverage_lab.store import (
    confirm_similar_item,
    confirmed_evaluation,
    confirmed_take_scores,
    connect,
    load_confirmations,
    load_evaluation,
    load_rubric,
    load_rubrics,
    load_similar_items,
    load_take_evaluations,
    save_evaluation,
    save_rubric,
)
from pitch_coach_ai.features.script_coverage.shared.rubric import EvaluationRubric, RubricMeta
from pitch_coach_ai.features.script_coverage.stt_evaluation.schemas import (
    SimilarItem,
    SlideEvaluation,
    SlideSTT,
    Take,
)


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "t.sqlite")
    yield connection
    connection.close()


def _rubric(script_name="대본", slide_number=1, rubric_id="rid1") -> EvaluationRubric:
    meta = RubricMeta(
        rubric_id=rubric_id,
        script_name=script_name,
        slide_number=slide_number,
        content_hash=f"hash{slide_number}",
        rubric_schema_version="1.2",
        llm_model="m",
        llm_config_hash="a",
    )
    return EvaluationRubric(
        meta=meta,
        normalized_script="",
        sentences=[],
        sentence_roles=[],
        core_claim="핵심",
        key_points=[],
        critical_facts=[],
        keywords=[],
        warnings=[],
    )


def _item(item_id="R1", script_text="좌석", stt_text="자석", **overrides) -> SimilarItem:
    fields = dict(
        item_id=item_id,
        kind="word",
        script_text=script_text,
        stt_text=stt_text,
        script_sentence_index=0,
        script_span=(9, 11),
        stt_sentence_index=0,
        stt_span=(11, 13),
        stt_raw_span=(11, 13),
        fact_id=None,
        key_point_ids=["KP1"],
        signals=["대본 단어가 빠지고 발음이 비슷한 다른 말이 나옴"],
        rule_guess="asr_error",
    )
    return SimilarItem(**(fields | overrides))


def _evaluation(slide_number=1, items=(), rubric_id="rid1") -> SlideEvaluation:
    return SlideEvaluation(
        take_id="대본_take1",
        script_name="대본",
        slide_number=slide_number,
        rubric_id=rubric_id,
        scores={"content_coverage": 1.0},
        sentences=[],
        key_points=[],
        critical_facts=[],
        similar_items=list(items),
        fidelity={},
        alignments=[],
        verification={"required": False},
        stt_sentences=[],
        stt_text="말했다",
    )


def test_connect_creates_all_tables_and_is_repeatable(tmp_path):
    path = tmp_path / "sub" / "t.sqlite"  # 폴더가 없어도 만든다
    connect(path).close()
    conn = connect(path)  # 다시 열어도 (IF NOT EXISTS) 오류 없음
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == {
        "semantic_cache",
        "semantic_samples",
        "final_cache",
        "unit_cache",
        "evaluation_rubrics",
        "stt_llm_cache",
        "slide_evaluations",
        "similar_items",
        "similar_confirmations",
        "confirmed_evaluations",
    }
    conn.close()


def test_rubric_round_trip(conn):
    rubric = _rubric()
    save_rubric(conn, rubric)
    assert load_rubric(conn, "대본", 1) == rubric
    assert load_rubric(conn, "대본", 2) is None
    # 시각은 코어가 아니라 저장할 때 DB 칸에 넣는다
    [(content_hash, created_at)] = conn.execute(
        "SELECT content_hash, created_at FROM evaluation_rubrics"
    ).fetchall()
    assert content_hash == "hash1" and datetime.fromisoformat(created_at).tzinfo is not None


def test_load_rubrics_returns_one_script_ordered_by_slide(conn):
    for script, slide in [("대본", 3), ("대본", 1), ("다른 대본", 2)]:
        save_rubric(conn, _rubric(script, slide))
    assert list(load_rubrics(conn, "대본")) == [1, 3]
    assert load_rubrics(conn, "없는 대본") == {}


def test_evaluation_round_trip_with_similar_items(conn):
    evaluation = _evaluation(items=[_item("R1"), _item("R2", "30분", "삼 분", kind="number")])
    save_evaluation(conn, evaluation)
    assert load_evaluation(conn, "대본_take1", 1) == evaluation
    assert load_evaluation(conn, "대본_take1", 2) is None
    items = load_similar_items(conn, "대본_take1")
    assert list(items["item_id"]) == ["R1", "R2"]
    row = items.iloc[0]
    assert (row["script_start"], row["script_end"], row["stt_raw_start"]) == (9, 11, 11)
    assert json.loads(row["key_point_ids"]) == ["KP1"]
    assert load_similar_items(conn, "대본_take1", slide_number=2).empty


def test_resaving_replaces_similar_items_and_drops_stale_confirmation_result(conn):
    save_evaluation(conn, _evaluation(items=[_item("R1")]))
    conn.execute(
        "INSERT INTO confirmed_evaluations VALUES (?, ?, ?, ?)", ("대본_take1", 1, "{}", "t")
    )
    save_evaluation(conn, _evaluation(items=[_item("R7")]))
    assert list(load_similar_items(conn, "대본_take1")["item_id"]) == ["R7"]
    assert conn.execute("SELECT count(*) FROM confirmed_evaluations").fetchone() == (0,)


def test_load_take_evaluations_follows_take_slide_order_and_skips_missing(conn):
    save_evaluation(conn, _evaluation(slide_number=2))
    save_evaluation(conn, _evaluation(slide_number=1))
    take = Take(
        script_name="대본",
        take_id="대본_take1",
        slides=[SlideSTT(slide_number=n, stt="") for n in (2, 3, 1)],
    )
    assert [e.slide_number for e in load_take_evaluations(conn, take)] == [2, 1]


def test_confirm_similar_item_validates_input(conn):
    save_evaluation(conn, _evaluation(items=[_item("R1")]))
    with pytest.raises(ValueError, match="answer 는"):
        confirm_similar_item(conn, "대본_take1", 1, "R1", "maybe")
    with pytest.raises(ValueError, match="비슷한 말 R9 가 없다"):
        confirm_similar_item(conn, "대본_take1", 1, "R9", "as_script")
    with pytest.raises(ValueError, match="비슷한 말 R1 가 없다"):
        confirm_similar_item(conn, "없는_take", 1, "R1", "as_script")


def test_confirmation_is_saved_and_applied_only_to_same_wording(conn):
    save_rubric(conn, _rubric())
    evaluation = _evaluation(items=[_item("R1")])
    save_evaluation(conn, evaluation)
    confirmed = confirm_similar_item(conn, "대본_take1", 1, "R1", "as_script")
    assert confirmed.applied_answers == {"R1": "as_script"}
    assert conn.execute(
        "SELECT item_id, script_text, stt_text, answer FROM similar_confirmations"
    ).fetchall() == [("R1", "좌석", "자석", "as_script")]
    assert conn.execute("SELECT count(*) FROM confirmed_evaluations").fetchone() == (1,)
    assert load_confirmations(conn, evaluation) == {"R1": "as_script"}
    # 다시 채점해서 같은 번호의 항목이 다른 표현이 되면 이전 답을 적용하지 않는다
    changed = _evaluation(items=[_item("R1", "센서", "센터")])
    assert load_confirmations(conn, changed) == {}
    # 표현이 바뀐 채로 다시 저장하면 이전 답은 쓰이지 않고, 처음 채점 결과가 그대로 나온다
    save_evaluation(conn, changed)
    assert confirmed_evaluation(conn, "대본_take1", 1) == changed
    assert confirmed_evaluation(conn, "대본_take1", 9) is None


def test_confirmations_accumulate_through_core_rescore(conn):
    save_rubric(conn, _rubric())
    save_evaluation(conn, _evaluation(items=[_item("R1"), _item("R2", "센서", "센터")]))
    confirm_similar_item(conn, "대본_take1", 1, "R1", "as_script")
    both = confirm_similar_item(conn, "대본_take1", 1, "R2", "as_stt")
    # 두 번째 확인이 첫 번째 답을 잃지 않는다 (저장된 답 전부를 처음 채점에 한 번에 반영)
    assert both.applied_answers == {"R1": "as_script", "R2": "as_stt"}
    assert confirmed_evaluation(conn, "대본_take1", 1) == both


def test_confirmed_take_scores_include_scored_ratio(conn):
    save_rubric(conn, _rubric())
    save_evaluation(conn, _evaluation(items=[_item("R1")]))
    confirm_similar_item(conn, "대본_take1", 1, "R1", "as_script")
    scores = confirmed_take_scores(conn, "대본_take1")
    assert (
        scores["scored_ratio"] == 1.0 and scores["similar_words"] == 0
    )  # 답한 항목은 센 값에서 빠진다
    # 연습의 대상 장을 주면 채점이 없는(실패한) 장도 분모에 든다
    assert confirmed_take_scores(conn, "대본_take1", [1, 2])["scored_ratio"] == 0.5
