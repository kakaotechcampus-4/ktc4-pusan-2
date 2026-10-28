"""Deepgram 에 보내는 오디오 길이를 서버 시간으로 묶기 (`event_ingestion.AudioBudget`)."""

import asyncio
import time
import uuid

import pytest

from pitch_coach_backend.realtime.event_ingestion import AUDIO_BURST_MS, MAX_SILENCE_FILL_MS
from pitch_coach_backend.realtime.stream import registry
from pitch_coach_backend.realtime.stt.adapter import SttConfig
from tests.realtime.conftest import (
    FakeSttAdapter,
    FakeTranscriptStore,
    FakeWebSocket,
    _wait_until_ok,
    frame,
)


@pytest.mark.anyio
async def test_tiny_frames_with_long_gaps_cannot_send_hours_to_deepgram():
    """1ms 프레임 하나가 5초 무음이 된다. 연결마다 720개를 5초 간격으로 보내고 다시 붙기를 3번 하면
    서버 시간 1초 안에 Deepgram 에 3시간 분량이 갔다. 보낸 길이는 지난 서버 시간 + 모아 둘 수 있는
    길이를 넘지 않고, 버린 프레임 때문에 세션을 갈지도 않는다 (새로고침으로 가는 것만 남는다)."""
    adapter = FakeSttAdapter()
    stream = registry.create_stream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    started = time.monotonic()
    stream.start()
    await _wait_until_ok(stream)

    for _ in range(3):
        tab = FakeWebSocket()
        await stream.attach(tab)
        for i in range(720):
            stream.push(frame(i + 1, i * MAX_SILENCE_FILL_MS, duration_ms=1), source=tab)
            if i % 50 == 0:
                # 큐를 비울 틈을 준다. 넘쳐 버린 오디오는 다음 프레임 앞 무음이 된다
                await asyncio.sleep(0)
    stream.request_stop()
    assert await stream.wait_closed(timeout=5.0)

    elapsed_ms = (time.monotonic() - started) * 1000
    sent_ms = sum(session.audio_ms for session in adapter.sessions)
    assert sent_ms <= AUDIO_BURST_MS + elapsed_ms
    assert stream.sequencer.over_budget > 2_000
    # 첫 세션과 새로고침(다시 붙은 연결의 첫 프레임이 처음부터 셈) 두 번
    assert len(adapter.sessions) == 3
