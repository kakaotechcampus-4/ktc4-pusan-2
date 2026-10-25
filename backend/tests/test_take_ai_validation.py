"""Take 채점 응답의 HTTP 경계와 저장 경계를 함께 검사한다."""

import asyncio
import copy
import json
import uuid
from functools import partial

import httpx
import pytest
from sqlalchemy.orm import Session

from pitch_coach_backend.module.take.entity import TakeAIResults, TakeSummary
from pitch_coach_backend.module.take.repository import TakeRepository
from pitch_coach_backend.module.take.service import coverage as coverage_service
from tests.test_take_history import _pitch, _take


def coverage(take_id: uuid.UUID) -> dict:
    scores = {
        "content_coverage": 0.81,
        "critical_fact_accuracy": None,
        "script_fidelity": 0.74,
        "similar_words": 1,
        "similar_numbers": 0,
    }
    return {
        "take_id": str(take_id),
        "evaluations": [
            {
                "slide_number": 1,
                "rubric_id": "rubric-1",
                "scores": {
                    **scores,
                    "_weights": {"key_points": 1, "facts": 0, "script_tokens": 10},
                },
                "sentences": [],
                "key_points": [],
                "critical_facts": [],
                "similar_items": [],
                "verification": {},
                "stt_sentences": [],
                "stt_text": "발표",
                "applied_answers": {},
            }
        ],
        "take_scores": {**scores, "scored_ratio": 1.0},
        "confirmations": [],
        "failed": [],
        "meta": {
            "schema_version": "1.0",
            "feature_version": "script_coverage-1.0",
            "criteria_versions": {"script_coverage": "coverage-1"},
            "model": None,
        },
        "future_field": {"preserved": True},
    }


def broken(reply: dict, case: str) -> dict:
    reply = copy.deepcopy(reply)
    if case == "other-take":
        reply["take_id"] = str(uuid.uuid4())
    elif case == "major":
        reply["meta"]["schema_version"] = "99.0"
    elif case == "missing-meta":
        del reply["meta"]
    elif case == "missing-failed":
        del reply["failed"]
    elif case == "scores-array":
        reply["take_scores"] = [81, 74]
    elif case == "missing-score":
        del reply["take_scores"]["scored_ratio"]
    elif case == "negative-count":
        reply["take_scores"]["similar_words"] = -1
    elif case == "bad-evaluation":
        reply["evaluations"][0]["slide_number"] = "one"
    elif case == "bad-confirmation":
        reply["confirmations"] = [{"slide_number": 1}]
    elif case == "bad-failed":
        reply["failed"] = [{"slide_number": 2, "error": {}}]
    elif case == "nul":
        reply["future_field"] = {"text": "bad\x00text"}
    elif case == "nan":
        reply["take_scores"]["content_coverage"] = float("nan")
    elif case == "surrogate":
        reply["future_field"] = {"text": "\ud800"}
    else:
        raise AssertionError(case)
    return reply


INVALID = [
    "other-take",
    "major",
    "missing-meta",
    "missing-failed",
    "scores-array",
    "missing-score",
    "negative-count",
    "bad-evaluation",
    "bad-confirmation",
    "bad-failed",
    "nul",
    "nan",
    "surrogate",
]


