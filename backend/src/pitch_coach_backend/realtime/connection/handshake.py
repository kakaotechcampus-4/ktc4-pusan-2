"""연결 하나의 접속 허용. Origin → auth(JWT) → 인가(DB 1회) 를 통과해야 스트림에 붙는다.

인가는 사용자 · Take 존재 · 소유 · 상태 · 대본 용어를 DB 왕복 한 번으로 읽는다. READY 면 RUNNING
으로 바꾸고 이 Take 에 쓸 값을 고정한 뒤 코치가 들고 있을 값을 읽는다. 거절하면 오류를 한 번 보내고
닫는다.

스트림 · 전사 상태를 보관하지 않는다. 도메인 service(user · take · pitch)와 코치 저장소만 부른다.
"""

import contextlib
import logging
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import cast
from urllib.parse import urlsplit

import anyio
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.coach import store as coach_store
from pitch_coach_backend.coach.requests import TakeContext
from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.database import is_transient_db_error
from pitch_coach_backend.core.exceptions import UnauthorizedException
from pitch_coach_backend.core.security import decode_access_token
from pitch_coach_backend.module.pitch.service import script as script_service
from pitch_coach_backend.module.take.service import lifecycle as lifecycle_service
from pitch_coach_backend.module.take.service import take as take_service
from pitch_coach_backend.module.user import service as user_service
from pitch_coach_backend.realtime.dto import (
    TAKE_END_REASONS,
    AuthMessage,
    AutoEndMessage,
    AutoEndReason,
    ErrorMessage,
    TakeEndReason,
    WsErrorCode,
    client_message_adapter,
)
from pitch_coach_backend.realtime.stream.auto_end import AutoEndPolicy

logger = logging.getLogger(__name__)

# RFC 6455 close code
CLOSE_POLICY_VIOLATION = 1008  # 인증·권한·프로토콜 위반
CLOSE_INTERNAL_ERROR = 1011  # 서버 쪽 일시 오류. FE 는 다시 붙는다
CLOSE_TRY_AGAIN_LATER = 1013  # 받을 자리가 없다. 받기 전이라 브라우저에는 연결 실패로 보인다

AUTH_TIMEOUT_SEC = 5.0

# 인증 전(연결을 받고 auth 를 기다리는 동안) 연결 수 상한. 정상 FE 는 연결하자마자 auth 를 보내 이
# 상태가 왕복 한 번이면 끝난다. 같은 와이파이의 수강생 수십 명이 동시에 새로고침해도 IP 하나에 몇
# 개를 넘지 않는다. auth 없이 붙여 두면 연결 하나가 `AUTH_TIMEOUT_SEC` 동안 남는다
MAX_PENDING_AUTH_PER_IP = 20
MAX_PENDING_AUTH = 200
# 상한에 걸린 연결을 로그에 남기는 간격(초). 걸리는 동안은 연결마다 남기지 않는다
PENDING_AUTH_LOG_SEC = 60.0

# 오디오를 받아도 되는 Take 상태 (FE `TakeStatus` 와 같은 값). 나머지(ANALYZING·COMPLETED·
# FAILED)는 이미 끝난 연습이라 전사를 더 붙이면 리포트가 오염된다.
# READY 는 첫 연결이다. 인가를 통과하면 RUNNING 으로 바꾼다 (`docs/ai/architecture.md` 1-3).
STREAMABLE_TAKE_STATUSES = frozenset({"READY", "RUNNING"})


def origin_allowed(origin: str | None) -> bool:
    """Origin 이 있으면 프론트 주소와 같아야 한다. 없으면(스크립트 등 비브라우저) 통과.

    여기서는 Origin 이 실제 인증 수단이 아니라 추가 방어 수단이다. WS 핸드셰이크
    자체엔 인증 정보가 없고, 연결 뒤 첫 메시지로 Access Token 을 직접 보내야
    `Handshake._authenticate` 를 통과한다 — 쿠키처럼 브라우저가 자동으로
    붙여주는 값이 아니므로, 공격 페이지가 Origin 을 속여 연결 자체는 만들어도
    토큰을 모르면 인증을 통과할 수 없다. 그래서 이 검사를 없애도 CSWSH 는 뚫리지
    않지만, 비용이 0 이라 방어선 하나로 남겨 둔다.

    Origin 이 없는 건 브라우저가 아니라는 뜻이다(서버 간 호출·스크립트 등은
    Origin 을 안 보낼 수 있다). 여기서 None 을 막으면 이런 정상적인 비브라우저
    호출까지 막게 되므로 통과시킨다.
    """
    if origin is None:
        return True
    try:
        given = urlsplit(origin)
        allowed = urlsplit(settings.frontend_base_url)
    except ValueError:
        return False
    return (given.scheme, given.netloc) == (allowed.scheme, allowed.netloc) and bool(given.netloc)


