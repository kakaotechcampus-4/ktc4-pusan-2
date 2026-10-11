"""runs 의 입력 검사와 results JSON. LLM 은 부르지 않는다 (평가 기준이 없으면 호출 전에 멈춘다)."""

import pytest

import coverage_lab.results as results
from coverage_lab.llm import Settings
from coverage_lab.runs import run_take_evaluation
from coverage_lab.store import connect
from script_coverage.stt_evaluation.schemas import SlideSTT, Take
from script_coverage.version import FEATURE_VERSION


def test_take_evaluation_without_rubrics_fails_before_any_llm_call(tmp_path):
    conn = connect(tmp_path / "t.sqlite")
    settings = Settings(base_url="http://127.0.0.1:9/v1", api_key="dummy", model="m")
    take = Take(
        script_name="없는 대본",
        take_id="없는 대본_take1",
        slides=[SlideSTT(slide_number=1, stt="말")],
    )
    with pytest.raises(ValueError, match="평가 기준이 없다"):
        run_take_evaluation(take, conn, settings)
    conn.close()


def test_write_result_adds_version_and_model(tmp_path, monkeypatch):
    monkeypatch.setattr(results, "RESULTS_DIR", tmp_path / "out")
    path = results.write_result("demo", "model-x", {"table": {"a": {"값": 1}}})
    assert path == tmp_path / "out" / "demo.json"
    payload = results.read_result("demo")
    assert payload == {
        "feature_version": FEATURE_VERSION,
        "model": "model-x",
        "table": {"a": {"값": 1}},
    }
    assert results.result_table("demo", "table").loc["a", "값"] == 1
