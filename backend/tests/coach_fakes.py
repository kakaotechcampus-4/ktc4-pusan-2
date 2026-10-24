"""코치 실행부 회귀 테스트가 공유하는 AI·저장소 대역."""

import asyncio
import copy
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.exc import OperationalError

from pitch_coach_backend.coach.client import (
    EvaluateReply,
    FinalizeReply,
    PlanReply,
    ReplayRequired,
)
from pitch_coach_backend.coach.requests import (
    FinalizeSource,
    MetricSecond,
    SlideEvent,
    TakeConstants,
    TakeContext,
    TranscriptSegment,
    build_plan,
)
from pitch_coach_backend.coach.session import CoachSession, FinalizeSnapshot

EXAMPLES = Path(__file__).resolve().parents[2] / "ai/research/coach-agent/src/coach/examples"

META = {"schema_version": "1.0", "feature_version": "coach-1.2", "model": None}

TAKE_ID = uuid.uuid7()

PITCH_ID = uuid.uuid7()

def example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))

def constants(mode: str = "COACHING") -> TakeConstants:
    return TakeConstants(
        take_id=TAKE_ID,
        mode=mode,
        plan=build_plan(300, time_limit_sec=300, lower_deviation_sec=30, upper_deviation_sec=30),
        script_mode="HIGHLIGHT",
    )

def reply(
    *,
    t_ms: int,
    action: str = "WAIT",
    indicators: dict[str, str] | None = None,
    events: list[dict[str, Any]] | None = None,
) -> EvaluateReply:
    feedback = None
    if action == "INTERVENE":
        feedback = {
            "area": "GAZE",
            "instruction": "LOOK_AT_CAMERA",
            "message": "청중을 봐 주세요",
            "priority": 80,
            "confidence": 0.9,
            "evidence": {"start_ms": t_ms - 5000, "end_ms": t_ms},
        }
    return EvaluateReply.model_validate(
        {
            "action": action,
            "feedback": feedback,
            "indicators": indicators or {},
            "reason_codes": [],
            "events": events or [],
            "coach_state": {"v": 2, "last_t_ms": t_ms},
            "meta": META,
        }
    )

class FakeClient:
    def __init__(self) -> None:
        self.evaluated: list[dict[str, Any]] = []
        self.finalized: list[dict[str, Any]] = []
        self.planned: list[dict[str, Any]] = []
        # 받은 요청마다 돌려줄 것. 비면 WAIT
        self.replies: list[EvaluateReply | None] = []
        self.finalize_replies: list[FinalizeReply | ReplayRequired | None] = []
        self.plan_reply: PlanReply | None = None
        # 이게 set 될 때까지 evaluate 응답을 미룬다
        self.release = asyncio.Event()
        self.release.set()

    async def evaluate(self, request: dict[str, Any]) -> EvaluateReply | None:
        self.evaluated.append(copy.deepcopy(request))
        await self.release.wait()
        if self.replies:
            return self.replies.pop(0)
        return reply(t_ms=request["t_ms"])

    async def finalize(self, request: dict[str, Any]) -> FinalizeReply | ReplayRequired | None:
        self.finalized.append(copy.deepcopy(request))
        return self.finalize_replies.pop(0) if self.finalize_replies else None

    async def plan(self, request: dict[str, Any]) -> PlanReply | None:
        self.planned.append(request)
        return self.plan_reply

