"""스트림에 지금 붙어 있는 FE 연결 하나와 그 연결로 보내기.

연결은 스트림보다 짧게 산다. 탭을 새로고침하면 바뀌고, 끊겨 다시 붙기를 기다리는 동안(grace)은
비어 있다. 스트림(`runtime.TakeStream`)의 수명 · 정리는 모른다.
"""

import asyncio
import contextlib
from collections.abc import Awaitable

from fastapi import WebSocket
from pydantic import BaseModel

from pitch_coach_backend.realtime.dto import ErrorMessage, WsErrorCode


class ClientSlot:
    def __init__(self) -> None:
        self._ws: WebSocket | None = None
        # 붙어 있던 연결이 Take 를 끝냈거나(stop) 떠났다. 자동 종료가 이걸 보고 FE 의 마지막 1초
        # 조각을 기다린다
        self.done = asyncio.Event()

    @property
    def ws(self) -> WebSocket | None:
        return self._ws

    @property
    def connected(self) -> bool:
        return self._ws is not None

    def holds(self, ws: WebSocket) -> bool:
        """지금 붙어 있는 연결인가. 다른 탭이 이어받아 쫓겨난 연결이면 False."""
        return self._ws is ws

    async def attach(self, ws: WebSocket) -> None:
        """새 연결을 붙인다. 이미 붙어 있으면 **기존 것을 쫓아낸다.**

        반대(새 연결을 거절) 로 하면 재연결 레이스에서 사용자가 영영 못 붙는다 —
        서버는 아직 옛 연결이 살아 있다고 믿는데 브라우저는 이미 그걸 버린 상태가 된다.
        """
        previous = self._ws
        self._ws = ws
        self.done.clear()
        if previous is not None and previous is not ws:
            await close_with(
                previous,
                ErrorMessage(
                    code=WsErrorCode.TAKE_TAKEN_OVER,
                    message="다른 탭에서 같은 연습을 이어받았어요.",
                ),
            )

    def detach(self) -> WebSocket | None:
        """연결을 뗀다. 붙어 있던 연결을 돌려준다."""
        ws, self._ws = self._ws, None
        self.done.set()
        return ws

    async def send(self, message: BaseModel) -> None:
        """붙어 있는 클라이언트에게만. grace 중이면 조용히 버린다."""
        ws = self._ws
        if ws is None:
            return
        await quiet(ws.send_text(message.model_dump_json()))

    async def send_checked(self, message: BaseModel) -> bool:
        """`send` 와 같지만 성공 여부를 알려준다. `resend_missed` 가 재시도를 판단하는 데 쓴다.

        일반 전사·상태 메시지는 실패해도 다음 메시지가 금방 또 오니 무시해도 되지만
        (`send`), 놓친 final 은 화면에 **다시 안 오면 그 구간이 비는 데이터**라 구분한다.
        """
        ws = self._ws
        if ws is None:
            return False
        try:
            await ws.send_text(message.model_dump_json())
        except Exception:  # noqa: BLE001 - 어떤 전송 실패든 "재시도 대상" 으로 취급한다
            return False
        return True


async def close_with(ws: WebSocket, error: ErrorMessage) -> None:
    """에러를 알리고 1008 로 닫는다. 이미 끊겼으면 조용히 넘어간다."""
    await quiet(ws.send_text(error.model_dump_json()))
    await quiet(ws.close(code=1008, reason=error.code))


async def quiet(awaitable: Awaitable[object]) -> None:
    """이미 끊긴 상대에게 보내거나 닫을 때 나는 예외는 무시한다.

    정리 경로라서 넓게 잡는다 — 여기서 예외가 새면 그다음 정리가 통째로 멈춘다.
    """
    with contextlib.suppress(Exception):
        await awaitable
