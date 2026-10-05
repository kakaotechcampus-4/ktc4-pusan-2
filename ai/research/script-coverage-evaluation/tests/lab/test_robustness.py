"""실패했을 때 DB · 결과 파일이 망가지지 않는지. LLM 은 부르면 바로 실패하는 객체로 막는다 (가짜 응답 없음)."""

import json

import pytest

from coverage_lab.cache import SqliteLLMCache
from coverage_lab.llm import Settings
from coverage_lab.runs import run_script_analysis
from coverage_lab.script_analysis import consistency
from coverage_lab.script_analysis.consistency import sample_rubrics
from coverage_lab.store import (
    confirm_similar_item,
    connect,
    load_evaluation,
    load_rubric,
    load_similar_items,
    save_evaluation,
    save_rubric,
)
from coverage_lab.stt_evaluation.stability import check_baseline, repeat_runs
from script_coverage.script_analysis.schemas import SlideSemanticAnalysis
from script_coverage.script_analysis.semantic import semantic_config_hash
from script_coverage.shared.text import normalize_script
from script_coverage.stt_evaluation.schemas import SlideSTT, Take
from tests.lab.test_store import _evaluation, _item, _rubric

MODEL = "m"
SETTINGS = Settings(base_url="http://127.0.0.1:9/v1", api_key="dummy", model=MODEL)


class NoLLM:
    """부르면 바로 실패한다."""

    def invoke(self, messages):
        raise RuntimeError("LLM 호출이 일어났다")


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "t.sqlite")
    yield connection
    connection.close()


def _take(slides=(1,)):
    return Take(
        script_name="대본",
        take_id="대본_take1",
        slides=[SlideSTT(slide_number=n, stt="말") for n in slides],
    )


# ── 1. 반복 채점 전 기준(0번) 검사 ───────────────────────────


def test_baseline_ok_when_every_slide_is_saved_with_current_rubric(conn):
    save_rubric(conn, _rubric(slide_number=1))
    evaluation = _evaluation(slide_number=1)
    check_baseline(conn, [_take()], {"대본_take1": [evaluation]})


def test_baseline_rejects_missing_evaluation(conn):
    save_rubric(conn, _rubric(slide_number=1))
    save_rubric(conn, _rubric(slide_number=2))
    with pytest.raises(
        RuntimeError, match=r"evaluate_takes\.py.*슬라이드 2: 처음 채점 결과가 없다"
    ):
        check_baseline(conn, [_take((1, 2))], {"대본_take1": [_evaluation(slide_number=1)]})


def test_baseline_rejects_stale_rubric(conn):
    save_rubric(conn, _rubric(slide_number=1, rubric_id="새 기준"))
    with pytest.raises(RuntimeError, match="평가 기준이 바뀌었거나 없다"):
        check_baseline(conn, [_take()], {"대본_take1": [_evaluation(rubric_id="옛 기준")]})


def test_baseline_rejects_empty_takes(conn):
    with pytest.raises(RuntimeError, match="채점할 연습이 없다"):
        check_baseline(conn, [], {})


def test_repeat_runs_stops_before_any_llm_call(conn):
    llms = {"eval_llm": NoLLM(), "verifier_llm": NoLLM()}
    with pytest.raises(RuntimeError, match="온전하지 않다"):
        repeat_runs(conn, [_take()], SETTINGS, {"대본_take1": []}, 3, llms=llms)


# ── 2. 분석이 실패한 슬라이드의 예전 평가 기준은 지운다 ──────


def test_failed_slide_loses_its_stale_rubric(conn, tmp_path):
    path = tmp_path / "대본.json"
    path.write_text(
        json.dumps(
            [{"slide_number": 1, "script": "안녕하세요. 발표를 시작합니다."}], ensure_ascii=False
        ),
        encoding="utf-8",
    )
    save_rubric(conn, _rubric("대본", 1))  # 대본을 고치기 전에 만든 기준
    save_rubric(conn, _rubric("대본", 5))  # 이번 분석 대상이 아닌 슬라이드는 건드리지 않는다
    llms = {"semantic_llm": NoLLM(), "final_llm": NoLLM(), "unit_llm": NoLLM()}
    rubrics, stats = run_script_analysis(path, conn, SETTINGS, llms=llms)
    assert rubrics == [] and list(stats["failed"]) == [1]
    assert load_rubric(conn, "대본", 1) is None
    assert load_rubric(conn, "대본", 5) is not None