def ai(monkeypatch, handler) -> None:
    client = partial(httpx.AsyncClient, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(coverage_service.httpx, "AsyncClient", client)


@pytest.mark.parametrize("endpoint", ["analyze", "rescore"])
@pytest.mark.parametrize("case", INVALID + ["not-json", "array"])
def test_invalid_coverage_response_is_not_retryable(monkeypatch, endpoint, case) -> None:
    take_id = uuid.uuid4()
    if case == "not-json":
        content = b"not json"
    elif case == "array":
        content = b"[]"
    else:
        content = json.dumps(broken(coverage(take_id), case)).encode()
    ai(monkeypatch, lambda request: httpx.Response(200, content=content))

    with pytest.raises(coverage_service.CoverageCallError) as error:
        request = getattr(coverage_service, "request_" + endpoint)
        asyncio.run(request({"take_id": str(take_id)}, "id"))

    assert error.value.retryable is False


@pytest.mark.parametrize("unmeasurable", [False, True])
def test_valid_coverage_preserves_unknown_fields_and_request_id(monkeypatch, unmeasurable) -> None:
    take_id = uuid.uuid4()
    reply = coverage(take_id)
    reply["meta"]["schema_version"] = "1.9"
    if unmeasurable:
        for key in ("content_coverage", "critical_fact_accuracy", "script_fidelity"):
            reply["take_scores"][key] = None
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=reply)

    ai(monkeypatch, handler)
    actual = asyncio.run(coverage_service.request_analyze({"take_id": str(take_id)}, "same-id"))

    assert actual == reply
    assert requests[0].headers["X-Request-ID"] == "same-id"


@pytest.mark.parametrize(
    "status,retryable",
    [(408, True), (429, True), (503, True), (422, False), (404, False), (302, False)],
)
def test_coverage_http_failure_classification(monkeypatch, status, retryable) -> None:
    ai(monkeypatch, lambda request: httpx.Response(status))
    with pytest.raises(coverage_service.CoverageCallError) as error:
        asyncio.run(coverage_service.request_analyze({"take_id": str(uuid.uuid4())}, "id"))
    assert error.value.retryable is retryable


@pytest.mark.parametrize("case", INVALID)
def test_storage_rejects_invalid_coverage_before_any_write(db_session, user_id, case) -> None:
    take = _take(
        db_session, _pitch(db_session, user_id), 1, status="ANALYZING", analysis_stage="SCORING"
    )
    repo = TakeRepository(db_session)
    invalid = broken(coverage(take.id), case)

    with pytest.raises(coverage_service.CoverageCallError):
        coverage_service._save_confirmed(db_session, take.id, invalid)

    assert repo.get_ai_result(take.id, "SCRIPT_COVERAGE") is None
    assert db_session.query(TakeSummary).filter_by(take_id=take.id).first() is None


def test_bad_rescore_preserves_confirmed_candidate_and_summary(db_session, user_id) -> None:
    take = _take(
        db_session, _pitch(db_session, user_id), 1, status="ANALYZING", analysis_stage="SCORING"
    )
    reply = coverage(take.id)
    coverage_service._save_confirmed(db_session, take.id, reply)
    db_session.add(
        TakeAIResults(
            take_id=take.id, kind="SCRIPT_COVERAGE_PENDING", payload={"base_revision": 0}, meta={}
        )
    )
    db_session.flush()
    repo = TakeRepository(db_session)

    with pytest.raises(coverage_service.CoverageCallError):
        coverage_service._apply_merged(db_session, take.id, 0, broken(reply, "other-take"))

    assert repo.get_ai_result(take.id, "SCRIPT_COVERAGE").revision == 0
    assert repo.get_ai_result(take.id, "SCRIPT_COVERAGE_PENDING") is not None
    assert db_session.query(TakeSummary).filter_by(take_id=take.id).one().speak_accuracy == 81