@dataclass
class FakeStore:
    events: list[tuple[dict[str, Any], dict[str, Any]]] = field(default_factory=list)
    errors: list[Exception] = field(default_factory=list)
    source: FinalizeSource | None = None
    saved: list[tuple[FinalizeReply, Sequence[Any]]] = field(default_factory=list)
    failed: list[uuid.UUID] = field(default_factory=list)
    save_result: bool = True
    plan_request: dict[str, Any] | None = None
    plans: list[tuple[uuid.UUID, dict[str, Any], PlanReply, datetime]] = field(default_factory=list)
    finalizing: list[uuid.UUID] = field(default_factory=list)
    # finalizing_takes 를 부를 때 받은 기준 시각
    stale_cutoffs: list[datetime | None] = field(default_factory=list)
    purged: list[datetime] = field(default_factory=list)

    async def append_events(self, take_id, events) -> None:
        await asyncio.sleep(0)
        if self.errors:
            raise self.errors.pop(0)
        self.events.extend((dict(e), dict(m)) for e, m in events)

    async def load_events(self, take_id) -> list[dict[str, Any]]:
        return [e for e, _ in self.events]

    async def load_finalize_source(self, take_id) -> FinalizeSource | None:
        return self.source

    async def save_take_result(self, take_id, reply, events) -> bool:
        self.saved.append((reply, list(events)))
        return self.save_result

    async def fail_take_result(self, take_id) -> bool:
        self.failed.append(take_id)
        return True

    async def finalizing_takes(self, stale_before: datetime | None = None) -> list[uuid.UUID]:
        self.stale_cutoffs.append(stale_before)
        return list(self.finalizing)

    async def build_plan_request(self, pitch_id) -> dict[str, Any] | None:
        return self.plan_request

    async def save_plan(self, pitch_id, request, reply, requested_at) -> bool:
        self.plans.append((pitch_id, request, reply, requested_at))
        return True

    async def purge_expired_raw(self, now) -> int:
        self.purged.append(now)
        return 3

def gaze(t_ms: int) -> dict[str, Any]:
    return {"t_ms": t_ms, "duration_ms": 1000, "state": "CAMERA", "confidence": 0.9}

def voice(t_ms: int) -> dict[str, Any]:
    return {
        "t_ms": t_ms,
        "duration_ms": 1000,
        "level_db": -30.0,
        "voiced_ms": 800,
        "silence_ms": 0,
        "audio_live": True,
    }

def session(
    client: FakeClient, store: FakeStore, mode: str = "COACHING", slides=()
) -> CoachSession:
    coach = CoachSession(TAKE_ID)
    coach.enable(TakeContext(constants(mode), slides=tuple(slides)), client=client, store=store)
    return coach

def tick(coach: CoachSession, t_ms: int, *, buffered: bool = False, stt_status: str = "ok") -> None:
    start = t_ms - 1000
    coach.on_metrics(
        t_ms, gaze=gaze(start), voice=voice(start), stt_status=stt_status, buffered=buffered
    )

async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)

def take_result(**overrides: Any) -> FinalizeReply:
    body = copy.deepcopy(example("finalize/response.json"))
    body["take_result"].update(overrides)
    return FinalizeReply.model_validate(body)

LIVE_STATE = {"v": 2}

ONE_SECOND = (MetricSecond(92_000, 1, None, None, "ok"),)

def snapshot(*, coach_state: dict[str, Any] | None = LIVE_STATE, events=()) -> FinalizeSnapshot:
    return FinalizeSnapshot(
        t_ms=92_000,
        inputs={"words": [], "stt_status": "ok"},
        constants=constants(),
        coach_state=coach_state,
        events=tuple(events),
    )

def source(*, seconds=ONE_SECOND, events=()) -> FinalizeSource:
    return FinalizeSource(
        context=TakeContext(constants(), slides=(SlideEvent(1, 0),)),
        seconds=tuple(seconds),
        segments=(
            TranscriptSegment(
                end_ms=500, words=[{"word": "가", "start_ms": 0, "end_ms": 500}], final_at_ms=0
            ),
        ),
        utterance_ends=(500,),
        events=tuple(events),
        raw_data_consent=False,
    )

EVENT = ({"kind": "INTERVENTION", "event_id": "iv-1", "t_ms": 1}, META)

def db_down() -> OperationalError:
    return OperationalError("SELECT 1", {}, ConnectionError("connection reset"))

PLAN_REQUEST = {
    "pitch_id": str(PITCH_ID),
    "source_take_id": None,
    "script_version_id": None,
    "plan": {"target_ms": 300_000, "min_ms": 270_000, "max_ms": 330_000, "slides": []},
}

def example_plan_reply() -> dict[str, Any]:
    return {
        "coaching_plan": {"focus": [], "relax": [], "max_interventions": None},
        "source_take_id": None,
        "script_version_id": None,
        "fallback_reason": None,
        "dropped": [],
        "planner_hash": "h",
        "meta": META,
    }
