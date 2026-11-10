"""First Take 튜토리얼 샘플 발표 (POST /api/pitches/tutorials/first-take) 와 리포트 1회 제한."""

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.adapter import sample as sample_files
from pitch_coach_backend.module.pitch.adapter.presentation_file import count_pdf_pages
from pitch_coach_backend.module.pitch.entity import (
    CoachingPlan,
    Pitch,
    PresentationVersion,
    ScriptSlide,
    ScriptVersion,
)
from pitch_coach_backend.module.pitch.exception import StorageError
from pitch_coach_backend.module.pitch.service import pitch as pitch_service
from pitch_coach_backend.module.pitch.service import tutorial as tutorial_service
from pitch_coach_backend.module.take.dto import ReviewResponseDTO
from pitch_coach_backend.module.take.entity import Take
from pitch_coach_backend.module.take.service import review as review_service
from pitch_coach_backend.module.user.entity import User
from tests.test_take_review_response import REVIEW

URL = "/api/pitches/tutorials/first-take"


@pytest.fixture
def uploads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """올린 S3 키. 실제 버킷을 부르지 않는다."""
    keys: list[str] = []

    def upload_bytes(data: bytes, key: str, content_type: str) -> str:
        keys.append(key)
        return key

    monkeypatch.setattr(tutorial_service, "upload_bytes", upload_bytes)
    return keys


@pytest.fixture
def committed_user(db_session: Session, user_id: uuid.UUID) -> uuid.UUID:
    # 서비스가 되돌리는 경로가 있어 사용자 행을 커밋해 둔다 (savepoint 모드)
    db_session.commit()
    return user_id


