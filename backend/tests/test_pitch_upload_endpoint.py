import uuid
from collections.abc import Callable
from io import BytesIO
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.pitch.entity import PresentationVersion
from pitch_coach_backend.module.pitch.service import presentation as presentation_service


@pytest.fixture(autouse=True)
def s3_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(presentation_service, "upload", lambda file, key, content_type=None: key)
    monkeypatch.setattr(presentation_service, "generate_presigned_url", lambda key: f"https://fake/{key}")


@pytest.fixture
def auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


@pytest.fixture
def other_user_headers(db_session: Session) -> dict[str, str]:
    from pitch_coach_backend.module.user.entity import User

    user = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db_session.add(user)
    db_session.flush()
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.fixture
def pitch_id(client: TestClient, auth_headers: dict[str, str]) -> uuid.UUID:
    response = client.post(
        "/api/pitches/add",
        json={
            "title": "통합 테스트 발표",
            "time_limit_sec": 600,
            "presentation_date": "2026-11-01",
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    return uuid.UUID(response.json()["pitch_id"])


@pytest.fixture
def pdf(make_pdf: Callable[[int], bytes]) -> bytes:
    return make_pdf(3)


def _files(content: bytes, filename: str = "deck.pdf") -> dict[str, Any]:
    return {"presentation_file": (filename, BytesIO(content), "application/pdf")}


def _upload(
    client: TestClient,
    pitch_id: uuid.UUID,
    content: bytes,
    headers: dict[str, str] | None = None,
    filename: str = "deck.pdf",
):
    return client.post(
        f"/api/pitches/add/{pitch_id}/presentation",
        files=_files(content, filename),
        headers=headers or {},
    )


def _presentation_count(db: Session, pitch_id: uuid.UUID) -> int | None:
    return db.scalar(
        select(func.count())
        .select_from(PresentationVersion)
        .where(PresentationVersion.pitch_id == pitch_id)
    )


def test_upload_persists_presentation_version_one(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    pdf: bytes,
) -> None:
    response = _upload(client, pitch_id, pdf, auth_headers)

    assert response.status_code == 200
    body = response.json()["presentation"]

    presentation = db_session.get(
        PresentationVersion, uuid.UUID(body["presentation_version_id"])
    )

    expected_key = f"pitches/{pitch_id}/presentations/1.pdf"
    assert presentation is not None
    assert presentation.version == 1
    assert presentation.file_key == expected_key
    assert presentation.page_count == 3
    assert body["pitch_id"] == str(pitch_id)
    assert body["file_url"] == f"https://fake/{expected_key}"
    assert body["page_count"] == 3


def test_second_upload_increments_version(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    pdf: bytes,
) -> None:
    assert _upload(client, pitch_id, pdf, auth_headers).status_code == 200
    response = _upload(client, pitch_id, pdf, auth_headers)

    assert response.status_code == 200
    body = response.json()["presentation"]

    presentation = db_session.get(
        PresentationVersion, uuid.UUID(body["presentation_version_id"])
    )

    assert presentation is not None
    assert presentation.version == 2
    assert presentation.file_key == f"pitches/{pitch_id}/presentations/2.pdf"


def test_upload_to_another_users_pitch_is_rejected(
    client: TestClient,
    db_session: Session,
    other_user_headers: dict[str, str],
    pitch_id: uuid.UUID,
    pdf: bytes,
) -> None:
    response = _upload(client, pitch_id, pdf, other_user_headers)

    assert response.status_code == 404
    assert response.json()["code"] == "PITCH_NOT_FOUND"
    assert _presentation_count(db_session, pitch_id) == 0


def test_upload_without_token_is_rejected(
    client: TestClient,
    db_session: Session,
    pitch_id: uuid.UUID,
    pdf: bytes,
) -> None:
    response = _upload(client, pitch_id, pdf)

    assert response.status_code == 401
    assert _presentation_count(db_session, pitch_id) == 0


def test_file_extension_does_not_decide_the_key(
    client: TestClient, auth_headers: dict[str, str], pitch_id: uuid.UUID, pdf: bytes
) -> None:
    """확장자는 클라이언트가 정한다. 열어 보고 PDF 면 받고, S3 키는 늘 .pdf 다."""
    response = _upload(client, pitch_id, pdf, auth_headers, filename="slides.key")

    assert response.status_code == 200
    assert response.json()["presentation"]["file_url"].endswith("/presentations/1.pdf")


@pytest.mark.parametrize(
    "content", [b"fake-pdf", b"", b"%PDF-1.7\n", b"PK\x03\x04 not a pdf"], ids=str
)
def test_file_that_is_not_a_pdf_is_400(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    content: bytes,
) -> None:
    response = _upload(client, pitch_id, content, auth_headers)

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_PRESENTATION"
    assert _presentation_count(db_session, pitch_id) == 0


def test_pdf_locked_with_an_open_password_is_400(
    client: TestClient, db_session: Session, auth_headers: dict[str, str], pitch_id: uuid.UUID
) -> None:
    """여는 암호가 걸린 PDF 는 FE(pdf.js)도 열지 못한다. 쓰기 제한만 걸린 것은 받는다."""
    from pypdf import PdfWriter

    def locked(user_password: str) -> bytes:
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        writer.encrypt(user_password=user_password, owner_password="owner", algorithm="AES-256")
        buffer = BytesIO()
        writer.write(buffer)
        return buffer.getvalue()

    assert _upload(client, pitch_id, locked(""), auth_headers).status_code == 200
    response = _upload(client, pitch_id, locked("open"), auth_headers)

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_PRESENTATION"
    assert _presentation_count(db_session, pitch_id) == 1


def test_file_over_the_size_limit_is_400(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    pdf: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(presentation_service, "MAX_PRESENTATION_BYTES", len(pdf) - 1)

    response = _upload(client, pitch_id, pdf, auth_headers)

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_PRESENTATION"
    assert _presentation_count(db_session, pitch_id) == 0
