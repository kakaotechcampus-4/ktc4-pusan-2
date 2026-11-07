import asyncio
import logging
import uuid
from functools import partial

import httpx
import pytest

from pitch_coach_backend.module.chat import ai_client
from pitch_coach_backend.module.chat.ai_client import ReviewTakeMismatch, request_review_once
from pitch_coach_backend.module.chat.service import reassess
from pitch_coach_backend.module.chat.service.reassess import PreparedReassess, call_reassess


def _prepared() -> PreparedReassess:
    return PreparedReassess(
        take_id=uuid.uuid4(),
        review_revision=1,
        official={},
        before={},
        after={},
        before_digest="d",
    )


def test_review_reply_for_another_take_is_a_mismatch(monkeypatch) -> None:
    reply = {"take_id": str(uuid.uuid4())}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=reply))
    monkeypatch.setattr(
        ai_client.httpx, "AsyncClient", partial(httpx.AsyncClient, transport=transport)
    )

    with pytest.raises(ReviewTakeMismatch):
        asyncio.run(request_review_once(uuid.uuid4(), {}))


@pytest.mark.parametrize(
    ("error", "code"),
    [(ReviewTakeMismatch(), "REVIEW_TAKE_MISMATCH"), (TimeoutError(), "REVIEW_FAILED")],
)
def test_reassess_failure_code(monkeypatch, error, code) -> None:
    async def fail(take_id, body):
        raise error

    monkeypatch.setattr(reassess, "request_review_once", fail)

    assert asyncio.run(call_reassess(_prepared())) == code


def test_reassess_failure_logs_only_the_kinds(monkeypatch, caplog) -> None:
    # 응답 원문이 실린 예외 문구는 남기지 않고 종류와 HTTP 상태만 남긴다
    async def fail(take_id, body):
        if body.get("side") == "after":
            raise ValueError("AI 응답 원문")
        request = httpx.Request("POST", "http://ai/takes/review")
        raise httpx.HTTPStatusError(
            "실패", request=request, response=httpx.Response(503, request=request)
        )

    monkeypatch.setattr(reassess, "request_review_once", fail)
    prepared = PreparedReassess(
        take_id=uuid.uuid4(),
        review_revision=1,
        official={},
        before={"side": "before"},
        after={"side": "after"},
        before_digest="d",
    )

    with caplog.at_level(logging.WARNING, logger=reassess.__name__):
        assert asyncio.run(call_reassess(prepared)) == "REVIEW_FAILED"

    assert "HTTPStatusError(503)" in caplog.text
    assert "ValueError" in caplog.text
    assert "AI 응답 원문" not in caplog.text
