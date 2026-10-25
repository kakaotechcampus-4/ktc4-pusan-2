"""WebSocket 엔드포인트. 프로토콜은 dto.py, 흐름은 service.py.

경로는 /api/ws/takes/{take_id} 가 된다 — Caddy 가 /api/* 만 백엔드로 넘기므로
FE 문서의 /ws/takes/{id} 그대로는 안 된다. /api 는 main.py 가 붙인다.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket
from sqlalchemy.orm import Session

from pitch_coach_backend.coach.dependencies import CoachClientDep, CoachStoreDep
from pitch_coach_backend.core.database import get_db
from pitch_coach_backend.realtime.connection.session import RealtimeSession
from pitch_coach_backend.realtime.dependencies import get_stt_adapter, get_transcript_store
from pitch_coach_backend.realtime.storage.store import TranscriptStore
from pitch_coach_backend.realtime.stt.adapter import SttAdapter

router = APIRouter(tags=["realtime"])


@router.websocket("/ws/takes/{take_id}")
async def take_stream(
    websocket: WebSocket,
    take_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    stt_adapter: Annotated[SttAdapter, Depends(get_stt_adapter)],
    transcript_store: Annotated[TranscriptStore, Depends(get_transcript_store)],
    coach_client: CoachClientDep,
    coach_store: CoachStoreDep,
) -> None:
    await RealtimeSession(
        websocket,
        take_id=take_id,
        db=db,
        stt_adapter=stt_adapter,
        transcript_store=transcript_store,
        coach_client=coach_client,
        coach_store=coach_store,
    ).run()
