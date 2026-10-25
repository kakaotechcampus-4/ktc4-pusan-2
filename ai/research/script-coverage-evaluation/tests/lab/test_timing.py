"""장 수별 시간 측정의 덱 만들기와 요청 기록 (LLM 없음)."""

from types import SimpleNamespace

import pytest

from coverage_lab.datasets import load_script_json, load_take
from coverage_lab.paths import SCRIPT_DIR, STT_DIR
from coverage_lab.timing import (
    PoolSlide,
    build_deck,
    request_row,
    retry_targets,
    rubric_with_retry,
    slide_pool,
)

POOL = [PoolSlide("대본", i, f"대본 {i}", f"STT {i}") for i in (1, 2, 3)]


@pytest.mark.parametrize("n", [1, 3, 7])
def test_deck_numbers_slides_and_reuses_the_pool(n):
    scripts, stts = build_deck(POOL, n)
    assert [s.slide_number for s in scripts] == list(range(1, n + 1))
    assert [s.slide_number for s in stts] == list(range(1, n + 1))
    for i, (script, stt) in enumerate(zip(scripts, stts, strict=True)):
        source = POOL[i % len(POOL)]
        assert (script.script, stt.stt) == (source.script, source.stt)  # 대본과 STT 가 같은 장


def test_real_pool_pairs_each_slide_with_its_own_stt():
    """풀의 장마다 대본은 원본 대본 파일의 그 장, STT 는 그 대본 take1 의 같은 장이다."""
    pool = slide_pool()
    assert len(pool) >= 20
    assert len({(p.script_name, p.slide_number) for p in pool}) == len(pool)
    for p in pool:
        script = {
            s.slide_number: s.script for s in load_script_json(SCRIPT_DIR / f"{p.script_name}.json")
        }
        take = {
            s.slide_number: s.stt for s in load_take(STT_DIR / f"{p.script_name}_take1.json").slides
        }
        assert (p.script, p.stt) == (script[p.slide_number], take[p.slide_number])
    scripts, _ = build_deck(pool, 30)
    assert len(scripts) == 30 and scripts[len(pool)].script == pool[0].script


def _row(**kwargs):
    base = dict(
        seconds=10.0,
        deadline_seconds=120.0,
        targets=4,
        failed={},
        produced=4,
        request_error=None,
        log_summary={"calls": 1},
    )
    return request_row(**{**base, **kwargs})


def test_whole_request_failure_counts_every_target_slide():
    row = _row(request_error="LLM_TIMEOUT", produced=0)
    assert (row["failed_slides"], row["failed"], row["within_deadline"]) == (4, {}, False)


def test_partial_failure_and_deadline():
    row = _row(failed={3: "LLM_TIMEOUT"}, produced=3)
    assert (row["failed_slides"], row["failed"], row["produced_slides"]) == (
        1,
        {"3": "LLM_TIMEOUT"},
        3,
    )
    assert row["within_deadline"] is False
    assert _row()["within_deadline"] is True
    assert _row(seconds=121.0)["within_deadline"] is False


def test_retry_targets_are_llm_failures_only():
    failed = {7: "LLM_TIMEOUT", 3: "RUBRIC_MISSING", 5: "LLM_ERROR", 9: "LLM_INVALID_OUTPUT"}
    assert retry_targets(failed) == [5, 7, 9]
    assert retry_targets({}) == []


def _response(made, failed):
    """made: 만든 장 번호, failed: {장 번호: 코드} → 응답처럼 보이는 객체."""
    return SimpleNamespace(
        rubrics=[f"rubric-{n}" for n in made],
        failed=[
            SimpleNamespace(slide_number=n, error=SimpleNamespace(code=c))
            for n, c in failed.items()
        ],
    )


class _Requests:
    """정해 둔 응답을 차례로 돌려주는 가짜 요청. 받은 slide_numbers 를 적는다."""

    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, slide_numbers):
        self.calls.append(slide_numbers)
        return {"seconds": 10.0 * len(self.calls)}, self.responses[len(self.calls) - 1]


ALL = [1, 2, 3, 4]


def test_no_retry_needed_when_everything_is_made():
    requests = _Requests(_response([1, 2, 3, 4], {}))
    job = rubric_with_retry(requests, ALL, retry=True)
    assert requests.calls == [None]
    assert (job["final_produced"], job["complete"], job["retry"]) == (4, True, None)
    assert job["rubrics"] == ["rubric-1", "rubric-2", "rubric-3", "rubric-4"]


def test_partial_failure_retries_only_llm_failures_once():
    requests = _Requests(
        _response([1, 4], {2: "LLM_TIMEOUT", 3: "RUBRIC_MISSING"}), _response([2], {})
    )
    job = rubric_with_retry(requests, ALL, retry=True)
    assert requests.calls == [None, [2]]  # 입력 오류 장(3)은 다시 부르지 않는다
    assert (job["final_produced"], job["complete"], job["seconds"]) == (3, False, 30.0)
    assert job["rubrics"] == ["rubric-1", "rubric-4", "rubric-2"]


def test_whole_request_failure_retries_every_slide_once():
    requests = _Requests(None, _response([1, 2, 3, 4], {}))
    job = rubric_with_retry(requests, ALL, retry=True)
    assert requests.calls == [None, ALL]
    assert (job["final_produced"], job["complete"]) == (4, True)
    assert job["rubrics"] == ["rubric-1", "rubric-2", "rubric-3", "rubric-4"]


def test_failed_retry_keeps_what_the_first_request_made():
    requests = _Requests(_response([1, 2, 3], {4: "LLM_ERROR"}), None)
    job = rubric_with_retry(requests, ALL, retry=True)
    assert requests.calls == [None, [4]]
    assert (job["final_produced"], job["complete"]) == (3, False)
    assert job["rubrics"] == ["rubric-1", "rubric-2", "rubric-3"]


def test_retry_off_calls_once():
    requests = _Requests(_response([1], {2: "LLM_TIMEOUT"}))
    job = rubric_with_retry(requests, ALL, retry=False)
    assert requests.calls == [None]
    assert job["final_produced"] == 1
