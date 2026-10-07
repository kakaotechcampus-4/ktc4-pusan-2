"""이벤트를 모으는 곳. 번호는 coach_state.seq 로 매겨 Take 안에서 겹치지 않는다."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from .state import CoachState

E = TypeVar("E", bound=BaseModel)


class EventSink:
    def __init__(self, state: CoachState) -> None:
        self.state = state
        self.events: list[Any] = []

    def emit(self, cls: type[E], **fields: Any) -> E:
        self.state.seq += 1
        event = cls(event_id=f"ev-{self.state.seq:05d}", **fields)
        self.events.append(event)
        return event
