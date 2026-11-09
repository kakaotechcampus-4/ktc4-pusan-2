"""대화 에이전트 기본 자료 크기 (chat-agent.md 6-12, 524,288바이트).

넘치면 focus_take 의 대본 전달도에서 장별 채점 → Take 상세의 장별 요약 → 실패 장의 간이 값 순으로
빼고 `omitted_paths` 에 적는다. 그래도 넘치면 `ContextTooLarge` 다.
"""

import copy
import uuid

import pytest

from pitch_coach_backend.module.chat.dto import AgentContextDTO
from pitch_coach_backend.module.chat.service.request import (
    CONTEXT_LIMIT_BYTES,
    ContextTooLarge,
    _fit_context,
    _json_size,
)
from tests.take.coverage_fixtures import FALLBACK, scored

SLIDES = 30
EVALUATIONS = "results.script_coverage.evaluations"
DETAIL_SLIDES = "results.script_coverage.detail.slides"
FALLBACKS = "results.script_coverage.failed[].fallback"


def _coverage(*, evaluation_bytes: int, slide_bytes: int = 0, fallback_bytes: int = 0) -> dict:
    """30장 채점. 앞 24장은 채점, 뒤 6장은 간이 값으로 실패했다. 크기는 `*_bytes` 로 키운다."""
    base = scored(uuid.uuid4())
    evaluation = base["evaluations"][0]
    detail = base["detail"]
    scored_count = SLIDES - 6
    base["evaluations"] = [
        {**copy.deepcopy(evaluation), "slide_number": n, "stt_text": "말" * evaluation_bytes}
        for n in range(1, scored_count + 1)
    ]
    base["failed"] = [
        {
            "slide_number": n,
            "error": {"code": "LLM_TIMEOUT", "message": "LLM 응답 시간이 초과되었습니다"},
            "fallback": {**FALLBACK, "padding": "x" * fallback_bytes},
        }
        for n in range(scored_count + 1, SLIDES + 1)
    ]
    detail["slides"] = [
        {"slide_number": n, "state": "scored", "padding": "x" * slide_bytes}
        for n in range(1, SLIDES + 1)
    ]
    del base["meta"]
    return base


def _context(coverage: dict | None, *, review_bytes: int = 0) -> AgentContextDTO:
    return AgentContextDTO(
        pitches=[],
        pitches_total=1,
        pitch={
            "pitch_id": str(uuid.uuid4()),
            "focus_take": {
                "summary": {"take_id": str(uuid.uuid4())},
                "results": {
                    "take_result": None,
                    "script_coverage": coverage,
                    "review": {"overall_summary": "x" * review_bytes},
                },
                "revisions": {"TAKE_RESULT": None, "SCRIPT_COVERAGE": 0, "REVIEW": 0},
                "omitted_paths": [],
            },
        },
        conversation_goal=None,
        goal_version=0,
        loaded_takes=[],
        removed_sources=[],
        selected_proposal=None,
    )


def _focus(context: AgentContextDTO) -> dict:
    return context.pitch["focus_take"]


def _size(context: AgentContextDTO) -> int:
    return _json_size(context.model_dump(mode="json"))


def test_context_within_the_limit_is_sent_as_is() -> None:
    context = _context(_coverage(evaluation_bytes=100))

    assert _fit_context(context) is context


def test_thirty_slides_with_details_fit_once_the_slide_scoring_is_left_out() -> None:
    # 장마다 약 30 KB 의 장별 채점(한글 1자 = 3바이트)
    coverage = _coverage(evaluation_bytes=10_000)
    context = _context(coverage)
    assert _size(context) > CONTEXT_LIMIT_BYTES

    fitted = _fit_context(context)

    focus = _focus(fitted)
    assert focus["omitted_paths"] == [EVALUATIONS]
    kept = focus["results"]["script_coverage"]
    assert "evaluations" not in kept
    # 맨 위 상세 · 실패 장의 간이 값은 그대로다
    assert kept["detail"] == coverage["detail"]
    assert kept["failed"] == coverage["failed"]
    assert _size(fitted) <= CONTEXT_LIMIT_BYTES


def test_slide_summaries_go_next_when_still_too_large() -> None:
    coverage = _coverage(evaluation_bytes=10_000, slide_bytes=20_000)

    fitted = _fit_context(_context(coverage))

    focus = _focus(fitted)
    assert focus["omitted_paths"] == [EVALUATIONS, DETAIL_SLIDES]
    kept = focus["results"]["script_coverage"]
    assert kept["detail"] == {
        key: value for key, value in coverage["detail"].items() if key != "slides"
    }
    assert kept["failed"] == coverage["failed"]
    assert _size(fitted) <= CONTEXT_LIMIT_BYTES


def test_fallbacks_go_last_and_the_failure_reasons_stay() -> None:
    coverage = _coverage(evaluation_bytes=10_000, slide_bytes=20_000, fallback_bytes=100_000)

    fitted = _fit_context(_context(coverage))

    focus = _focus(fitted)
    assert focus["omitted_paths"] == [EVALUATIONS, DETAIL_SLIDES, FALLBACKS]
    assert focus["results"]["script_coverage"]["failed"] == [
        {"slide_number": item["slide_number"], "error": item["error"]}
        for item in coverage["failed"]
    ]
    assert _size(fitted) <= CONTEXT_LIMIT_BYTES


def test_steps_with_nothing_to_drop_are_skipped() -> None:
    """예전 채점에는 맨 위 상세 · 간이 값이 없다. 빼지 않은 경로는 적지 않는다."""
    coverage = _coverage(evaluation_bytes=0, fallback_bytes=100_000)
    del coverage["detail"]

    fitted = _fit_context(_context(coverage))

    assert _focus(fitted)["omitted_paths"] == [EVALUATIONS, FALLBACKS]


@pytest.mark.parametrize("coverage", [True, False], ids=["after-every-step", "no-coverage"])
def test_context_still_too_large_is_rejected(coverage: bool) -> None:
    context = _context(
        _coverage(evaluation_bytes=10_000) if coverage else None, review_bytes=CONTEXT_LIMIT_BYTES
    )

    with pytest.raises(ContextTooLarge):
        _fit_context(context)