def client_ip(ws: WebSocket) -> str:
    """연결한 쪽의 IP. Caddy 뒤라 `X-Forwarded-For` 의 첫 값이다.

    바깥에서 붙는 첫 Caddy 는 믿지 않는 상대(`infra/Caddyfile` 의 `trusted_proxies` 밖)가 보낸
    `X-Forwarded-For` 를 버리고 상대 주소로 채운다. release 는 그 Caddy 를 한 번 더 거쳐 뒤에 그
    Caddy 주소가 붙으므로 마지막 값이 아니라 첫 값을 쓴다. 헤더가 없으면(Caddy 없이 띄운 로컬) 소켓
    상대 주소다.
    """
    forwarded = ws.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    return ws.client.host if ws.client is not None else "unknown"


class _PendingAuth:
    """인증 전 연결 수. 전체와 IP 별로 센다. uvicorn 워커 하나 · 프로세스 하나가 전제다
    (`stream/state.py`)."""

    def __init__(self) -> None:
        self.total = 0
        self.per_ip: Counter[str] = Counter()
        self._logged_at: float | None = None

    def acquire(self, ip: str) -> bool:
        if self.total >= MAX_PENDING_AUTH or self.per_ip[ip] >= MAX_PENDING_AUTH_PER_IP:
            now = time.monotonic()
            if self._logged_at is None or now - self._logged_at >= PENDING_AUTH_LOG_SEC:
                self._logged_at = now
                logger.warning(
                    "인증 전 연결이 상한이라 받지 않는다 ip=%s ip_count=%d total=%d",
                    ip,
                    self.per_ip[ip],
                    self.total,
                )
            return False
        self.total += 1
        self.per_ip[ip] += 1
        return True

    def release(self, ip: str) -> None:
        self.total -= 1
        self.per_ip[ip] -= 1
        if self.per_ip[ip] <= 0:
            del self.per_ip[ip]


pending_auth = _PendingAuth()


