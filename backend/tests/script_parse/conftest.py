"""대본 파싱 흐름: POST /scripts 202 → 백그라운드 파싱 → 폴링 → 재시도.

AI 만 가짜 파서로 바꾸고 나머지(엔드포인트·BackgroundTasks·runner·service·DB)는 실제로 돈다.
대본은 S3 를 거치지 않는다 (원문은 DB). TestClient 는 응답을 돌려준 뒤 백그라운드 작업까지 마치고
반환하므로, 올린 직후 GET 하면 파싱이 끝난 상태를 본다.
"아직 PENDING" 은 conftest 의 기본값(아무것도 안 하는 runner)으로 본다.
"""

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.main import app
from pitch_coach_backend.module.pitch.adapter.script_parser import (
    ParsedScript,
    ParsedSlide,
    ParseMeta,
)
from pitch_coach_backend.module.pitch.dependencies import get_script_parse_runner
from pitch_coach_backend.module.pitch.entity import ScriptVersion
from pitch_coach_backend.module.pitch.service.script_parse_runner import ScriptParseRunner

SCRIPT_TEXT = "슬라이드 1:\n안녕하세요. SeatFlow 입니다.\n\n슬라이드 2:\n감사합니다."
# _new_pitch 의 발표 제한 시간 600초
TARGET_MS = 600_000

META = ParseMeta(schema_version="1.0", feature_version="script_parser-1.0", model="test-model")

TWO_SLIDES = ParsedScript(
    segmented=True,
    slides=(
        ParsedSlide(
            slide_number=1,
            content="안녕하세요. SeatFlow 입니다.",
            keywords=("SeatFlow",),
            highlights=((7, 15),),
            script_chars=16,
            expected_ms=457_143,
        ),
        ParsedSlide(
            slide_number=2,
            content="감사합니다.",
            keywords=("감사",),
            highlights=(),
            script_chars=5,
            expected_ms=142_857,
        ),
    ),
    terms=("SeatFlow",),
    meta=META,
)


class FakeParser:
    """AI 역할. 정해 둔 결과를 돌려주거나 예외를 던지고, 받은 대본과 목표 시간을 남긴다."""

    def __init__(self, result: ParsedScript | Exception = TWO_SLIDES) -> None:
        self.result = result
        self.calls: list[str] = []
        self.targets: list[int | None] = []

    async def parse(self, script_text: str, target_ms: int | None) -> ParsedScript:
        self.calls.append(script_text)
        self.targets.append(target_ms)
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
