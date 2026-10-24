"""AI 리뷰 응답 검사 (service.request_review).

응답이 missions 칸 · 제약에 맞지 않으면 저장 전에 InvalidReviewResponse(502) 로 거른다.
AI 서버는 httpx2.MockTransport 로 대신한다.
"""

import copy
import uuid
from functools import partial
from typing import Any

import httpx2
import pytest

from pitch_coach_backend.module.take import service
from pitch_coach_backend.module.take.dto import ReviewRequestDTO
from pitch_coach_backend.module.take.exception import InvalidReviewResponse

TAKE_ID = uuid.uuid4()


def _mission(priority: int, area: str = "FILLER") -> dict[str, Any]:
    return {
        "area": area,
        "slide_number": None,
        "description": "추임새 5번 이하",
        "target": {"metric": "filler_count", "operator": "LTE", "value": 5},
        "priority": priority,
        "reason": "직접 정한 기준인데 이번 Take 7번",
    }


REVIEW = {
    "take_id": str(TAKE_ID),
    "overall_summary": "먼저 고칠 차원: 대본 의존(3번 장).",
    "score": 74,
    "dimensions": {
        "audience_gaze": "GOOD",
        "script_independence": "PRIORITY",
        "pacing_rhythm": "GOOD",
        "vocal_delivery": "GOOD",
        "fluency": "NEEDS_WORK",
        "content_delivery": "GOOD",
    },
    "next_missions": [_mission(1), _mission(2, "GAZE")],
    "meta": {"schema_version": "1", "feature_version": "1", "criteria_versions": {}},
}


def _review(**changes: Any) -> dict[str, Any]:
    review = copy.deepcopy(REVIEW)
    review.update(changes)
    return review


@pytest.fixture
def ai(monkeypatch: pytest.MonkeyPatch):
    """AI 서버가 돌려줄 응답을 정한다."""

    def use(response: httpx2.Response) -> None:
        transport = httpx2.MockTransport(lambda request: response)
        monkeypatch.setattr(service.httpx2, "Client", partial(httpx2.Client, transport=transport))

    return use


def test_valid_review_is_returned(ai) -> None:
    ai(httpx2.Response(200, json=REVIEW))

    review = service.request_review(TAKE_ID, ReviewRequestDTO(take_result={}))

    assert [m.priority for m in review.next_missions] == [1, 2]


@pytest.mark.parametrize(
    "review",
    [
        # missions.area 는 String(16) 이고 영역 이름만 받는다
        _review(next_missions=[_mission(1, "VERY_LONG_AREA_NAME")]),
        _review(next_missions=[_mission(1, "EYE")]),
        # UQ(source_take_id, priority) 에 걸린다
        _review(next_missions=[_mission(1), _mission(1, "GAZE")]),
        # 20개 이하
        _review(next_missions=[_mission(p) for p in range(1, 22)]),
        # 다른 Take 의 응답
        _review(take_id=str(uuid.uuid4())),
    ],
    ids=["area-too-long", "area-unknown", "priority-duplicate", "too-many", "other-take"],
)
def test_invalid_review_is_rejected(ai, review: dict[str, Any]) -> None:
    ai(httpx2.Response(200, json=review))

    with pytest.raises(InvalidReviewResponse) as e:
        service.request_review(TAKE_ID, ReviewRequestDTO(take_result={}))

    assert e.value.status_code == 502


def test_non_json_review_is_rejected(ai) -> None:
    ai(httpx2.Response(200, text="not json"))

    with pytest.raises(InvalidReviewResponse):
        service.request_review(TAKE_ID, ReviewRequestDTO(take_result={}))
