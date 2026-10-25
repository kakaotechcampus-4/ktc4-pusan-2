"""실패했을 때 DB · 결과 파일이 망가지지 않는지. LLM 은 부르면 바로 실패하는 객체로 막는다 (가짜 응답 없음)."""

import json

import pytest

from coverage_lab import rubric_consistency as consistency
from coverage_lab.cache import SqliteLLMCache
from coverage_lab.llm import Settings
from coverage_lab.rubric_consistency import sample_rubrics
from coverage_lab.runs import run_script_analysis, run_take_evaluation
from coverage_lab.store import (
    confirm_similar_item,
    connect,
    load_evaluation,
    load_rubric,
    load_similar_items,
    save_evaluation,
    save_rubric,
)
from coverage_lab.stt_stability import check_baseline, repeat_runs
from script_coverage.core import CoverageError, ScriptLLMs, TakeLLMs
from script_coverage.script_analysis.schemas import SlideSemanticAnalysis
from script_coverage.script_analysis.semantic import semantic_config_hash
from script_coverage.shared.text import normalize_script
from script_coverage.stt_evaluation.schemas import SlideSTT, Take, VerifierResult
from tests.lab.test_store import _evaluation, _item, _rubric
from tests.unit.fakes import DeckAwareLLM, FakeLLM
from tests.unit.shared.slides import SLIDES
from tests.unit.test_take_core import SaidAllLLM

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
        RuntimeError, match=r"04_evaluate_takes.py.*슬라이드 2: 처음 채점 결과가 없다"
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
    llms = TakeLLMs(eval=NoLLM(), verifier=NoLLM())
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
    llms = ScriptLLMs(semantic=NoLLM(), final=NoLLM(), unit=NoLLM())
    with pytest.raises(
        CoverageError
    ) as error:  # 모든 장이 실패하면 최상위 진입 함수가 요청 전체 오류를 낸다
        run_script_analysis(path, conn, SETTINGS, llms=llms)
    assert error.value.code == "LLM_ERROR"
    assert load_rubric(conn, "대본", 1) is None
    assert load_rubric(conn, "대본", 5) is not None


def test_partial_failure_goes_through_core_saves_ok_slides_and_deletes_failed_ones(conn, tmp_path):
    path = tmp_path / "대본.json"
    path.write_text(
        json.dumps(
            [
                {"slide_number": 1, "script": "안녕하세요. 발표를 시작합니다."},
                {
                    "slide_number": 2,
                    "script": "",
                },  # 읽을 내용이 없는 장은 LLM 없이 평가 기준이 만들어진다
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    save_rubric(conn, _rubric("대본", 1))
    llms = ScriptLLMs(semantic=NoLLM(), final=NoLLM(), unit=NoLLM())
    rubrics, stats = run_script_analysis(path, conn, SETTINGS, llms=llms)
    assert [r.meta.slide_number for r in rubrics] == [2]
    assert stats["failed"] == {1: "LLM_ERROR"} and stats["causes"] == {
        1: "LLM 호출에 실패했습니다."
    }
    assert stats["analysis_calls"] == 1  # 호출 수는 실험 도구의 감싸개가 센다 (실패한 호출 포함)
    assert load_rubric(conn, "대본", 1) is None
    assert load_rubric(conn, "대본", 2) is not None
    assert load_rubric(conn, "대본", 2).meta.script_name == "대본"


def test_call_counts_through_run_functions_and_cache_replay(conn, tmp_path):
    """다섯 호출 수(1차 분석 · 최종 결론 · 전달 단위 · 의미 평가 · 교차 검증)는 실제로 LLM 을 부른 횟수이고, 같은 캐시로 다시 하면 0 이다."""
    path = tmp_path / "대본.json"
    scripts = [{"slide_number": n, "script": SLIDES[("가상대본1", n)]} for n in (4, 6)]
    path.write_text(json.dumps(scripts, ensure_ascii=False), encoding="utf-8")
    script_llms = ScriptLLMs(**{s: DeckAwareLLM(s) for s in ("semantic", "final", "unit")})
    rubrics, stats = run_script_analysis(path, conn, SETTINGS, llms=script_llms)
    assert [r.meta.slide_number for r in rubrics] == [4, 6] and stats["failed"] == {}
    assert (stats["analysis_calls"], stats["final_calls"], stats["unit_calls"]) == (2, 2, 2)
    _, again = run_script_analysis(path, conn, SETTINGS, llms=script_llms)
    assert (again["analysis_calls"], again["final_calls"], again["unit_calls"]) == (0, 0, 0)

    take = Take(
        script_name="대본",
        take_id="대본_take1",
        slides=[SlideSTT(slide_number=n, stt=SLIDES[("가상대본1", n)]) for n in (4, 6)],
    )
    # 6번 장의 의미 평가가 실패한다: 실패한 호출도 센다
    flaky = TakeLLMs(
        eval=SaidAllLLM(failing={6: RuntimeError("대본 원문")}),
        verifier=FakeLLM(VerifierResult(judgments=[])),
    )
    evaluations, stats = run_take_evaluation(take, conn, SETTINGS, llms=flaky)
    assert [e.slide_number for e in evaluations] == [4]
    assert stats["failed"] == {6: "LLM_ERROR"} and stats["causes"] == {
        6: "LLM 호출에 실패했습니다."
    }
    assert (stats["semantic_calls"], stats["verifier_calls"]) == (
        2,
        1,
    )  # 4번 장에서 규칙과 LLM 이 어긋나 교차 검증을 1번 부른다
    # 성공한 4번 장은 캐시에 있어 실패했던 6번 장만 다시 부른다
    fine = TakeLLMs(eval=SaidAllLLM(), verifier=FakeLLM(VerifierResult(judgments=[])))
    evaluations, stats = run_take_evaluation(take, conn, SETTINGS, llms=fine)
    assert [e.slide_number for e in evaluations] == [4, 6] and stats["failed"] == {}
    assert (stats["semantic_calls"], stats["verifier_calls"]) == (1, 1)
    _, stats = run_take_evaluation(take, conn, SETTINGS, llms=fine)
    assert (stats["semantic_calls"], stats["verifier_calls"]) == (0, 0)


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
    with pytest.raises(CoverageError) as error:
        confirm_similar_item(conn, "대본_take1", 1, "R1", "as_script")
    assert error.value.code == "RUBRIC_MISSING"
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


def test_segmentation_report_skips_slides_without_rubric(conn):
    from coverage_lab.stt_report import segmentation_report

    save_rubric(conn, _rubric("대본", 1))
    mixed = segmentation_report(conn, [_take(slides=(1, 2))])  # 2번 장은 평가 기준이 없다
    alone = segmentation_report(conn, [_take(slides=(1,))])
    missing = segmentation_report(conn, [_take(slides=(2,))])
    assert list(mixed["take_id"]) == ["대본_take1"]
    # 평가 기준이 있는 장은 그대로 세고, 없는 장만 빠진다
    assert mixed.drop(columns="take_id").to_dict("records") == alone.drop(
        columns="take_id"
    ).to_dict("records")
    assert alone["STT 문장 수"].iloc[0] > 0
    assert missing["STT 문장 수"].iloc[0] == 0
