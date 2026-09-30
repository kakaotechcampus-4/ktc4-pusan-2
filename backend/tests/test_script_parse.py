"""대본 파싱 흐름: POST /scripts 202 → 백그라운드 파싱 → 폴링 → 재시도.

AI 만 가짜 파서로 바꾸고 나머지(엔드포인트·BackgroundTasks·runner·service·DB)는 실제로 돈다.
대본은 S3 를 거치지 않는다 (원문은 DB). TestClient 는 응답을 돌려준 뒤 백그라운드 작업까지 마치고
반환하므로, 올린 직후 GET 하면 파싱이 끝난 상태를 본다.
"아직 PENDING" 은 conftest 의 기본값(아무것도 안 하는 runner)으로 본다.
"""

import logging
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.main import app
from pitch_coach_backend.module.pitch import service
from pitch_coach_backend.module.pitch.dependencies import get_script_parse_runner
from pitch_coach_backend.module.pitch.dto import ParseTicket, ScriptParseErrorCode
from pitch_coach_backend.module.pitch.entity import (
    Pitch,
    ScriptParseStatus,
    ScriptSlide,
    ScriptVersion,
)
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.pitch.script_parse_runner import ScriptParseRunner
from pitch_coach_backend.module.pitch.script_parser import (
    ParsedScript,
    ParsedSlide,
    ScriptParseError,
)

SCRIPT_TEXT = "슬라이드 1:\n안녕하세요. SeatFlow 입니다.\n\n슬라이드 2:\n감사합니다."

TWO_SLIDES = ParsedScript(
    segmented=True,
    slides=(
        ParsedSlide(
            slide_number=1,
            content="안녕하세요. SeatFlow 입니다.",
            keywords=("SeatFlow",),
            highlights=((7, 15),),
        ),
        ParsedSlide(slide_number=2, content="감사합니다.", keywords=("감사",), highlights=()),
    ),
    terms=("SeatFlow",),
)


class FakeParser:
    """AI 역할. 정해 둔 결과를 돌려주거나 예외를 던지고, 받은 대본을 남긴다."""

    def __init__(self, result: ParsedScript | Exception = TWO_SLIDES) -> None:
        self.result = result
        self.calls: list[str] = []

    async def parse(self, script_text: str) -> ParsedScript:
        self.calls.append(script_text)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def parser(db_session: Session, client: TestClient) -> FakeParser:
    """가짜 파서를 끼운 실제 runner. 저장은 테스트 세션으로 한다."""
    fake = FakeParser()
    runner = ScriptParseRunner(fake, session_factory=lambda: db_session)
    app.dependency_overrides[get_script_parse_runner] = lambda: runner
    return fake


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


def _new_pitch(client: TestClient, headers: dict[str, str]) -> uuid.UUID:
    response = client.post(
        "/api/pitches/add",
        json={"title": "파싱 테스트", "time_limit_sec": 600, "presentation_date": "2026-11-01"},
        headers=headers,
    )
    assert response.status_code == 200
    return uuid.UUID(response.json()["pitch_id"])


@pytest.fixture
def pitch_id(client: TestClient, auth_headers: dict[str, str]) -> uuid.UUID:
    return _new_pitch(client, auth_headers)


def _create(
    client: TestClient,
    pitch_id: uuid.UUID,
    headers: dict[str, str],
    content: str = SCRIPT_TEXT,
):
    return client.post(
        f"/api/pitches/{pitch_id}/scripts", json={"content": content}, headers=headers
    )


def _create_ok(client: TestClient, pitch_id: uuid.UUID, headers: dict[str, str]) -> uuid.UUID:
    response = _create(client, pitch_id, headers)
    assert response.status_code == 202
    return uuid.UUID(response.json()["script_version_id"])


def _get(client: TestClient, pitch_id: uuid.UUID, script_id: uuid.UUID, headers: dict[str, str]):
    return client.get(f"/api/pitches/{pitch_id}/scripts/{script_id}", headers=headers)


