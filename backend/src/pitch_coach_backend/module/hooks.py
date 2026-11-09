"""module 이 바깥(발표 중 처리 realtime · 코치 coach)에 알릴 일을 부르는 자리.

module 은 realtime · coach 를 import 하지 않는다 (README '의존 방향'). 그래서 HTTP 요청이 그쪽에
알릴 일은 여기 세 자리를 거쳐 부른다.

  - `live_takes()`: Take · 발표 · 회원을 지움, 창을 닫은 Take, STT 없이 끝낸 Take (끝내기 PUT),
    로그아웃, 관리자 콘솔이 멈춘 연습을 끝냄
  - `coaching()`: 리뷰 뒤 · 제한 시간을 바꾼 뒤 · 대본을 파싱한 뒤 다음 Take 의 코칭 계획
  - `finalize_recovery()`: 관리자 콘솔이 Take 의 finalize 복구 요청을 내려받거나, finalize 가
    실패한 Take 의 Take 결과를 다시 만들 때

실제 구현은 main.py 가 앱을 만들 때 꽂는다 (`install`). 꽂기 전에는 바깥이 없는 것처럼 군다 — 아무
것도 하지 않고, 끝내기는 DB 에 적기만 한다.
"""

import uuid
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session

# Take 를 끝난 것으로 DB 에 적는다. (자동 종료 이유, 문장 끝 신호)를 받아, 적었으면 True
type MarkEnded = Callable[[str | None, list[int] | None], bool]


class LiveTakes(Protocol):
    """발표 중인 Take 의 스트림 · 코치 (realtime). 요청 스레드(동기 엔드포인트)에서 부른다."""

    def discard(self, take_ids: Iterable[uuid.UUID]) -> None:
        """지운 Take 들의 스트림 · 코치를 끊는다. 지우기를 커밋한 뒤에 부른다."""
        ...

    def is_live(self, take_id: uuid.UUID) -> bool:
        """그 Take 를 어느 창에서 발표 중이다."""
        ...

    def end_closed_tab(self, take_id: uuid.UUID, *, replaced: bool = False) -> None:
        """창을 닫아 실패(`CLOSED`)로 끝낸 Take 의 스트림 · 떠난 Take 타이머 · 코치를 정리한다.
        DB 에 적은 뒤 부른다. `replaced` 면 다른 곳에서 새 Take 를 시작하려고 끝낸 것이다."""
        ...

    def blocking_take(self, user_id: uuid.UUID) -> uuid.UUID | None:
        """그 사용자의 새 Take 를 막는, 어느 창에서 발표 중인 Take. 없으면 None."""
        ...

    def end(self, take_id: uuid.UUID, mark: MarkEnded) -> None:
        """WS stop 없이 Take 를 끝낸다 (끝내기 PUT). 남은 전사 · 원자료를 저장하고 `mark` 로 끝난
        것으로 적은 뒤 Take 결과를 띄운다. 연결이 그 Take 를 끝내는 중이면 아무것도 하지 않는다.
        `mark` 가 실패하면 그 예외를 그대로 올리고, 적기는 뒤에서 다시 한다."""
        ...

    def sign_out(self, user_id: uuid.UUID) -> None:
        """로그아웃한 사용자의 발표 연결을 `UNAUTHORIZED` 로 닫는다. Take 는 끝내지 않는다.
        Refresh 를 폐기한 커밋 뒤에 부른다."""
        ...

    def end_stuck(self, take_id: uuid.UUID, last_active_at: datetime) -> bool:
        """연결 없이 진행 중으로 남은 Take 를 떠난 것(`ABANDONED`)으로 끝낸다. 떠난 Take 타이머가
        끝낼 때와 같고 끝난 시각은 `last_active_at` 이다. 적었으면 True. 스트림이 아직 돌거나
        그 사이 끝났으면 False. 관리자 콘솔이 부른다."""
        ...


class Coaching(Protocol):
    """다음 Take 의 코칭 계획 (coach). 요청 스레드에서도 이벤트 루프에서도 부른다."""

    def plan_next_take(self, pitch_id: uuid.UUID) -> None:
        """그 발표의 다음 Take 코칭 계획을 백그라운드로 요청한다 (coach-agent.md 5-1)."""
        ...


class FinalizeRecovery(Protocol):
    """Take 결과(finalize)의 수동 복구 (coach). 관리자 콘솔이 부른다."""

    def recovery_request(self, db: Session, take_id: uuid.UUID) -> dict[str, Any] | None:
        """finalize 를 원자료(replay)로 다시 부를 요청. 1초 원자료가 없으면 None."""
        ...

    def restart(self, take_id: uuid.UUID) -> None:
        """Take 결과를 기다리는 중으로 되돌린 Take 의 finalize 를 띄운다. 이벤트 루프에서 부른다."""
        ...


class _NoLiveTakes:
    def discard(self, take_ids: Iterable[uuid.UUID]) -> None:
        return None

    def is_live(self, take_id: uuid.UUID) -> bool:
        return False

    def end_closed_tab(self, take_id: uuid.UUID, *, replaced: bool = False) -> None:
        return None

    def blocking_take(self, user_id: uuid.UUID) -> uuid.UUID | None:
        return None

    def end(self, take_id: uuid.UUID, mark: MarkEnded) -> None:
        mark(None, None)

    def sign_out(self, user_id: uuid.UUID) -> None:
        return None

    def end_stuck(self, take_id: uuid.UUID, last_active_at: datetime) -> bool:
        return False


class _NoCoaching:
    def plan_next_take(self, pitch_id: uuid.UUID) -> None:
        return None


class _NoFinalizeRecovery:
    def recovery_request(self, db: Session, take_id: uuid.UUID) -> dict[str, Any] | None:
        return None

    def restart(self, take_id: uuid.UUID) -> None:
        return None


_live_takes: LiveTakes = _NoLiveTakes()
_coaching: Coaching = _NoCoaching()
_finalize_recovery: FinalizeRecovery = _NoFinalizeRecovery()


def install(
    *, live_takes: LiveTakes, coaching: Coaching, finalize_recovery: FinalizeRecovery
) -> None:
    """main.py 가 앱을 만들 때 실제 구현을 꽂는다."""
    global _live_takes, _coaching, _finalize_recovery
    _live_takes = live_takes
    _coaching = coaching
    _finalize_recovery = finalize_recovery


def live_takes() -> LiveTakes:
    return _live_takes


def coaching() -> Coaching:
    return _coaching


def finalize_recovery() -> FinalizeRecovery:
    return _finalize_recovery
