"""이벤트를 모으는 곳. event_id 는 이벤트의 종류와 대상으로 만든다.

    INTERVENTION  iv-<t_ms>                              OUTCOME   oc-<intervention_id>
    EPISODE       ep-<issue_type>-<장>-<start_ms>        SLIDE     sl-<장>-<start_ms>
    STRATEGY      st-<issue_type>-<장>-<t_ms>            SUPPRESSED su-<issue_type>-<t_ms>

<장> 은 장 번호이고 장이 없으면 none 이다. 번호를 세지 않으므로 같은 이벤트는 다시 만들어도
같은 id 가 된다 — BE 가 두 번 저장해도 finalize 가 거른다.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, TypeVar

from pydantic import BaseModel

from .state import CoachState

E = TypeVar("E", bound=BaseModel)


def _text(value: Any) -> str:
    return str(value.value if isinstance(value, Enum) else value)


def event_id(cls_or_kind: type[BaseModel] | str, fields: dict[str, Any]) -> str:
    """이벤트 종류(클래스 또는 kind 이름)와 필드로 id 를 만든다."""
    kind = cls_or_kind if isinstance(cls_or_kind, str) else cls_or_kind.model_fields["kind"].default
    slide = fields.get("slide_number")
    where = "none" if slide is None else _text(slide)
    match kind:
        case "INTERVENTION":
            return f"iv-{fields['t_ms']}"
        case "OUTCOME":
            return f"oc-{fields['intervention_id']}"
        case "EPISODE":
            return f"ep-{_text(fields['issue_type'])}-{where}-{fields['start_ms']}"
        case "SLIDE":
            return f"sl-{where}-{fields['start_ms']}"
        case "STRATEGY":
            return f"st-{_text(fields['issue_type'])}-{where}-{fields['t_ms']}"
        case "SUPPRESSED":
            return f"su-{_text(fields['issue_type'])}-{fields['t_ms']}"
    raise ValueError(f"알 수 없는 이벤트 종류: {kind}")


class EventSink:
    def __init__(self, state: CoachState) -> None:
        self.state = state
        self.events: list[Any] = []

    def emit(self, cls: type[E], **fields: Any) -> E:
        eid = event_id(cls, fields)
        if cls.model_fields["kind"].default == "INTERVENTION":
            # 개입 이벤트의 intervention_id 는 event_id 와 같다
            fields = {**fields, "intervention_id": eid}
        event = cls(event_id=eid, **fields)
        self.events.append(event)
        return event