def _reparse(
    client: TestClient, pitch_id: uuid.UUID, script_id: uuid.UUID, headers: dict[str, str]
):
    return client.post(f"/api/pitches/{pitch_id}/scripts/{script_id}/parse", headers=headers)


def _age(db: Session, script_id: uuid.UUID, seconds: float) -> None:
    """요청 시각을 과거로 옮긴다 (BE 가 재시작돼 작업이 사라진 상황)."""
    script = db.get(ScriptVersion, script_id)
    assert script is not None
    script.parse_requested_at -= timedelta(seconds=seconds)
    # flush 가 아니라 commit. service 가 경합에서 지면 rollback() 하는데,
    # 그때 같이 되돌려지면 안 된다
    db.commit()


# ── 업로드 → 파싱 → 조회 ─────────────────────────────────────────────


def test_create_returns_pending_then_background_parse_makes_it_done(
    client: TestClient,
    parser: FakeParser,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    response = _create(client, pitch_id, auth_headers)

    assert response.status_code == 202
    created = response.json()
    assert (created["version"], created["parse_status"]) == (1, "PENDING")
    script_id = uuid.UUID(created["script_version_id"])
    # AI 에는 올린 텍스트가 그대로 간다
    assert parser.calls == [SCRIPT_TEXT]

    body = _get(client, pitch_id, script_id, auth_headers).json()

    assert body == {
        "script_version_id": str(script_id),
        "version": 1,
        "original_content": SCRIPT_TEXT,
        "parse_status": "DONE",
        "segmented": True,
        "slides": [
            {
                "slide_number": 1,
                "content": "안녕하세요. SeatFlow 입니다.",
                "keywords": ["SeatFlow"],
                "highlights": [{"start": 7, "end": 15}],
            },
            {"slide_number": 2, "content": "감사합니다.", "keywords": ["감사"], "highlights": []},
        ],
        "terms": ["SeatFlow"],
        "error_code": None,
    }
    assert "안녕하세요. SeatFlow 입니다."[7:15] == "SeatFlow"


def test_poll_before_parse_finishes_is_pending_with_the_same_keys(
    client: TestClient,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    # parser fixture 를 안 쓰면 conftest 의 runner 가 아무것도 안 한다 = 아직 파싱 중
    script_id = _create_ok(client, pitch_id, auth_headers)

    body = _get(client, pitch_id, script_id, auth_headers).json()

    assert body == {
        "script_version_id": str(script_id),
        "version": 1,
        "original_content": SCRIPT_TEXT,
        "parse_status": "PENDING",
        "segmented": None,
        "slides": [],
        "terms": [],
        "error_code": None,
    }


def test_unsegmented_result_is_done_with_one_slide(
    client: TestClient,
    parser: FakeParser,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    parser.result = ParsedScript(
        segmented=False,
        slides=(ParsedSlide(1, SCRIPT_TEXT, ("SeatFlow",), ()),),
        terms=(),
    )
    script_id = _create_ok(client, pitch_id, auth_headers)

    body = _get(client, pitch_id, script_id, auth_headers).json()

    assert body["parse_status"] == "DONE"
    assert body["segmented"] is False
    assert [slide["slide_number"] for slide in body["slides"]] == [1]


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (
            ScriptParseError(ScriptParseErrorCode.AI_TIMEOUT, "slow", retryable=True),
            "AI_TIMEOUT",
        ),
        (
            ScriptParseError(ScriptParseErrorCode.AI_REJECTED, "422", retryable=False),
            "AI_REJECTED",
        ),
        # 파서 밖의 예상 못 한 오류도 PENDING 으로 남기지 않는다
        (RuntimeError("boom"), "INTERNAL_ERROR"),
    ],
)
def test_parse_failure_is_failed_with_code(
    client: TestClient,
    db_session: Session,
    parser: FakeParser,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    error: Exception,
    code: str,
) -> None:
    parser.result = error
    script_id = _create_ok(client, pitch_id, auth_headers)

    body = _get(client, pitch_id, script_id, auth_headers).json()

    assert body["parse_status"] == "FAILED"
    assert body["error_code"] == code
    assert body["slides"] == []
    slides = db_session.scalar(
        select(func.count())
        .select_from(ScriptSlide)
        .where(ScriptSlide.script_version_id == script_id)
    )
    assert slides == 0


# ── 입력 검사 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("   \n\t ", id="blank"),
        pytest.param("가" * (service.MAX_SCRIPT_CHARS + 1), id="too-many-chars"),
        # JSON 은 \u0000 을 허용하지만 PostgreSQL TEXT 는 NUL 을 넣지 못한다
        pytest.param("가\x00나", id="nul"),
    ],
)
def test_invalid_script_is_rejected_and_nothing_is_stored(
    client: TestClient,
    db_session: Session,
    parser: FakeParser,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    content: str,
) -> None:
    response = _create(client, pitch_id, auth_headers, content=content)

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_SCRIPT"
    count = db_session.scalar(
        select(func.count()).select_from(ScriptVersion).where(ScriptVersion.pitch_id == pitch_id)
    )
    assert count == 0
    assert parser.calls == []


