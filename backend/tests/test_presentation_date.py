"""발표자료 상세의 생성 날짜는 DB의 UTC 시각을 한국 날짜로 변환한다."""

import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion
from pitch_coach_backend.module.pitch.service import presentation as presentation_service


@pytest.mark.parametrize(
    ("created_at", "expected_date"),
    [
        ("2026-10-24T14:59:59+00:00", "2026-10-24"),
        ("2026-10-24T15:00:00+00:00", "2026-10-25"),
        ("2026-10-24T23:59:59+00:00", "2026-10-25"),
        ("2026-10-25T00:00:00+00:00", "2026-10-25"),
    ],
    ids=["before-kst-midnight", "kst-midnight", "kst-085959", "kst-090000"],
)
def test_presentation_detail_returns_the_korean_date(
    client: TestClient,
    db_session: Session,
    user_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
    created_at: str,
    expected_date: str,
) -> None:
    # DB 세션이 UTC 시각을 반환해도 API의 날짜는 한국 기준이어야 한다.
    db_session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    pitch = Pitch(user_id=user_id, title="발표자료 날짜", time_limit_sec=300)
    db_session.add(pitch)
    db_session.flush()
    presentation = PresentationVersion(
        pitch_id=pitch.id,
        version=1,
        file_key=f"pitches/{pitch.id}/presentations/1.pdf",
        page_count=7,
        created_at=datetime.fromisoformat(created_at),
    )
    db_session.add(presentation)
    db_session.flush()
    db_session.expire(presentation, ["created_at"])
    monkeypatch.setattr(presentation_service, "generate_presigned_url", lambda key: f"https://fake/{key}")

    response = client.get(
        f"/api/pitches/{pitch.id}/presentations/{presentation.id}",
        headers={"Authorization": f"Bearer {create_access_token(user_id)}"},
    )

    assert response.status_code == 200
    assert response.json()["created_at"] == expected_date