@pytest.mark.parametrize("revision,applied", [(0, True), (1, False)])
def test_valid_rescore_replaces_only_the_expected_revision(db_session, user_id, revision, applied):
    take = _take(
        db_session,
        _pitch(db_session, user_id),
        1,
        status="ANALYZING",
        analysis_stage="SCORING",
    )
    original = coverage(take.id)
    coverage_service._save_confirmed(db_session, take.id, original)
    db_session.add(
        TakeAIResults(
            take_id=take.id,
            kind="SCRIPT_COVERAGE_PENDING",
            payload={"base_revision": 0},
            meta={},
        )
    )
    # 실제 후보는 rescore 호출 전에 커밋되어 있다. stale 결과의 rollback과 분리한다.
    db_session.commit()
    replacement = coverage(take.id)
    replacement["take_scores"]["content_coverage"] = 0.9

    assert coverage_service._apply_merged(db_session, take.id, revision, replacement) is applied

    repo = TakeRepository(db_session)
    saved = repo.get_ai_result(take.id, "SCRIPT_COVERAGE")
    assert saved.revision == (1 if applied else 0)
    assert saved.payload["take_scores"]["content_coverage"] == (0.9 if applied else 0.81)
    assert (repo.get_ai_result(take.id, "SCRIPT_COVERAGE_PENDING") is None) is applied
    assert db_session.query(TakeSummary).filter_by(take_id=take.id).one().speak_accuracy == (
        90 if applied else 81
    )


def test_malformed_analyze_advances_to_no_coverage_without_a_partial_save(
    db_session: Session,
    user_id,
    monkeypatch,
) -> None:
    take = _take(
        db_session, _pitch(db_session, user_id), 1, status="ANALYZING", analysis_stage="SCORING"
    )
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text="not json")

    ai(monkeypatch, handler)

    async def in_session(factory, fn, *args):
        return fn(db_session, *args)

    monkeypatch.setattr(coverage_service, "_in_session", in_session)
    assert asyncio.run(coverage_service.analyze_script_coverage(take.id)) == "NO_COVERAGE"
    assert len(calls) == 1
    db_session.refresh(take)
    assert take.analysis_stage == "REVIEWING"
    assert TakeRepository(db_session).get_ai_result(take.id, "SCRIPT_COVERAGE") is None


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout])
def test_coverage_network_failure_is_retryable(monkeypatch, error_type) -> None:
    def handler(request):
        raise error_type("fake network failure", request=request)

    ai(monkeypatch, handler)
    with pytest.raises(coverage_service.CoverageCallError) as error:
        asyncio.run(coverage_service.request_analyze({"take_id": str(uuid.uuid4())}, "id"))
    assert error.value.retryable is True


def test_coverage_attempt_has_an_overall_deadline(monkeypatch) -> None:
    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200)

    ai(monkeypatch, handler)
    with pytest.raises(coverage_service.CoverageCallError) as error:
        asyncio.run(coverage_service._post_ai("/takes/analyze", {}, "id", 0.01))
    assert error.value.retryable is True


@pytest.mark.parametrize("merge", [False, True], ids=["whole-analyze", "candidate-rescore"])
def test_coverage_failure_preserves_results_and_finishes_its_failure_path(
    db_session: Session,
    user_id,
    monkeypatch,
    merge,
) -> None:
    take = _take(
        db_session, _pitch(db_session, user_id), 1, status="ANALYZING", analysis_stage="SCORING"
    )
    reply = coverage(take.id)
    if merge:
        coverage_service._save_confirmed(db_session, take.id, reply)
        assert coverage_service._save_candidate(
            db_session,
            take.id,
            {
                "script_version_id": str(take.script_version_id),
                "base_revision": 0,
                "evaluations": reply["evaluations"],
                "failed": [],
                "retry_meta": reply["meta"],
            },
        )
    calls = []

    def handler(request):
        calls.append(request)
        if merge:
            return httpx.Response(200, json=broken(reply, "other-take"))
        return httpx.Response(503)

    ai(monkeypatch, handler)

    async def in_session(factory, fn, *args):
        return fn(db_session, *args)

    monkeypatch.setattr(coverage_service, "_in_session", in_session)
    result = asyncio.run(coverage_service.analyze_script_coverage(take.id))

    db_session.refresh(take)
    repo = TakeRepository(db_session)
    if merge:
        assert result == "HELD"
        assert take.analysis_hold_reason == "COVERAGE_RESCORE_FAILED"
        assert repo.get_ai_result(take.id, "SCRIPT_COVERAGE").payload == {
            key: value for key, value in reply.items() if key != "meta"
        }
        assert repo.get_ai_result(take.id, "SCRIPT_COVERAGE_PENDING") is not None
        assert db_session.query(TakeSummary).filter_by(take_id=take.id).one().speak_accuracy == 81
        assert len(calls) == 1
    else:
        assert result == "NO_COVERAGE"
        assert take.analysis_stage == "REVIEWING"
        assert take.coverage_retry_used is True
        assert repo.get_ai_result(take.id, "SCRIPT_COVERAGE") is None
        assert len(calls) == 2
        assert calls[0].headers["X-Request-ID"] == calls[1].headers["X-Request-ID"]


