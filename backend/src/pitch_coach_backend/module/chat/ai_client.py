import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from pitch_coach_backend.core.ai_response_limit import AI_RESPONSE_HOOKS
from pitch_coach_backend.core.config import settings
from pitch_coach_backend.module.chat.ai_response import (
    UPSTREAM_ERROR,
    FinalView,
    InvalidTurnReply,
    StreamLine,
    StreamReader,
    pre_stream_error_code,
    stream_error_code,
    valid_diagnostics,
    validate_final,
)
from pitch_coach_backend.module.chat.dto import AgentTrigger, AgentTurnRequestDTO

TURN_TIMEOUT_SEC = 50.0
BUSY_RETRY_AFTER_SEC = 5
NDJSON = "application/x-ndjson"

type OnProgress = Callable[[StreamLine], Awaitable[None]]


@dataclass(frozen=True)
class TurnFinal:
    view: FinalView
    data: dict[str, Any]
    diagnostics: dict[str, Any] | None


@dataclass(frozen=True)
class TurnFailure:
    code: str
    retry_after_s: int | None = None
    diagnostics: dict[str, Any] | None = None


async def request_turn(
    request: AgentTurnRequestDTO,
    *,
    request_id: str,
    turn_call: int,
    first_trigger: AgentTrigger,
    on_progress: OnProgress,
) -> TurnFinal | TurnFailure:
    body = request.model_dump(mode="json")
    try:
        async with (
            asyncio.timeout(TURN_TIMEOUT_SEC),
            httpx.AsyncClient(timeout=TURN_TIMEOUT_SEC, event_hooks=AI_RESPONSE_HOOKS) as client,
            client.stream(
                "POST",
                f"{settings.ai_base_url}/agent/turn",
                json=body,
                headers={"X-Request-ID": request_id},
            ) as response,
        ):
            if response.status_code != 200:
                return _pre_stream_failure(await response.aread())
            if not response.headers.get("content-type", "").startswith(NDJSON):
                return TurnFailure(UPSTREAM_ERROR)
            terminal = await _read(response, str(request.run.run_id), on_progress)
    except TimeoutError:
        return TurnFailure("AGENT_TIMEOUT")
    except httpx.HTTPError, InvalidTurnReply:
        return TurnFailure(UPSTREAM_ERROR)
    diagnostics = valid_diagnostics(
        terminal.data.get("diagnostics"),
        request_id=request_id,
        run_id=str(request.run.run_id),
        turn_call=turn_call,
    )
    if terminal.event == "error":
        return TurnFailure(stream_error_code(terminal.data), diagnostics=diagnostics)
    try:
        view = validate_final(terminal.data, first_trigger)
    except InvalidTurnReply:
        return TurnFailure(UPSTREAM_ERROR, diagnostics=diagnostics)
    data = {key: value for key, value in terminal.data.items() if key != "diagnostics"}
    return TurnFinal(view=view, data=data, diagnostics=diagnostics)


def _pre_stream_failure(body: bytes) -> TurnFailure:
    code = pre_stream_error_code(body)
    return TurnFailure(code, BUSY_RETRY_AFTER_SEC if code == "AGENT_BUSY" else None)


async def _read(response: httpx.Response, run_id: str, on_progress: OnProgress) -> StreamLine:
    reader = StreamReader(run_id)
    async for chunk in response.aiter_bytes():
        for line in reader.feed(chunk):
            if line.event in ("plan", "tool_done"):
                await on_progress(line)
    return reader.finish()
