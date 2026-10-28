"""장별 평가 기준 만들기 (/scripts/rubric). docs/ai/architecture.md 1-7 · 3절.

어댑터는 실제 httpx 요청을 만들고 MockTransport 로 AI 응답만 바꿔 끼운다.
작업(runner)은 AI 만 가짜로 바꾸고 service · repository · DB 는 실제로 돈다.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.main import app
from pitch_coach_backend.module.pitch.adapter.script_parser import ParsedScript, ParsedSlide
from pitch_coach_backend.module.pitch.adapter.script_rubric import (
    RUBRIC_PATH,
    HttpRubricClient,
    RubricCallError,
    RubricReply,
)
from pitch_coach_backend.module.pitch.dependencies import get_script_parse_runner
from pitch_coach_backend.module.pitch.dto import ParseSaved, ParseTicket
from pitch_coach_backend.module.pitch.entity import (
    Pitch,
    ScriptParseStatus,
    ScriptRubricStatus,
    ScriptSlide,
    ScriptVersion,
)
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.pitch.service import script as script_service
from pitch_coach_backend.module.pitch.service.script_parse_runner import ScriptParseRunner
from pitch_coach_backend.module.pitch.service.script_rubric_runner import ScriptRubricRunner

pytestmark = pytest.mark.anyio

BASE_URL = "http://ai:8000"
META = {"schema_version": "1.0", "feature_version": "script_coverage-1.0", "model": "test-model"}
SLIDES = ((1, "안녕하세요."), (2, "매출은 37% 늘었습니다."), (3, "감사합니다."))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _rubric(slide_number: int) -> dict[str, Any]:
    return {
        "meta": {"rubric_id": f"r-{slide_number}", "slide_number": slide_number},
        "core_claim": f"{slide_number}번 장의 주장",
        "key_points": [],
    }


def _failed(slide_number: int, code: str) -> dict[str, Any]:
    return {"slide_number": slide_number, "error": {"code": code, "message": "실패"}}


def _reply(ok: list[int], failed: dict[int, str] | None = None) -> RubricReply:
    return RubricReply(
        rubrics={number: _rubric(number) for number in ok},
        failed=dict(failed or {}),
        meta=dict(META),
    )


# ── 어댑터 ─────────────────────────────────────────────────────────────


class Recorder:
    def __init__(self, response: httpx.Response | Exception) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _client(recorder: Recorder) -> HttpRubricClient:
    return HttpRubricClient(BASE_URL, transport=httpx.MockTransport(recorder))


async def _generate(
    client: HttpRubricClient, slide_numbers: list[int] | None = None
) -> RubricReply:
    return await client.generate(
        pitch_id="p-1",
        script_version_id="sv-1",
        slides=SLIDES,
        slide_numbers=slide_numbers,
        request_id="req-1",
    )


async def test_sends_all_slides_and_request_id() -> None:
    body = {"rubrics": [_rubric(2)], "failed": [], "meta": META}
    recorder = Recorder(httpx.Response(200, json=body))

    await _generate(_client(recorder), [2])

    request = recorder.requests[0]
    assert str(request.url) == f"{BASE_URL}{RUBRIC_PATH}"
    assert request.headers["X-Request-ID"] == "req-1"
    # 일부 장만 만들 때도 모든 장을 싣는다 (발표 전체 맥락)
    assert json.loads(request.content) == {
        "pitch_id": "p-1",
        "script_version_id": "sv-1",
        "slides": [{"slide_number": n, "script": s} for n, s in SLIDES],
        "slide_numbers": [2],
    }


async def test_reply_is_split_by_slide_and_drops_what_was_not_asked() -> None:
    nameless = {"meta": {}, "core_claim": "번호 없음"}
    body = {
        "rubrics": [_rubric(1), _rubric(1), _rubric(9), nameless, _rubric(2)],
        "failed": [_failed(3, "LLM_TIMEOUT")],
        "meta": {**META, "unknown": "무시"},
    }
    reply = await _generate(_client(Recorder(httpx.Response(200, json=body))))

    assert reply.rubrics == {1: _rubric(1), 2: _rubric(2)}
    assert reply.failed == {3: "LLM_TIMEOUT"}
    assert reply.meta == META


async def test_rubric_with_nul_is_dropped() -> None:
    broken = _rubric(1) | {"key_points": [{"content": "a\x00b"}]}
    # 대본에 글자 그대로 적힌 역슬래시 u0000 은 NUL 이 아니다
    literal = _rubric(2) | {"core_claim": "문자열 \\u0000 을 보세요"}
    body = {"rubrics": [broken, literal], "failed": [], "meta": META}

    reply = await _generate(_client(Recorder(httpx.Response(200, json=body))))

    assert reply.rubrics == {2: literal}


async def test_rubric_with_nan_or_infinity_is_dropped() -> None:
    """JSONB 는 NaN · Infinity 를 넣지 못한다. 그 장만 버리고 나머지 장은 남긴다."""
    nan = json.dumps(_rubric(1) | {"weight": None}).replace("null", "NaN")
    # 1e400 은 float 범위를 넘어 Infinity 로 읽힌다
    infinite = json.dumps(_rubric(2) | {"weight": None}).replace("null", "1e400")
    meta = json.dumps(META)
    body = f'{{"rubrics": [{nan}, {infinite}, {json.dumps(_rubric(3))}], "meta": {meta}}}'
    response = httpx.Response(200, content=body, headers={"Content-Type": "application/json"})

    reply = await _generate(_client(Recorder(response)))

    assert reply.rubrics == {3: _rubric(3)}


@pytest.mark.parametrize(
    ("response", "retryable"),
    [
        (httpx.Response(503, json={"error": {"code": "LLM_UNAVAILABLE"}}), True),
        (httpx.Response(504, json={"error": {"code": "LLM_TIMEOUT"}}), True),
        (httpx.Response(429), True),
        (httpx.Response(408), True),
        (httpx.Response(422, json={"error": {"code": "VALIDATION_ERROR"}}), False),
        (httpx.ConnectError("refused"), True),
        (httpx.ReadTimeout("slow"), True),
        (httpx.DecodingError("broken gzip"), True),
        # 계약이 다시 부르라고 한 경우가 아니다
        (httpx.Response(200, json={"rubrics": "x"}), False),
        (
            httpx.Response(200, json={"rubrics": [], "meta": {**META, "schema_version": "2.0"}}),
            False,
        ),
    ],
)
async def test_errors_tell_whether_to_retry(
    response: httpx.Response | Exception, retryable: bool
) -> None:
    with pytest.raises(RubricCallError) as caught:
        await _generate(_client(Recorder(response)))
    assert caught.value.retryable is retryable


# ── 작업 ───────────────────────────────────────────────────────────────


class FakeClient:
    """AI 역할. 정해 둔 응답을 차례로 돌려주고, 받은 장 번호 · 요청 ID 를 남긴다."""

    def __init__(self, *results: RubricReply | Exception) -> None:
        self.results = list(results)
        self.calls: list[tuple[list[int] | None, str]] = []
        # 응답을 돌려주기 직전에 부른다 (그 사이 다른 작업이 끼어든 경우를 만든다)
        self.before_reply: Any = None

    async def generate(self, *, slide_numbers: Any, request_id: str, **kwargs: Any) -> RubricReply:
        assert kwargs["slides"] == SLIDES
        self.calls.append((None if slide_numbers is None else list(slide_numbers), request_id))
        if self.before_reply is not None:
            self.before_reply()
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _retryable() -> RubricCallError:
    return RubricCallError("503", retryable=True)


@pytest.fixture
def version_id(db_session: Session, user_id: uuid.UUID) -> uuid.UUID:
    """파싱이 끝난(DONE) 대본 버전 하나. 장은 SLIDES."""
    pitch = Pitch(user_id=user_id, title="평가 기준", time_limit_sec=600)
    db_session.add(pitch)
    db_session.flush()
    script = ScriptVersion(
        pitch_id=pitch.id,
        version=1,
        content="원문",
        parse_status=ScriptParseStatus.DONE,
        segmented=True,
    )
    db_session.add(script)
    db_session.flush()
    db_session.add_all(
        ScriptSlide(script_version_id=script.id, slide_number=n, full_content=s) for n, s in SLIDES
    )
    # 작업이 표식 조건에 져서 rollback 해도 준비한 행은 남게 커밋한다 (savepoint 만 닫힌다)
    db_session.commit()
    return script.id


def _queued(db_session: Session, version_id: uuid.UUID) -> datetime:
    """complete_parse 처럼 첫 작업을 PENDING 으로 둔다. 표식을 돌려준다."""
    marker = datetime.now(UTC)
    script = db_session.get(ScriptVersion, version_id)
    script.rubric_status = ScriptRubricStatus.PENDING
    script.rubric_requested_at = marker
    db_session.commit()
    return marker


def _runner(db_session: Session, fake: FakeClient) -> ScriptRubricRunner:
    return ScriptRubricRunner(fake, session_factory=lambda: db_session)


def _state(db_session: Session, version_id: uuid.UUID) -> tuple[str | None, bool, dict[int, Any]]:
    db_session.expire_all()
    script = db_session.get(ScriptVersion, version_id)
    assert script is not None
    rubrics = {s.slide_number: s.rubric for s in PitchRepository(db_session).get_slides(version_id)}
    return script.rubric_status, script.rubric_retry_used, rubrics


async def test_all_slides_made_at_once_is_done(db_session: Session, version_id: uuid.UUID) -> None:
    fake = FakeClient(_reply([1, 2, 3]))

    await _runner(db_session, fake).start(version_id, _queued(db_session, version_id))

    status, retry_used, rubrics = _state(db_session, version_id)
    assert status == ScriptRubricStatus.DONE
    assert retry_used is False
    assert rubrics == {n: _rubric(n) for n in (1, 2, 3)}
    assert [numbers for numbers, _ in fake.calls] == [None]
    slide = PitchRepository(db_session).get_slides(version_id)[0]
    assert slide.rubric_meta == META


async def test_only_llm_failed_slides_are_retried_once(
    db_session: Session, version_id: uuid.UUID
) -> None:
    fake = FakeClient(
        _reply([1], failed={2: "LLM_TIMEOUT", 3: "RUBRIC_MISSING"}),
        _reply([2]),
    )

    await _runner(db_session, fake).start(version_id, _queued(db_session, version_id))

    status, retry_used, rubrics = _state(db_session, version_id)
    # 입력 오류 장(3)은 다시 부르지 않아 실패로 남는다
    assert status == ScriptRubricStatus.PARTIAL
    assert retry_used is True
    assert rubrics == {1: _rubric(1), 2: _rubric(2), 3: None}
    assert [numbers for numbers, _ in fake.calls] == [None, [2]]
    # 본문이 다른 요청이라 요청 ID 도 새로 만든다
    assert fake.calls[0][1] != fake.calls[1][1]


async def test_failed_subset_retry_keeps_the_first_reply(
    db_session: Session, version_id: uuid.UUID
) -> None:
    fake = FakeClient(_reply([1, 3], failed={2: "LLM_ERROR"}), _retryable())

    await _runner(db_session, fake).start(version_id, _queued(db_session, version_id))

    status, retry_used, rubrics = _state(db_session, version_id)
    assert status == ScriptRubricStatus.PARTIAL
    assert retry_used is True
    assert rubrics == {1: _rubric(1), 2: None, 3: _rubric(3)}
    assert len(fake.calls) == 2


async def test_first_error_retries_everything_once_with_the_same_request_id(
    db_session: Session, version_id: uuid.UUID
) -> None:
    # 전체 재시도의 200 에 남은 실패는 더 부르지 않는다
    fake = FakeClient(_retryable(), _reply([1, 2], failed={3: "LLM_TIMEOUT"}))

    await _runner(db_session, fake).start(version_id, _queued(db_session, version_id))

    status, retry_used, _ = _state(db_session, version_id)
    assert status == ScriptRubricStatus.PARTIAL
    assert retry_used is True
    assert [numbers for numbers, _ in fake.calls] == [None, None]
    assert fake.calls[0][1] == fake.calls[1][1]


@pytest.mark.parametrize(
    ("results", "calls"),
    [
        ((_retryable(), _retryable()), 2),
        # 입력 오류는 다시 불러도 같다
        ((RubricCallError("422", retryable=False),), 1),
    ],
)
async def test_no_rubric_at_all_is_failed(
    db_session: Session,
    version_id: uuid.UUID,
    results: tuple[RubricCallError, ...],
    calls: int,
) -> None:
    fake = FakeClient(*results)

    await _runner(db_session, fake).start(version_id, _queued(db_session, version_id))

    status, _, rubrics = _state(db_session, version_id)
    assert status == ScriptRubricStatus.FAILED
    assert set(rubrics.values()) == {None}
    assert len(fake.calls) == calls


async def test_a_version_gets_only_one_job(db_session: Session, version_id: uuid.UUID) -> None:
    fake = FakeClient(_reply([1, 2, 3]))
    runner = _runner(db_session, fake)
    marker = _queued(db_session, version_id)

    await runner.start(version_id, marker)
    await runner.start(version_id, marker)

    assert len(fake.calls) == 1


async def test_start_without_a_queued_job_does_nothing(
    db_session: Session, version_id: uuid.UUID
) -> None:
    fake = FakeClient()

    await _runner(db_session, fake).start(version_id, datetime.now(UTC))

    assert fake.calls == []
    assert _state(db_session, version_id)[0] is None


async def test_late_reply_of_a_replaced_job_is_dropped(
    db_session: Session, version_id: uuid.UUID
) -> None:
    """작업이 AI 를 기다리는 동안 다른 작업이 이어받았다 (표식이 바뀜)."""
    fake = FakeClient(_reply([1, 2, 3]))

    def taken_over() -> None:
        script = db_session.get(ScriptVersion, version_id)
        script.rubric_requested_at = datetime.now(UTC) + timedelta(seconds=1)
        db_session.commit()

    fake.before_reply = taken_over

    await _runner(db_session, fake).start(version_id, _queued(db_session, version_id))

    status, _, rubrics = _state(db_session, version_id)
    assert status == ScriptRubricStatus.PENDING
    assert set(rubrics.values()) == {None}


def _leave_pending(
    db_session: Session, version_id: uuid.UUID, *, retry_used: bool, made: list[int]
) -> datetime:
    """앞 프로세스가 작업 도중 죽은 상태를 만든다."""
    started = datetime.now(UTC) - timedelta(minutes=5)
    script = db_session.get(ScriptVersion, version_id)
    script.rubric_status = ScriptRubricStatus.PENDING
    script.rubric_requested_at = started
    script.rubric_retry_used = retry_used
    for slide in PitchRepository(db_session).get_slides(version_id):
        if slide.slide_number in made:
            slide.rubric = _rubric(slide.slide_number)
            slide.rubric_meta = META
    db_session.commit()
    return started


async def test_resume_uses_the_remaining_retry_for_missing_slides(
    db_session: Session, version_id: uuid.UUID
) -> None:
    started = _leave_pending(db_session, version_id, retry_used=False, made=[1])
    fake = FakeClient(_reply([2, 3]))

    await _runner(db_session, fake).resume_all()

    status, retry_used, rubrics = _state(db_session, version_id)
    assert status == ScriptRubricStatus.DONE
    assert retry_used is True
    assert [numbers for numbers, _ in fake.calls] == [[2, 3]]
    assert all(rubric is not None for rubric in rubrics.values())
    # 이어받으며 표식을 바꿨다 — 앞 작업의 늦은 출력은 버려진다
    assert db_session.get(ScriptVersion, version_id).rubric_requested_at > started


async def test_resume_with_nothing_made_asks_for_all_slides(
    db_session: Session, version_id: uuid.UUID
) -> None:
    _leave_pending(db_session, version_id, retry_used=False, made=[])
    fake = FakeClient(_reply([1, 2, 3]))

    await _runner(db_session, fake).resume_all()

    assert [numbers for numbers, _ in fake.calls] == [None]
    assert _state(db_session, version_id)[0] == ScriptRubricStatus.DONE


async def test_resume_after_the_retry_was_used_only_settles_the_status(
    db_session: Session, version_id: uuid.UUID
) -> None:
    _leave_pending(db_session, version_id, retry_used=True, made=[1, 2])
    fake = FakeClient()

    await _runner(db_session, fake).resume_all()

    assert fake.calls == []
    assert _state(db_session, version_id)[0] == ScriptRubricStatus.PARTIAL


async def test_saved_rubric_is_not_overwritten(db_session: Session, version_id: uuid.UUID) -> None:
    _leave_pending(db_session, version_id, retry_used=False, made=[1])
    other = _rubric(1) | {"core_claim": "새 응답"}
    fake = FakeClient(RubricReply(rubrics={1: other, 2: _rubric(2)}, failed={}, meta=META))

    await _runner(db_session, fake).resume_all()

    rubrics = _state(db_session, version_id)[2]
    assert rubrics[1] == _rubric(1)
    assert rubrics[2] == _rubric(2)


# ── 파싱 뒤 이어서 · 재파싱 비교 ──────────────────────────────────────────


class FakeParser:
    def __init__(self, result: ParsedScript) -> None:
        self.result = result

    async def parse(self, script_text: str, target_ms: int | None) -> ParsedScript:
        return self.result


def _parsed(*slides: tuple[int, str]) -> ParsedScript:
    return ParsedScript(
        segmented=True,
        slides=tuple(ParsedSlide(n, s, (), ()) for n, s in slides),
        terms=(),
    )


def test_upload_parses_then_makes_rubrics(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    from pitch_coach_backend.core.security import create_access_token

    fake = FakeClient(_reply([1, 2, 3]))
    runner = ScriptParseRunner(
        FakeParser(_parsed(*SLIDES)),
        session_factory=lambda: db_session,
        rubric_runner=_runner(db_session, fake),
    )
    app.dependency_overrides[get_script_parse_runner] = lambda: runner
    headers = {"Authorization": f"Bearer {create_access_token(user_id)}"}
    pitch = client.post(
        "/api/pitches/add",
        json={"title": "흐름", "time_limit_sec": 600, "presentation_date": "2026-11-01"},
        headers=headers,
    ).json()["pitch_id"]

    created = client.post(
        f"/api/pitches/{pitch}/scripts", json={"content": "원문"}, headers=headers
    )

    script_id = uuid.UUID(created.json()["script_version_id"])
    assert _state(db_session, script_id)[0] == ScriptRubricStatus.DONE
    assert len(fake.calls) == 1


def _pending_parse(db_session: Session, version_id: uuid.UUID) -> ParseTicket:
    """저장본이 있는 버전을 다시 파싱 중인 상태로 만든다."""
    now = datetime.now(UTC)
    script = db_session.get(ScriptVersion, version_id)
    script.parse_status = ScriptParseStatus.PENDING
    script.parse_requested_at = now
    db_session.commit()
    return ParseTicket(
        script_version_id=version_id, requested_at=now, script_text="원문", target_ms=None
    )


def test_same_reparse_reuses_stored_slides(db_session: Session, version_id: uuid.UUID) -> None:
    ticket = _pending_parse(db_session, version_id)

    # 순서가 달라도 번호순으로 비교한다
    saved = script_service.complete_parse(db_session, ticket, _parsed(*reversed(SLIDES)))

    assert saved is ParseSaved.REUSED
    script = db_session.get(ScriptVersion, version_id)
    assert (script.parse_status, script.parse_error) == (ScriptParseStatus.DONE, None)
    assert len(PitchRepository(db_session).get_slides(version_id)) == 3
    # 기준을 만든 적 없는 버전이라 첫 작업을 둔다
    assert (script.rubric_status, script.rubric_requested_at) == (
        ScriptRubricStatus.PENDING,
        ticket.requested_at,
    )


def test_reused_reparse_keeps_the_finished_rubric_job(
    db_session: Session, version_id: uuid.UUID
) -> None:
    finished_at = datetime.now(UTC) - timedelta(days=1)
    script = db_session.get(ScriptVersion, version_id)
    script.rubric_status = ScriptRubricStatus.PARTIAL
    script.rubric_requested_at = finished_at
    script.rubric_retry_used = True
    ticket = _pending_parse(db_session, version_id)

    assert script_service.complete_parse(db_session, ticket, _parsed(*SLIDES)) is ParseSaved.REUSED

    script = db_session.get(ScriptVersion, version_id)
    assert (script.rubric_status, script.rubric_requested_at, script.rubric_retry_used) == (
        ScriptRubricStatus.PARTIAL,
        finished_at,
        True,
    )


def test_different_reparse_is_not_applied(db_session: Session, version_id: uuid.UUID) -> None:
    ticket = _pending_parse(db_session, version_id)

    saved = script_service.complete_parse(
        db_session, ticket, _parsed((1, "안녕하세요."), (2, "다른 말"))
    )

    assert saved is ParseSaved.MISMATCH
    script = db_session.get(ScriptVersion, version_id)
    assert script.rubric_status is None
    assert (script.parse_status, script.parse_error) == (ScriptParseStatus.DONE, "REPARSE_MISMATCH")
    stored = [
        (s.slide_number, s.full_content) for s in PitchRepository(db_session).get_slides(version_id)
    ]
    assert stored == list(SLIDES)


async def test_mismatched_reparse_does_not_make_rubrics(
    db_session: Session, version_id: uuid.UUID
) -> None:
    _pending_parse(db_session, version_id)
    script = db_session.get(ScriptVersion, version_id)
    fake = FakeClient()
    runner = ScriptParseRunner(
        FakeParser(_parsed((1, "다른 말"))),
        session_factory=lambda: db_session,
        rubric_runner=_runner(db_session, fake),
    )

    await runner.run(
        ParseTicket(
            script_version_id=version_id,
            requested_at=script.parse_requested_at,
            script_text="원문",
            target_ms=None,
        )
    )

    assert fake.calls == []
    assert _state(db_session, version_id)[0] is None
