"""대본 파싱 재시도 · 만료 · 늦게 끝난 옛 작업."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch import service
from pitch_coach_backend.module.pitch.dto import ParseSaved, ParseTicket, ScriptParseErrorCode
from pitch_coach_backend.module.pitch.entity import Pitch, ScriptParseStatus, ScriptVersion
from pitch_coach_backend.module.pitch.script_parser import ScriptParseError
from tests.script_parse.conftest import (
    SCRIPT_TEXT,
    TARGET_MS,
    TWO_SLIDES,
    FakeParser,
    _age,
    _create_ok,
    _get,
    _reparse,
)

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


def test_retry_sends_the_time_limit_at_retry_time(
    client: TestClient,
    db_session: Session,
    parser: FakeParser,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    parser.result = ScriptParseError(ScriptParseErrorCode.AI_ERROR, "503", retryable=True)
    script_id = _create_ok(client, pitch_id, auth_headers)
    pitch = db_session.get(Pitch, pitch_id)
    assert pitch is not None
    pitch.time_limit_sec = 300
    db_session.commit()

    parser.result = TWO_SLIDES
    assert _reparse(client, pitch_id, script_id, auth_headers).status_code == 202

    assert parser.targets == [TARGET_MS, 300_000]
    script = db_session.get(ScriptVersion, script_id)
    assert script is not None
    assert script.parse_meta is not None
    assert script.parse_meta["target_ms"] == 300_000


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
    return ParseTicket(script_id, script.parse_requested_at, SCRIPT_TEXT, TARGET_MS)


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
    assert service.complete_parse(db_session, old, TWO_SLIDES) is ParseSaved.STALE
    assert service.fail_parse(db_session, old, ScriptParseErrorCode.AI_ERROR) is False
    assert _get(client, pitch_id, script_id, auth_headers).json()["parse_status"] == "PENDING"

    # 새 작업은 반영된다. 끝난 뒤에는 같은 작업이 다시 와도 무시된다 (중복 실행 방지)
    assert service.complete_parse(db_session, new, TWO_SLIDES) is ParseSaved.APPLIED
    assert service.fail_parse(db_session, new, ScriptParseErrorCode.AI_ERROR) is False
    body = _get(client, pitch_id, script_id, auth_headers).json()
    assert body["parse_status"] == "DONE"
    assert len(body["slides"]) == 2


# ── 만료 (BE 재시작으로 작업이 사라진 경우) ──────────────────────────────


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

    assert service.complete_parse(db_session, late, TWO_SLIDES) is ParseSaved.STALE
    assert service.fail_parse(db_session, late, ScriptParseErrorCode.AI_ERROR) is False

    body = _get(client, pitch_id, script_id, auth_headers).json()
    assert (body["parse_status"], body["error_code"]) == ("FAILED", "PARSE_EXPIRED")
    assert _reparse(client, pitch_id, script_id, auth_headers).status_code == 202
