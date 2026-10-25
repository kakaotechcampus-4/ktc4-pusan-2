"""코치 종료 응답을 요청과 대조하고 원자료를 보존한다."""

import asyncio
import copy
import uuid

import httpx
import pytest
from sqlalchemy import select

from pitch_coach_backend.coach import store as coach_store
from pitch_coach_backend.coach.client import MAX_T_MS, FinalizeReply, HttpCoachClient
from pitch_coach_backend.module.take.entity import TakeAIResults, TakeMetricSecond
from tests import test_coach_finalize_storage as storage

user = storage.user
pitch = storage.pitch
presentation = storage.presentation
script = storage.script
take_parts = storage.take_parts


def call(result, request, *, replay=False):
    calls = []

    def respond(incoming):
        calls.append(incoming)
        return httpx.Response(
            200,
            json={
                "take_result": result,
                "events": [],
                "meta": {"schema_version": "1.0", "feature_version": "coach-1.2"},
            },
        )

    client = HttpCoachClient(
        "http://ai.test", transport=httpx.MockTransport(respond), retry_delay_sec=0
    )
    reply = asyncio.run(client.finalize({**request, **({"replay": {}} if replay else {})}))
    return reply, calls


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize(
    "field,value",
    [
        ("take_id", "other-take"),
        ("duration_ms", 2000),
        ("duration_ms", -1),
        ("duration_ms", MAX_T_MS + 1),
        ("duration_ms", True),
        ("duration_ms", 1000.0),
        ("duration_ms", "1000"),
    ],
)
def test_wrong_finalize_reply_is_rejected_without_retry(field, value, replay):
    result = {"take_id": "take-1", "duration_ms": 1000, "areas": {}}
    result[field] = value
    reply, calls = call(result, {"take_id": "take-1", "t_ms": 1000}, replay=replay)
    assert reply is None
    assert len(calls) == 1


@pytest.mark.parametrize("duration", [0, 1000, MAX_T_MS])
@pytest.mark.parametrize("replay", [False, True])
def test_matching_reply_preserves_unknown_fields(duration, replay):
    result = {"take_id": "take-1", "duration_ms": duration, "areas": {}, "future": {"v": 1}}
    reply, calls = call(result, {"take_id": "take-1", "t_ms": duration}, replay=replay)
    assert isinstance(reply, FinalizeReply)
    assert reply.take_result == result
    assert len(calls) == 1


def test_save_rejects_another_take_without_changing_results_or_raw(db_session, take_parts):
    take = storage.finalizing(db_session, storage.make_take(db_session, *take_parts, 1))
    db_session.commit()
    reply = storage.finalize_reply(take)
    wrong = copy.deepcopy(reply.take_result)
    wrong["take_id"] = str(uuid.uuid4())
    reply = reply.model_copy(update={"take_result": wrong})
    with pytest.raises(ValueError, match="Take"):
        coach_store.save_take_result(db_session, take.id, reply, [])
    db_session.refresh(take)
    assert take.analysis_stage == "FINALIZING"
    assert db_session.scalar(select(TakeAIResults).where(TakeAIResults.take_id == take.id)) is None
    assert (
        db_session.scalar(select(TakeMetricSecond).where(TakeMetricSecond.take_id == take.id))
        is not None
    )
