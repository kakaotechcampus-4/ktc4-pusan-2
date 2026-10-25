"""코칭 계획을 부르는 때와 원자료 사용 동의 설정.

계획은 리뷰를 저장한 직후(끝내 실패해도), 대본 파싱 직후, 제한 시간 · 허용 범위를 바꾼 뒤에 부른다
(`docs/ai/features/coach-agent.md` 5-1). 동의는 마이페이지에서 받고 Take 를 시작할 때 복사한다
(`docs/ai/architecture.md` 1-1).
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.coach import dependencies as coach_dependencies
from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.pitch.adapter.script_parser import ParsedScript
from pitch_coach_backend.module.pitch.dto import ParseSaved, ParseTicket
from pitch_coach_backend.module.pitch.entity import Pitch, ScriptVersion
from pitch_coach_backend.module.pitch.service.script_parse_runner import ScriptParseRunner
from tests.realtime.conftest import wait_until
from tests.test_coach_session import FakeClient, FakeStore


class PlanSpy(FakeStore):
    """계획 요청을 만들려고 한 발표를 센다."""

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[uuid.UUID] = []

    async def build_plan_request(self, pitch_id):
        self.asked.append(pitch_id)
        return None

@pytest.fixture
def spy(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> PlanSpy:
    store = PlanSpy()
    monkeypatch.setattr(coach_dependencies, "_coach_client", FakeClient())
    monkeypatch.setattr(coach_dependencies, "_coach_store", store)
    return store

@pytest.fixture
def headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}

@pytest.fixture
def pitch(db_session: Session, user_id: uuid.UUID) -> Pitch:
    pitch = Pitch(user_id=user_id, title="발표", time_limit_sec=300)
    db_session.add(pitch)
    db_session.commit()
    return pitch

def _update(client: TestClient, pitch: Pitch, headers: dict[str, str], **changes) -> None:
    body = {
        "title": pitch.title,
        "time_limit_sec": pitch.time_limit_sec,
        "upper_deviation": pitch.upper_deviation,
        "lower_deviation": pitch.lower_deviation,
        **changes,
    }
    response = client.patch(f"/api/pitches/update/{pitch.id}", json=body, headers=headers)
    assert response.is_success, response.text

def test_changing_the_time_limit_asks_for_a_new_plan(
    client: TestClient, spy: PlanSpy, pitch: Pitch, headers: dict[str, str]
):
    _update(client, pitch, headers, time_limit_sec=360)
    _update(client, pitch, headers, upper_deviation=30)

    # 백그라운드로 띄운다 (앱을 내릴 때 같이 멈추는 계획 요청 목록에 든다)
    wait_until(lambda: spy.asked == [pitch.id, pitch.id])

def test_changing_only_the_title_does_not_ask_for_a_plan(
    client: TestClient, spy: PlanSpy, pitch: Pitch, headers: dict[str, str]
):
    import time

    _update(client, pitch, headers, title="새 제목")
    time.sleep(0.1)

    assert spy.asked == []

def test_review_asks_for_the_next_plan_even_when_it_fails(
    client: TestClient,
    spy: PlanSpy,
    pitch: Pitch,
    headers: dict[str, str],
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    from pitch_coach_backend.module.pitch.entity import PresentationVersion
    from pitch_coach_backend.module.take import controller
    from pitch_coach_backend.module.take.entity import Take

    presentation = PresentationVersion(pitch_id=pitch.id, version=1, file_key="d.pdf")
    script = ScriptVersion(pitch_id=pitch.id, version=1, content="대본")
    db_session.add_all([presentation, script])
    db_session.flush()
    take = Take(
        pitch_id=pitch.id,
        take_number=1,
        presentation_version_id=presentation.id,
        script_version_id=script.id,
        mode="COACHING",
        script_mode="HIGHLIGHT",
        goal_time_sec=300,
        status="ANALYZING",
    )
    db_session.add(take)
    db_session.commit()

    def failing_review(db, take_id):
        raise RuntimeError("AI down")

    monkeypatch.setattr(controller, "get_review_result", failing_review)

    with pytest.raises(RuntimeError):
        client.post(f"/api/pitches/{pitch.id}/takes/{take.id}/review", headers=headers)

    wait_until(lambda: spy.asked == [pitch.id])

@pytest.mark.anyio
async def test_parsing_a_script_asks_for_the_first_plan(db_session: Session, pitch: Pitch):
    script = ScriptVersion(pitch_id=pitch.id, version=1, content="[1] 안녕하세요")
    db_session.add(script)
    db_session.commit()
    asked: list[uuid.UUID] = []

    class Parser:
        async def parse(self, text, target_ms):
            return ParsedScript(segmented=True, slides=(), terms=())

    async def on_parsed(pitch_id: uuid.UUID) -> None:
        asked.append(pitch_id)

    runner = ScriptParseRunner(Parser(), session_factory=lambda: db_session, on_parsed=on_parsed)
    ticket = ParseTicket(script.id, script.parse_requested_at, script.content, 300_000, pitch.id)
    saved = {"value": ParseSaved.APPLIED}
    # 결과 저장은 tests/script_parse/ 가 본다. 여기서는 저장한 뒤에 부르는지만 본다
    runner._complete = lambda ticket, parsed: saved["value"]  # type: ignore[method-assign]

    await runner.run(ticket)
    # 지난 작업의 결과를 버렸거나(저장 안 함) 저장본과 달라 적용하지 않았으면 부르지 않는다
    for skipped in (ParseSaved.STALE, ParseSaved.MISMATCH):
        saved["value"] = skipped
        await runner.run(ticket)

    assert asked == [pitch.id]

@pytest.fixture
def anyio_backend():
    return "asyncio"

def test_review_refused_before_scoring_does_not_ask_for_a_plan(
    client: TestClient,
    spy: PlanSpy,
    pitch: Pitch,
    headers: dict[str, str],
    db_session: Session,
):
    """리뷰를 부르지 못했으면(409) 계획도 부르지 않는다 — 다시 누를 때마다 AI 를 부르게 된다."""
    import time

    from pitch_coach_backend.module.pitch.entity import PresentationVersion
    from pitch_coach_backend.module.take.entity import Take

    presentation = PresentationVersion(pitch_id=pitch.id, version=1, file_key="d.pdf")
    script = ScriptVersion(pitch_id=pitch.id, version=1, content="대본")
    db_session.add_all([presentation, script])
    db_session.flush()
    take = Take(
        pitch_id=pitch.id,
        take_number=1,
        presentation_version_id=presentation.id,
        script_version_id=script.id,
        mode="COACHING",
        script_mode="HIGHLIGHT",
        goal_time_sec=300,
        status="ANALYZING",
        analysis_stage="FINALIZING",
    )
    db_session.add(take)
    db_session.commit()

    response = client.post(f"/api/pitches/{pitch.id}/takes/{take.id}/review", headers=headers)
    time.sleep(0.1)

    assert response.status_code == 409
    assert spy.asked == []

def test_failing_to_plan_does_not_hide_the_review_failure(
    client: TestClient,
    spy: PlanSpy,
    pitch: Pitch,
    headers: dict[str, str],
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    from pitch_coach_backend.coach import planner
    from pitch_coach_backend.module.pitch.entity import PresentationVersion
    from pitch_coach_backend.module.take import controller
    from pitch_coach_backend.module.take.entity import Take

    presentation = PresentationVersion(pitch_id=pitch.id, version=1, file_key="d.pdf")
    script = ScriptVersion(pitch_id=pitch.id, version=1, content="대본")
    db_session.add_all([presentation, script])
    db_session.flush()
    take = Take(
        pitch_id=pitch.id,
        take_number=1,
        presentation_version_id=presentation.id,
        script_version_id=script.id,
        mode="COACHING",
        script_mode="HIGHLIGHT",
        goal_time_sec=300,
        status="ANALYZING",
    )
    db_session.add(take)
    db_session.commit()

    def failing_review(db, take_id):
        raise RuntimeError("AI down")

    def broken_spawn(*args, **kwargs):
        raise ValueError("loop gone")

    monkeypatch.setattr(controller, "get_review_result", failing_review)
    monkeypatch.setattr(planner, "spawn", broken_spawn)

    with pytest.raises(RuntimeError, match="AI down"):
        client.post(f"/api/pitches/{pitch.id}/takes/{take.id}/review", headers=headers)