class Handshake:
    def __init__(
        self,
        ws: WebSocket,
        *,
        take_id: uuid.UUID,
        db: Session,
        needs_coach_context: Callable[[], bool],
    ) -> None:
        self.ws = ws
        self.take_id = take_id
        self.db = db
        # 코치가 Take 동안 들고 있을 값을 읽어야 하는가. 인가할 때 묻는다 — 다시 붙은 연결이면
        # 코치는 이미 Take 를 시작할 때의 값을 들고 있다
        self._needs_coach_context = needs_coach_context

        # 통과하면 채운다. entity 가 아니라 id 만 들고 있는다
        self.user_id: uuid.UUID | None = None
        # Take 대본의 STT 용어 (우선순위 순, 자르기 전). 인가 때 같은 DB 왕복에서 읽는다
        self.script_terms: list[str] = []
        # 코치가 Take 동안 들고 있을 값. 인가 때 읽는다. 못 읽으면 코칭 없이 STT 만 돈다
        self.coach_context: TakeContext | None = None

    async def admit(self) -> bool:
        """Origin → auth → 인가. 통과하면 True. 거절했으면 이미 닫았다.

        auth 를 기다리는 동안은 인증 전 연결로 센다 (`pending_auth`). 상한이면 받지 않는다.
        """
        if not origin_allowed(self.ws.headers.get("origin")):
            await self.ws.close(code=CLOSE_POLICY_VIOLATION, reason="origin not allowed")
            return False
        ip = client_ip(self.ws)
        if not pending_auth.acquire(ip):
            # 받기 전에 닫으면 HTTP 403 이 된다. FE 는 연결이 실패한 것으로(닫힘 코드 1006) 보고
            # 백오프로 다시 붙는다 — 1008 로 닫으면 다시 붙지 않는다
            await self.ws.close(code=CLOSE_TRY_AGAIN_LATER, reason="too many connections")
            return False
        try:
            await self.ws.accept()
            user_id = await self._authenticate()
        finally:
            pending_auth.release(ip)
        if user_id is None:
            return False
        if not await self._authorize(user_id):
            return False
        self.user_id = user_id
        return True

    async def _authenticate(self) -> uuid.UUID | None:
        """첫 메시지가 5초 안에 auth 여야 한다. 아니면 1008 로 닫는다. DB 는 안 본다.

        왜 첫 메시지인가 — 브라우저 `WebSocket` 생성자는 URL 과 서브프로토콜만 받고
        **헤더를 붙일 수 없다.** REST 처럼 `Authorization: Bearer` 를 실을 방법이 없고,
        Access 토큰은 XSS 노출을 피하려고 메모리에만 두므로 쿠키로도 못 보낸다.
        남는 건 URL 쿼리(토큰이 접속 로그·히스토리에 남는다) 아니면 연결 뒤 메시지다.
        그래서 핸드셰이크는 그냥 받고(`accept()`), 검증 전에는 오디오를 받지 않는다.
        """
        try:
            with anyio.fail_after(AUTH_TIMEOUT_SEC):
                message = await self.ws.receive()
        except TimeoutError:
            await self.reject(WsErrorCode.UNAUTHORIZED, "인증 메시지가 오지 않았습니다.")
            return None
        if message["type"] == "websocket.disconnect":
            return None

        text = message.get("text")
        if text is None:
            await self.reject(WsErrorCode.UNAUTHORIZED, "첫 메시지는 auth 여야 합니다.")
            return None
        try:
            parsed = client_message_adapter.validate_json(text)
        except ValidationError:
            await self.reject(WsErrorCode.UNAUTHORIZED, "첫 메시지는 auth 여야 합니다.")
            return None
        if not isinstance(parsed, AuthMessage):
            await self.reject(WsErrorCode.UNAUTHORIZED, "첫 메시지는 auth 여야 합니다.")
            return None

        try:
            payload = decode_access_token(parsed.token)
            return uuid.UUID(payload["sub"])
        except UnauthorizedException, ValueError, TypeError:
            await self.reject(WsErrorCode.UNAUTHORIZED, "유효하지 않은 토큰입니다.")
            return None

    async def _authorize(self, user_id: uuid.UUID) -> bool:
        """사용자 존재 · Take 존재·소유 · Take 상태를 DB 왕복 한 번으로 검사한다.

        통과하면 keyterm 에 쓸 대본 용어도 같이 읽어 둔다 (`self.script_terms`).

        없거나 남의 Take 는 `TAKE_NOT_FOUND` 하나로 답한다 (REST 의 `get_owned_pitch` 와
        같은 규칙 — 존재 여부를 흘리지 않는다). 끝난 Take 는 `TAKE_ENDED`. BE 가 자동으로 끝낸
        Take 는 상태가 `ANALYZING` 이라 같이 걸리고, 이유(`end_reason`)는 알림에만 쓴다.
        `READY` 면 `RUNNING` 으로 바꾼다. 다시 붙은 연결(이미 `RUNNING`)은 그대로 둔다.
        바꾸지 못하면(DB 오류) 1011 로 닫는다.
        """

        # 다시 붙은 연결이면 코치는 이미 Take 를 시작할 때의 값을 들고 있다
        need_context = self._needs_coach_context()

        def load() -> tuple[
            bool, bool, bool, str | None, str | None, list[str], TakeContext | None
        ]:
            # 동기 SQLAlchemy 를 이벤트 루프에서 직접 부르면 다른 Take 의 오디오까지 멈춘다.
            # 그래서 threadpool 이고, 왕복을 아끼려 한 함수에서 전부 읽는다
            user = user_service.find(self.db, user_id)
            take = (
                take_service.find_owned(self.db, self.take_id, user_id)
                if user is not None
                else None
            )
            # rollback() 전에 값을 읽어 둔다. rollback() 은 세션의 객체를 전부 expire 시켜서
            # 그 뒤에 take.status 를 읽으면 SQLAlchemy 가 **커넥션을 다시 꺼내 SELECT 를
            # 한 번 더** 날린다 — 커넥션을 돌려준 의미가 없어진다. user 도 같은 이유로
            # id 를 안 읽고 이미 아는 user_id 를 그대로 쓴다
            status = take.status if take is not None else None
            end_reason = take.end_reason if take is not None else None
            terms: list[str] = []
            if status in STREAMABLE_TAKE_STATUSES:
                # 용어는 부가 기능이다. 조회가 실패해도 filler 만으로 STT 는 돈다 — 여기서
                # 예외를 흘리면 연결이 1011 로 닫히고 FE 재연결이 같은 실패를 되풀이한다.
                # 실패한 트랜잭션은 아래 rollback() 이 정리한다
                try:
                    terms = script_service.stt_keyterm_candidates(
                        self.db, take.pitch_id, take.script_version_id
                    )
                except Exception:
                    logger.exception("대본 용어 조회 실패, filler 만 쓴다 take=%s", self.take_id)
            # 조회가 끝나면 커넥션을 풀에 돌려준다. 이 연결은 몇 분씩 살아 있는데
            # 트랜잭션을 연 채로 두면 Take 수만큼 풀이 마른다
            self.db.rollback()
            take_found = take is not None
            started = True
            if status == "READY":
                # 첫 연결이 Take 를 시작한다. 위 조회와 따로 짧은 트랜잭션 하나로 쓴다 — 용어 조회가
                # 실패해 트랜잭션이 깨졌어도 적히고, 커밋하면 커넥션도 돌려준다.
                # 미션 · 평가 기준 버전 · 원자료 사용 동의가 고정되기 전에는 ready 를 보내지 않는다
                # (`docs/ai/architecture.md` 1-3 1번). 못 적으면 연결을 받지 않는다.
                # READY 로 남은 채 발표하면 BE 가 시작하지 않은 Take 로 끝내고
                # (`abandon_stale_ready_takes`) 끝내기도 받지 않는다. FE 는 ready 를 받기 전이라
                # 발표를 시작하지 않았고 다시 붙는다
                for attempt in range(2):
                    try:
                        if not lifecycle_service.mark_running(self.db, self.take_id):
                            # 그사이 다른 연결이 시작했거나 시작하지 않은 Take 로 끝났다.
                            # 다시 읽는다
                            again = take_service.find_owned(self.db, self.take_id, user_id)
                            take_found = again is not None
                            status = again.status if again is not None else None
                            end_reason = again.end_reason if again is not None else None
                            self.db.rollback()
                        break
                    except Exception as error:
                        self.db.rollback()
                        if attempt == 0 and is_transient_db_error(error):
                            logger.warning("Take 시작 값 저장을 다시 한다 take=%s", self.take_id)
                            continue
                        logger.exception("Take 를 RUNNING 으로 못 바꿨다 take=%s", self.take_id)
                        started = False
                        break
            context = None
            if started and status in STREAMABLE_TAKE_STATUSES and need_context:
                # 코칭은 부가 기능이다. 못 읽어도 STT 는 돈다
                try:
                    context = coach_store.load_take_context(self.db, self.take_id)
                except Exception:
                    logger.exception("코치 값을 읽지 못해 코칭 없이 돈다 take=%s", self.take_id)
                self.db.rollback()
            return user is not None, take_found, started, status, end_reason, terms, context

        (
            user_found,
            take_found,
            started,
            status,
            end_reason,
            terms,
            context,
        ) = await run_in_threadpool(load)
        if not user_found:
            await self.reject(WsErrorCode.UNAUTHORIZED, "유효하지 않은 토큰입니다.")
            return False
        if not take_found:
            await self.reject(WsErrorCode.TAKE_NOT_FOUND, "연습을 찾을 수 없습니다.")
            return False
        if not started:
            # 잠깐 DB 가 안 됐다. 1011 이면 FE 가 백오프로 다시 붙는다 (1000 · 1008 만 포기한다)
            await self._quiet(self.ws.close(code=CLOSE_INTERNAL_ERROR, reason="take not started"))
            return False
        if status not in STREAMABLE_TAKE_STATUSES:
            await self.reject_ended(end_reason)
            return False
        self.script_terms = terms
        self.coach_context = context
        return True

    async def reject(
        self, code: WsErrorCode, message: str, *, end_reason: TakeEndReason | None = None
    ) -> None:
        """인증 단계 거절. 에러를 한 번 보내고 1008 로 닫는다."""
        await self._quiet(
            self._send(ErrorMessage(code=code, message=message, end_reason=end_reason))
        )
        await self._quiet(self.ws.close(code=CLOSE_POLICY_VIOLATION, reason=code))

    async def reject_ended(self, end_reason: str | None) -> None:
        """끝난 Take 라 `TAKE_ENDED` 로 거절한다.

        BE 가 자동으로 끝낸 Take 면 그 앞에 `auto_end` 를 한 번 더 보낸다. 자동 종료 순간 떨어져
        있던 FE 는 `auto_end` 를 못 받았다 — 다시 붙을 때 받아야 무대가 종료 흐름을 탄다. 이유가
        이 버전이 모르는 값(나중에 늘어남)이면 FE 도 분기할 수 없으니 일반 문구만 보낸다.

        `TAKE_ENDED` 에는 BE 가 끝낸 이유(`end_reason`)를 싣는다. FE 는 떠난 것으로 끝낸 Take 중
        창을 닫은 것(`CLOSED`)과 STT 없이 발표를 이어 가도 되는 것(`ABANDONED`)을 이걸로 가른다.
        """
        known = cast(TakeEndReason, end_reason) if end_reason in TAKE_END_REASONS else None
        try:
            reason = AutoEndReason(end_reason) if end_reason is not None else None
        except ValueError:
            reason = None
        if reason is None:
            await self.reject(WsErrorCode.TAKE_ENDED, "이미 끝난 연습입니다.", end_reason=known)
            return
        message = AutoEndPolicy.from_settings().message(reason)
        await self._quiet(self._send(AutoEndMessage(reason=reason, message=message)))
        await self.reject(WsErrorCode.TAKE_ENDED, message, end_reason=known)

    async def _send(self, message: BaseModel) -> None:
        await self.ws.send_text(message.model_dump_json())

    @staticmethod
    async def _quiet(awaitable: Awaitable[object]) -> None:
        """이미 끊긴 상대에게 보내거나 닫을 때 나는 예외는 무시한다."""
        with contextlib.suppress(WebSocketDisconnect, RuntimeError, OSError):
            await awaitable
