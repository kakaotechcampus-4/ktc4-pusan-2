import uuid
from datetime import date

import pytest
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch import service
from pitch_coach_backend.module.pitch.dto import PitchSaveRequestDTO


@pytest.fixture
def pitch_id(db_session: Session, user_id) -> uuid.UUID:
    return service.add_pitch_service(
        db_session,
        user_id,
        PitchSaveRequestDTO(
            title="기존 발표", time_limit_sec=300, presentation_date=date(2026, 3, 1)),
    )

def _make_standards(db_session: Session, pitch_id, version, position=1, title="평가 기준"):
    from pitch_coach_backend.module.pitch.entity import Standards

    for i in range(3):
        standard = Standards(
            pitch_id=pitch_id, version=version, position=position + i, title=f"{title} {i+1}")
        db_session.add(standard)
        db_session.flush()

def test_get_standards(db_session, pitch_id):
    from pitch_coach_backend.module.pitch.entity import Standards

    _make_standards(db_session, pitch_id, 1)
    _make_standards(db_session, pitch_id, 2)

    standards = db_session.query(Standards).filter_by(pitch_id=pitch_id, version=1).all()
    assert len(standards) == 3