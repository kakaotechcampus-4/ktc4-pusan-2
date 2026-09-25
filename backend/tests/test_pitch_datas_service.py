import uuid
from datetime import date

import pytest
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch import service
from pitch_coach_backend.module.pitch.dto import PitchDTO
from pitch_coach_backend.module.pitch.entity import (
    PresentationVersion,
    ScriptVersion,
    Standards,
)
from pitch_coach_backend.module.pitch.exception import NonExistentPitch


def _make_pitch(db: Session, user_id: uuid.UUID, title: str = "기존 발표") -> uuid.UUID:
    return service.add_pitch_service(
        db,
        user_id,
        PitchDTO(title=title, time_limit_sec=300, presentation_date=date(2026, 3, 1)),
    )


def _make_presentation(
    db: Session, pitch_id: uuid.UUID, version: int
) -> PresentationVersion:
    presentation = PresentationVersion(
        pitch_id=pitch_id,
        version=version,
        file_key=f"pitches/{pitch_id}/presentations/{version}.pdf",
    )
    db.add(presentation)
    db.flush()
    return presentation


def _make_script(db: Session, pitch_id: uuid.UUID, version: int) -> ScriptVersion:
    script = ScriptVersion(
        pitch_id=pitch_id,
        version=version,
        file_key=f"pitches/{pitch_id}/scripts/{version}.txt",
    )
    db.add(script)
    db.flush()
    return script


def _make_standard(
    db: Session, pitch_id: uuid.UUID, version: int, title: str = "평가 기준"
) -> Standards:
    standard = Standards(pitch_id=pitch_id, version=version, title=title)
    db.add(standard)
    db.flush()
    return standard


@pytest.fixture
def pitch_id(db_session: Session, user_id: uuid.UUID) -> uuid.UUID:
    return _make_pitch(db_session, user_id)


def test_raises_when_pitch_does_not_exist(db_session: Session) -> None:
    with pytest.raises(NonExistentPitch):
        service.get_pitch_datas(db_session, uuid.uuid4())


def test_returns_the_pitch_id(db_session: Session, pitch_id: uuid.UUID) -> None:
    result = service.get_pitch_datas(db_session, pitch_id)

    assert result.pitch_id == pitch_id


def test_returns_empty_lists_when_nothing_exists(
    db_session: Session, pitch_id: uuid.UUID
) -> None:
    result = service.get_pitch_datas(db_session, pitch_id)

    assert result.presentation_versions == []
    assert result.script_versions == []
    assert result.evaluation_versions == []


def test_maps_presentation_id_and_version(
    db_session: Session, pitch_id: uuid.UUID
) -> None:
    presentation = _make_presentation(db_session, pitch_id, 1)

    result = service.get_pitch_datas(db_session, pitch_id)

    assert len(result.presentation_versions) == 1
    summary = result.presentation_versions[0]
    assert summary.id == presentation.id
    assert summary.version == 1


def test_returns_every_presentation_version(
    db_session: Session, pitch_id: uuid.UUID
) -> None:
    _make_presentation(db_session, pitch_id, 3)
    _make_presentation(db_session, pitch_id, 1)
    _make_presentation(db_session, pitch_id, 2)

    result = service.get_pitch_datas(db_session, pitch_id)

    assert sorted(p.version for p in result.presentation_versions) == [1, 2, 3]


def test_maps_script_id_and_version(db_session: Session, pitch_id: uuid.UUID) -> None:
    script = _make_script(db_session, pitch_id, 1)

    result = service.get_pitch_datas(db_session, pitch_id)

    assert len(result.script_versions) == 1
    summary = result.script_versions[0]
    assert summary.id == script.id
    assert summary.version == 1


def test_returns_every_script_version(db_session: Session, pitch_id: uuid.UUID) -> None:
    _make_script(db_session, pitch_id, 3)
    _make_script(db_session, pitch_id, 1)
    _make_script(db_session, pitch_id, 2)

    result = service.get_pitch_datas(db_session, pitch_id)

    assert sorted(s.version for s in result.script_versions) == [1, 2, 3]


def test_maps_evaluation_id_and_version(
    db_session: Session, pitch_id: uuid.UUID
) -> None:
    standard = _make_standard(db_session, pitch_id, 1)

    result = service.get_pitch_datas(db_session, pitch_id)

    assert len(result.evaluation_versions) == 1
    summary = result.evaluation_versions[0]
    assert summary.id == standard.id
    assert summary.version == 1


def test_returns_every_evaluation(db_session: Session, pitch_id: uuid.UUID) -> None:
    _make_standard(db_session, pitch_id, 2, title="개정 기준")
    _make_standard(db_session, pitch_id, 1, title="초기 기준")

    result = service.get_pitch_datas(db_session, pitch_id)

    assert sorted(e.version for e in result.evaluation_versions) == [1, 2]


def test_returns_only_the_requested_pitch_data(
    db_session: Session, user_id: uuid.UUID, pitch_id: uuid.UUID
) -> None:
    other_pitch_id = _make_pitch(db_session, user_id, "다른 발표")
    _make_presentation(db_session, other_pitch_id, 1)
    _make_script(db_session, other_pitch_id, 1)
    _make_standard(db_session, other_pitch_id, 1)

    result = service.get_pitch_datas(db_session, pitch_id)

    assert result.presentation_versions == []
    assert result.script_versions == []
    assert result.evaluation_versions == []
