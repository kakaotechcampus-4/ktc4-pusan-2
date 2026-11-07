"""`test_coach_*_storage.py` 가 함께 쓰는 준비 데이터. 필요한 fixture 를 import 해서 쓴다."""

import uuid
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.entity import (
    Pitch,
    PresentationVersion,
    ScriptSlide,
    ScriptVersion,
)
from pitch_coach_backend.module.take.entity import Mission, Take, TakeAIResults, TakeMetricSecond
from pitch_coach_backend.module.user.entity import User

META = {"schema_version": "1.0", "feature_version": "coach-1.2", "model": None}


@pytest.fixture
def user(db_session: Session) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", name="코치")
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def pitch(db_session: Session, user: User) -> Pitch:
    pitch = Pitch(
        user_id=user.id, title="발표", time_limit_sec=300, upper_deviation=30, lower_deviation=30
    )
    db_session.add(pitch)
    db_session.flush()
    return pitch


@pytest.fixture
def script(db_session: Session, pitch: Pitch) -> ScriptVersion:
    script = ScriptVersion(
        pitch_id=pitch.id,
        version=1,
        content="대본",
        parse_status="DONE",
        # 파싱할 때의 목표는 제한 시간(300초)이었다
        parse_meta={"target_ms": 300_000},
    )
    db_session.add(script)
    db_session.flush()
    db_session.add_all(
        [
            ScriptSlide(
                script_version_id=script.id,
                slide_number=n,
                full_content=f"{n}번 장 대본",
                script_chars=100,
                expected_ms=100_000,
            )
            for n in (1, 2, 3)
        ]
    )
    db_session.flush()
    return script


@pytest.fixture
def presentation(db_session: Session, pitch: Pitch) -> PresentationVersion:
    presentation = PresentationVersion(pitch_id=pitch.id, version=1, file_key="deck.pdf")
    db_session.add(presentation)
    db_session.flush()
    return presentation


def make_take(
    db: Session,
    pitch: Pitch,
    script: ScriptVersion,
    presentation: PresentationVersion,
    number: int,
    **fields: Any,
) -> Take:
    take = Take(
        pitch_id=pitch.id,
        take_number=number,
        presentation_version_id=presentation.id,
        script_version_id=script.id,
        mode=fields.pop("mode", "COACHING"),
        script_mode=fields.pop("script_mode", "HIGHLIGHT"),
        goal_time_sec=fields.pop("goal_time_sec", 300),
        status=fields.pop("status", "READY"),
        **fields,
    )
    db.add(take)
    db.flush()
    return take


def reviewed(db: Session, take: Take, *, completed_at: datetime | None, **review: Any) -> Take:
    """리뷰까지 끝난 Take 로 만든다."""
    take.status = "COMPLETED"
    take.review_status = "DONE"
    take.completed_at = completed_at
    db.add(TakeAIResults(take_id=take.id, kind="REVIEW", payload=review, meta=META))
    db.flush()
    return take


def mission(db: Session, source: Take, priority: int, **fields: Any) -> Mission:
    row = Mission(
        source_take_id=source.id,
        area=fields.pop("area", "GAZE"),
        slide_number=fields.pop("slide_number", None),
        target=fields.pop("target", {"metric": "script_ratio", "operator": "LTE", "value": 0.3}),
        description=fields.pop("description", f"미션 {priority}"),
        priority=priority,
        status=fields.pop("status", "ACTIVE"),
        **fields,
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def take_parts(pitch: Pitch, script: ScriptVersion, presentation: PresentationVersion):
    return pitch, script, presentation


def _event(event_id: str, t_ms: int, kind: str = "INTERVENTION") -> dict[str, Any]:
    return {"kind": kind, "event_id": event_id, "t_ms": t_ms, "evidence": {"start_ms": 0}}


def finalizing(db: Session, take: Take, *, consent: bool = False) -> Take:
    take.status = "ANALYZING"
    take.analysis_stage = "FINALIZING"
    take.raw_data_consent = consent
    db.add(
        TakeMetricSecond(
            take_id=take.id, t_ms=1_000, slide_number=1, gaze=None, voice=None, stt_status="ok"
        )
    )
    db.flush()
    return take