def test_invalid_partial_retry_keeps_the_initial_success(db_session, user_id, monkeypatch) -> None:
    take = _take(
        db_session, _pitch(db_session, user_id), 1, status="ANALYZING", analysis_stage="SCORING"
    )
    reply = coverage(take.id)
    reply["failed"] = [{"slide_number": 2, "error": {"code": "LLM_TIMEOUT", "message": "fake"}}]
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=reply if len(calls) == 1 else broken(reply, "major"))

    ai(monkeypatch, handler)

    async def in_session(factory, fn, *args):
        return fn(db_session, *args)

    monkeypatch.setattr(coverage_service, "_in_session", in_session)
    assert asyncio.run(coverage_service.analyze_script_coverage(take.id)) == "READY_FOR_REVIEW"

    repo = TakeRepository(db_session)
    assert len(calls) == 2
    assert repo.get_ai_result(take.id, "SCRIPT_COVERAGE").revision == 0
    assert repo.get_ai_result(take.id, "SCRIPT_COVERAGE").payload["failed"] == reply["failed"]
    assert repo.get_ai_result(take.id, "SCRIPT_COVERAGE_PENDING") is None


@pytest.mark.parametrize("storage_path", ["confirmed", "merged"])
@pytest.mark.parametrize(
    ("ratio", "expected"),
    [(0.621, 62), (0.624, 62), (0.625, 63), (0.285, 29), (0, 0), (1, 100), (None, None)],
)
def test_coverage_storage_rounds_summary_and_preserves_raw_scores(
    db_session: Session,
    user_id: uuid.UUID,
    storage_path: str,
    ratio: float | None,
    expected: int | None,
) -> None:
    take = _take(
        db_session, _pitch(db_session, user_id), 1, status="ANALYZING", analysis_stage="SCORING"
    )
    # 측정·리뷰 칸은 대본 전달도 요약 갱신으로 바뀌지 않아야 한다.
    db_session.add(TakeSummary(take_id=take.id, audience_gaze=42, score=77, speak_accuracy=81))
    db_session.flush()
    repo = TakeRepository(db_session)
    reply = coverage(take.id)
    reply["take_scores"]["content_coverage"] = ratio

    if storage_path == "merged":
        assert coverage_service._save_confirmed(db_session, take.id, coverage(take.id)) is not None
        db_session.add(
            TakeAIResults(
                take_id=take.id,
                kind="SCRIPT_COVERAGE_PENDING",
                payload={"base_revision": 0},
                meta={},
            )
        )
        db_session.commit()
        assert coverage_service._apply_merged(db_session, take.id, 0, reply)
        assert repo.get_ai_result(take.id, "SCRIPT_COVERAGE_PENDING") is None
    else:
        assert coverage_service._save_confirmed(db_session, take.id, reply) is not None

    db_session.expire_all()
    summary = db_session.query(TakeSummary).filter_by(take_id=take.id).one()
    assert summary.speak_accuracy == expected
    assert (summary.audience_gaze, summary.score) == (42, 77)
    confirmed = repo.get_ai_result(take.id, "SCRIPT_COVERAGE")
    assert confirmed.payload["take_scores"] == reply["take_scores"]
    assert confirmed.meta == reply["meta"]
    assert confirmed.revision == (1 if storage_path == "merged" else 0)
