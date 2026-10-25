"""대본 업로드 → 백그라운드 파싱 → 조회 · 입력 검사. AI 파서만 가짜로 바꾼다."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch import service
from pitch_coach_backend.module.pitch.dto import ScriptParseErrorCode
from pitch_coach_backend.module.pitch.entity import (
    Pitch,
    ScriptParseStatus,
    ScriptSlide,
    ScriptVersion,
)
from pitch_coach_backend.module.pitch.script_parser import (
    ParsedScript,
    ParsedSlide,
    ScriptParseError,
)
from tests.script_parse.conftest import (
    SCRIPT_TEXT,
    TARGET_MS,
    FakeParser,
    _create,
    _create_ok,
    _get,
    _new_pitch,
)

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


def _slides(db: Session, script_id: uuid.UUID) -> list[ScriptSlide]:
    return list(
        db.scalars(
            select(ScriptSlide)
            .where(ScriptSlide.script_version_id == script_id)
            .order_by(ScriptSlide.slide_number)
        )
    )


def test_parse_sends_time_limit_and_saves_timing_and_meta(
    client: TestClient,
    db_session: Session,
    parser: FakeParser,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    script_id = _create_ok(client, pitch_id, auth_headers)

    # 발표 제한 시간(초)을 ms 로 바꿔 싣는다
    assert parser.targets == [TARGET_MS]
    script = db_session.get(ScriptVersion, script_id)
    assert script is not None
    # 응답의 meta 에 요청한 target_ms 를 더해 둔다
    assert script.parse_meta == {
        "schema_version": "1.0",
        "feature_version": "script_parser-1.0",
        "model": "test-model",
        "target_ms": TARGET_MS,
    }
    slides = _slides(db_session, script_id)
    assert [(slide.script_chars, slide.expected_ms) for slide in slides] == [
        (16, 457_143),
        (5, 142_857),
    ]


def test_result_without_new_fields_leaves_the_new_columns_empty(
    client: TestClient,
    db_session: Session,
    parser: FakeParser,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    """지금 AI 연구 코드의 응답처럼 script_chars · expected_ms · meta 가 없어도 DONE 이다."""
    parser.result = ParsedScript(
        segmented=True, slides=(ParsedSlide(1, "안녕하세요.", (), ()),), terms=()
    )
    script_id = _create_ok(client, pitch_id, auth_headers)

    assert _get(client, pitch_id, script_id, auth_headers).json()["parse_status"] == "DONE"
    script = db_session.get(ScriptVersion, script_id)
    assert script is not None
    assert script.parse_meta is None
    [slide] = _slides(db_session, script_id)
    assert (slide.script_chars, slide.expected_ms) == (None, None)


def test_pitch_without_a_valid_time_limit_is_parsed_without_target(
    client: TestClient,
    db_session: Session,
    parser: FakeParser,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    """입력 검증(#147) 전에 0 으로 저장된 발표. 계약은 1 이상이라 목표 없이(null) 부른다."""
    pitch = db_session.get(Pitch, pitch_id)
    assert pitch is not None
    pitch.time_limit_sec = 0
    db_session.commit()

    script_id = _create_ok(client, pitch_id, auth_headers)

    assert parser.targets == [None]
    script = db_session.get(ScriptVersion, script_id)
    assert script is not None
    assert script.parse_meta is not None
    assert script.parse_meta["target_ms"] is None


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


# ── 조회 ────────────────────────────────────────────────────────────────


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