def test_missing_content_is_a_validation_error(
    client: TestClient, auth_headers: dict[str, str], pitch_id: uuid.UUID
) -> None:
    response = client.post(f"/api/pitches/{pitch_id}/scripts", json={}, headers=auth_headers)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_script_versions_count_up_per_pitch(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    """대본 버전은 pitch 안에서 1, 2, … 로 자동으로 매겨진다 (발표자료 버전과는 테이블이 따로다)."""
    first = _create(client, pitch_id, auth_headers, content="첫 대본").json()
    second = _create(client, pitch_id, auth_headers, content="고친 대본").json()
    other_pitch_first = _create(client, _new_pitch(client, auth_headers), auth_headers).json()

    assert (first["version"], second["version"]) == (1, 2)
    assert other_pitch_first["version"] == 1  # 다른 pitch 는 1 부터
    # 원문은 S3 가 아니라 DB 에 그대로 들어간다
    stored = db_session.get(ScriptVersion, uuid.UUID(second["script_version_id"]))
    assert stored is not None
    assert stored.content == "고친 대본"


# ── 재시도 ─────────────────────────────────────────────────────────


def test_retry_after_failure_reuses_stored_text_and_finishes(
    client: TestClient,
    parser: FakeParser,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    parser.result = ScriptParseError(ScriptParseErrorCode.AI_ERROR, "503", retryable=True)
    script_id = _create_ok(client, pitch_id, auth_headers)
    assert _get(client, pitch_id, script_id, auth_headers).json()["parse_status"] == "FAILED"

    parser.result = TWO_SLIDES
    response = _reparse(client, pitch_id, script_id, auth_headers)

    assert response.status_code == 202
    assert response.json() == {"script_version_id": str(script_id), "parse_status": "PENDING"}
    # 두 번째 호출도 같은 원문이다 (DB 의 content)
    assert parser.calls == [SCRIPT_TEXT, SCRIPT_TEXT]
    body = _get(client, pitch_id, script_id, auth_headers).json()
    assert body["parse_status"] == "DONE"
    assert body["error_code"] is None
    assert len(body["slides"]) == 2


def test_retry_is_refused_while_pending(
    client: TestClient,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    script_id = _create_ok(client, pitch_id, auth_headers)  # 기본 runner: PENDING 으로 남는다

    response = _reparse(client, pitch_id, script_id, auth_headers)
    assert response.status_code == 409
    assert response.json()["code"] == "SCRIPT_PARSE_IN_PROGRESS"


def test_retry_is_refused_after_done(
    client: TestClient,
    parser: FakeParser,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    script_id = _create_ok(client, pitch_id, auth_headers)

    response = _reparse(client, pitch_id, script_id, auth_headers)

    assert response.status_code == 409
    assert response.json()["code"] == "SCRIPT_ALREADY_PARSED"
    assert len(parser.calls) == 1


def test_double_retry_starts_only_one_job(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    """재시도 버튼을 두 번 눌러도 두 번째는 409. 첫 요청이 이미 PENDING 으로 바꿨다."""
    script_id = _create_ok(client, pitch_id, auth_headers)
    _age(db_session, script_id, 3600)  # 만료 → 재시도 가능

    assert _reparse(client, pitch_id, script_id, auth_headers).status_code == 202
    second = _reparse(client, pitch_id, script_id, auth_headers)

    assert second.status_code == 409
    assert second.json()["code"] == "SCRIPT_PARSE_IN_PROGRESS"


# ── 만료 (BE 재시작으로 작업이 사라진 경우) ──────────────────────────────


def test_stale_pending_is_reported_as_expired_and_can_be_retried(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    script_id = _create_ok(client, pitch_id, auth_headers)
    _age(db_session, script_id, service.PARSE_EXPIRE_AFTER.total_seconds() + 1)

    body = _get(client, pitch_id, script_id, auth_headers).json()
    assert body["parse_status"] == "FAILED"
    assert body["error_code"] == "PARSE_EXPIRED"
    # 조회는 DB 를 바꾸지 않는다. 만료는 읽을 때 계산한 값이다
    script = db_session.get(ScriptVersion, script_id)
    assert script is not None
    assert script.parse_status == ScriptParseStatus.PENDING

    assert _reparse(client, pitch_id, script_id, auth_headers).status_code == 202


def test_pending_just_before_expiry_is_still_pending(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    script_id = _create_ok(client, pitch_id, auth_headers)
    _age(db_session, script_id, service.PARSE_EXPIRE_AFTER.total_seconds() - 5)

    assert _get(client, pitch_id, script_id, auth_headers).json()["parse_status"] == "PENDING"
    assert _reparse(client, pitch_id, script_id, auth_headers).status_code == 409


def test_legacy_script_asks_for_reupload(
    client: TestClient,
    db_session: Session,
    parser: FakeParser,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    """파싱 기능 전에 올라온 대본: 원문이 S3 파일로만 있었고(.docx 포함) DB 에는 없다.

    migration 이 FAILED + LEGACY_UNPARSED 로 채운다.
    재시도로는 풀 수 없으니 409 로 다시 올리라고 답한다.
    """
    legacy = ScriptVersion(
        pitch_id=pitch_id,
        version=1,
        parse_status=ScriptParseStatus.FAILED,
        parse_error=ScriptParseErrorCode.LEGACY_UNPARSED,
    )
    db_session.add(legacy)
    db_session.flush()

    body = _get(client, pitch_id, legacy.id, auth_headers).json()
    assert (body["parse_status"], body["error_code"]) == ("FAILED", "LEGACY_UNPARSED")
    assert body["original_content"] is None

    response = _reparse(client, pitch_id, legacy.id, auth_headers)

    assert response.status_code == 409
    assert response.json()["code"] == "SCRIPT_REUPLOAD_REQUIRED"
    assert _get(client, pitch_id, legacy.id, auth_headers).json()["parse_status"] == "FAILED"
    assert parser.calls == []


# ── 늦게 끝난 옛 작업 ────────────────────────────────────────────────


def _ticket(db: Session, script_id: uuid.UUID) -> ParseTicket:
    script = db.get(ScriptVersion, script_id)
    assert script is not None
    return ParseTicket(script_id, script.parse_requested_at, SCRIPT_TEXT)


def test_old_job_cannot_overwrite_a_newer_attempt(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    """만료로 보인 뒤 재시도가 시작됐는데, 사실 살아 있던 옛 작업이 늦게 끝난 경우."""
    script_id = _create_ok(client, pitch_id, auth_headers)
    old = _ticket(db_session, script_id)
    _age(db_session, script_id, 3600)
    assert _reparse(client, pitch_id, script_id, auth_headers).status_code == 202
    new = _ticket(db_session, script_id)
    assert new.requested_at != old.requested_at

    # 옛 작업의 성공도 실패도 반영되지 않는다
    assert service.complete_parse(db_session, old, TWO_SLIDES) is False
    assert service.fail_parse(db_session, old, ScriptParseErrorCode.AI_ERROR) is False
    assert _get(client, pitch_id, script_id, auth_headers).json()["parse_status"] == "PENDING"

    # 새 작업은 반영된다. 끝난 뒤에는 같은 작업이 다시 와도 무시된다 (중복 실행 방지)
    assert service.complete_parse(db_session, new, TWO_SLIDES) is True
    assert service.fail_parse(db_session, new, ScriptParseErrorCode.AI_ERROR) is False
    body = _get(client, pitch_id, script_id, auth_headers).json()
    assert body["parse_status"] == "DONE"
    assert len(body["slides"]) == 2


# ── 권한 ───────────────────────────────────────────────────────────


def test_other_users_script_is_not_found(
    client: TestClient,
    auth_headers: dict[str, str],
    other_user_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    script_id = _create_ok(client, pitch_id, auth_headers)

    for response in (
        _get(client, pitch_id, script_id, other_user_headers),
        _reparse(client, pitch_id, script_id, other_user_headers),
    ):
        assert response.status_code == 404
        assert response.json()["code"] == "PITCH_NOT_FOUND"


def test_script_of_another_pitch_is_not_found(
    client: TestClient,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    """내 pitch 두 개 사이에서 id 를 바꿔 끼워도 안 된다."""
    script_id = _create_ok(client, pitch_id, auth_headers)
    other_pitch = _new_pitch(client, auth_headers)

    for response in (
        _get(client, other_pitch, script_id, auth_headers),
        _reparse(client, other_pitch, script_id, auth_headers),
        _get(client, pitch_id, uuid.uuid7(), auth_headers),
    ):
        assert response.status_code == 404
        assert response.json()["code"] == "SCRIPT_NOT_FOUND"


def test_expired_job_cannot_flip_failed_back_to_done(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    """FE 가 이미 FAILED(PARSE_EXPIRED) 를 본 뒤 옛 작업이 늦게 끝나도 DONE 으로 뒤집지 않는다.

    뒤집히면 FE 의 "다시 시도" 가 409 SCRIPT_ALREADY_PARSED 를 받는다.
    """
    script_id = _create_ok(client, pitch_id, auth_headers)
    _age(db_session, script_id, service.PARSE_EXPIRE_AFTER.total_seconds() + 1)
    late = _ticket(db_session, script_id)

    assert service.complete_parse(db_session, late, TWO_SLIDES) is False
    assert service.fail_parse(db_session, late, ScriptParseErrorCode.AI_ERROR) is False

    body = _get(client, pitch_id, script_id, auth_headers).json()
    assert (body["parse_status"], body["error_code"]) == ("FAILED", "PARSE_EXPIRED")
    assert _reparse(client, pitch_id, script_id, auth_headers).status_code == 202


# ── 조건부 UPDATE 에서 진 요청 ────────────────────────────────────────
# 순서대로 보내면 두 번째 요청은 service 의 빠른 검사에서 걸려 UPDATE 까지 가지 않는다.
# 그래서 "검사는 통과했는데 UPDATE 직전에 다른 쪽이 먼저 바꾼" 상황을 직접 만든다.


def test_restart_parse_lets_only_one_of_two_win(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    script_id = _create_ok(client, pitch_id, auth_headers)
    _age(db_session, script_id, 3600)
    repo = PitchRepository(db_session)
    now = datetime.now(UTC)
    expired_before = now - service.PARSE_EXPIRE_AFTER

    assert repo.restart_parse(script_id, now, expired_before) is True
    assert repo.restart_parse(script_id, now, expired_before) is False


# monkeypatch 전에 잡아 둔 원래 함수. competitor 가 이걸 불러야 재귀하지 않는다
_REAL_RESTART_PARSE = PitchRepository.restart_parse


def _race(
    monkeypatch: pytest.MonkeyPatch,
    db: Session,
    competitor: Callable[[uuid.UUID], None],
) -> None:
    """restart_parse 직전에 다른 요청(competitor)이 먼저 커밋하게 만든다."""
    real = _REAL_RESTART_PARSE

    def racing(self: PitchRepository, script_id: uuid.UUID, now: Any, expired_before: Any):
        competitor(script_id)
        db.commit()
        return real(self, script_id, now, expired_before)

    monkeypatch.setattr(PitchRepository, "restart_parse", racing)


def test_losing_a_retry_race_answers_in_progress(
    client: TestClient,
    db_session: Session,
    parser: FakeParser,
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    parser.result = ScriptParseError(ScriptParseErrorCode.AI_ERROR, "503", retryable=True)
    script_id = _create_ok(client, pitch_id, auth_headers)
    parser.calls.clear()

    def other_retry(script_id: uuid.UUID) -> None:
        now = datetime.now(UTC)
        repo = PitchRepository(db_session)
        _REAL_RESTART_PARSE(repo, script_id, now, now - service.PARSE_EXPIRE_AFTER)

    _race(monkeypatch, db_session, other_retry)
    response = _reparse(client, pitch_id, script_id, auth_headers)

    assert response.status_code == 409
    assert response.json()["code"] == "SCRIPT_PARSE_IN_PROGRESS"
    # 진 쪽은 작업을 띄우지 않는다
    assert parser.calls == []


def test_losing_to_a_finished_job_answers_already_parsed(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    """검사와 UPDATE 사이에 살아 있던 작업이 DONE 을 쓴 경우.

    IN_PROGRESS 가 아니라 ALREADY_PARSED 로 답해야 FE 가 폴링으로 결과를 가져간다.
    """
    script_id = _create_ok(client, pitch_id, auth_headers)
    _age(db_session, script_id, 3600)

    def job_finished(script_id: uuid.UUID) -> None:
        script = db_session.get(ScriptVersion, script_id)
        assert script is not None
        script.parse_status = ScriptParseStatus.DONE

    _race(monkeypatch, db_session, job_finished)
    response = _reparse(client, pitch_id, script_id, auth_headers)

    assert response.status_code == 409
    assert response.json()["code"] == "SCRIPT_ALREADY_PARSED"


# ── runner: 저장이 실패해도 던지지 않는다 ─────────────────────────────


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _broken_session() -> Session:
    raise RuntimeError("DB 연결 실패")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "result",
    [
        pytest.param(TWO_SLIDES, id="result-save-fails"),
        pytest.param(
            ScriptParseError(ScriptParseErrorCode.AI_TIMEOUT, "slow", retryable=True),
            id="failure-save-fails",
        ),
    ],
)
async def test_runner_swallows_db_failures_and_logs(
    caplog: pytest.LogCaptureFixture, result: ParsedScript | Exception
) -> None:
    """응답은 이미 나갔으니 던질 곳이 없다. 로그만 남긴다 (행은 PENDING 으로 남아 만료를 기다린다).

    DB 가 없는 테스트라 행 상태가 아니라 "던지지 않는다"와 로그만 본다.
    """
    runner = ScriptParseRunner(FakeParser(result), session_factory=_broken_session)
    ticket = ParseTicket(uuid.uuid7(), datetime.now(UTC), SCRIPT_TEXT)

    with caplog.at_level(logging.ERROR):
        await runner.run(ticket)  # 예외가 나오면 테스트 실패

    assert any("저장 실패" in record.message for record in caplog.records)


def test_exactly_max_chars_is_accepted(
    client: TestClient,
    parser: FakeParser,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    script = "가" * service.MAX_SCRIPT_CHARS

    response = _create(client, pitch_id, auth_headers, content=script)

    assert response.status_code == 202
    assert parser.calls == [script]


def test_save_failure_ends_as_failed_not_pending(
    client: TestClient,
    parser: FakeParser,
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    """결과 저장이 값 문제로 실패하면 FAILED(INTERNAL_ERROR). PENDING 으로 두면 FE 가 90초를
    기다린 뒤 엉뚱한 PARSE_EXPIRED 를 받는다."""

    def broken_complete(*args: object) -> bool:
        raise ValueError("저장할 수 없는 값")

    monkeypatch.setattr(service, "complete_parse", broken_complete)
    script_id = _create_ok(client, pitch_id, auth_headers)

    body = _get(client, pitch_id, script_id, auth_headers).json()

    assert (body["parse_status"], body["error_code"]) == ("FAILED", "INTERNAL_ERROR")
    assert _reparse(client, pitch_id, script_id, auth_headers).status_code == 202


def test_unknown_error_code_in_db_does_not_break_polling(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    script_id = _create_ok(client, pitch_id, auth_headers)
    script = db_session.get(ScriptVersion, script_id)
    assert script is not None
    script.parse_status = ScriptParseStatus.FAILED
    script.parse_error = "SOMETHING_REMOVED"
    db_session.flush()

    response = _get(client, pitch_id, script_id, auth_headers)

    assert response.status_code == 200
    assert response.json()["error_code"] == "INTERNAL_ERROR"


def test_concurrent_script_creates_on_one_pitch_get_distinct_versions(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """같은 pitch 에 대본 둘이 동시에 와도 둘 다 성공하고 버전이 1, 2 로 갈린다.

    잠금이 없으면 둘 다 max(version)+1 = 1 을 받아 두 번째가 unique 위반(500)이 된다.
    트랜잭션 두 개가 실제로 겹쳐야 해서 테스트 세션(savepoint) 대신 진짜 세션 둘을 쓰고,
    만든 데이터는 끝에 지운다.
    """
    from pitch_coach_backend.module.user.entity import User

    first_request_numbering = threading.Event()
    real_next_version = PitchRepository.next_script_version

    def slow_next_version(self: PitchRepository, pitch_id: uuid.UUID) -> int:
        # 첫 요청이 번호를 매기는 중(잠금을 쥔 채)에 두 번째 요청이 들어오게 한다
        version = real_next_version(self, pitch_id)
        if not first_request_numbering.is_set():
            first_request_numbering.set()
            time.sleep(0.3)
        return version

    monkeypatch.setattr(PitchRepository, "next_script_version", slow_next_version)

    with Session(engine) as setup:
        user = User(email=f"{uuid.uuid4()}@example.com", name="동시 업로드")
        setup.add(user)
        setup.flush()
        pitch = Pitch(user_id=user.id, title="동시", time_limit_sec=60)
        setup.add(pitch)
        setup.commit()
        user_id, target = user.id, pitch.id

    results: dict[str, Any] = {}

    def create(name: str) -> None:
        with Session(engine, expire_on_commit=False) as db:
            try:
                created, _ = service.create_script_service(db, target, f"대본 {name}")
                results[name] = created.version
            except Exception as exc:  # 잠금이 없으면 여기로 온다
                results[name] = exc

    try:
        first = threading.Thread(target=create, args=("a",))
        first.start()
        assert first_request_numbering.wait(5)
        second = threading.Thread(target=create, args=("b",))
        second.start()
        first.join(10)
        second.join(10)

        # 잠금이 없으면 한쪽이 IntegrityError(unique 위반) 다
        assert all(isinstance(v, int) for v in results.values()), results
        assert sorted(results.values()) == [1, 2]
    finally:
        with Session(engine) as cleanup:
            cleanup.delete(cleanup.get(User, user_id))  # pitch·버전은 CASCADE
            cleanup.commit()