# ── 4. 여러 문장 저장은 한 번에 (실패하면 되돌림) ────────────


def test_failed_save_evaluation_rolls_back_everything(conn):
    old = _evaluation(items=[_item("R1")])
    save_evaluation(conn, old)
    conn.execute(
        "INSERT INTO confirmed_evaluations VALUES (?, ?, ?, ?)", ("대본_take1", 1, "{}", "t")
    )
    conn.commit()
    duplicated = _evaluation(items=[_item("R2"), _item("R2")], rubric_id="바뀐 기준")
    with pytest.raises(Exception, match="UNIQUE"):
        save_evaluation(conn, duplicated)
    conn.commit()  # 뒤이은 다른 저장이 commit 해도 부분 변경이 새어 나오지 않는다
    assert load_evaluation(conn, "대본_take1", 1) == old
    assert list(load_similar_items(conn, "대본_take1")["item_id"]) == ["R1"]
    assert conn.execute("SELECT count(*) FROM confirmed_evaluations").fetchone() == (1,)


def test_failed_recompute_does_not_keep_confirmation(conn):
    save_evaluation(
        conn, _evaluation(items=[_item("R1")])
    )  # 평가 기준이 DB 에 없어 다시 계산이 실패한다
    with pytest.raises(AttributeError):
        confirm_similar_item(conn, "대본_take1", 1, "R1", "as_script")
    conn.commit()
    assert conn.execute("SELECT count(*) FROM similar_confirmations").fetchone() == (0,)


# ── 6. 일관성 측정의 0번 1차 분석 읽기 ───────────────────────


class _Stop(Exception):
    def __init__(self, core_claim):
        self.core_claim = core_claim


def _semantic(core_claim):
    return SlideSemanticAnalysis(sentence_roles=[], core_claim=core_claim, key_points=[])


@pytest.fixture
def script_path(tmp_path):
    path = tmp_path / "대본.json"
    path.write_text(
        json.dumps(
            [{"slide_number": 1, "script": "안녕하세요. 발표를 시작합니다."}], ensure_ascii=False
        ),
        encoding="utf-8",
    )
    return path


def _used_core_claim(monkeypatch, conn, script_path) -> str:
    """0번 벌만 만들고(LLM 호출 없이) draft_rubric 이 받은 1차 분석의 core_claim 을 돌려준다."""

    def stop(script_name, slide, norm, facts, keywords, semantics, deck_texts, model):
        raise _Stop(semantics.core_claim)

    monkeypatch.setattr(consistency, "draft_rubric", stop)
    with pytest.raises(_Stop) as stopped:
        sample_rubrics(script_path, SqliteLLMCache(conn), NoLLM(), NoLLM(), MODEL, 1)
    return stopped.value.core_claim


def _put(conn, table_sql, args):
    conn.execute(table_sql, args)
    conn.commit()


def test_sample_zero_falls_back_to_samples_table(conn, script_path, monkeypatch):
    key = normalize_script("안녕하세요. 발표를 시작합니다.").content_hash
    cfg = semantic_config_hash(MODEL)
    _put(
        conn,
        "INSERT INTO semantic_samples VALUES (?, ?, ?, ?, ?)",
        (key, cfg, 0, _semantic("표본0").model_dump_json(), "t"),
    )
    assert _used_core_claim(monkeypatch, conn, script_path) == "표본0"


def test_semantic_cache_takes_precedence_over_samples_table(conn, script_path, monkeypatch):
    key = normalize_script("안녕하세요. 발표를 시작합니다.").content_hash
    cfg = semantic_config_hash(MODEL)
    _put(
        conn,
        "INSERT INTO semantic_samples VALUES (?, ?, ?, ?, ?)",
        (key, cfg, 0, _semantic("표본0").model_dump_json(), "t"),
    )
    _put(
        conn,
        "INSERT INTO semantic_cache VALUES (?, ?, ?, ?)",
        (key, cfg, _semantic("기본").model_dump_json(), "t"),
    )
    assert _used_core_claim(monkeypatch, conn, script_path) == "기본"
