"""전사를 FE 화면으로 보낸다. 스트림(`runtime.TakeStream`)이 STT 결과를 받아 넘긴다.

interim 은 기다리지 않고 가장 새것만 보내고, final 은 못 보냈으면 모아 두었다가 다시 붙은 연결에
보낸다 (`TranscriptBuffer`). 저장은 화면과 따로 한다 (`FinalBuffer`). 스트림의 수명 · 정리는 모른다.
"""

import asyncio
import uuid

import anyio
from fastapi import WebSocket

from pitch_coach_backend.realtime.dto import TranscriptMessage
from pitch_coach_backend.realtime.storage.transcripts import TranscriptBuffer
from pitch_coach_backend.realtime.stream.client import ClientSlot
from pitch_coach_backend.realtime.stt import loop as stt_loop

# 중간 자막(interim) 하나를 보내는 상한. 받는 쪽이 멈춰 있어도 다음 것을 보낼 수 있게 한다 — 다음
# interim 이 덮으므로 못 보낸 것은 버린다
INTERIM_SEND_TIMEOUT_SEC = 1.0
# 끝난 Take 의 마지막 final 을 FE 로 보내는 상한
FINAL_SEND_TIMEOUT_SEC = 3.0


class TranscriptSender:
    def __init__(
        self, take_id: uuid.UUID, *, client: ClientSlot, transcripts: TranscriptBuffer
    ) -> None:
        self.take_id = take_id
        self._client = client
        self._transcripts = transcripts
        # 놓친 final 을 다시 보내기 시작한 연결 (`resend_missed`). ready 를 받은 연결이다
        self._caught_up: WebSocket | None = None
        # 보낼 가장 새 interim 과 보내는 태스크 (`show_interim`). STT 처리는 전송을 기다리지 않는다
        self._interim_next: TranscriptMessage | None = None
        self._interim_task: asyncio.Task[None] | None = None

    def forget_client(self) -> None:
        """연결을 뗐다. 다음에 붙는 연결은 ready 를 받은 뒤에 따라잡는다."""
        self._caught_up = None

    def stop_interims(self) -> None:
        """스트림이 닫혔다. 닫힌 스트림의 중간 자막은 보낼 곳이 없다."""
        self._interim_next = None
        if self._interim_task is not None:
            self._interim_task.cancel()

    async def resend_missed(self, source: WebSocket | None = None) -> None:
        """떨어져 있는 동안 온 final 을 순서대로 다시 보낸다 (`TranscriptBuffer.resend`).

        `ready` 뒤에 부른다. 실시간 화면(자막·2단 코치)이 끊긴 구간을 건너뛰지 않게 하는 용도다.
        """
        target = self._client.ws if source is None else source
        if target is None or not self._client.holds(target):
            return
        self._caught_up = target

        async def send(message: TranscriptMessage) -> bool:
            return await self._send_final_to(message, target)

        await self._transcripts.resend(send)

    def show_interim(self, message: TranscriptMessage) -> None:
        """interim 을 기다리지 않고 보낸다. 보내는 중이면 가장 새것만 남겼다가 이어 보낸다.

        STT 이벤트 펌프가 `TakeStream._on_transcript` 를 기다리므로 여기서 전송을 기다리면 받는 쪽이
        느린 FE 하나가 다음 STT 결과 · 문장 끝 신호를 늦춘다. 사이의 interim 은 다음 것이 덮으니
        버린다.
        """
        self._interim_next = message
        if self._interim_task is None or self._interim_task.done():
            self._interim_task = asyncio.create_task(
                self._send_interims(), name=f"take-stream-interim:{self.take_id}"
            )

    async def _send_interims(self) -> None:
        while self._interim_next is not None:
            message, self._interim_next = self._interim_next, None
            # uvicorn 은 쓸 수 있을 때까지 기다린 뒤 프레임을 한 번에 쓴다. 기다리다 끊으면 그
            # 프레임은 나가지 않을 뿐 깨지지 않는다
            with anyio.move_on_after(INTERIM_SEND_TIMEOUT_SEC):
                await self._client.send(message)

    async def _settle_interims(self) -> None:
        """final 을 보내기 전에 부른다. 남은 interim 을 버리고 보내는 중인 것이 끝나기를 기다린다.

        같은 구간의 옛 interim 이 final 뒤에 닿으면 FE 는 같은 `segment_id` 로 덮어 확정 자막이
        중간 자막으로 돌아간다. 보내는 중인 것은 상한 안에 끝나고, 그래도 남았으면 끊는다 — 쓰기
        전에 끊긴 프레임은 나가지 않는다.
        """
        self._interim_next = None
        task = self._interim_task
        if task is not None and not task.done():
            await asyncio.wait({task}, timeout=INTERIM_SEND_TIMEOUT_SEC)
            task.cancel()

    async def show_final(self, message: TranscriptMessage) -> None:
        """final 을 화면에 보낸다. 못 보냈으면 모아 두었다가 다시 붙은 연결에 보낸다.

        FE 가 떨어져 있을 때만이 아니라 보내다 실패하거나 상한에 걸렸을 때도 모은다 — 보낸 셈 치면
        다시 붙은 화면에서 그 구간이 빈다. 전송은 상대가 멈춰 있을 수 있어 상한을 둔다. 늦었을 뿐
        실제로는 닿았으면 같은 `segment_id` 로 한 번 더 가고 FE 는 덮어쓴다.
        interim 은 다음 것이 덮으니 모으지 않는다.
        """
        await self._settle_interims()
        target = self._client.ws
        if target is not None and await self._send_final_to(message, target):
            return
        self._transcripts.hold_missed(message)
        current = self._client.ws
        if current is not None and current is not target and current is self._caught_up:
            # 보내는 동안 다른 탭이 이어받았고 그 연결은 놓친 final 을 이미 받았다. 다음 재연결까지
            # 기다리지 않고 지금 보낸다. 아직 ready 전인 연결이면 그 연결이 따라잡을 때 같이 간다
            with anyio.move_on_after(FINAL_SEND_TIMEOUT_SEC):
                await self.resend_missed()

    async def _send_final_to(self, message: TranscriptMessage, target: WebSocket) -> bool:
        # ready를 받은 연결 하나에 고정한다. 보내는 동안 이어받기가 일어나면 옛 연결로 성공한
        # 전송도 현재 화면에는 닿지 않은 것이므로 목록에서 지우지 않는다.
        if not self._client.holds(target):
            return False
        sent = False
        with anyio.move_on_after(stt_loop.ABORT_TIMEOUT_SEC):
            sent = await self._client.send_checked(message)
        return sent and self._client.holds(target)