def _first_take(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(URL, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _demo_pitches(db: Session, user_id: uuid.UUID) -> list[Pitch]:
    return list(db.scalars(select(Pitch).where(Pitch.user_id == user_id, Pitch.is_demo)))


def test_sample_files_are_complete() -> None:
    sample = sample_files.first_take()

    assert count_pdf_pages(BytesIO(sample.pdf)) == sample.page_count
    assert [slide.slide_number for slide in sample.slides] == list(range(1, sample.page_count + 1))
    # 평가 기준이 모든 장에 있어야 DONE 으로 넣을 수 있다
    assert all(slide.rubric and slide.rubric_meta for slide in sample.slides)
    assert sample.segmented is True
    assert sample.plan.payload["fallback_reason"] is None
    assert sample.thumbnail


def test_creates_the_sample_pitch_ready_for_device_check(
    client: TestClient,
    db_session: Session,
    committed_user: uuid.UUID,
    auth_headers: dict[str, str],
    uploads: list[str],
) -> None:
    sample = sample_files.first_take()

    body = _first_take(client, auth_headers)

    pitch_id = uuid.UUID(body["pitch_id"])
    assert body["report_received"] is False
    pitch = db_session.get(Pitch, pitch_id)
    assert pitch is not None and pitch.is_demo
    assert (pitch.title, pitch.time_limit_sec, pitch.lower_deviation, pitch.upper_deviation) == (
        sample.title,
        sample.time_limit_sec,
        sample.lower_deviation,
        sample.upper_deviation,
    )

    presentation = db_session.scalars(
        select(PresentationVersion).where(PresentationVersion.pitch_id == pitch_id)
    ).one()
    assert presentation.page_count == sample.page_count
    # 발표를 지울 때 이 경로 아래를 같이 지운다
    assert presentation.file_key.startswith(f"pitches/{pitch_id}/")
    assert uploads == [presentation.file_key, presentation.thumbnail_key]

    script = db_session.scalars(
        select(ScriptVersion).where(ScriptVersion.pitch_id == pitch_id)
    ).one()
    assert (script.parse_status, script.rubric_status, script.segmented) == ("DONE", "DONE", True)
    assert script.content == sample.content
    slides = db_session.scalars(
        select(ScriptSlide)
        .where(ScriptSlide.script_version_id == script.id)
        .order_by(ScriptSlide.slide_number)
    ).all()
    assert len(slides) == sample.page_count
    assert {slide.rubric["meta"]["script_name"] for slide in slides} == {str(script.id)}

    plan = db_session.scalars(select(CoachingPlan).where(CoachingPlan.pitch_id == pitch_id)).one()
    assert (plan.script_version_id, plan.source_take_id) == (script.id, None)

    # 장치 점검 화면이 바로 이 버전들을 쓴다
    prepare = client.get(f"/api/pitches/{pitch_id}/prepare", headers=auth_headers).json()
    assert prepare["script"]["id"] == str(script.id)
    assert prepare["presentation"]["id"] == str(presentation.id)


def test_second_call_returns_the_same_pitch_without_uploading(
    client: TestClient,
    db_session: Session,
    committed_user: uuid.UUID,
    auth_headers: dict[str, str],
    uploads: list[str],
) -> None:
    first = _first_take(client, auth_headers)
    uploads.clear()

    assert _first_take(client, auth_headers)["pitch_id"] == first["pitch_id"]
    assert uploads == []
    assert len(_demo_pitches(db_session, committed_user)) == 1


def test_losing_a_concurrent_create_returns_the_winner(
    db_session: Session,
    committed_user: uuid.UUID,
    uploads: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 다른 요청이 먼저 만들어 커밋했고, 이 요청은 그 전에 없다고 읽었다
    winner = Pitch(user_id=committed_user, title="샘플", time_limit_sec=180, is_demo=True)
    db_session.add(winner)
    db_session.commit()
    real = tutorial_service.PitchRepository.get_demo_pitch_id
    reads: list[uuid.UUID] = []

    def read(self: tutorial_service.PitchRepository, user_id: uuid.UUID) -> uuid.UUID | None:
        reads.append(user_id)
        return None if len(reads) == 1 else real(self, user_id)

    monkeypatch.setattr(tutorial_service.PitchRepository, "get_demo_pitch_id", read)

    result = tutorial_service.get_or_create_first_take(db_session, committed_user)

    # 처음에는 없다고 읽고, 넣기가 막힌 뒤 다시 읽어 이긴 요청의 발표를 받는다
    assert len(reads) == 2
    assert result.pitch_id == winner.id
    assert uploads == []
    assert len(_demo_pitches(db_session, committed_user)) == 1


def test_concurrent_creates_make_one_sample(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """두 요청이 동시에 와도 샘플 발표는 하나이고 둘 다 같은 발표를 받는다.

    첫 요청이 행을 넣고 파일을 올리는 동안(커밋 전) 둘째 요청이 들어오게 한다. 둘째는 넣기에서
    첫 요청의 커밋을 기다렸다가 그 발표를 받는다. 트랜잭션 두 개가 실제로 겹쳐야 해서 테스트
    세션(savepoint) 대신 진짜 세션 둘을 쓰고, 만든 데이터는 끝에 지운다.
    """
    first_uploading = threading.Event()
    keys: list[str] = []

    def slow_upload(data: bytes, key: str, content_type: str) -> str:
        keys.append(key)
        if not first_uploading.is_set():
            first_uploading.set()
            time.sleep(0.3)
        return key

    monkeypatch.setattr(tutorial_service, "upload_bytes", slow_upload)

    with Session(engine) as setup:
        user = User(email=f"{uuid.uuid4()}@example.com", name="동시 튜토리얼")
        setup.add(user)
        setup.commit()
        user_id = user.id

    results: dict[str, Any] = {}

    def create(name: str) -> None:
        with Session(engine, expire_on_commit=False) as db:
            try:
                results[name] = tutorial_service.get_or_create_first_take(db, user_id).pitch_id
            except Exception as exc:
                results[name] = exc

    try:
        first = threading.Thread(target=create, args=("a",))
        first.start()
        assert first_uploading.wait(5)
        second = threading.Thread(target=create, args=("b",))
        second.start()
        first.join(10)
        second.join(10)

        assert all(isinstance(value, uuid.UUID) for value in results.values()), results
        assert results["a"] == results["b"]
        # 진 요청은 파일을 올리지 않는다
        assert len(keys) == 2
        with Session(engine) as check:
            assert len(_demo_pitches(check, user_id)) == 1
    finally:
        with Session(engine) as cleanup:
            cleanup.delete(cleanup.get(User, user_id))  # 발표 · 버전 · 계획은 CASCADE
            cleanup.commit()


def test_upload_failure_leaves_no_sample_pitch(
    client: TestClient,
    db_session: Session,
    committed_user: uuid.UUID,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(data: bytes, key: str, content_type: str) -> str:
        raise StorageError()

    monkeypatch.setattr(tutorial_service, "upload_bytes", fail)

    assert client.post(URL, headers=auth_headers).status_code == 503
    assert _demo_pitches(db_session, committed_user) == []


def test_db_failure_after_upload_discards_the_files(
    db_session: Session,
    committed_user: uuid.UUID,
    uploads: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    discarded: list[tuple[str, str | None]] = []

    def broken(*args: object) -> None:
        raise RuntimeError("DB")

    monkeypatch.setattr(tutorial_service, "_add_materials", broken)
    monkeypatch.setattr(
        tutorial_service,
        "discard_upload",
        lambda db, presentation_id, key, thumbnail_key: discarded.append((key, thumbnail_key)),
    )

    with pytest.raises(RuntimeError):
        tutorial_service.get_or_create_first_take(db_session, committed_user)

    assert discarded == [(uploads[0], uploads[1])]
    assert _demo_pitches(db_session, committed_user) == []


def test_pitch_list_and_detail_tell_the_sample_apart(
    client: TestClient,
    committed_user: uuid.UUID,
    auth_headers: dict[str, str],
    uploads: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pitch_service, "generate_presigned_url", lambda key: f"https://fake/{key}")
    demo_id = _first_take(client, auth_headers)["pitch_id"]
    normal_id = client.post(
        "/api/pitches/add",
        json={"title": "내 발표", "time_limit_sec": 300},
        headers=auth_headers,
    ).json()["pitch_id"]

    listed = client.get("/api/pitches/", headers=auth_headers).json()["pitches"]
    assert {item["pitch_id"]: item["is_demo"] for item in listed} == {
        demo_id: True,
        normal_id: False,
    }
    assert client.get(f"/api/pitches/{demo_id}", headers=auth_headers).json()["is_demo"] is True
    assert client.get(f"/api/pitches/{normal_id}", headers=auth_headers).json()["is_demo"] is False


def test_deleted_sample_is_made_again(
    client: TestClient,
    db_session: Session,
    committed_user: uuid.UUID,
    auth_headers: dict[str, str],
    uploads: list[str],
) -> None:
    first = _first_take(client, auth_headers)["pitch_id"]
    assert client.delete(f"/api/pitches/delete/{first}", headers=auth_headers).status_code == 200

    again = _first_take(client, auth_headers)["pitch_id"]

    assert again != first
    assert [pitch.id for pitch in _demo_pitches(db_session, committed_user)] == [uuid.UUID(again)]


# ── 튜토리얼 리포트는 계정당 한 번 ─────────────────────────────────────────


def _versions(db: Session, pitch_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    presentation = db.scalars(
        select(PresentationVersion.id).where(PresentationVersion.pitch_id == pitch_id)
    ).one()
    script = db.scalars(select(ScriptVersion.id).where(ScriptVersion.pitch_id == pitch_id)).one()
    return presentation, script


def _start_take(client: TestClient, db: Session, pitch_id: str, headers: dict[str, str]):
    presentation_id, script_id = _versions(db, uuid.UUID(pitch_id))
    return client.post(
        f"/api/pitches/{pitch_id}/takes/",
        json={
            "mode": "COACHING",
            "script_mode": "HIGHLIGHT",
            "presentation_version_id": str(presentation_id),
            "script_version_id": str(script_id),
        },
        headers=headers,
    )


def _add_take(db: Session, pitch_id: str, number: int, **values: object) -> Take:
    presentation_id, script_id = _versions(db, uuid.UUID(pitch_id))
    take = Take(
        pitch_id=uuid.UUID(pitch_id),
        take_number=number,
        presentation_version_id=presentation_id,
        script_version_id=script_id,
        mode="COACHING",
        script_mode="HIGHLIGHT",
        goal_time_sec=180,
        **values,
    )
    db.add(take)
    db.flush()
    return take


def _mark_report(db: Session, user_id: uuid.UUID) -> None:
    db.get(User, user_id).tutorial_report_at = datetime.now(UTC)
    db.flush()


def test_sample_take_is_refused_after_the_tutorial_report(
    client: TestClient,
    db_session: Session,
    committed_user: uuid.UUID,
    auth_headers: dict[str, str],
    uploads: list[str],
) -> None:
    pitch_id = _first_take(client, auth_headers)["pitch_id"]
    assert _start_take(client, db_session, pitch_id, auth_headers).status_code == 200

    _mark_report(db_session, committed_user)

    response = _start_take(client, db_session, pitch_id, auth_headers)
    assert response.status_code == 409
    assert response.json()["code"] == "TUTORIAL_REPORT_USED"
    assert _first_take(client, auth_headers)["report_received"] is True


def test_sample_is_not_made_again_after_the_tutorial_report(
    client: TestClient,
    db_session: Session,
    committed_user: uuid.UUID,
    auth_headers: dict[str, str],
    uploads: list[str],
) -> None:
    first = _first_take(client, auth_headers)["pitch_id"]
    _mark_report(db_session, committed_user)
    # 남아 있는 샘플은 기록을 보라고 그대로 준다
    assert _first_take(client, auth_headers) == {"pitch_id": first, "report_received": True}
    client.delete(f"/api/pitches/delete/{first}", headers=auth_headers)
    uploaded = list(uploads)

    again = _first_take(client, auth_headers)

    # 리포트 기록은 사용자에 남아 있다. 만들어도 연습할 수 없으니 만들지 않고 파일도 올리지 않는다
    assert again == {"pitch_id": None, "report_received": True}
    assert _demo_pitches(db_session, committed_user) == []
    assert uploads == uploaded


def test_normal_pitch_is_not_limited_by_the_tutorial_report(
    client: TestClient,
    db_session: Session,
    committed_user: uuid.UUID,
    auth_headers: dict[str, str],
) -> None:
    pitch = Pitch(user_id=committed_user, title="내 발표", time_limit_sec=300)
    db_session.add(pitch)
    db_session.flush()
    db_session.add_all(
        [
            PresentationVersion(pitch_id=pitch.id, version=1, file_key=f"{pitch.id}/1.pdf"),
            ScriptVersion(pitch_id=pitch.id, version=1, content="대본", parse_status="DONE"),
        ]
    )
    _mark_report(db_session, committed_user)

    assert _start_take(client, db_session, str(pitch.id), auth_headers).status_code == 200


def test_sample_take_waits_for_a_running_analysis(
    client: TestClient,
    db_session: Session,
    committed_user: uuid.UUID,
    auth_headers: dict[str, str],
    uploads: list[str],
) -> None:
    pitch_id = _first_take(client, auth_headers)["pitch_id"]
    analyzing = _add_take(
        db_session,
        pitch_id,
        1,
        status="ANALYZING",
        analysis_stage="SCORING",
        analysis_stage_at=datetime.now(UTC),
    )

    response = _start_take(client, db_session, pitch_id, auth_headers)
    assert response.status_code == 409
    assert response.json()["code"] == "TUTORIAL_ANALYSIS_RUNNING"

    # 멈춘 분석은 리포트가 나오지 않으니 막지 않는다 (발표 삭제와 같은 기준)
    analyzing.analysis_stage_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.flush()
    assert _start_take(client, db_session, pitch_id, auth_headers).status_code == 200


# ── 장치 점검 준비가 미리 알리는 막힘 ─────────────────────────────────────


def _tutorial_block(client: TestClient, pitch_id: object, headers: dict[str, str]) -> object:
    response = client.get(f"/api/pitches/{pitch_id}/prepare", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["tutorial_block"]


def test_prepare_tells_why_the_sample_cannot_start(
    client: TestClient,
    db_session: Session,
    committed_user: uuid.UUID,
    auth_headers: dict[str, str],
    uploads: list[str],
) -> None:
    pitch_id = _first_take(client, auth_headers)["pitch_id"]
    assert _tutorial_block(client, pitch_id, auth_headers) is None

    analyzing = _add_take(
        db_session,
        pitch_id,
        1,
        status="ANALYZING",
        analysis_stage="SCORING",
        analysis_stage_at=datetime.now(UTC),
    )
    # Take 생성과 같은 판정이다. 분석 중을 먼저 본다
    _mark_report(db_session, committed_user)
    assert _tutorial_block(client, pitch_id, auth_headers) == "TUTORIAL_ANALYSIS_RUNNING"
    assert _start_take(client, db_session, pitch_id, auth_headers).json()["code"] == (
        "TUTORIAL_ANALYSIS_RUNNING"
    )

    analyzing.status = "COMPLETED"
    db_session.flush()
    assert _tutorial_block(client, pitch_id, auth_headers) == "TUTORIAL_REPORT_USED"
    assert _start_take(client, db_session, pitch_id, auth_headers).json()["code"] == (
        "TUTORIAL_REPORT_USED"
    )


def test_prepare_of_a_normal_pitch_is_not_blocked_by_the_tutorial_report(
    client: TestClient,
    db_session: Session,
    committed_user: uuid.UUID,
    auth_headers: dict[str, str],
) -> None:
    pitch = Pitch(user_id=committed_user, title="내 발표", time_limit_sec=300)
    db_session.add(pitch)
    db_session.flush()
    _mark_report(db_session, committed_user)

    assert _tutorial_block(client, pitch.id, auth_headers) is None


def _reviewing_take(db: Session, pitch_id: str) -> tuple[Take, uuid.UUID]:
    request_id = uuid.uuid4()
    take = _add_take(
        db,
        pitch_id,
        1,
        status="ANALYZING",
        analysis_stage="REVIEWING",
        analysis_stage_at=datetime.now(UTC),
        review_request_id=request_id,
    )
    return take, request_id


def test_saved_review_of_the_sample_counts_as_the_tutorial_report(
    client: TestClient,
    db_session: Session,
    committed_user: uuid.UUID,
    auth_headers: dict[str, str],
    uploads: list[str],
) -> None:
    pitch_id = _first_take(client, auth_headers)["pitch_id"]
    take, request_id = _reviewing_take(db_session, pitch_id)
    review = ReviewResponseDTO.model_validate({**REVIEW, "take_id": str(take.id)})

    assert review_service.save_review(db_session, take.id, review, request_id=request_id)

    assert db_session.get(User, committed_user).tutorial_report_at is not None


def test_failed_review_of_the_sample_does_not_count(
    client: TestClient,
    db_session: Session,
    committed_user: uuid.UUID,
    auth_headers: dict[str, str],
    uploads: list[str],
) -> None:
    pitch_id = _first_take(client, auth_headers)["pitch_id"]
    take, request_id = _reviewing_take(db_session, pitch_id)

    assert review_service.fail_review(db_session, take.id, request_id)

    assert db_session.get(User, committed_user).tutorial_report_at is None
    assert _start_take(client, db_session, pitch_id, auth_headers).status_code == 200


def test_saved_review_of_a_normal_pitch_does_not_count(
    db_session: Session, committed_user: uuid.UUID
) -> None:
    pitch = Pitch(user_id=committed_user, title="내 발표", time_limit_sec=300)
    db_session.add(pitch)
    db_session.flush()
    db_session.add_all(
        [
            PresentationVersion(pitch_id=pitch.id, version=1, file_key=f"{pitch.id}/1.pdf"),
            ScriptVersion(pitch_id=pitch.id, version=1, content="대본", parse_status="DONE"),
        ]
    )
    db_session.flush()
    take, request_id = _reviewing_take(db_session, str(pitch.id))
    review = ReviewResponseDTO.model_validate({**REVIEW, "take_id": str(take.id)})

    assert review_service.save_review(db_session, take.id, review, request_id=request_id)

    assert db_session.get(User, committed_user).tutorial_report_at is None
